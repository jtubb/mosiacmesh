# Stale Render-Asset Garbage Collection — Design

**Date:** 2026-06-15
**Status:** Approved — ready for implementation plan.

## Problem

The auto-render model writes per-screen assets to disk named by a content+calibration
hash (`render_token`): `media/<clientKey>/{images,videos}/{seg_,ind_}<token>_<i>.{png,mp4}`
for SEGMENT/INDIVIDUAL, and `media/server/{images,videos}/full_<token>_<i>.{png,mp4}`
for FULL (Mirror).

When a `(playlist × group)` re-renders — because the playlist was edited, the group
recalibrated, or `encode_ver` bumped — the `render_token` changes and a **new** set of
files is written under the new token. The **old** token's files are never reclaimed.
Today the only deletion paths are `cleanup_playlist_renders` (playlist delete) and
`cleanup_group_renders` (group delete) in `mosaicmesh/render.py`; nothing removes
superseded assets while the playlist/group still exists. Server disk therefore grows
monotonically across every re-render.

(The on-device iPad cache *is* swept — `_reconcile_ipad_cache` runs every ~5 s in
`process()` — but the server's own `media/` tree is not.)

## Central safety constraint

`render_token(media_elements, display_id)` (`render.py:345`) hashes
`(items, boundingBox, clients, encode_ver)` — **not the playlist name**. Therefore two
*different* playlists with identical item lists on the *same* group hash to the **same
token** and **share the same files on disk**. A naive "delete the old token's files when
this playlist re-renders" would delete files another playlist's READY entry still serves.

Every deletion in this design is gated by a single predicate:

```
_token_is_live(token) -> bool
    True if ANY display.renders[*]["token"] across ALL groups equals `token`,
    OR ANY display.renderedToken equals `token`. Else False.
```

Nothing is deleted unless its token is referenced **nowhere** in the registry or as a
live serving token. This makes the shared-token case correct by construction (a shared
token is, by definition, still live) rather than by special-casing.

## Components

### 1. `_token_is_live(token)` — shared guard (new, `render.py`)

Pure-ish read over `server.settings.displays`:

```python
def _token_is_live(token):
    import server
    if not token:
        return False
    for display in server.settings.displays.values():
        for e in (getattr(display, "renders", {}) or {}).values():
            if e.get("token") == token:
                return True
        if getattr(display, "renderedToken", "") == token:
            return True
    return False
```

### 2. `_delete_token_assets(token, display_id)` — token-scoped delete (refactor, `render.py`)

The existing `_delete_render_assets(playlist_name, display_id)` (`render.py:897`) already
globs and removes a group's `seg_/ind_/full_<token>_*` files, but reads the token from the
registry entry. Generalize it to take an explicit token; `_delete_render_assets` becomes a
thin wrapper that looks up the entry's token and delegates. No glob logic is duplicated.
Best-effort: each `os.remove` is wrapped in `try/except OSError`.

### 3. Lifecycle delete-after-READY (modify `render_playlist_for_group_async`, `render.py:726`)

- **Capture early.** At the top of the function, before the `RENDERING` `_set_render_state`
  call overwrites `entry["token"]`, capture:
  `prev_token = (display.renders.get(playlist_name) or {}).get("token")`.
  (This is required: the RENDERING transition at `render.py:742` writes the *new* token, so
  the old value is gone by the time we reach READY.)
- **Delete on success.** After the entry flips to `RENDER_READY` with the new token, if
  `prev_token` is truthy, `prev_token != token` (new), and `not _token_is_live(prev_token)`,
  call `_delete_token_assets(prev_token, display_id)`.
- **Failure leaves old intact.** On the FAILED branch nothing is deleted — the previous
  working assets survive a failed re-render.

### 4. Boot orphan sweep — `sweep_orphan_render_assets()` (new, `render.py`)

Called once from `server.py`'s `__main__` block, immediately after
`revalidate_renders_on_boot()` (so the registry/`renderedToken` state is already
revalidated and the live-token set is accurate).

```python
def sweep_orphan_render_assets():
    # 1. Build the live-token set once (all registry tokens + all renderedToken).
    # 2. Walk media/*/{images,videos}/ and media/server/{images,videos}/.
    # 3. For each filename matching the STRICT pattern, extract the token;
    #    if the token is not in the live set, os.remove (best-effort).
```

Strict filename pattern (the blast-radius guard):

```
^(seg|ind|full)_([0-9a-f]{12})_\d+\.(mp4|png)$
```

Uploaded source media (arbitrary names) and `aruco.png` do **not** match this pattern, so
the sweep can only ever touch rendered assets — even in `media/server/`, where FULL assets
live alongside uploaded source. The token capture group (`[0-9a-f]{12}`) matches the
`sha1(...)[:12]` shape that `render_token` produces.

**Boot-only, not periodic.** The lifecycle path (Component 3) covers steady-state
supersession. The sweep's job is the one-time concerns: reclaiming cruft accumulated before
this feature existed, and assets a mid-render crash orphaned. A startup pass handles both;
a periodic disk-walk would add recurring cost for no steady-state benefit.

## Data flow

```
edit / recalibrate / encode_ver bump
        │
        ▼
render_token changes ──► is_playlist_ready False ──► re-render enqueued
        │
        ▼
render_playlist_for_group_async
   capture prev_token  ──►  RENDERING (writes new token)  ──►  encode
        │                                                        │
        │                                                  success ▼
        │                                          READY (new token written)
        │                                                        │
        ▼                                                        ▼
   (on FAILED: keep all)                  if prev_token && prev_token != new
                                          && !_token_is_live(prev_token):
                                              _delete_token_assets(prev_token, group)

server boot
   load settings.dat ──► migrate ──► revalidate_renders_on_boot()
                                          │
                                          ▼
                              sweep_orphan_render_assets()
                              (delete any seg_/ind_/full_<token> whose
                               token is in no registry entry / renderedToken)
```

## Error handling / edge cases

- **Best-effort deletes.** Every `os.remove` is wrapped in `try/except OSError`; a
  missing, locked, or in-use file is skipped and never aborts the render or the sweep
  (mirrors existing `_delete_render_assets`).
- **Shared token.** Handled by `_token_is_live` — a token referenced by any other entry
  or `renderedToken` is never deleted.
- **No registry at all** (fresh boot, empty `media/`): sweep walks nothing, returns
  cleanly.
- **FULL assets in `media/server/`.** Same directory as uploaded source; the strict regex
  is what prevents the sweep from deleting uploads.
- **STALE entries.** A STALE entry's `token` field holds the *new* (not-yet-rendered)
  token, so that token counts as live and its (future) files are protected; the old
  superseded files it replaced are already unreferenced and get reclaimed.

## Testing / validation

All unit-level — no ffmpeg, no SSH, no real media encode required.

- **`_token_is_live`:** true for a token present in another group's registry entry; true
  for a token equal to some `display.renderedToken`; false for an unreferenced token;
  false for empty/`None`.
- **Lifecycle delete:** seed a display with a READY entry (old token) + its files on a
  temp `media/` tree; drive a re-render to a new token; assert old-token files removed,
  new-token files present. Then the shared-token case: a second display/entry still
  referencing the old token → assert old-token files **kept**.
- **Sweep:** seed a temp `media/` with (a) live-token files, (b) orphan-token files,
  (c) non-matching files (an uploaded `myvideo.mp4`, an `aruco.png`); assert only the
  orphan-token files are deleted.

## Non-goals

- No periodic/background sweep (boot-only).
- No change to the on-device iPad cache reconciliation (already handled by
  `_reconcile_ipad_cache`).
- No GC of uploaded *source* media — that stays operator-driven via `DELETE /api/media`.
- No change to `render_token`, the registry state machine, or `RENDERS_CHANGED`.
