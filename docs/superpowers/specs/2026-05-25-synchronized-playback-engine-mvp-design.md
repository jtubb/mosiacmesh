# Synchronized Playback Engine — MVP (vertical slice)

**Date:** 2026-05-25
**Status:** Design approved, pending implementation plan

## Context

MosaicMesh has the *substrate* for synchronized media playback but none of the *logic*:

- **Data model exists, unused.** `Display` carries `mediaElements[]`, `loop`, `currentFrame`, `action` (`PlayState`: NOACTION/STOP/PLAY/PAUSE). `MediaElement` has `id`, `file`, `duration`, `playmode` (`PlayMode`: DEFAULT/FULL/SEGMENT/SCRIPT). Persisted in `settings.dat` via jsonpickle. Nothing reads or writes it (only a dead startup stub).
- **Plumbing exists.** Upload (`POST /upload/image` → `media/server/images/`), serving (`media_handler`, images + mp4 with range/streaming), and a partial client `PRELOAD` handler (caches image URLs hidden, sets `mediaReady` → green heartbeat) — but nothing sends `PRELOAD` and nothing renders/sequences the cached media.
- **Sync substrate ready.** GoTime clock-sync + the 1s `ProgrammableTimer` tick + the settled heartbeat give a shared clock accurate to ~±8ms across displays.

This slice proves the high-value, high-risk core — *synchronized playback* — end to end, reusing that substrate.

## Goals

- One display group plays an **identical-mode, image-only** playlist.
- Manual **PLAY / STOP** with a **loop** flag.
- **Frame-accurate**, clock-synchronized advance: every display in the group shows the same item at the same instant.
- Playlist persists in `settings.dat`.
- A client that **joins mid-playback** catches up in sync.

## Non-goals (later slices)

PAUSE (next slice) · video items · split/mosaic mode (`SEGMENT`) · synchronized JS animations (`SCRIPT`) · admin authoring UI · media-library listing · scheduling · auto-resume of active playback across a server restart.

## Core mechanism — clock-derived, frame-accurate scheduling

The server does **not** push "show frame N" messages. It sends **PLAY once** with a `startEpoch` (server-time ms), the item durations, and the loop flag. Each client computes the current item itself from the shared clock:

```
elapsed = GoTime.now() - startEpoch          // GoTime.now() ~= server time ms
if loop: elapsed = elapsed % totalDuration
walk cumulative durations -> { index, offsetMs }   // or "ended" when !loop and past the end
```

Because `GoTime.now()` is synchronized across displays, every display independently lands on the **same `index` at the same wall-clock instant** — synchronized with zero ongoing server traffic and resilient to dropped messages.

**Frame-accurate boundaries:** when a client shows item `i` at `offsetMs`, it schedules the next transition exactly `duration[i] - offsetMs` ms out via `setTimeout`. When that fires it **recomputes `{index, offsetMs}` from `GoTime.now()`** rather than blindly incrementing — this self-corrects against `setTimeout` drift and against the page being briefly busy, and keeps boundaries locked to the synchronized clock.

## Data model

- **Playlist = `Display.mediaElements`** (the group's ordered items). `Display.loop` = loop flag. `Display.action` = `PlayState`.
- Add a transient `Display.playStartEpoch` (server-time ms when playback last (re)started). Persisted is fine, but on restart playback resets to STOP (see Persistence).
- `Display.currentFrame` is **not** the playback source of truth in this model (the clock is); it stays at 0 / is left vestigial here. Kept only because it is part of the persisted shape.
- `MediaElement`: `id`, `file` (path under `/media/...`), `duration` (ms), `playmode`.
- **`playmode` usage:** MVP uses `FULL` (identical full-screen). Reserved for later slices: `SEGMENT` (a spatial crop of a larger image, the split/mosaic mode, driven by ArUco calibration geometry), `SCRIPT` (a named client-side JavaScript animation — e.g. bouncing balls — that reads `GoTime.now()` so all displays animate in unison; `file` would name the animation rather than a media asset), `DEFAULT`.

## Messages (REQUEST-based protocol, handled in `msg_response`)

All group fan-out uses `broadcast_to_display_group(displayID, msg)` (central socketmanager + DEST).

- **`SETPLAYLIST { displayID, items: [{file, duration, playmode}], loop }`** → store items + loop on the group's `Display`; broadcast `PRELOAD { items }` to the group so clients cache the images (drives the green heartbeat). Response `SUCCESS`.
- **`PLAY { displayID }`** → `action = PLAY`, `playStartEpoch = int(time.time()*1000)`; broadcast `PLAY { startEpoch, items, loop }` to the group.
- **`STOP { displayID }`** → `action = STOP`, `currentFrame = 0`; broadcast `STOP` to the group.
- **Mid-playback join:** when a client registers/joins a group whose `action == PLAY`, the server sends it `PRELOAD` + `PLAY { startEpoch, items, loop }` so it syncs to the in-progress playlist.

## Client rendering (`index.html`, ES5 / iOS 5-safe)

- Maintain a `playback` state object: `{ items, startEpoch, loop, active }`.
- On `PRELOAD`: cache images hidden (existing behavior) and set `mediaReady`.
- On `PLAY`: store state, `active = true`, hide the TICK/TOCK text, render the current item and start the scheduled-transition loop (see core mechanism). `showItem(index)` toggles visibility of the preloaded `<img>` for that item.
- On `STOP`: `active = false`, clear any pending transition timer, restore idle (TICK/TOCK).
- Advance is driven by the self-correcting `setTimeout` chain, independent of the 1s heartbeat tick (which keeps doing the heartbeat).

## Shared logic — `playlist_index` (the testable core)

A pure function `playlist_index(elapsedMs, durations, loop) -> { index, offsetMs } | null`:

- Sum `durations` → `total`. If `total == 0` → `null`.
- If `loop`: `elapsedMs %= total`. Else if `elapsedMs >= total` → `null` (ended).
- Walk cumulative durations to find the item containing `elapsedMs`; return its `index` and `offsetMs` within it.

Implemented in JS (client) and **mirrored in Python** purely so the math can be unit-tested in the existing pytest suite.

## Persistence & edge cases

- `mediaElements` + `loop` persist in `settings.dat`. On server start, groups reset to `action = STOP` (matches the existing "send stop" startup stub) — no auto-resume in this slice.
- Empty playlist → PLAY is a no-op / idle. Non-loop playlist past its end → client clears its playback state and returns to idle (TICK/TOCK); no special message required in this slice.
- Late joiner handled via the mid-playback join path above.

## Testing

- **pytest:** `playlist_index` (Python mirror) across cases — single item, multiple, loop wrap, non-loop end, zero-duration/empty. `SETPLAYLIST` / `PLAY` / `STOP` handlers assert `Display` state changes and that `broadcast_to_display_group` fans out (mocked `socketmanager`).
- **Playwright:** drive two simulated clients (or two evaluations sharing the synchronized clock) and assert they compute the **same `index` at the same instant**; confirm a transition fires within a few ms of the clock-computed boundary; verify PLAY → render, STOP → idle.

## ES5 / legacy constraint

All client additions stay ES5 (1st-gen iPad / iOS 5 / Safari 5.1); no new client libraries. Server-side Python is unconstrained.
