# Calibration Geometry Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconstruct each video-wall screen's quad from the fixed-size centered ArUco marker (robust to occlusion/messy borders), validate it against the detected black-band outline to catch mobile auto-rotation, and render each segment at the screen's true canvas dimensions instead of the orientation-confused reported resolution.

**Architecture:** Two pure, unit-tested geometry helpers (`reconstruct_screen_quad`, `reconcile_screen_quad`) feed a reworked `calibrate()` that stores a clean 4-corner `measuredPerimeter`. The render paths switch their output dimensions from `deviceWidth/Height` to `canvasWidth/Height`.

**Tech Stack:** Python, OpenCV (`cv2` as `cv`), NumPy. Tests via `python pytest_runner.py --unit` (config at `tests/pytest.ini`).

Design: `docs/superpowers/specs/2026-05-27-calibration-geometry-design.md`.

---

## File Structure

- `server.py` — add geometry helpers near `warp_image_for_screen` (~line 273); rework `calibrate()` (~line 1966); switch render output dims in `render_group_async` (~line 478-527) and the ffmpeg builders' call sites.
- `tests/unit/test_mosaic.py` — helper unit tests (already holds warp/aruco tests).

Prereq (already shipped): clients send `canvasWidth/canvasHeight`; `Client` stores them; marker→client is by `arucoID`.

---

### Task 1: `reconstruct_screen_quad` helper

**Files:**
- Modify: `server.py` (add near `warp_image_for_screen`)
- Test: `tests/unit/test_mosaic.py`

- [ ] **Step 1: Write the failing test**

```python
class TestScreenQuad:
    def test_axis_aligned_marker_extrapolates_full_screen(self):
        # marker 300px centered in a 1000x800 canvas, photographed axis-aligned
        # at the SAME scale (1px canvas = 1px photo): screen corners are the
        # canvas corners offset so the marker sits centered.
        # marker canvas corners: (350,250),(650,250),(650,550),(350,550)
        # photographed identically -> screen quad == (0,0),(1000,0),(1000,800),(0,800)
        marker_quad = [[350,250],[650,250],[650,550],[350,550]]
        q = server.reconstruct_screen_quad(marker_quad, 1000, 800).reshape(4,2)
        assert abs(q[0][0]-0) <= 1 and abs(q[0][1]-0) <= 1       # TL
        assert abs(q[1][0]-1000) <= 1 and abs(q[1][1]-0) <= 1    # TR
        assert abs(q[2][0]-1000) <= 1 and abs(q[2][1]-800) <= 1  # BR
        assert abs(q[3][0]-0) <= 1 and abs(q[3][1]-800) <= 1     # BL

    def test_scaled_marker(self):
        # marker photographed at half scale, centered at photo (500,400):
        # marker corners +/-75 -> (425,325)... screen should be 500x400 centered.
        marker_quad = [[425,325],[575,325],[575,475],[425,475]]
        q = server.reconstruct_screen_quad(marker_quad, 1000, 800).reshape(4,2)
        assert abs(q[0][0]-250) <= 1 and abs(q[0][1]-200) <= 1   # TL ~ (250,200)
        assert abs(q[2][0]-750) <= 1 and abs(q[2][1]-600) <= 1   # BR ~ (750,600)
```

- [ ] **Step 2: Run it, expect failure** — `python -m pytest tests/unit/test_mosaic.py::TestScreenQuad -c tests/pytest.ini -v` → FAIL (no `reconstruct_screen_quad`).

- [ ] **Step 3: Implement**

```python
def reconstruct_screen_quad(marker_quad, cw, ch, marker_px=300):
    """Photo-space quad of the full screen, extrapolated from the centered,
    fixed-size ArUco marker (marker and screen are coplanar). marker_quad is
    [TL,TR,BR,BL] in photo px (ordered). Returns a (4,1,2) int32 array of the
    screen corners [TL,TR,BR,BL]."""
    cw = float(cw); ch = float(ch); h = marker_px / 2.0
    marker_canvas = np.array([
        [cw/2 - h, ch/2 - h], [cw/2 + h, ch/2 - h],
        [cw/2 + h, ch/2 + h], [cw/2 - h, ch/2 + h]], dtype="float32")
    dst = np.array(marker_quad, dtype="float32").reshape(4, 2)
    H = cv.getPerspectiveTransform(marker_canvas, dst)
    screen = np.array([[[0, 0]], [[cw, 0]], [[cw, ch]], [[0, ch]]], dtype="float32")
    return cv.perspectiveTransform(screen, H).astype("int32")
```

- [ ] **Step 4: Run, expect pass.**
- [ ] **Step 5: Commit** — `feat(calibrate): reconstruct_screen_quad from the fixed-size marker`

---

### Task 2: `reconcile_screen_quad` helper (sanity check + rotation)

**Files:**
- Modify: `server.py` (after Task 1)
- Test: `tests/unit/test_mosaic.py`

- [ ] **Step 1: Write the failing test**

```python
class TestReconcileQuad:
    # marker centered in a 1000x800 (landscape) canvas, axis-aligned at scale 1
    MARKER = [[350,250],[650,250],[650,550],[350,550]]

    def test_agreeing_border_keeps_fiducial(self):
        border = [[0,0],[1000,0],[1000,800],[0,800]]   # matches landscape screen
        quad, src = server.reconcile_screen_quad(self.MARKER, border, 1000, 800)
        assert src == "fiducial"

    def test_rotated_canvas_is_swapped(self):
        # canvas reported PORTRAIT (800x1000) but the photo border is LANDSCAPE
        # 1000x800 -> reconcile should swap cw/ch and report 'rotated'.
        border = [[0,0],[1000,0],[1000,800],[0,800]]
        quad, src = server.reconcile_screen_quad(self.MARKER, border, 800, 1000)
        assert src == "rotated"

    def test_irreconcilable_falls_back_to_border(self):
        border = [[100,100],[200,100],[200,150],[100,150]]  # tiny, unrelated
        quad, src = server.reconcile_screen_quad(self.MARKER, border, 1000, 800)
        assert src == "border"

    def test_no_border_uses_fiducial(self):
        quad, src = server.reconcile_screen_quad(self.MARKER, None, 1000, 800)
        assert src == "fiducial"
```

- [ ] **Step 2: Run, expect failure.**

- [ ] **Step 3: Implement**

```python
def _quad_box(contour):
    """Clean convex 4-corner box (minAreaRect) from any contour/quad, ordered."""
    pts = np.array(contour, dtype="float32").reshape(-1, 1, 2)
    return order_points(cv.boxPoints(cv.minAreaRect(pts)))

def _quad_iou(a, b):
    """Intersection-over-union of two convex quads (each (4,2) or (4,1,2))."""
    a = np.array(a, dtype="float32").reshape(-1, 2)
    b = np.array(b, dtype="float32").reshape(-1, 2)
    inter, _ = cv.intersectConvexConvex(a, b)
    union = cv.contourArea(a) + cv.contourArea(b) - inter
    return float(inter / union) if union > 0 else 0.0

def reconcile_screen_quad(marker_quad, border_contour, cw, ch, marker_px=300, min_iou=0.5):
    """Pick the screen quad: fiducial extrapolation, validated against the
    detected black-band outline. If the fiducial disagrees with the band but a
    cw<->ch swap agrees, a mobile auto-rotation is assumed (the reported canvas
    orientation was stale). Returns (quad (4,1,2) int32, source) where source is
    'fiducial' | 'rotated' | 'border'."""
    fid = reconstruct_screen_quad(marker_quad, cw, ch, marker_px)
    if border_contour is None or len(np.array(border_contour).reshape(-1, 2)) < 3:
        return fid, "fiducial"
    box = _quad_box(border_contour)
    iou = _quad_iou(fid, box)
    fid_sw = reconstruct_screen_quad(marker_quad, ch, cw, marker_px)
    iou_sw = _quad_iou(fid_sw, box)
    if iou >= iou_sw and iou >= min_iou:
        return fid, "fiducial"
    if iou_sw > iou and iou_sw >= min_iou:
        return fid_sw, "rotated"
    return box.astype("int32").reshape(4, 1, 2), "border"
```

- [ ] **Step 4: Run, expect pass.**
- [ ] **Step 5: Commit** — `feat(calibrate): reconcile fiducial quad with band outline (rotation guard)`

---

### Task 3: Rework `calibrate()` to use the helpers

**Files:**
- Modify: `server.py` `calibrate()` (~line 1966)

- [ ] **Step 1: Locate the per-marker loop.** The marker→client mapping by `arucoID` is already in place. The enclosing-contour search currently sets `measuredPerimeter = approximatedShape`.

- [ ] **Step 2: Replace the perimeter assignment.** Keep finding the enclosing contour (call it `border_contour`, may be None/messy). Then:

```python
# Fiducial-derived quad, validated against the black-band outline.
cw = getattr(settings.clients[clientID], "canvasWidth", 0) or settings.clients[clientID].deviceWidth
ch = getattr(settings.clients[clientID], "canvasHeight", 0) or settings.clients[clientID].deviceHeight
quad, source = reconcile_screen_quad(markerCorner.reshape(4, 2), border_contour, cw, ch)
if source == "rotated":
    # reported canvas orientation was stale (auto-rotate) -> persist the fix
    settings.clients[clientID].canvasWidth, settings.clients[clientID].canvasHeight = ch, cw
    logging.info("calibrate: detected rotation for %s; swapped canvas to %sx%s", clientID, ch, cw)
elif source == "border":
    logging.warning("calibrate: fiducial/band mismatch for %s; using band outline", clientID)
settings.clients[clientID].measuredPerimeter = quad
```

- [ ] **Step 3: Draw both quads on the debug image when they differ** (fiducial in blue, band in red) so the returned calibration image aids diagnosis. (Reuse existing `cv.line` calls.)

- [ ] **Step 4: Run the suite** — `python pytest_runner.py --unit` → all pass (existing no-marker test still passes; helpers covered).
- [ ] **Step 5: Commit** — `feat(calibrate): store fiducial+reconciled screen quad as measuredPerimeter`

---

### Task 4: Render to canvas dimensions

**Files:**
- Modify: `server.py` render paths (~lines 478-527) and the SCRIPT/INDIVIDUAL/SEGMENT branches.

- [ ] **Step 1:** At each render call site that passes `int(c.deviceWidth) or 1, int(c.deviceHeight) or 1` to `warp_image_for_screen`, `build_ffmpeg_perspective_cmd`, and `build_ffmpeg_individual_cmd`, switch to canvas with device fallback:

```python
out_w = int(getattr(c, "canvasWidth", 0) or c.deviceWidth) or 1
out_h = int(getattr(c, "canvasHeight", 0) or c.deviceHeight) or 1
```

and pass `out_w, out_h`.

- [ ] **Step 2: Add/adjust a test** asserting the ffmpeg builder receives canvas dims — extend an existing `build_ffmpeg_perspective_cmd` test to check the `scale=` segment equals `canvasWidth:canvasHeight`.

- [ ] **Step 3: Run the suite** → all pass.
- [ ] **Step 4: Commit** — `feat(render): output per-screen segments at canvas (true) dimensions`

---

### Task 5: Manual end-to-end validation

- [ ] **Step 1:** Restart the server; **reload all display devices** (to send `canvasWidth/Height`).
- [ ] **Step 2:** Generate ArUco for the group; confirm each screen shows a **distinct** marker.
- [ ] **Step 3:** Photograph the wall; upload to calibrate.
- [ ] **Step 4:** Verify via `curl /api/discovery/devices` + websocket CLIENTS that every screen has a clean 4-corner `measuredPerimeter`; check server log for any "rotation detected" / "mismatch" lines.
- [ ] **Step 5:** Render; `ffprobe` each `seg_*.mp4` → dimensions equal that client's **canvas** resolution (the iPad's should be its true orientation, not 768×1024 portrait if it rendered landscape).
- [ ] **Step 6:** Deliberately rotate a mobile device, recalibrate, confirm the "rotation detected" path swaps canvas dims and the quad still matches the band.

---

## Self-Review notes

- Spec coverage: fiducial reconstruction (T1), band sanity check + rotation (T2), calibrate integration (T3), canvas-dim render (T4), e2e incl. rotation (T5). ✓
- `order_points` and `cv`/`np` are already imported in `server.py`.
- `cv.intersectConvexConvex` returns `(area, points)`; both quads must be convex — `reconstruct_screen_quad` output and `_quad_box` (minAreaRect) are convex by construction.
- Marker assumed centered + 300px (verified in `index.html`); if that changes, parameterize via `marker_px`/position (out of scope v1).
