# PAUSE / Resume — Design (playback engine slice 3)

**Date:** 2026-05-25
**Status:** Design approved, pending implementation plan
**Builds on:** `2026-05-25-synchronized-playback-engine-mvp-design.md`, `2026-05-25-video-playlist-items-design.md`

## Context

The playback engine plays per-group playlists (images + video) with manual PLAY/STOP + loop, synchronized via a clock-derived index (`playlist_index`) off a server-supplied `startEpoch`. This slice adds **PAUSE** (and resume), the transport verb deferred from the MVP. `PlayState.PAUSE` already exists in the model but is unused.

## Goals

- A group playing a playlist can be **paused** — every display in the group freezes on the same frame.
- **Resume** continues from where it paused (standard transport: `PLAY` resumes a paused group, starts fresh from a stopped/idle one; `STOP` is the reset).
- No regression to existing PLAY/STOP/loop, images, or video.

## Non-goals (deferred)

Syncing a client that *joins a group while it is paused* (it shows idle until the next PLAY) · split/mosaic (`SEGMENT`) · `SCRIPT` · authoring UI · scheduling.

## Mechanism

Resume is a clock-offset recomputation. The server tracks `playStartEpoch` (server-time ms when playback last started). On pause it records how far in we are; on resume it shifts `startEpoch` back by that amount so `playlist_index(GoTime.now() - startEpoch, ...)` continues from the frozen point.

```
PAUSE:   pauseOffset = now_ms - playStartEpoch        # elapsed into the playlist
RESUME:  playStartEpoch = now_ms - pauseOffset        # continue from pauseOffset
```

## Server (`server.py`)

- Add a transient field `Display.pauseOffset = 0` (ms).
- **`PAUSE { displayID }`** handler (new branch in `msg_response`):
  - `display = settings.displays.get(displayID)`.
  - If `display and display.action == PlayState.PLAY`: `display.pauseOffset = int(time.time()*1000) - display.playStartEpoch`; `display.action = PlayState.PAUSE`.
  - Broadcast `PAUSE { displayID }` to the group via `broadcast_to_display_group`.
  - `response["PAYLOAD"] = "SUCCESS"`.
- **`PLAY { displayID }`** handler change (resume-aware):
  - When starting, if `display.action == PlayState.PAUSE`: `display.playStartEpoch = int(time.time()*1000) - display.pauseOffset` (resume); else `display.playStartEpoch = int(time.time()*1000)` (fresh).
  - Then set `display.action = PlayState.PLAY` and broadcast `PLAY { startEpoch, items, loop }` exactly as today.
  - (The existing guard `if display and display.mediaElements:` stays.)
- **`STOP`** unchanged — it remains the reset (`action = STOP`, `currentFrame = 0`); a subsequent `PLAY` then starts fresh because `action != PAUSE`.

## Client (`index.html`, ES5)

- Add `playback.paused` (boolean) to the `playback` state object.
- **`PAUSE`** message branch in `mosiacMeshCallback` (sibling of PLAY/STOP):
  ```javascript
  else if(data_obj.REQUEST == "PAUSE")
  {
      pausePlayback();
  }
  ```
  `pausePlayback()`:
  - `playback.paused = true;`
  - clear the schedule timer: `if (playback.timer) { clearTimeout(playback.timer); playback.timer = null; }`
  - stop the drift loop: `if (playback.driftTimer) { clearInterval(playback.driftTimer); playback.driftTimer = null; }`
  - freeze video in place: `if (playback.video) { try { playback.video.pause(); } catch (e) {} }`
  - The current frame stays displayed (no DOM change).
- **`PLAY`** branch: add `playback.paused = false;` before calling `renderPlayback()` (the handler otherwise unchanged — it sets `items`/`startEpoch`/`loop`/`active` from the payload). Because the server supplies the resumed `startEpoch`, `renderPlayback` re-shows/seeks the current item (rebuilding the `<video>` and seeking to the offset for video) and resumes scheduling + the drift loop. The same path serves resume and fresh start.
- **`renderPlayback`**: add a guard at the top — `if (playback.paused) { return; }` — so a stray timer can't advance a paused playlist.
- **`stopPlayback`**: add `playback.paused = false;` (STOP fully resets).

## Edge cases

- PAUSE on a group that isn't playing: server no-ops the state change (guarded by `action == PlayState.PLAY`) but still broadcasts PAUSE; clients freeze harmlessly (nothing scheduled).
- Resume after the natural end of a non-loop playlist: `pauseOffset` ≥ total ⇒ `playlist_index` returns null ⇒ `renderPlayback` calls `stopPlayback` (idle). Acceptable.
- A client joining a paused group: shows idle until the next PLAY (deferred, see Non-goals).

## Testing

- **pytest** (`tests/unit/test_playback.py`): 
  - PAUSE on a playing group sets `action == PlayState.PAUSE`, sets `pauseOffset == now_ms - playStartEpoch` (mock `time.time`), and broadcasts once.
  - PLAY on a paused group sets `playStartEpoch == now_ms - pauseOffset` (resume) and `action == PlayState.PLAY`.
  - PLAY on a stopped group sets `playStartEpoch == now_ms` (fresh) — regression guard that resume logic doesn't leak into fresh starts.
- **Playwright:** start a looping playlist; send PAUSE → the clock-derived frame index stays constant over ~3s and (for a video item) `video.paused === true`; send PLAY → playback resumes from the frozen frame (not frame 1) and the index advances again; send STOP → idle.

## ES5 / legacy

All client additions stay ES5 (1st-gen iPad). No new client libraries.
