# Auto-Render Model — Design

**Date:** 2026-06-13
**Status:** Design — pending review, then implementation plan.
**Area:** server render pipeline + admin UI (Fleet, Play Now, Content), calibration.

---

## Problem

Today rendering and playback are wired together awkwardly:

- **Render requires a playlist already assigned to a group.** `RENDER {displayID}` (Fleet "Render now") renders `display.mediaElements` — whatever was last assigned. You can't render/stage a playlist for a group without first assigning it. This blocks staging content ahead of time.
- **"Render now" lives in Fleet** (device ops), but it's a content operation.
- **Play Now plays unrendered content.** `firePlayNow()` is fire-and-forget: it sends `ASSIGN_PLAYLIST` + `PLAY` and toasts success without reading the reply. The server *does* gate mosaic content (`PLAY` → `RENDER_REQUIRED` when `has_renderable && token mismatch`), but the UI never surfaces it — so an unrendered SEGMENT/INDIVIDUAL playlist appears to "play" while the server silently refused.
- **Recalibration silently invalidates renders.** Changing a group's calibration changes `compute_render_token`, making its rendered assets stale, with no warning.

## Goals

1. **Render is automatic and decoupled from assignment** — staging works without assigning.
2. **You cannot assign/schedule/play a renderable playlist that isn't rendered (and current) for that group.**
3. **Recalibration warns and auto-re-renders** the affected playlists.
4. **"Render now" leaves Fleet**; render has no manual trigger in normal use.
5. **Play Now shows only content that's ready for the selected group.**

## Non-goals

- Changing the ffmpeg render itself (`render_group_async`, perspective/segment/mosaic encoding) — unchanged.
- Changing the coordinated-start / PREPARE / GO playback path.
- Per-client (vs per-group) render granularity changes.

---

## Core model: render as an automatic per-(playlist × group) asset

A render is a function of **(playlist items, group calibration)** — `compute_render_token` (render.py:295) already hashes the playlist items + the group's `boundingBox` + each client's resolution & measured quad. The same playlist rendered for Lobby ≠ for OEB Sign 1.

So we track render state **per (playlist, group)**, not the single `Display.renderedToken` we have today.

### Data model

On each `Display` (group), add a render registry:

```
Display.renders : dict[str playlistName -> RenderEntry]
RenderEntry = { token: str, state: str, updatedAt: float, error: str|None }
state ∈ { QUEUED, RENDERING, READY, STALE, FAILED }
```

- The legacy single `renderedToken` / `renderStatus` fields are superseded by this registry; keep them only if a migration shim needs them, else remove (and backfill an empty `renders={}` in `migrate_client_objects`).
- A playlist is **READY for a group** iff: it has no renderable items (**N/A** — see below), OR `renders[name].state == READY and renders[name].token == render_token(playlist, group)`.
- **Persistence:** `renders` lives on `Display` and persists in `settings.dat` (jsonpickle). On boot, each `READY` entry is **re-validated** — if its `token` still matches `render_token(playlist, group)` **and** the rendered assets exist on disk, it stays `READY` (no re-render); otherwise it's re-enqueued. Avoids a full-fleet render storm on every restart while self-healing missing/stale assets.

### `render_token(playlist, group)` refactor

`compute_render_token(display_id)` currently hashes the group's *currently applied* `mediaElements`. Generalize it to `render_token(playlist_items, display_id)` so we can compute a token for **any** playlist against a group's calibration (not just the applied one). The existing call site (applied playlist) becomes `render_token(display.mediaElements, display_id)`.

### N/A (nothing to render)

`_is_renderable(me)` = playmode ∈ {SEGMENT, INDIVIDUAL} (render.py:333). A playlist with **no** renderable items (only SCRIPT / FULL / image) is **N/A**: always READY for every group, never rendered, always assignable/playable. The registry can omit N/A playlists (treated as ready by the readiness check).

---

## Render lifecycle & triggers (no manual render in normal use)

### 1. Save playlist → auto-render for all calibrated groups (debounced)

On playlist save (`SAVE_PLAYLIST` / `PUT|POST /api/playlists`), if the playlist has renderable items:

- For **every calibrated group** (group with `boundingBox` and ≥1 calibrated screen), enqueue a render of (playlist, group).
- **Debounce: 60 s.** Coalesce edits of the same playlist — schedule the enqueue 60 s after the last save so a burst of edits (and a settling window) produces one render pass, not one per save. A per-playlist debounce timer, on fire, enqueues the (playlist × calibrated-groups) jobs. (A later save within the window resets the timer; the playlist's render entries show `QUEUED` meanwhile.)
- Set each affected `renders[name]` to `QUEUED` immediately (so UI shows "rendering…" right away).

### 2. Calibrate / recalibrate group → warn + render all that group's renderable playlists

When `calibrate()` (server.py) sets or updates a group's `measuredPerimeter`/`boundingBox`:

- **Recalibrate:** existing `renders[name]` whose `token != render_token(playlist, group)` go `STALE`.
- **First calibration:** the group has no renders yet and needs every renderable playlist to be usable there.
- Either way, the calibrate flow shows a **warning listing the playlists that will render, with a rough ETA** (e.g. *"Calibrating OEB Sign 1 will render 6 playlists (~8 min)."*), then on confirm **auto-enqueues a render of every renderable playlist for that group** — refreshing stale renders and populating a freshly-calibrated group in one rule. This matches auto-render-on-save's invariant (every renderable playlist rendered for every calibrated group). The burst goes through the bounded queue (below), not all at once.

### 3. Delete playlist / delete group → housekeeping

- Delete playlist → remove its `renders[name]` entry from every group + delete its rendered asset files.
- Delete group → drop the group's whole `renders` map + assets.

### 4. Render failure → FAILED + manual retry (only manual render affordance)

If `render_group_async` errors for a (playlist, group), set `renders[name].state = FAILED` + `error`. Surface it in the Content render-status UI with a **Retry** action (re-enqueue that one combo). This is the *only* place a human triggers a render.

### Render queue (bounded concurrency)

Auto-render-on-save fans out to N calibrated groups; a fleet save shouldn't spawn N simultaneous ffmpeg jobs.

- A single background **render queue** processes jobs with **bounded concurrency** (configurable, default 1–2 ffmpeg at a time).
- Each job: (playlist, group). On start → `RENDERING`; on success → `READY` (+ store token); on error → `FAILED`.
- Progress per job surfaced via the existing render-progress broadcast mechanism (extended to carry playlist+group+state).
- Enqueue is idempotent: re-enqueuing an in-flight (playlist, group) is a no-op or supersedes.

---

## Render status & progress (fleet-wide view)

Operators must see render progress across **all** groups, not just a ready/not-ready badge. Each job reports live progress, surfaced in a **Render Status** view.

**Per-job progress.** `render_group_async` runs ffmpeg; parse its progress output (`-progress pipe:` or stderr `frame=`/`time=`) to compute, per (playlist, group):
- `percent` = encoded position ÷ total output duration,
- `eta` = remaining ÷ smoothed encode rate,
- `state` (QUEUED/RENDERING/READY/FAILED), `startedAt`.
A (playlist, group) render is often several sub-encodes (per-screen perspective/segment + mosaic) — aggregate them into one percent for that pair.

**Feed.** A snapshot of every active/queued job `{playlist, group, state, percent, eta, startedAt, error}` + queue depth, exposed via `GET /api/renders` and pushed on change via a throttled (`≤1/s`) `RENDERS_CHANGED` SockJS broadcast.

**UI — Render Status panel.** A global, always-reachable surface (e.g. a header indicator `▣ 3 rendering…` that opens a drawer) listing every in-flight/queued render across all groups: playlist · group · **progress bar + % complete** · **ETA** · state (Retry on FAILED). Idle: "All renders up to date." This is the one place to watch a fleet-wide burst (a calibrate, or a multi-group save) drain.

---

## Gating: assignment requires READY

Every path that points a group at a playlist must check readiness and reject if not `READY`/`N/A`:

- **Play Now** (`firePlayNow` → `ASSIGN_PLAYLIST` + `PLAY`): server rejects assign of a non-ready renderable playlist; **and** `firePlayNow` must **read the response** and toast the real outcome (no more blind "Playing now"). The Play Now picker (below) only lists ready playlists, so this is a backstop.
- **Schedules** (`/api/schedules` create/update + `evaluate_schedules`): reject/validate that (playlistName, displayID) is ready at assign time. If a scheduled playlist is mid-render when its window opens, hold/skip with a logged reason (it'll be ready shortly via the queue).
- **Group default playlist** (`defaultPlaylistName`): same gate.

Server response convention for a blocked assign: `{status: "RENDER_REQUIRED"|"RENDERING"|"FAILED", displayID, name}` — surfaced as a toast.

---

## UI changes

### Play Now (`modals/play-now.js`)
- The picker lists only playlists that are **READY/N/A for the selected group**. Not-ready renderable playlists are shown **disabled** with a "rendering…" / "render failed" hint (so the operator knows *why* it's unavailable rather than it just being absent).
- `firePlayNow` reads the `PLAY` response and toasts the actual result.

### Fleet (`fleet/fleet-view.js`, `admin.html` Fleet section)
- **Remove `renderNow()` + the "Render now" button.** (`RELOAD` stays.)
- Fleet group-detail shows **read-only render readiness** per playlist for that group (e.g. "Menu ✓ · Promo rendering… · Ad failed ⚠") sourced from the group's `renders` map — informational, no trigger.

### Content / playlist view
- New **render-status surface**: per playlist, readiness across calibrated groups — e.g. a badge "rendered 3/3 groups", "rendering…", or "failed on Lobby ⚠ (Retry)". This is where render visibility now lives.

### Recalibration (calibration modal / flow)
- After a successful calibrate that invalidates renders, show the **warning** listing the playlists that will re-render for that group (from the calibrate response), then the auto-re-render proceeds (queued).

---

## Playback during re-render — keep playing stale until ready

When a playing group's assigned playlist is re-rendering (because of an edit or a recalibrate):

- The group **keeps playing the existing (stale) rendered assets** — no interruption, no blackout.
- When the fresh render reaches `READY`, the group **hot-swaps** to the new assets at the next natural loop/item boundary (not mid-clip), via the existing per-client preload/cache-push path.
- Rationale: a wall should never go black for a content edit; a few seconds/minutes of slightly-stale mosaic is preferable to interruption. (Recalibration's stale render is geometrically off, but still strictly better than blackout; the swap closes the gap quickly.)

---

## Edge cases & housekeeping

- **Non-renderable playlist** (SCRIPT/FULL/image only): N/A → always ready; save triggers no render.
- **Group not calibrated:** renderable playlists can't be ready there (can't render without calibration) → not assignable to that group; Content shows "needs calibration" rather than "rendering".
- **Playlist edited to remove all renderable items:** becomes N/A → drop its render entries.
- **New group calibrated:** calibration renders **all** renderable playlists for it (with the warning + ETA above), through the bounded queue — an intentional, warned, throttled burst, not a silent storm.
- **Asset cleanup:** STALE/superseded render assets are removed when their replacement reaches READY or on playlist/group delete.

---

## Components to change

**Server**
- `mosaicmesh/state.py` — `Display.renders` registry + `RenderEntry`; migration backfill (`migrate_client_objects`).
- `mosaicmesh/render.py` — generalize `compute_render_token` → `render_token(items, display_id)`; add the **render queue** (bounded concurrency) + enqueue API; per-job state transitions; asset cleanup.
- save hook — `SAVE_PLAYLIST` (legacy.py:495) and `/api/playlists` create/update (`mosaicmesh/api/playlists.py`) → debounced enqueue across calibrated groups.
- calibrate hook — `calibrate()` (server.py) → mark stale + warn list + enqueue re-renders.
- gating — `PLAY` / `ASSIGN_PLAYLIST` (legacy.py), `mosaicmesh/api/schedules.py`, default-playlist path → readiness check.
- **remove `RENDER` handler** (legacy.py:461) (or repurpose to the failure-retry enqueue).
- API/broadcast — expose per-(playlist,group) render state (extend `/api/playback` or a new `/api/renders`; extend the render-progress broadcast).

**Client**
- `js/timeline/modals/play-now.js` — filter to ready, read PLAY response.
- `js/timeline/fleet/fleet-view.js` + `admin.html` — remove Render now; read-only readiness display.
- Content/playlist view — render-status surface + Retry.
- calibration modal — recalibrate warning (stale-list).
- store/api — render-state hydration + the `RENDERS_CHANGED` broadcast handler.

## Test plan (sketch)

- Unit: `render_token` stability/variance (items, bbox, quad); readiness predicate; debounce coalescing; queue concurrency cap; gating rejects non-ready.
- Integration: save renderable playlist → all calibrated groups go QUEUED→RENDERING→READY; recalibrate → stale-list + re-render; delete → cleanup; failure → FAILED + retry.
- E2e (Playwright): Play Now hides not-ready; Content shows status; Fleet has no Render now; recalibrate warning appears.
- Device: hot-swap keeps stale playing until the fresh render lands (verify via `?tdbg` / no blackout).

## Resolved decisions

These were open during brainstorming and are now settled:

1. **Newly-calibrated group → render all renderable playlists, with a warning.** When a group is first calibrated (or recalibrated), the server enqueues a render of *every* renderable playlist for that group and surfaces a warning + ETA so the operator knows a burst is coming. This is bounded by the render queue (so it can't storm), and it populates a fresh group without the operator having to re-save each playlist.
2. **`renders` persists in `settings.dat`.** Render state survives restart. On boot the server re-validates each entry against the current `render_token` (playlist items + group bbox + per-client quads) **and** the on-disk assets; an entry whose token still matches and whose assets exist stays `READY`, otherwise it drops to `STALE` and re-renders lazily. This avoids a re-render storm on every restart while guaranteeing we never serve an asset that no longer matches its inputs.
3. **Debounce window = 60 s.** A save coalesces into a single render pass after 60 s of quiet, so rapid successive edits to a playlist don't each kick off a full render.
4. **Fleet-wide render status is first-class.** Operators need to see, across all screens, what's rendering: per-job state (running / queued / done / failed), percent complete, and ETA. This is the "Render status & progress" section above — not a follow-up. `GET /api/renders` + throttled `RENDERS_CHANGED` broadcast back the global panel.
