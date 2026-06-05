# Video Playlist Items — Design (playback engine slice 2)

**Date:** 2026-05-25
**Status:** Design approved, pending implementation plan
**Builds on:** `2026-05-25-synchronized-playback-engine-mvp-design.md`

## Context

The synchronized playback engine MVP plays identical-mode, image-only, per-group playlists with manual PLAY/STOP + loop, using a clock-derived index (`playlist_index`) and frame-accurate self-correcting scheduling (`renderPlayback`). This slice adds **`.mp4` video items** to that same engine.

No server protocol changes are required: `SETPLAYLIST`/`PLAY`/`STOP` already pass items through verbatim, and `playlist_index` already schedules advances from per-item `duration`. The new work is entirely client-side rendering + synchronization of the `<video>` element.

## Goals

- A playlist may contain `.mp4` items alongside images, in the same group, with the same manual PLAY/STOP + loop.
- Video stays **synchronized across displays** as it plays (not just at the boundary).
- The green heartbeat / preload only completes once videos are actually playable.

## Non-goals (later slices)

Split/mosaic (`SEGMENT`) · synchronized JS animations (`SCRIPT`) · audio / per-item volume · authoring UI · scheduling · a built-in "tap to arm" autoplay flow (see Known Risks — handled via device preparation instead).

## Item model

Items keep the existing shape `{id, file, duration}` with `playmode = FULL`. An item is a **video** when its `file` ends in `.mp4` (case-insensitive, ignoring any `?query`). `duration` is the clip length in ms, **provided in the item** (same as images; a future media-library slice can auto-populate it from metadata). No server-side change.

## Client rendering (`index.html`, ES5)

`showItem(i)` branches on type:
- **Image** (existing): replace `#canvas` content with `<img src=... style="max-width:100%;max-height:100%;">`.
- **Video:** build `<video muted webkit-playsinline playsinline preload="auto" style="max-width:100%;max-height:100%;">` with `src = item.file`, hard-seek `currentTime = clockOffsetMs / 1000`, call `play()`, and start the drift loop. Always `muted` (multi-display wall + autoplay constraints).

When it shows a video, `showItem` records the active video element and its item index (e.g. `playback.video` / `playback.videoIndex`) so the drift loop and transitions can reference them. On any transition (`renderPlayback` advancing to a new item, or `stopPlayback`), tear down the current element, clear that recorded state, and clear the drift loop before showing the next.

### Synchronization — `playbackRate` controller with seek fallback

While a video item is active, a drift loop runs on a ~500ms interval:

```
elapsed   = GoTime.now() - playback.startEpoch
pos       = playlistIndex(elapsed, durations, loop)
if pos.index != currentVideoIndex: return   // renderPlayback will handle the transition
targetMs  = pos.offsetMs
errorMs   = video.currentTime * 1000 - targetMs   // + = video is ahead of the clock

if abs(errorMs) > HARD_SEEK_MS (400):
    video.currentTime = targetMs / 1000           // hard correction
    video.playbackRate = 1
else:
    rate = 1 - errorMs / 2000                      // proportional controller
    video.playbackRate = clamp(rate, 0.85, 1.15)
```

When the displays are aligned, `errorMs → 0` and `playbackRate → 1` (smooth, no visible seeks). The hard-seek branch both handles large drift (e.g. a stall) **and** serves as the iOS-5 fallback: if a device ignores `playbackRate`, the error simply grows until it crosses `HARD_SEEK_MS` and gets corrected by a seek — bounding drift to ~400ms without smooth tracking. `HARD_SEEK_MS`, the proportional window (2000), and the rate clamp are tunable constants.

Initial entry and mid-clip joiners use the same hard-seek (`currentTime = offsetMs/1000`), so a client that receives `PLAY` partway through a clip jumps to the right spot.

The boundary advance is unchanged: `renderPlayback` schedules `setTimeout(renderPlayback, duration - offset)` to move to the next item.

## Preload / readiness

The `PRELOAD` handler counts each item as it settles toward `mediaReady` (which drives the green heartbeat):
- **Image:** `<img>` with `onload`/`onerror` (existing).
- **Video:** a throwaway `<video preload="auto">` with `src = item.file`; counts `canplaythrough` as ready, and `error`/`stalled` also count as "settled" so a bad/large URL can't wedge the dot below green forever.

`mediaReady` becomes true once every item (image or video) has settled.

## Known risks / legacy

On a **1st-gen iPad (iOS 5)**: programmatic `video.play()` may require a user gesture, and `playbackRate` changes may be ignored.
- The seek-fallback bounds synchronization without `playbackRate`.
- The autoplay-gesture limitation is handled **outside this slice** via **device preparation**: the deployment iPads have SSH sideloaded, so unlock/launch/navigate/**gesture** can be scripted to "arm" video playback (see the `device-ssh-prep` project note). No in-page "tap to arm" flow is built here.
- Validate the iPad video path on hardware. Desktop/modern displays are unaffected.

All client additions stay ES5 (no `let`/`const`/arrow/template-literals); no new client libraries. `webkit-playsinline` is included for old iOS inline playback.

## Testing

- **Server:** already covered — video items are ordinary items; no new server logic. (Optionally a sanity test that `SETPLAYLIST`/`PLAY` round-trips an `.mp4` item, but this exercises no new code path.)
- **Client (Playwright):** with a short `.mp4` (served by the existing `media_handler`, or a small data/object URL):
  - A video item renders a `<video>` element and begins playing (`currentTime` advances).
  - On entry mid-clip (non-zero `startEpoch` offset), `currentTime` is seeked near the clock offset.
  - The drift loop drives `errorMs` toward 0 (assert `playbackRate` adjusts, or that injected drift gets corrected within a hard-seek bound).
  - A mixed image→video→image playlist transitions correctly and tears the video down between items.
  - `STOP` clears the video and the drift loop.
