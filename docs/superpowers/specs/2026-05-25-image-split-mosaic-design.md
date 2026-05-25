# Image Split / Mosaic — Design (playback engine slice 4)

**Date:** 2026-05-25
**Status:** Design approved, pending implementation plan
**Builds on:** the synchronized playback engine MVP, video items, and PAUSE/resume specs.

## Context

Display groups can play synchronized playlists (images + video, PLAY/PAUSE/STOP + loop). This slice adds the **mosaic**: a `SEGMENT` item is one source image displayed *across* a group of physically-arranged screens, each screen showing a perspective-correct slice. Arbitrary screen positions/rotations are supported, so each screen's slice is a true perspective warp (homography).

The work is **server-side only** — the server pre-warps each image per screen and hands each client its own pre-fitted bitmaps; the display client is byte-identical to identical-mode (it just renders images, clock-synchronized). Warping is an **explicit, decoupled "render" step** with a readiness state, so a future playlist editor maps to: Save → `SETPLAYLIST`, Render → `RENDER`, assign/play while stale → `RENDER_REQUIRED` notice.

## Goals

- A group can play a playlist whose items are marked `SEGMENT`; each screen shows its perspective-correct slice of the source image, all synchronized.
- Rendering is explicit (`RENDER`) and has a **readiness token** that auto-invalidates when the playlist or calibration changes.
- `PLAY` of a SEGMENT playlist only plays when rendered; otherwise it emits `RENDER_REQUIRED`.
- No change to the display client; no regression to FULL (identical) playlists or PLAY/PAUSE/STOP.

## Non-goals (deferred)

Video split (re-encode vs `matrix3d` — its own slice) · the playlist editor UI · asynchronous render / progress reporting · camera lens-distortion correction · non-coplanar 3D reconstruction beyond the per-screen homography.

## Geometry — calibration (server-side)

`calibrate()` already records, per client, `measuredPerimeter` (the screen's outline quad) and `measuredCenter` in **photo-pixel coordinates**. Enhance calibration so that, **per display group**, it computes and persists the mosaic bounding box:

- `Display.boundingBox = [x, y, w, h]` — the tight **axis-aligned** rectangle (in the photo/calibration frame) enclosing the `measuredPerimeter` quads of that group's ArUco-bearing screens (`cv.boundingRect` of their union). The calibration photo defines horizontal/vertical; the box shrinks to exactly enclose the group's screens.
- `Display.boundingBoxCenter = [cx, cy]`.

The full source image is stretched to fill this box, so the outermost screens' edges become the media's edges.

## Per-screen warp (the core math)

For a source image of size `W×H` mapped onto group bbox `(bx,by,bw,bh)`:

- **media→photo:** `(mx,my)` ⇒ `(bx + mx/W·bw, by + my/H·bh)`. **photo→media (inverse):** `(px,py)` ⇒ `((px−bx)/bw·W, (py−by)/bh·H)`.
- **Order the screen quad:** reduce `measuredPerimeter` (which may have ≥4 points from `approxPolyDP`) to 4 ordered corners `[TL,TR,BR,BL]` using the standard `order_points` extreme-corner method: `TL = argmin(x+y)`, `BR = argmax(x+y)`, `TR = argmax(x−y)`, `BL = argmin(x−y)`. Robust for a convex quad with extra points.
- **Homography:** `src = [photo→media(corner) for corner in orderedQuad]`; `dst = [[0,0],[Wd,0],[Wd,Hd],[0,Hd]]` where `Wd,Hd` are the screen's registered resolution (`deviceWidth`,`deviceHeight`). `M = cv.getPerspectiveTransform(float32(src), float32(dst))`; `warped = cv.warpPerspective(sourceImg, M, (Wd, Hd))`.
- `warped` (the screen's native resolution) is exactly what that screen displays full-screen.

These are pure functions (bbox-from-quads, order_points, photo↔media mapping, warp) — unit-testable without the network.

## Render workflow

- **`SETPLAYLIST`** gains per-item `playmode` (`"FULL"` default, or `"SEGMENT"`). Storing a playlist marks the group **unrendered** (clears `renderedToken`). (FULL-only playlists never need rendering.)
- **`RENDER { displayID }`** (new): synchronous (image warps are ms). For each `SEGMENT` item, for each client in the group **with calibration geometry**, read the source image from disk, warp per the math above, and write `media/<clientKey>/images/seg_<token>_<itemIndex>.png`. Then set `display.renderedToken = compute_render_token(display)`. Respond `SUCCESS` with the token, or an error PAYLOAD if there is no playlist, no SEGMENT items, or no calibrated screens.
- **Render token** = a stable hash of: each item `(id, file, duration, playmode)`; the group `boundingBox`; and for each group client `(clientKey, deviceWidth, deviceHeight, measuredPerimeter)`. Computed on demand; rendered assets are valid only while `compute_render_token(display) == display.renderedToken`. So any playlist edit or recalibration auto-invalidates the render (stale).
- **`PLAY` gating** (only when the playlist contains ≥1 SEGMENT item):
  - If `compute_render_token(display) != display.renderedToken` ⇒ do **not** play; the PLAY response PAYLOAD is `{ "status": "RENDER_REQUIRED", "displayID": ... }` so the caller (admin/editor) is notified. (No display-client broadcast — the screens keep showing whatever they were.)
  - If rendered ⇒ for each client, build its personalized `items` list — SEGMENT items use that client's warped file URL (`/media/<clientKey>/seg_<token>_<i>.png`); FULL items use the shared source URL — and send `PLAY { startEpoch, items, loop }` to that client via `broadcast_to_client`, all sharing one `startEpoch`.
  - A client in a SEGMENT group **without** calibration geometry falls back to the full source URL for SEGMENT items (graceful, not blank).
- **FULL-only playlists** keep the existing PLAY path unchanged (`broadcast_to_display_group` with one shared `items` list) — no rendering, no per-client send. PAUSE/STOP unchanged.

### Source resolution

A SEGMENT item's `file` is a server-local media URL (e.g. `/media/server/clouds.jpg`, produced by the existing image upload). The renderer resolves it to a disk path via the same convention as `media_handler` (`/media/<client>/<name>` → `media/<client>/images/<name>` for images) and `cv.imread`s it.

## Data model summary

- `Display.boundingBox`, `Display.boundingBoxCenter` (already exist) — populated by calibration per group.
- `Display.renderedToken` (new, default `""`).
- `MediaElement.playmode` — set from the SETPLAYLIST item (`FULL`/`SEGMENT`).

## Edge cases

- Screen with no calibration geometry → full-source fallback for SEGMENT items.
- `measuredPerimeter` with >4 points → reduced to 4 via order_points extremes.
- Playlist edited or group recalibrated after render → token mismatch → `RENDER_REQUIRED` on next PLAY.
- `RENDER` with no playlist / no SEGMENT items / no calibrated screens → error response, no files written.
- Mixed FULL+SEGMENT playlist → per-item handling (FULL shared, SEGMENT warped).

## Testing

- **pytest (math, no IO):** group bbox from a set of quads; `order_points` on a shuffled/≥4-point quad; photo↔media inverse round-trips; `compute_render_token` is stable for equal inputs and changes when playlist/geometry/resolution changes.
- **pytest (warp + handlers):** warp a synthetic source (created with `cv.imwrite` to a temp dir) for a known quad → assert output shape `(Hd,Wd)` and that a corner maps where expected. `RENDER` writes one file per (SEGMENT item × calibrated client) and sets `renderedToken`. `PLAY` on a rendered SEGMENT group sends one per-client `PLAY` each (via `broadcast_to_client`) with a shared `startEpoch` and that client's warped URLs; `PLAY` on a stale/unrendered SEGMENT group returns a `RENDER_REQUIRED` status PAYLOAD and does not send per-client PLAYs. FULL-only `PLAY` still uses the group path (regression guard).
- **Playwright (light):** the client is unchanged; a sanity check that two clients in a rendered SEGMENT group receive distinct warped file URLs with the same `startEpoch`.

## ES5 / legacy

No client changes in this slice, so ES5 is unaffected. The display client renders pre-warped bitmaps exactly as it renders any image.
