# Mesh Geometry Rectification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in (default OFF) homography rectification of mesh-animation geometry so a meshed SCRIPT animation centers on the true physical grid center (removing the calibration-photo keystone), while raw-bbox stays the standing behavior until the toggle is flipped.

**Architecture:** A `MESH_RECTIFY` flag gates a server-side pipeline that detects the screen grid, fits a homography from the screen centers to a regular lattice, applies it to each screen's real quad corners, and stores a rectified per-group global canvas (`Display.meshGlobalRect`) + per-client rectified cell (`Client.meshCellQuad`). `_per_client_items` emits the rectified geometry only when the flag is on AND the fields exist; otherwise it uses today's raw-bbox path. Isolated to the SCRIPT-mesh path; SEGMENT/INDIVIDUAL media and the ES5 client (`mmMeshTransform`) are untouched.

**Tech Stack:** Python (`mosaicmesh/calibration.py`, `mosaicmesh/render.py`, `mosaicmesh/state.py`) with `numpy` + `cv2` (imported in calibration.py as `np` / `cv`), `statistics`/`math` (stdlib). pytest (`tests/unit/`, run `-c tests/pytest.ini`). No client/JS change.

**Reference spec:** `docs/superpowers/specs/2026-06-21-mesh-rectify-design.md`
**Branch:** `experiment/mesh-rectify` (off main).

---

## File Structure

- `mosaicmesh/state.py` — `Client` gains `meshCellQuad=None`; `Display` gains `meshGlobalRect=None`.
- `mosaicmesh/calibration.py` — `MESH_RECTIFY=False` constant; `_cluster_1d`, `_detect_grid`, `rectify_group_grid` helpers; one gated call from `assign_group_bounding_boxes`.
- `mosaicmesh/render.py` — `_per_client_items` mesh branch selects rectified-vs-raw via `calibration.MESH_RECTIFY` (referenced as a module attribute so tests can patch it).
- `tests/unit/test_mesh_rectify.py` — new test module (grid detection, rectification of a synthetic keystoned grid, render-path selection).

No migration code: field defaults + `getattr(..., None)` cover old `settings.dat`.

---

## Task 1: Data model + toggle

**Files:**
- Modify: `mosaicmesh/state.py` (`Client.__init__` ~line 175 area; `Display.__init__` ~line 31 area)
- Modify: `mosaicmesh/calibration.py` (module top — add `MESH_RECTIFY`)
- Test: `tests/unit/test_mesh_rectify.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_mesh_rectify.py`:

```python
# tests/unit/test_mesh_rectify.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import argparse
_orig = argparse.ArgumentParser.parse_args
class _MockArgs:
    Port = 3000
    Verbose = False
argparse.ArgumentParser.parse_args = lambda self, a=None, n=None: _MockArgs()
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

import pytest
import numpy as np
from unittest.mock import MagicMock
from mosaicmesh.state import Settings, Display, Client, MediaElement, PlayMode
from mosaicmesh import calibration as CAL
from mosaicmesh import render as R


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    server.socketmanager = MagicMock()
    yield server.settings
    server.settings = prev


def test_defaults_and_flag():
    assert Client().meshCellQuad is None
    assert Display().meshGlobalRect is None
    assert CAL.MESH_RECTIFY is False   # opt-in: off by default
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_mesh_rectify.py -c tests/pytest.ini -v`
Expected: FAIL — `AttributeError: 'Client' object has no attribute 'meshCellQuad'` / no `MESH_RECTIFY`.

- [ ] **Step 3: Add the fields + flag**

In `mosaicmesh/state.py`, `Display.__init__`, after the `self.meshGlobal = None` block (added previously):

```python
        self.meshGlobal = None
        # [GW, GH] device-pixel RECTIFIED global wall canvas (homography-rectified
        # grid). None unless MESH_RECTIFY computed it. Used by mesh animations only
        # when calibration.MESH_RECTIFY is on; else the raw meshGlobal/bbox is used.
        self.meshGlobalRect = None
```

In `mosaicmesh/state.py`, `Client.__init__`, after `self.measuredPerimeter = None`:

```python
        self.measuredCenter = None
        self.measuredPerimeter = None
        # Homography-rectified cell quad (normalized 0..1, TL/TR/BR/BL) for mesh
        # animations; None unless MESH_RECTIFY computed it. See calibration.rectify_group_grid.
        self.meshCellQuad = None
```

(Confirm `self.measuredPerimeter = None` is the actual preceding line; insert `meshCellQuad` right after it.)

In `mosaicmesh/calibration.py`, near the top (after the imports, before the first function):

```python
# Opt-in (default OFF): when True, mesh-animation geometry is homography-rectified
# (keystone removed) — see rectify_group_grid + docs/.../2026-06-21-mesh-rectify-design.md.
# Gates BOTH the compute (assign_group_bounding_boxes) and the use (_per_client_items),
# so OFF == today's raw-bbox behavior. Flip True + restart to A/B on the wall.
MESH_RECTIFY = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_mesh_rectify.py -c tests/pytest.ini -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/state.py mosaicmesh/calibration.py tests/unit/test_mesh_rectify.py
git commit -m "feat(mesh-rectify): data model + MESH_RECTIFY toggle (default off)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Grid detection (`_cluster_1d`, `_detect_grid`)

**Files:**
- Modify: `mosaicmesh/calibration.py` (add two helpers near `assign_group_bounding_boxes`)
- Test: `tests/unit/test_mesh_rectify.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_mesh_rectify.py`:

```python
def _grid_centers(R, C, pitch=100.0, x0=0.0, y0=0.0):
    return [(x0 + c * pitch, y0 + r * pitch) for r in range(R) for c in range(C)]


def test_detect_grid_clean_6x4():
    centers = _grid_centers(4, 6, pitch=100.0)   # 24 points, cell extent ~80
    res = CAL._detect_grid(centers, 80.0, 80.0)
    assert res is not None
    rows, cols, Rn, Cn = res
    assert Rn == 4 and Cn == 6
    # every (row,col) cell present exactly once
    assert sorted(zip(rows, cols)) == sorted((r, c) for r in range(4) for c in range(6))


def test_detect_grid_skips_irregular():
    # 23 points: a clean 6x4 minus one cell -> R*C (24) != N (23) -> skip.
    centers = _grid_centers(4, 6, pitch=100.0)[:-1]
    assert CAL._detect_grid(centers, 80.0, 80.0) is None


def test_cluster_1d_bands_by_gap():
    # three tight clusters around 0, 100, 200; threshold 40 -> 3 bands.
    vals = [0, 2, 1, 100, 101, 99, 200, 198, 202]
    bands = CAL._cluster_1d(vals, 40.0)
    assert max(bands) == 2
    assert bands[0] == bands[1] == bands[2] == 0
    assert bands[3] == bands[4] == bands[5] == 1
    assert bands[6] == bands[7] == bands[8] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_mesh_rectify.py -c tests/pytest.ini -k "detect_grid or cluster" -v`
Expected: FAIL — `module 'mosaicmesh.calibration' has no attribute '_detect_grid'`.

- [ ] **Step 3: Implement the helpers**

In `mosaicmesh/calibration.py`, immediately BEFORE `def assign_group_bounding_boxes():`:

```python
def _cluster_1d(vals, threshold):
    """Assign each value to a band; a new band starts where the sorted gap to the
    previous value exceeds `threshold`. Returns band indices in the SAME order as
    `vals`, numbered ascending by value."""
    n = len(vals)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: vals[i])
    bands = [0] * n
    band = 0
    for si in range(1, n):
        if vals[order[si]] - vals[order[si - 1]] > threshold:
            band += 1
        bands[order[si]] = band
    return bands


def _detect_grid(centers, cell_w, cell_h):
    """Cluster screen centers into rows (by y) and cols (by x), splitting on a gap
    larger than half a median cell extent. Returns (rows, cols, R, C) per-center,
    or None if the result is not a clean full grid (R*C != N, or any cell
    empty/doubled) -> caller falls back to raw-bbox."""
    n = len(centers)
    if n < 4:
        return None
    cols = _cluster_1d([c[0] for c in centers], cell_w * 0.5)
    rows = _cluster_1d([c[1] for c in centers], cell_h * 0.5)
    R = max(rows) + 1
    C = max(cols) + 1
    if R * C != n:
        return None
    seen = set()
    for rc in zip(rows, cols):
        if rc in seen:
            return None
        seen.add(rc)
    return rows, cols, R, C
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_mesh_rectify.py -c tests/pytest.ini -k "detect_grid or cluster" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/calibration.py tests/unit/test_mesh_rectify.py
git commit -m "feat(mesh-rectify): grid detection (cluster centers into rows/cols)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `rectify_group_grid` + wire into `assign_group_bounding_boxes`

**Files:**
- Modify: `mosaicmesh/calibration.py` (add `rectify_group_grid`; call it gated in `assign_group_bounding_boxes`)
- Test: `tests/unit/test_mesh_rectify.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_mesh_rectify.py`:

```python
from unittest.mock import patch


def _client(did, quad, dw=1024, dh=768):
    c = Client(); c.displayID = did
    c.measuredPerimeter = np.array(quad, dtype="int32").reshape(-1, 1, 2)
    c.deviceWidth = dw; c.deviceHeight = dh
    return c


def _keystoned_clients(did="G", R=4, C=6):
    """Build a regular R×C grid of cell quads, then push them through a known
    perspective (bottom enlarged) so the photo coords are keystoned — the exact
    bug. cell 80×60 on a 100×100 pitch (gap 20)."""
    import cv2 as _cv
    PITCH, CW, CH = 100.0, 80.0, 60.0
    # regular ("physical") corner points per cell, TL/TR/BR/BL
    cells = []
    for r in range(R):
        for c in range(C):
            cx, cy = c * PITCH, r * PITCH
            cells.append([[cx - CW/2, cy - CH/2], [cx + CW/2, cy - CH/2],
                          [cx + CW/2, cy + CH/2], [cx - CW/2, cy + CH/2]])
    # mild keystone: map the grid's physical bbox to a bottom-wider/taller trapezoid
    xs = [p[0] for cell in cells for p in cell]; ys = [p[1] for cell in cells for p in cell]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    src = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype="float32")
    dst = np.array([[120, 0], [480, 0], [600, 360], [0, 360]], dtype="float32")  # bottom wider+taller
    Hk = _cv.getPerspectiveTransform(src, dst)
    clients = []
    for cell in cells:
        pts = np.array(cell, dtype="float32").reshape(-1, 1, 2)
        photo = _cv.perspectiveTransform(pts, Hk).reshape(-1, 2)
        clients.append(_client(did, photo.tolist()))
    return clients


def _cell_centroid(clients):
    cs = []
    for c in clients:
        q = c.meshCellQuad
        cs.append((sum(p[0] for p in q) / 4.0, sum(p[1] for p in q) / 4.0))
    return (sum(p[0] for p in cs) / len(cs), sum(p[1] for p in cs) / len(cs))


def test_rectify_centers_keystoned_grid(fresh_settings):
    # The core fix: after rectification the cell centroid is the canvas center
    # (0.5, 0.5) — the keystone-induced low center is gone.
    d = Display()
    clients = _keystoned_clients("G")
    res = CAL.rectify_group_grid(d, clients)
    assert res is True
    assert d.meshGlobalRect and d.meshGlobalRect[0] > 0 and d.meshGlobalRect[1] > 0
    mx, my = _cell_centroid(clients)
    assert abs(mx - 0.5) < 0.02, mx
    assert abs(my - 0.5) < 0.02, my
    # native floats for JSON; quad has 4 corners
    q = clients[0].meshCellQuad
    assert len(q) == 4 and all(type(v) is float for pair in q for v in pair)


def test_rectify_skips_non_grid(fresh_settings):
    d = Display()
    clients = _keystoned_clients("G")[:-1]  # 23 -> not a clean 6x4
    assert CAL.rectify_group_grid(d, clients) is False
    assert d.meshGlobalRect is None
    assert all(c.meshCellQuad is None for c in clients)


def test_assign_calls_rectify_only_when_flag_on(fresh_settings):
    for i, c in enumerate(_keystoned_clients("G")):
        fresh_settings.clients["c%d" % i] = c
    # flag OFF (default): no rectified fields
    CAL.assign_group_bounding_boxes()
    assert fresh_settings.displays["G"].meshGlobalRect is None
    # flag ON: rectified fields populated
    with patch.object(CAL, "MESH_RECTIFY", True):
        CAL.assign_group_bounding_boxes()
    assert fresh_settings.displays["G"].meshGlobalRect is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_mesh_rectify.py -c tests/pytest.ini -k rectify -v`
Expected: FAIL — `module 'mosaicmesh.calibration' has no attribute 'rectify_group_grid'`.

- [ ] **Step 3: Implement `rectify_group_grid`**

In `mosaicmesh/calibration.py`, after `_detect_grid` (and before `assign_group_bounding_boxes`):

```python
def rectify_group_grid(display, clients):
    """Homography-rectify a group's mesh geometry. Maps the (keystoned) screen
    centers to a regular lattice, applies that homography to each screen's REAL
    quad corners, and stores Display.meshGlobalRect (device-px rectified canvas)
    + per-client Client.meshCellQuad (rectified cell, normalized 0..1 TL/TR/BR/BL,
    native floats for JSON). Returns True on success; on any non-grid/failure it
    clears the fields to None and returns False so the render path falls back to
    raw-bbox. Consumed only by the mesh-animation path."""
    cal = [c for c in clients if getattr(c, "measuredPerimeter", None) is not None]

    def _clear():
        display.meshGlobalRect = None
        for c in cal:
            c.meshCellQuad = None

    if len(cal) < 4:
        _clear(); return False
    corners, centers, cws, chs, dws, dhs = [], [], [], [], [], []
    for c in cal:
        q = np.array(c.measuredPerimeter, dtype="float64").reshape(-1, 2)
        corners.append(q)
        centers.append((float(q[:, 0].mean()), float(q[:, 1].mean())))
        cws.append(float(q[:, 0].max() - q[:, 0].min()))
        chs.append(float(q[:, 1].max() - q[:, 1].min()))
        dws.append(getattr(c, "deviceWidth", 0) or 0)
        dhs.append(getattr(c, "deviceHeight", 0) or 0)
    grid = _detect_grid(centers, statistics.median(cws), statistics.median(chs))
    if grid is None:
        _clear(); return False
    rows, cols, R, C = grid
    src = np.array(centers, dtype="float32")
    dst = np.array([[cols[i], rows[i]] for i in range(len(cal))], dtype="float32")
    H, _m = cv.findHomography(src, dst, 0)
    if H is None:
        _clear(); return False
    rect = []
    for q in corners:
        out = cv.perspectiveTransform(q.reshape(-1, 1, 2).astype("float32"), H).reshape(-1, 2)
        rect.append(out)
    allpts = np.concatenate(rect, axis=0)
    rbx = float(allpts[:, 0].min()); rby = float(allpts[:, 1].min())
    rbw = float(allpts[:, 0].max()) - rbx; rbh = float(allpts[:, 1].max()) - rby
    if rbw <= 0 or rbh <= 0:
        _clear(); return False
    rcw = [float(p[:, 0].max() - p[:, 0].min()) for p in rect]
    rch = [float(p[:, 1].max() - p[:, 1].min()) for p in rect]
    mcw = statistics.median(rcw); mch = statistics.median(rch)
    dwv = [d for d in dws if d > 0]; dhv = [d for d in dhs if d > 0]
    scale_x = (statistics.median(dwv) / mcw) if (dwv and mcw > 0) else 1.0
    scale_y = (statistics.median(dhv) / mch) if (dhv and mch > 0) else 1.0
    display.meshGlobalRect = [int(round(rbw * scale_x)), int(round(rbh * scale_y))]
    for i, c in enumerate(cal):
        p = rect[i]
        c.meshCellQuad = [[float((p[j, 0] - rbx) / rbw), float((p[j, 1] - rby) / rbh)]
                          for j in range(p.shape[0])]
    return True
```

- [ ] **Step 4: Wire it into `assign_group_bounding_boxes`**

In `mosaicmesh/calibration.py`, `assign_group_bounding_boxes`, the per-group loop currently ends with:

```python
        k = statistics.median(ratios) if ratios else 1.0
        display.meshGlobal = [int(round(bw * k)), int(round(bh * k))]
```

Add the gated rectify call right after that line (still inside the `for display_id, quads` loop):

```python
        k = statistics.median(ratios) if ratios else 1.0
        display.meshGlobal = [int(round(bw * k)), int(round(bh * k))]
        if MESH_RECTIFY:
            rectify_group_grid(display, members.get(display_id, []))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_mesh_rectify.py -c tests/pytest.ini -v`
Expected: PASS (all). If `test_rectify_centers_keystoned_grid` fails because `_detect_grid` returned None (the test keystone over-split a column), reduce the keystone in `_keystoned_clients` (move `dst` corners closer to `src`, e.g. `[[60,0],[540,0],[560,300],[40,300]]`) so columns stay separable, and re-run — the assertion (centroid ≈ 0.5) is the contract, the keystone strength is just test data.

- [ ] **Step 6: Run the full unit suite (no regressions)**

Run: `python pytest_runner.py --unit`
Expected: PASS — existing suite still green (MESH_RECTIFY off by default means `assign_group_bounding_boxes` behaves exactly as before for all existing tests).

- [ ] **Step 7: Commit**

```bash
git add mosaicmesh/calibration.py tests/unit/test_mesh_rectify.py
git commit -m "feat(mesh-rectify): rectify_group_grid homography pipeline + gated wiring

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Render-path selection in `_per_client_items`

**Files:**
- Modify: `mosaicmesh/render.py` (top-of-file import; the SCRIPT-mesh branch ~lines 1213-1221)
- Test: `tests/unit/test_mesh_rectify.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_mesh_rectify.py`:

```python
import json


def _mesh_display(fresh_settings, did="G"):
    d = Display()
    d.boundingBox = [0, 0, 200, 100]
    d.meshGlobal = [1774, 887]
    d.meshGlobalRect = [2000, 1000]
    me = MediaElement(); me.id = "a"; me.file = "plasma"
    me.playmode = PlayMode.SCRIPT; me.duration = 5.0; me.scriptSpan = "mesh"
    d.mediaElements = [me]
    fresh_settings.displays[did] = d
    return d


def test_per_client_items_uses_rectified_when_flag_on(fresh_settings):
    d = _mesh_display(fresh_settings)
    c = _client("G", [[0, 0], [100, 0], [100, 100], [0, 100]])
    c.meshCellQuad = [[0.1, 0.1], [0.4, 0.1], [0.4, 0.9], [0.1, 0.9]]
    with patch.object(CAL, "MESH_RECTIFY", True):
        items = R._per_client_items(d, "c1", c)
    assert items[0]["meshGlobal"] == [2000, 1000]            # rectified canvas
    assert items[0]["meshQuad"] == [[0.1, 0.1], [0.4, 0.1], [0.4, 0.9], [0.1, 0.9]]
    json.dumps(items)                                         # JSON-safe


def test_per_client_items_raw_when_flag_off(fresh_settings):
    d = _mesh_display(fresh_settings)
    c = _client("G", [[0, 0], [100, 0], [100, 100], [0, 100]])
    c.meshCellQuad = [[0.1, 0.1], [0.4, 0.1], [0.4, 0.9], [0.1, 0.9]]  # present but ignored
    # MESH_RECTIFY default False -> raw-bbox path even though rect fields exist
    items = R._per_client_items(d, "c1", c)
    assert items[0]["meshGlobal"] == [1774, 887]             # raw canvas
    assert items[0]["meshQuad"][0] == [0.0, 0.0] and items[0]["meshQuad"][1] == [0.5, 0.0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_mesh_rectify.py -c tests/pytest.ini -k per_client -v`
Expected: FAIL — `test_per_client_items_uses_rectified_when_flag_on` gets the raw `meshGlobal` (`[1774,887]`), not the rectified one.

- [ ] **Step 3: Add the import + selection branch**

In `mosaicmesh/render.py`, ensure the module imports calibration at top (so `calibration.MESH_RECTIFY` is read at call time and patchable). Add near the other `from mosaicmesh import ...` imports if not already present:

```python
from mosaicmesh import calibration
```

(`calibration` imports `server` only lazily inside functions, so there is no import cycle from render importing calibration at module load.)

In `_per_client_items`, replace the current mesh block:

```python
        if (me.playmode == PlayMode.SCRIPT
                and getattr(me, "scriptSpan", "mirror") == "mesh"
                and c.measuredPerimeter is not None
                and display.boundingBox and getattr(display, "meshGlobal", None)):
            bx, by, bw, bh = display.boundingBox
            quad = np.array(c.measuredPerimeter).reshape(-1, 2)
            item["meshQuad"] = [[float((px - bx) / float(bw)), float((py - by) / float(bh))]
                                for (px, py) in quad]
            item["meshGlobal"] = list(display.meshGlobal)
        items.append(item)
```

with (flag tested first, so OFF == today's raw path exactly):

```python
        if me.playmode == PlayMode.SCRIPT and getattr(me, "scriptSpan", "mirror") == "mesh":
            if (calibration.MESH_RECTIFY
                    and getattr(display, "meshGlobalRect", None)
                    and getattr(c, "meshCellQuad", None)):
                # Homography-rectified geometry (keystone removed).
                item["meshQuad"] = c.meshCellQuad
                item["meshGlobal"] = list(display.meshGlobalRect)
            elif (c.measuredPerimeter is not None
                    and display.boundingBox and getattr(display, "meshGlobal", None)):
                # Raw-bbox path (today's behavior). measuredPerimeter is numpy
                # (4,1,2) -> reshape(-1,2); native float for the JSON payload.
                bx, by, bw, bh = display.boundingBox
                quad = np.array(c.measuredPerimeter).reshape(-1, 2)
                item["meshQuad"] = [[float((px - bx) / float(bw)), float((py - by) / float(bh))]
                                    for (px, py) in quad]
                item["meshGlobal"] = list(display.meshGlobal)
            # else: omit -> client goes black (uncalibrated mesh)
        items.append(item)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_mesh_rectify.py -c tests/pytest.ini -v`
Expected: PASS (all).

- [ ] **Step 5: Run full unit + JS suites (no regressions)**

Run: `python pytest_runner.py --unit` then `python pytest_runner.py --js`
Expected: both green. The existing `tests/unit/test_mesh_animations.py` (raw-bbox mesh) still passes because `MESH_RECTIFY` is off by default; the client/JS is untouched.

- [ ] **Step 6: Commit**

```bash
git add mosaicmesh/render.py tests/unit/test_mesh_rectify.py
git commit -m "feat(mesh-rectify): _per_client_items selects rectified geometry when flag on

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: iPad-1 on-wall sign-off (the real acceptance)

**Files:** none (manual hardware verification + experiment decision).

> No automated test can judge the wall. This experiment is kept-or-reverted on what it looks like on OEB.

- [ ] **Step 1: Enable + deploy**

Set `MESH_RECTIFY = True` in `mosaicmesh/calibration.py`, restart the server (the boot backfill's `assign_group_bounding_boxes` now computes `meshGlobalRect` + `meshCellQuad` from the persisted quads — no re-photograph needed).

- [ ] **Step 2: Verify on the wall**

Play a meshed radial animation (e.g. `radialPulse` or `particleGalaxy`, Wall mode = Mesh) on OEB Sign 1. Confirm: the animation's center sits at the **true wall center** (not low), vertical mapping is linear, and inter-screen gaps are intact. Compare against `MESH_RECTIFY = False` + restart (today's low-center raw behavior).

- [ ] **Step 3: Decide keep-vs-revert**

If it looks right: keep `MESH_RECTIFY = True` and merge `experiment/mesh-rectify` to main. If not: set it back to `False` (or abandon the branch). Record the outcome in the PR/commit.

---

## Self-Review

**Spec coverage:**
- `Client.meshCellQuad` + `Display.meshGlobalRect` defaults + `MESH_RECTIFY=False` → Task 1. ✅
- Grid detection (cluster rows/cols, validity `R*C==N` + one-per-cell, skip otherwise) → Task 2. ✅
- `rectify_group_grid`: centers, `_detect_grid`, `cv.findHomography` centers→(col,row) lattice, `cv.perspectiveTransform` on real corners, per-axis device-px scale with div0 guards, store native-float fields; gated call from `assign_group_bounding_boxes` → Task 3. ✅
- Render-path selection (flag-first, rectified when on+present, else raw, else omit→black) → Task 4. ✅
- Tests: clean-grid detect + irregular skip (T2); keystoned-grid rectifies to centered lattice + skip non-grid + flag-gated assign (T3); `_per_client_items` rectified-on / raw-off + JSON-safe (T4); full-suite no-regression (T3/T4). ✅
- Toggle gates compute + use; OFF == today → Tasks 3 (compute gate) + 4 (use gate). ✅
- iPad-1 sign-off + keep/revert → Task 5. ✅
- ES5 client / `mmMeshTransform` untouched; SEGMENT/INDIVIDUAL untouched → no task touches them (render change is inside the SCRIPT-mesh branch only). ✅

**Placeholder scan:** No TBD/TODO; every code step has full code. The Task-3 Step-5 note about reducing keystone strength is test-data tuning guidance with concrete fallback values, not a placeholder (the assertion contract is fixed).

**Type/name consistency:** `meshGlobalRect` (`[GW,GH]` ints) and `meshCellQuad` (`[[u,v]×4]` native floats, TL/TR/BR/BL) consistent across state.py / calibration.py / render.py / tests. `rectify_group_grid(display, clients)`, `_detect_grid(centers, cell_w, cell_h)`, `_cluster_1d(vals, threshold)` signatures identical at definition and call sites. `calibration.MESH_RECTIFY` referenced as a module attribute everywhere (patchable). ✅
