# Calibration Geometry Rework — Design

**Goal:** Render each video-wall screen's segment from the *measured* calibration
geometry — true orientation, perspective, and aspect — derived from the
known-size ArUco fiducial, instead of the unreliable client-reported resolution
or the fragile screen-border contour. Reconstruct a clean 4-corner screen quad
even when a screen is partially occluded or its border detects poorly.

## Problem (observed)

- `calibrate()` records `measuredPerimeter` as the *enclosing contour* of the
  marker (the 8px screen border). In practice this yields **non-quad,
  self-intersecting contours** (8 and 6 points seen for two of three screens),
  so the perspective warp samples a garbage polygon.
- The render scales output to **client-reported `deviceWidth×deviceHeight`**,
  which is orientation-confused — the iPad reports `768×1024` portrait but was
  measured landscape. So segments come out the wrong aspect/orientation.

## Inputs now available

- **`arucoID`** — globally unique per client; marker→client mapping is by id.
- **Marker is fixed 300×300 CSS px, centered** in the canvas (verified). This is
  a known-scale, known-position fiducial on the screen plane.
- **Device resolution** (`deviceWidth×deviceHeight`) — orientation-independent.
- **Canvas/viewport resolution** (`canvasWidth×canvasHeight`) — the *true*
  rendered area and orientation. (Now collected at REGISTER.)
- Detected marker quad (4 corners) from `cv.aruco` in the calibration photo.

## Core idea: extrapolate the screen quad from the marker

The marker and the screen are **coplanar** on the device surface, and the
marker's position/size *within the canvas* is known. So a single homography
from canvas-pixel space → photo space (fit from the marker's 4 corners) maps the
**full screen rectangle** into the photo — no dependence on detecting the screen
border, and robust to edge occlusion (only the marker must be visible).

### Geometry

Let `cw, ch` = canvas resolution; marker = 300×300 centered.

Marker corners in canvas px (center `(cw/2, ch/2)`):
```
TLm=(cw/2-150, ch/2-150)  TRm=(cw/2+150, ch/2-150)
BRm=(cw/2+150, ch/2+150)  BLm=(cw/2-150, ch/2+150)
```
Screen corners in canvas px: `TLs=(0,0) TRs=(cw,0) BRs=(cw,ch) BLs=(0,ch)`.

1. Detect the marker's photo quad `Pm = [P_TL,P_TR,P_BR,P_BL]` (ordered).
2. `H = getPerspectiveTransform(marker_canvas_corners, Pm)` — canvas px → photo.
3. `screen_quad_photo = perspectiveTransform([TLs,TRs,BRs,BLs], H)` — a clean,
   correctly-ordered 4-corner quad. **This becomes `measuredPerimeter`.**

Because the quad is derived from the (centered, fully-visible) marker, a screen
whose *edges* are occluded by a neighbor still reconstructs correctly. Rotation
and perspective are carried by `H`; corner ordering preserves orientation.

### Sanity check: fiducial quad vs. detected black-band border

The fiducial extrapolation trusts the client-reported `canvasWidth/Height`. A
mobile device that auto-rotated (or reported its viewport in a different
orientation than it was photographed in) would make that canvas aspect wrong,
and the extrapolated quad would be confidently *wrong* (e.g. portrait
reconstruction over a landscape screen). The 8px black band is the **ground-truth
outline in the photo**, so cross-check against it:

1. Also detect the screen-border contour enclosing the marker (the existing
   `find_squares` / enclosing-contour logic) → `border_quad` (may be messy).
2. If a usable border is found, compare its **orientation + aspect** to the
   fiducial quad (e.g. `cv.minAreaRect` angle/aspect on each, or polygon IoU):
   - **Agree within tolerance** → accept the fiducial quad (clean 4 corners).
   - **Disagree** → assume an orientation flip: swap `cw↔ch`, recompute the
     fiducial quad, and re-compare. If the swapped version now agrees, accept it
     and **log that an auto-rotation was detected** (and persist the corrected
     `canvasWidth/Height` back onto the client so future renders use it).
   - **Still disagree** → log a warning naming the client and **fall back to the
     border quad** (the photo's direct measurement), since the reported geometry
     can't be trusted. The screen is still calibrated, just from the band.
3. If no usable border is found → accept the fiducial quad (best available).

This keeps the fiducial as the clean primary source while the band guards
against stale/rotated canvas data — neither alone is trusted blindly.

### Render

For SEGMENT: map the source region under `screen_quad_photo` (within the group
bounding box) and warp to **`canvasWidth × canvasHeight`** (the true rendered
aspect/orientation), not `deviceWidth×deviceHeight`. INDIVIDUAL likewise targets
the canvas dimensions.

## Components / files

- `server.py` `calibrate()` — per detected marker: look up the client by
  `arucoID`, fit `H` from the marker corners + that client's `canvasWidth/Height`,
  transform the screen rectangle to a fiducial quad. **Keep** the enclosing-
  contour (black-band) detection as a validator and run the sanity check
  (above) to choose/correct the final quad; store it as `measuredPerimeter`.
  Keep drawing overlays for the returned debug image (draw both quads when they
  disagree, to aid diagnosis).
- `server.py` render paths (`render_group_async`, `build_ffmpeg_perspective_cmd`,
  `build_ffmpeg_individual_cmd`, `warp_image_for_screen`) — target
  `canvasWidth/Height` instead of `deviceWidth/Height` for output dimensions.
- Pure helper `reconstruct_screen_quad(marker_quad, cw, ch, marker_px=300)` —
  unit-testable; returns the 4 screen corners in photo space.

## Edge cases

- **Marker occluded / not detected** → no quad for that client (uncalibrated;
  falls back to source, as today).
- **Missing canvas dims** (older client) → fall back to device resolution
  (already the REGISTER default).
- **Marker not perfectly centered** (future) → parameterize marker
  position/size rather than assuming center; out of scope for v1.

## Testing

- `reconstruct_screen_quad`: identity case (marker fills a known fraction,
  axis-aligned) → screen corners at expected canvas extents; a rotated/sheared
  marker quad → correspondingly transformed screen quad.
- Sanity check / rotation reconcile (pure helper, e.g.
  `reconcile_screen_quad(fiducial, border, cw, ch)`):
  - fiducial agrees with border → returns fiducial unchanged.
  - portrait canvas vs landscape border → returns the `cw↔ch`-swapped fiducial
    and signals a detected rotation.
  - irreconcilable → returns the border quad and signals a warning.
- Render output dims = canvas dims (assert on the ffmpeg `scale=` args / image
  warp size).

## Out of scope (v1)

- Non-centered or non-300px markers.
- Sub-pixel border refinement.
- Auto-detecting orientation flips beyond what `H` encodes.
