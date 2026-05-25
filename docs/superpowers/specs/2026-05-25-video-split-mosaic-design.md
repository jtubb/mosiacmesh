# Video Split / Mosaic — Design (playback engine slice 5)

**Date:** 2026-05-25
**Status:** Design approved, pending implementation plan
**Builds on:** the image split/mosaic spec (`2026-05-25-image-split-mosaic-design.md`) and the video-items spec.

## Context

Image `SEGMENT` mode already warps a source image onto each calibrated screen server-side (OpenCV homography), gated by an explicit synchronous `RENDER` + readiness token; the display client just plays its own pre-fitted file. This slice extends `SEGMENT` to **`.mp4`**: each screen gets a perspective-warped slice of the source video, H.264-encoded by **ffmpeg**, and — because re-encoding is slow — **rendering becomes asynchronous** (the deferred path, built now and unified for images + video).

## Goals

- A `SEGMENT` playlist item may be a `.mp4`; `RENDER` produces one perspective-warped, H.264 clip per calibrated screen.
- Rendering is **asynchronous** with a status (`rendering` → `ready`/`error`), reported via `RENDER_STATUS`; `PLAY` gates on it.
- The display **client is unchanged** (it plays its per-screen `.mp4` exactly as in the video slice).
- No regression to image SEGMENT, FULL playlists, or PLAY/PAUSE/STOP.

## Non-goals (deferred)

`SCRIPT` animations · playlist authoring UI · scheduling · parallel/queued multi-group renders (renders run one item×screen at a time) · audio in mosaic clips (dropped) · orphaned-render-file garbage collection · H.264 hardware-encode tuning.

## New dependency

**ffmpeg** (with `libx264`) must be on the server's PATH. It is a system binary, not pip-installable: documented in `requirements.txt` (comment) and the README/CLAUDE.md. `RENDER` of a video item errors gracefully if ffmpeg is missing.

## Per-screen video warp (ffmpeg)

For each (video item × calibrated screen):
1. Read the **source video dimensions** `W×H` (`get_video_dimensions(path)` via `cv2.VideoCapture`).
2. Compute the screen's quad in **source-video pixel coords** — the same homography as images: `order_points(measuredPerimeter)` → photo coords → media coords via the group `boundingBox`, with media = `W×H`.
3. Build one ffmpeg command (pure helper `build_ffmpeg_perspective_cmd`) using the **`perspective` filter** + `scale` to the screen's native resolution, encoding iPad-compatible H.264:
   ```
   ffmpeg -y -i <src> \
     -vf "perspective=<x0>:<y0>:<x1>:<y1>:<x2>:<y2>:<x3>:<y3>:sense=source,scale=<Wd>:<Hd>" \
     -an -c:v libx264 -profile:v baseline -level 3.0 -pix_fmt yuv420p \
     -preset veryfast -movflags +faststart <out>
   ```
   - `-an` drops audio (muted wall). `-profile:v baseline -level 3.0 -pix_fmt yuv420p` for 1st-gen iPad decode. `+faststart` for web seeking.
   - Output: `media/<clientKey>/videos/seg_<token>_<itemIndex>.mp4`.
   - **Empirical verification point:** the `perspective` filter's exact corner order and `sense` semantics (source vs destination) must be confirmed with one real encode during implementation — `order_points` yields `[TL,TR,BR,BL]`, and ffmpeg's `perspective` expects `[TL,TR,BL,BR]`, so a reorder is needed; treat the precise mapping as something to validate, not assume.

`build_ffmpeg_perspective_cmd(src, out, quad_src, out_w, out_h)` returns the arg **list** (no execution) and is unit-tested for a known quad/screen.

## Unified asynchronous render

`RENDER` no longer blocks; image and video items share one async job.

- **`Display.renderStatus`** (new) ∈ `"" | "rendering" | "ready" | "error"`. `renderedToken` (existing) is set **only** on a successful, complete render.
- **`render_group_async(display_id)`** (coroutine): set `renderStatus="rendering"` + broadcast `RENDER_STATUS`; capture `token = compute_render_token(display_id)`; for each SEGMENT item × calibrated screen: image → `warp_image_for_screen` inline (fast) and `cv.imwrite` a `.png`; video → `await asyncio.create_subprocess_exec(*build_ffmpeg_perspective_cmd(...))` (serially) writing an `.mp4`. On full success: `renderedToken = token`, `renderStatus="ready"`, broadcast `RENDER_STATUS ready`. On any failure (missing source, ffmpeg returns non-zero or missing binary, missing output): `renderStatus="error"`, broadcast `RENDER_STATUS error`, leave `renderedToken` unchanged.
- **`RENDER {displayID}` handler** (in sync `msg_response`): validate (playlist exists, `boundingBox` set, ≥1 SEGMENT item, ≥1 calibrated screen) — on failure respond an ERROR PAYLOAD and start nothing. If already `rendering`, respond `{status:"rendering"}` (no double-start). Otherwise schedule `asyncio.ensure_future(render_group_async(display_id))` (a running loop exists — `msg_response` is invoked from the `ws_handler` coroutine) and respond `{status:"rendering"}` immediately.
- **`RENDER_STATUS {displayID, status}`** broadcast via `socketmanager.broadcast` on every status change — the editor/admin notification hook.

## PLAY gating (extended)

For a playlist containing ≥1 SEGMENT item:
- `renderStatus == "rendering"` ⇒ respond `{status:"RENDER_IN_PROGRESS", displayID}` (do not play).
- else if `compute_render_token(display_id) != renderedToken` (never rendered, or stale — i.e. playlist/calibration changed since the last successful render) ⇒ respond `{status:"RENDER_REQUIRED", displayID}`. (A failed re-render leaves the prior `renderedToken` intact, so if its inputs are unchanged the last good render still plays.)
- else (ready + token match) ⇒ send each client its per-client `PLAY` with a shared `startEpoch` (existing `_broadcast_segment_play`).

`_broadcast_segment_play` chooses the warped file extension by source type: a `.mp4` SEGMENT source → `/media/<key>/seg_<token>_<i>.mp4`; an image source → `…/seg_<token>_<i>.png` (matching the render output and `media_handler`'s subdir routing). FULL items and uncalibrated-screen fallback unchanged.

## Edge cases

- ffmpeg missing / non-zero exit → `renderStatus="error"`, no `renderedToken`; PLAY → `RENDER_REQUIRED`.
- Playlist edited or recalibrated mid-render → token computed at start no longer matches current → next PLAY is `RENDER_REQUIRED` (re-render needed). Stale per-screen files are orphaned (GC deferred).
- Re-issuing `RENDER` while `rendering` → no-op (responds `rendering`).
- Mixed image+video SEGMENT playlist → handled per item in one job.

## Testing

- **pytest (pure / mocked):** `build_ffmpeg_perspective_cmd` arg string for a known quad/screen (corner reorder + H.264 flags present); `get_video_dimensions` with `cv2.VideoCapture` mocked; `render_group_async` with `asyncio.create_subprocess_exec` **mocked** (assert one ffmpeg call per video-item×screen with the expected output path, `renderStatus` transitions `rendering→ready`, `renderedToken` set, `RENDER_STATUS` broadcasts); the image-only `render_group_async` path still writes `.png` + sets token (now `await`ed — the image-render test becomes async); the `RENDER` handler schedules/validates and reports `rendering`/errors; PLAY gating returns `RENDER_IN_PROGRESS`/`RENDER_REQUIRED`/ready correctly; `_broadcast_segment_play` emits `.mp4` URLs for video SEGMENT sources.
- **pytest (opt-in integration):** a test that actually invokes ffmpeg on a tiny generated clip, `@pytest.mark.skipif(shutil.which('ffmpeg') is None)` — produces a real per-screen `.mp4` and asserts it exists and is non-empty. This is where the `perspective` corner-order/`sense` mapping is validated for real.
- **Playwright (light):** confirm the status/gate wiring end to end (SEGMENT video PLAY before render → `RENDER_REQUIRED`/`RENDER_IN_PROGRESS`).

## ES5 / legacy

No client changes. iPad-1 compatibility is carried by the H.264 baseline/`yuv420p` encode flags (validated on hardware, per the open hardware-validation item).
