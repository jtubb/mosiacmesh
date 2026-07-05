# Client-Side Clip Warming — Design

**Date:** 2026-07-05
**Status:** Design (approved for planning)

## Goal

Eliminate the cold, glitchy clip-to-clip transition on video playlists by **warming the next clip** (pre-loading + pre-decoding it) while the current one plays, then flipping to the already-warm element on advance. This is a **per-screen smoothness** improvement, implemented in the MosaicMesh **client JS** so it works across the whole device fleet, not just the legacy iPad-1.

## Non-goals / scope

- **Not** a sync feature. Cross-screen frame-sync is already handled by the existing `driftTick` seek/rate loop + `GoTime.steerTick`; warming is orthogonal and leaves that untouched.
- **iPad-1 warming is out of scope — verified infeasible.** An empirical two-`<video>` test on the iPad-1 (2026-07-05) showed both clips decoded for ~1s, then one went black and the other froze: the A4 cannot sustain two concurrent H.264 decode sessions, and the mmvideo transplant is single-video-architected. The iPad-1 therefore keeps today's single-element behavior (graceful degradation), and becomes warm automatically as it is replaced by modern devices.
- **MSE** (segment streaming) is out of scope; it targets in-clip gapless streaming, not whole-clip playlists, and isn't supported on iOS-5 anyway.

## Verified constraints (do not re-derive)

- iPad-1 (A4 / iOS 5.1) cannot run two concurrent inline video decodes → no warming there.
- Item advance is **schedule/GoTime-driven, not `'ended'`-driven**: `playback` computes `pos = playlistIndex(GoTime.now() - startEpoch, durations, loop)` each tick and advances when `pos.index` changes (index.html:915-916). The client **already holds the full playlist** (`playback.items[]`, each `{file, duration}`) — the next item is already known client-side; no protocol is needed to discover it.
- The sync loop, render, and `?tdbg` HUD all reference the one persistent `<video>` today. They must be redirected to reference "the active element" so they are agnostic to how many elements exist.
- The server already classifies the legacy device via `_is_legacy_ipad_signal(...)` (legacy.py:250).

## Architecture

Introduce a **video-buffer manager** (`vbuf`) in the client that owns the `<video>` element(s) and exposes a single "active element" to the rest of the code.

- **Warmable device (modern):** `vbuf` owns **two** `<video>` elements, `A` and `B`. `vbuf.active()` returns the visible/playing one; the other is the **warm buffer**.
- **Non-warmable device (iPad-1):** `vbuf` owns **one** `<video>` (today's `pvid`). `vbuf.active()` always returns it; all warm-buffer operations are no-ops. Behavior is byte-for-byte today's single-element path.
- **All existing consumers key off `vbuf.active()`** — the `driftTick` sync loop, GoTime, the render, arming, the HUD. None of them learn there may be two elements; the sync machinery is unchanged.

The 1-vs-2 branch is decided **once at setup** from the server-delivered `warmable` flag.

### Isolation / interfaces

`vbuf` (client module — new, or a self-contained IIFE section in index.html):
- `vbuf.setup(warmable)` — create 1 or 2 elements; establish the active pointer.
- `vbuf.active()` — the current live `<video>` (what everything else uses).
- `vbuf.warmNext(nextIndex, item)` — preload `item.file` onto the idle (buffer) element (no-op if not warmable, if the buffer already holds it, or if `item` is a non-video).
- `vbuf.flipTo(item)` — reveal the warm buffer, play it, hide/pause the old active, swap the pointer; returns `true` if the buffer was warm, `false` if a cold load is required instead.

A small **pure helper** `nextPlaylistIndex(curIndex, itemCount, loop)` computes the next index (loop-wrapped or clamped) — extracted so it is node-testable and shares semantics with `playlistIndex`.

## Data flow

**Preload (each tick, during the current clip):** alongside the existing `pos = playlistIndex(...)`, compute `nextIndex = nextPlaylistIndex(pos.index, items.length, loop)`. If warmable and `items[nextIndex]` is a video and the buffer isn't already holding it, call `vbuf.warmNext(nextIndex, items[nextIndex])` (sets `buffer.src`, `buffer.load()`; `preload='auto'` buffers + first-frame-decodes, paused at 0 = warm).

**Advance (`pos.index` changes):** call `vbuf.flipTo(items[pos.index])`.
- If the buffer was warm (holds this item) → reveal it, `play()` (instant), hide/pause the old active, swap the active pointer. Then preload the *new* next item on the now-idle element.
- If not warm (preload failed / raced / warming disabled) → fall back to loading the item on the active element (today's cold path).

**Sync composes untouched:** on a flip the new item is at offset ≈ 0, which is where the schedule places it; `driftTick` then seeks/rate-nudges `vbuf.active()` to the GoTime offset exactly as today.

**Cold-start exception:** the first item has nothing to warm from → load it on `active()` directly (one time). Every subsequent video→video transition is warm.

**Non-video next item** (SCRIPT animation / image): nothing to preload; `warmNext` skips; the advance uses the existing non-video handling.

**Single-item / no-video-next playlists:** `warmNext` is a no-op (a single looping clip needs no swap element).

## Fingerprint wiring

- **Server:** in the `REGISTER` handler (`mosaicmesh/websocket/legacy.py`), derive `warmable = not _is_legacy_ipad_signal(...)` (extended to also treat any iOS ≤ 5 / Safari ≤ 5 / legacy-WebKit `engine` as non-warmable). Send it to the client **per-client** via a new message `{REQUEST: "CONFIG", PAYLOAD: {warmable: bool}}` through the existing `broadcast_to_client`, emitted from the REGISTER handler.
- **Client:** add a `data_obj.REQUEST == "CONFIG"` branch (alongside the existing PREPARE/PLAY/PRELOAD handlers) that stores `warmable` and calls `vbuf.setup(warmable)` once. Default `warmable=false` if the flag/message is absent (safe: no warming = today's behavior). Because `vbuf.active()` must exist before the first PREPARE arms a video, `vbuf` initializes as single-element (non-warmable) at page load and upgrades to two-element only when a `CONFIG` with `warmable=true` arrives.

## Error handling

- **Preload error / buffer not warm in time:** `flipTo` returns `false` → cold-load on the active element (today's behavior). Never worse than today.
- **Playlist replaced mid-play (new PREPARE):** the buffered item may be stale → on the next tick, `warmNext` re-preloads the correct `nextIndex` (idempotent: it checks what the buffer holds).
- **`warmable` flag missing:** treated as `false` (graceful, no warming).
- **Modern device that still can't double-decode** (unexpected): the flip fails / stutters, `flipTo` cold-fallback covers it; no crash.

## Testing

- **Node unit tests** (`tests/unit/js/`): `nextPlaylistIndex` (loop wrap, clamp, single item, empty); a `vbuf` state machine test against a mock `<video>` (setup 1 vs 2, warmNext idempotency + non-video skip, flip warm vs cold-fallback, pointer swap).
- **Python unit test** (`tests/unit/`): `warmable` derivation from representative user-agents (iPad-1 → false; modern iPad / desktop Chrome / modern Safari → true).
- **Manual / on-device:** modern browser — confirm the clip transition is visually seamless (no black flash) and `?tdbg` shows sync unaffected; iPad-1 — confirm behavior is unchanged from today (single element, no regression) and no second element is created.
