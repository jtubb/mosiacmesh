# FULL renders for uncalibrated display groups — design

**Date:** 2026-07-09
**Status:** approved (brainstorming)

## Problem

Display groups that are **not ArUco-calibrated** (e.g. the `Desktop` / `Mobile`
groups, or any single modern-browser client) cannot get a render, and therefore
cannot receive an auto-PRECACHE, even for `FULL` (mirror) media that does not
need calibration at all.

This surfaced while verifying the modern (Service-Worker / Cache-API) client-pull
cache backend end-to-end: a `Desktop`-group Chromium correctly reaches
`cacheMode="service-worker"` and its Cache-API + SW serve a segment correctly
(proven 2026-07-09), but the server never fires a PRECACHE at it because there is
no render — and a render can't be triggered for an uncalibrated group.

## Root cause (grounded in code)

The FULL encode path already works with **no calibration**: `_encode_group`
(`mosaicmesh/render.py`) transcodes/downscales the source and targets *all* group
clients for the shared `full_<token>_<i>` asset (`render.py:701`, commented
"FULL mirrors on every screen regardless of calibration"). Only `SEGMENT` /
`INDIVIDUAL` items consume `measuredPerimeter` / `boundingBox`.

The blockers are purely the **trigger gates**, both keyed on `_group_is_calibrated`
(which requires a `boundingBox` + ≥1 client with a measured perimeter):

1. `RENDER` websocket handler (`mosaicmesh/websocket/legacy.py:589`) — rejects with
   `"group not calibrated"`.
2. Auto-enqueue on playlist save
   (`mosaicmesh/render_queue.enqueue_playlist_for_calibrated_groups`) — only enqueues
   for calibrated groups.

## Design

Introduce one predicate and apply it at both trigger points. No change to the
encode pipeline, the PRECACHE two-gate logic, or any calibrated-group behavior.

### The predicate

A playlist **needs calibration** iff it contains at least one `SEGMENT` or
`INDIVIDUAL` item (the two playmodes that warp per-screen and read
`measuredPerimeter` / `boundingBox`). `FULL` and `SCRIPT` items never need it.

```
def _playlist_needs_calibration(items) -> bool:
    return any(item playmode in (SEGMENT, INDIVIDUAL) for item in items)
```

A group may render a playlist iff:

```
_group_is_calibrated(display_id)  OR  not _playlist_needs_calibration(playlist.items)
```

Live in `mosaicmesh/render.py` next to `_group_is_calibrated`, exported through
`server` like its neighbors, as a small pure helper (unit-testable in isolation).

### Trigger point 1 — `RENDER` handler (`legacy.py:589`)

Replace the bare `elif not _group_is_calibrated(display_id):` guard with the
combined predicate. When the playlist needs calibration AND the group is
uncalibrated → keep the existing `{"status":"ERROR","error":"group not calibrated"}`
response (unchanged semantics for that case). Otherwise proceed to
`render_queue.enqueue(name, display_id)` exactly as today.

### Trigger point 2 — auto-enqueue on save

`enqueue_playlist_for_calibrated_groups(playlist_name)` is **defined in
`mosaicmesh/render.py`** and called from `mosaicmesh/render_queue.py:90` (via the
`R.` render-module alias); it currently iterates calibrated groups only. Widen it
to also include uncalibrated groups **when the playlist does not need
calibration**. Rename to `enqueue_playlist_for_eligible_groups` (the old name
becomes inaccurate) and update all references: the definition (`render.py`), the
caller (`render_queue.py:90`), and the monkeypatch in
`tests/unit/test_render_queue.py:74`. Group eligibility =
`_group_is_calibrated(g) or not _playlist_needs_calibration(items)`.

### Decisions

- **Mixed playlist** (contains both `FULL` and `SEGMENT`/`INDIVIDUAL`) on an
  uncalibrated group → **refused** (it "needs calibration"). Only entirely
  `FULL`/`SCRIPT` playlists render on uncalibrated groups. This preserves the
  invariant that a READY render means the *whole* playlist is playable — no
  partial-readiness state is introduced.
- **Scope** — both the manual `RENDER` trigger and the automatic on-save enqueue.

## What is explicitly unchanged

- `_encode_group` — already renders FULL without calibration.
- The PRECACHE two-gate logic (`_client_is_push_eligible` includes `service-worker`;
  URL-rewrite stays `lighttpd-localhost`-only).
- All calibrated-group behavior — calibrated groups still render every playmode.
- `_group_is_calibrated` itself — unchanged; it is now one term in a larger OR.

## Testing

Pure-function + handler unit tests (no ffmpeg/browser needed):

1. `_playlist_needs_calibration`: FULL-only → False; SCRIPT-only → False;
   FULL+SCRIPT → False; any SEGMENT → True; any INDIVIDUAL → True; empty → False.
2. `RENDER` handler: FULL-only playlist on an uncalibrated group → `QUEUED`
   (previously `ERROR`); playlist with a SEGMENT item on an uncalibrated group →
   still `ERROR "group not calibrated"`; any playlist on a calibrated group →
   `QUEUED` (unchanged).
3. `enqueue_playlist_for_eligible_groups`: a FULL-only playlist enqueues for an
   uncalibrated group; a playlist needing calibration does not enqueue the
   uncalibrated group; calibrated groups always enqueue.

## Out of scope

- Auto-calibration or degenerate boundingBox synthesis for uncalibrated groups.
- Partial rendering of mixed playlists.
- Any change to how modern clients reach a secure context (deployment concern,
  see `modern-cache-needs-secure-context` memory).
