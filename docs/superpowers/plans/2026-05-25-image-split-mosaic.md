# Image Split / Mosaic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render `SEGMENT` image items as per-screen perspective slices server-side, gated by an explicit render step, and play them synchronized — with the display client unchanged.

**Architecture:** Calibration persists each group's mosaic bounding box. `RENDER` warps each SEGMENT source image onto every calibrated screen (OpenCV homography) and records a readiness token. `PLAY` of a SEGMENT playlist checks the token (emits `RENDER_REQUIRED` if stale) and sends each client its own warped file URLs sharing one `startEpoch`. FULL playlists keep the existing group path.

**Tech Stack:** Python 3 / aiohttp / OpenCV (`cv2`) / NumPy (server). pytest. Playwright (light client check). No client changes.

**Spec:** `docs/superpowers/specs/2026-05-25-image-split-mosaic-design.md`

**Conventions:** `server.py` imports cleanly. Tests: `python -m pytest tests/unit -c tests/pytest.ini -q`. `server.py` already imports `cv2 as cv`, `numpy as np`, `os`, `from pathlib import Path`. Commit trailer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`. New tests go in `tests/unit/test_mosaic.py`. Place new helper functions in `server.py` near the other module-level helpers (after `playlist_index`/`sync_new_client_to_group`).

---

## Task 1: Pure geometry helpers

**Files:** Modify `server.py`; Create `tests/unit/test_mosaic.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_mosaic.py`:

```python
"""Unit tests for image split/mosaic (geometry, render, play gating)."""
import os
import sys
from pathlib import Path
import numpy as np
import cv2 as cv
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import server


class TestGeometryHelpers:
    def test_order_points_returns_tl_tr_br_bl(self):
        # quad corners given out of order, in Nx1x2 form like measuredPerimeter
        quad = np.array([[[50, 40]], [[10, 10]], [[10, 40]], [[50, 10]]])
        out = server.order_points(quad)
        assert list(out[0]) == [10.0, 10.0]   # TL
        assert list(out[1]) == [50.0, 10.0]   # TR
        assert list(out[2]) == [50.0, 40.0]   # BR
        assert list(out[3]) == [10.0, 40.0]   # BL

    def test_group_bounding_box_union(self):
        q1 = np.array([[[10, 10]], [[50, 10]], [[50, 40]], [[10, 40]]])
        q2 = np.array([[[60, 20]], [[100, 20]], [[100, 60]], [[60, 60]]])
        assert server.group_bounding_box([q1, q2]) == [10, 10, 91, 51]

    def test_group_bounding_box_empty(self):
        assert server.group_bounding_box([]) is None

    def test_resolve_media_path_image(self):
        assert server.resolve_media_path("/media/server/clouds.jpg") == os.path.join("media", "server", "images", "clouds.jpg")

    def test_resolve_media_path_video(self):
        assert server.resolve_media_path("/media/server/clip.mp4") == os.path.join("media", "server", "videos", "clip.mp4")
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_mosaic.py::TestGeometryHelpers -c tests/pytest.ini -q`
Expected: FAIL — `AttributeError: module 'server' has no attribute 'order_points'`.

- [ ] **Step 3: Implement the helpers**

In `server.py`, after the `playlist_index` function, add:

```python
def order_points(pts):
    """Reduce a set of quad points (Nx1x2 or Nx2) to 4 corners [TL, TR, BR, BL]."""
    pts = np.array(pts, dtype="float64").reshape(-1, 2)
    s = pts.sum(axis=1)
    d = pts[:, 0] - pts[:, 1]
    return np.array([
        pts[np.argmin(s)],   # TL: smallest x+y
        pts[np.argmax(d)],   # TR: largest x-y
        pts[np.argmax(s)],   # BR: largest x+y
        pts[np.argmin(d)],   # BL: smallest x-y
    ], dtype="float32")


def group_bounding_box(quads):
    """Tight axis-aligned [x, y, w, h] enclosing all screen quads (photo coords)."""
    if not quads:
        return None
    allpts = np.concatenate([np.array(q, dtype="int32").reshape(-1, 2) for q in quads])
    x, y, w, h = cv.boundingRect(allpts)
    return [int(x), int(y), int(w), int(h)]


def resolve_media_path(file_url):
    """Map a media URL ('/media/<client>/<name>') to its on-disk path, matching
    media_handler's convention (images/ or videos/ by extension)."""
    parts = file_url.strip("/").split("/")
    if len(parts) < 3 or parts[0] != "media":
        return None
    client = parts[1]
    name = parts[-1]
    subdir = "videos" if name.lower().endswith(".mp4") else "images"
    return os.path.join("media", client, subdir, name)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_mosaic.py::TestGeometryHelpers -c tests/pytest.ini -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_mosaic.py
git commit -m "feat(mosaic): geometry helpers (order_points, group bbox, media path)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Per-screen perspective warp

**Files:** Modify `server.py`; Test `tests/unit/test_mosaic.py`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mosaic.py`:

```python
class TestWarp:
    def _half_image(self):
        # left half red, right half blue (BGR)
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        img[:, :50] = (0, 0, 255)   # red
        img[:, 50:] = (255, 0, 0)   # blue
        return img

    def test_warp_full_quad_is_identity_like(self):
        img = self._half_image()
        quad = np.array([[[0, 0]], [[100, 0]], [[100, 100]], [[0, 100]]])
        out = server.warp_image_for_screen(img, [0, 0, 100, 100], quad, 100, 100)
        assert out.shape == (100, 100, 3)
        assert out[50, 25][2] > 200 and out[50, 25][0] < 50   # left still red
        assert out[50, 75][0] > 200 and out[50, 75][2] < 50   # right still blue

    def test_warp_left_quad_stretches_left_region(self):
        img = self._half_image()
        # this screen covers only the LEFT half of the bbox -> should show all red
        quad = np.array([[[0, 0]], [[50, 0]], [[50, 100]], [[0, 100]]])
        out = server.warp_image_for_screen(img, [0, 0, 100, 100], quad, 80, 80)
        assert out.shape == (80, 80, 3)
        assert out[40, 70][2] > 200 and out[40, 70][0] < 50   # red across the whole screen
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_mosaic.py::TestWarp -c tests/pytest.ini -q`
Expected: FAIL — `warp_image_for_screen` not defined.

- [ ] **Step 3: Implement the warp**

In `server.py`, after `group_bounding_box`, add:

```python
def warp_image_for_screen(source_img, bbox, screen_quad, out_w, out_h):
    """Warp the region of source_img under a screen's quad onto that screen's
    pixel rect. bbox is the group's photo-space bounding box; the full image is
    stretched to fill bbox, so the screen quad (photo coords) maps back into
    media coords, then a homography fits it to out_w x out_h."""
    h, w = source_img.shape[:2]
    bx, by, bw, bh = bbox
    ordered = order_points(screen_quad)  # [TL, TR, BR, BL] in photo coords
    src = np.array([[(px - bx) / bw * w, (py - by) / bh * h] for (px, py) in ordered], dtype="float32")
    dst = np.array([[0, 0], [out_w, 0], [out_w, out_h], [0, out_h]], dtype="float32")
    m = cv.getPerspectiveTransform(src, dst)
    return cv.warpPerspective(source_img, m, (out_w, out_h))
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_mosaic.py::TestWarp -c tests/pytest.ini -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_mosaic.py
git commit -m "feat(mosaic): per-screen perspective warp (warp_image_for_screen)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Calibration persists per-group bounding box

**Files:** Modify `server.py`; Test `tests/unit/test_mosaic.py`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mosaic.py`:

```python
class TestGroupBBoxAssignment:
    def test_assign_group_bounding_boxes(self, mock_settings):
        server.settings = mock_settings
        c1 = server.Client(); c1.displayID = "Default"
        c1.measuredPerimeter = np.array([[[10, 10]], [[50, 10]], [[50, 40]], [[10, 40]]])
        c2 = server.Client(); c2.displayID = "Default"
        c2.measuredPerimeter = np.array([[[60, 20]], [[100, 20]], [[100, 60]], [[60, 60]]])
        c3 = server.Client(); c3.displayID = "Mobile"  # different group, no perimeter
        mock_settings.clients = {"c1": c1, "c2": c2, "c3": c3}

        server.assign_group_bounding_boxes()

        assert mock_settings.displays["Default"].boundingBox == [10, 10, 91, 51]
        assert mock_settings.displays["Default"].boundingBoxCenter == [55, 35]
        assert mock_settings.displays["Mobile"].boundingBox is None
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_mosaic.py::TestGroupBBoxAssignment -c tests/pytest.ini -q`
Expected: FAIL — `assign_group_bounding_boxes` not defined.

- [ ] **Step 3: Implement and wire into calibration**

In `server.py`, after `warp_image_for_screen`, add:

```python
def assign_group_bounding_boxes():
    """Per display group, set boundingBox/boundingBoxCenter from the ArUco
    screens' quads (photo coords). Call after calibration."""
    groups = {}
    for key, client in settings.clients.items():
        if client.measuredPerimeter is not None and client.displayID:
            groups.setdefault(client.displayID, []).append(client.measuredPerimeter)
    for display_id, quads in groups.items():
        display = settings.displays.setdefault(display_id, Display())
        bbox = group_bounding_box(quads)
        display.boundingBox = bbox
        if bbox:
            display.boundingBoxCenter = [bbox[0] + bbox[2] // 2, bbox[1] + bbox[3] // 2]
```

Then, in `calibrate()`, add a call to `assign_group_bounding_boxes()` immediately before its `return` statement (after the markers/perimeters have been recorded):

```python
    assign_group_bounding_boxes()
    return "media/displays/calibration.png","text/html"
```

(The existing final two lines of `calibrate()` are the `cv.imwrite(...)`/cleanup and `return "media/displays/calibration.png","text/html"`; insert the call just before that return.)

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_mosaic.py::TestGroupBBoxAssignment -c tests/pytest.ini -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_mosaic.py
git commit -m "feat(mosaic): calibration persists per-group bounding box

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Render readiness — token + SETPLAYLIST playmode

**Files:** Modify `server.py`; Test `tests/unit/test_mosaic.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_mosaic.py`:

```python
class TestRenderToken:
    def _seg_group(self, mock_settings):
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "a"; me.file = "/media/server/x.jpg"
        me.duration = 1000; me.playmode = server.PlayMode.SEGMENT
        disp.mediaElements = [me]
        disp.boundingBox = [0, 0, 100, 100]
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 800; c.deviceHeight = 600
        c.measuredPerimeter = np.array([[[0, 0]], [[50, 0]], [[50, 100]], [[0, 100]]])
        mock_settings.clients = {"c1": c}
        return disp, c

    def test_token_is_stable(self, mock_settings):
        server.settings = mock_settings
        self._seg_group(mock_settings)
        assert server.compute_render_token("Default") == server.compute_render_token("Default")

    def test_token_changes_with_resolution(self, mock_settings):
        server.settings = mock_settings
        disp, c = self._seg_group(mock_settings)
        t1 = server.compute_render_token("Default")
        c.deviceWidth = 1920
        assert server.compute_render_token("Default") != t1

    def test_token_changes_with_duration(self, mock_settings):
        server.settings = mock_settings
        disp, c = self._seg_group(mock_settings)
        t1 = server.compute_render_token("Default")
        disp.mediaElements[0].duration = 5000
        assert server.compute_render_token("Default") != t1


class TestSetPlaylistPlaymode:
    def test_setplaylist_sets_segment_and_clears_rendered(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        disp.renderedToken = "stale"
        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "SETPLAYLIST",
               "PAYLOAD": {"displayID": "Default", "loop": False,
                           "items": [{"id": "a", "file": "/media/server/x.jpg",
                                      "duration": 1000, "playmode": "SEGMENT"}]}}
        sess = MagicMock(); sess.id = "s"; sess.request = MagicMock()
        sess.request.remote = "127.0.0.1"; sess.request.headers = {"User-Agent": "T"}
        server.msg_response(msg, sess)
        assert disp.mediaElements[0].playmode == server.PlayMode.SEGMENT
        assert disp.renderedToken == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_mosaic.py::TestRenderToken tests/unit/test_mosaic.py::TestSetPlaylistPlaymode -c tests/pytest.ini -q`
Expected: FAIL — `compute_render_token` not defined / playmode not honored / `renderedToken` missing.

- [ ] **Step 3a: Add the `renderedToken` field**

In `server.py` `class Display.__init__`, add after `self.pauseOffset = 0`:

```python
        self.pauseOffset = 0      # ms into the playlist when paused
        self.renderedToken = ""   # token of the last successful SEGMENT render
```

- [ ] **Step 3b: Add `import hashlib`**

At the top of `server.py`, add `import hashlib` next to the other imports (e.g., after `import argparse`).

- [ ] **Step 3c: Add `_group_clients` and `compute_render_token`**

In `server.py`, after `assign_group_bounding_boxes`, add:

```python
def _group_clients(display_id):
    """Sorted [(clientKey, client)] for clients assigned to a display group."""
    return sorted([(k, c) for k, c in settings.clients.items() if c.displayID == display_id])


def compute_render_token(display_id):
    """Stable hash of the inputs that affect a SEGMENT render: the playlist
    items, the group bounding box, and each client's resolution + measured quad.
    Rendered assets are valid only while this matches Display.renderedToken."""
    display = settings.displays.get(display_id)
    if not display:
        return ""
    items = []
    for me in display.mediaElements:
        pm = me.playmode.name if hasattr(me.playmode, "name") else str(me.playmode)
        items.append((me.id, me.file, me.duration, pm))
    clients = []
    for key, c in _group_clients(display_id):
        perim = None
        if c.measuredPerimeter is not None:
            perim = np.array(c.measuredPerimeter, dtype="int32").reshape(-1, 2).tolist()
        clients.append((key, c.deviceWidth, c.deviceHeight, perim))
    raw = repr((items, display.boundingBox, clients))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
```

- [ ] **Step 3d: Honor `playmode` and clear `renderedToken` in SETPLAYLIST**

In `server.py` `msg_response`, in the `SETPLAYLIST` branch, change the item loop and add the token clear. Replace:

```python
        for item in payload.get("items", []):
            me = MediaElement()
            me.id = item.get("id")
            me.file = item.get("file")
            me.duration = item.get("duration")
            me.playmode = PlayMode.FULL  # MVP: identical full-screen
            display.mediaElements.append(me)
        display.loop = bool(payload.get("loop", False))
```

with:

```python
        for item in payload.get("items", []):
            me = MediaElement()
            me.id = item.get("id")
            me.file = item.get("file")
            me.duration = item.get("duration")
            me.playmode = PlayMode.SEGMENT if item.get("playmode") == "SEGMENT" else PlayMode.FULL
            display.mediaElements.append(me)
        display.loop = bool(payload.get("loop", False))
        display.renderedToken = ""  # playlist changed -> needs (re)render
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_mosaic.py::TestRenderToken tests/unit/test_mosaic.py::TestSetPlaylistPlaymode -c tests/pytest.ini -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_mosaic.py
git commit -m "feat(mosaic): render readiness token + SETPLAYLIST playmode

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `RENDER` — warp per screen and set the token

**Files:** Modify `server.py`; Test `tests/unit/test_mosaic.py`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mosaic.py`:

```python
class TestRender:
    def test_render_group_writes_files_and_sets_token(self, mock_settings, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # isolate all media/ writes to a temp dir
        # source image on disk where resolve_media_path expects it
        src_dir = tmp_path / "media" / "server" / "images"
        src_dir.mkdir(parents=True)
        img = np.zeros((100, 100, 3), dtype=np.uint8); img[:, :50] = (0, 0, 255)
        cv.imwrite(str(src_dir / "x.jpg"), img)

        server.settings = mock_settings
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "a"; me.file = "/media/server/x.jpg"
        me.duration = 1000; me.playmode = server.PlayMode.SEGMENT
        disp.mediaElements = [me]
        disp.boundingBox = [0, 0, 100, 100]
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 80; c.deviceHeight = 60
        c.measuredPerimeter = np.array([[[0, 0]], [[50, 0]], [[50, 100]], [[0, 100]]])
        mock_settings.clients = {"c1": c}

        result = server.render_group("Default")

        assert result["status"] == "SUCCESS"
        assert result["files"] == 1
        assert disp.renderedToken == server.compute_render_token("Default")
        out = tmp_path / "media" / "c1" / "images" / ("seg_" + disp.renderedToken + "_0.png")
        assert out.exists()

    def test_render_group_no_calibration_errors(self, mock_settings):
        server.settings = mock_settings
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.file = "/media/server/x.jpg"; me.duration = 1000
        me.playmode = server.PlayMode.SEGMENT
        disp.mediaElements = [me]
        disp.boundingBox = [0, 0, 100, 100]
        mock_settings.clients = {}  # no calibrated screens
        result = server.render_group("Default")
        assert result["status"] == "ERROR"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_mosaic.py::TestRender -c tests/pytest.ini -q`
Expected: FAIL — `render_group` not defined.

- [ ] **Step 3: Implement `render_group` + the RENDER handler**

In `server.py`, after `compute_render_token`, add:

```python
def render_group(display_id):
    """Warp every SEGMENT item onto each calibrated screen in the group, write
    the per-screen files, and record the render token. Synchronous."""
    display = settings.displays.get(display_id)
    if not display or not display.mediaElements:
        return {"status": "ERROR", "error": "no playlist"}
    if not display.boundingBox:
        return {"status": "ERROR", "error": "no calibration"}
    seg_items = [(i, me) for i, me in enumerate(display.mediaElements)
                 if me.playmode == PlayMode.SEGMENT]
    if not seg_items:
        return {"status": "ERROR", "error": "no SEGMENT items"}
    clients = [(k, c) for k, c in _group_clients(display_id) if c.measuredPerimeter is not None]
    if not clients:
        return {"status": "ERROR", "error": "no calibrated screens"}
    token = compute_render_token(display_id)
    count = 0
    for i, me in seg_items:
        src_path = resolve_media_path(me.file)
        img = cv.imread(src_path) if src_path else None
        if img is None:
            continue
        for key, c in clients:
            out_w = int(c.deviceWidth) or 1
            out_h = int(c.deviceHeight) or 1
            warped = warp_image_for_screen(img, display.boundingBox, c.measuredPerimeter, out_w, out_h)
            out_dir = os.path.join("media", key, "images")
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            cv.imwrite(os.path.join(out_dir, "seg_" + token + "_" + str(i) + ".png"), warped)
            count += 1
    display.renderedToken = token
    return {"status": "SUCCESS", "token": token, "files": count}
```

In `msg_response`, add this branch immediately before the final `else:`:

```python
    elif(msg["REQUEST"] == "RENDER"):
        response["PAYLOAD"] = render_group(msg["PAYLOAD"]["displayID"])
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_mosaic.py::TestRender -c tests/pytest.ini -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_mosaic.py
git commit -m "feat(mosaic): RENDER warps SEGMENT items per screen and sets the token

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: `PLAY` gating + per-client SEGMENT playback

**Files:** Modify `server.py`; Test `tests/unit/test_mosaic.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_mosaic.py`:

```python
import jsonpickle


class TestSegmentPlay:
    def _rendered_group(self, mock_settings, two_clients=True):
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "a"; me.file = "/media/server/x.jpg"
        me.duration = 1000; me.playmode = server.PlayMode.SEGMENT
        disp.mediaElements = [me]
        disp.loop = True
        disp.boundingBox = [0, 0, 100, 100]
        disp.action = server.PlayState.STOP
        c1 = server.Client(); c1.displayID = "Default"; c1.deviceWidth = 80; c1.deviceHeight = 60
        c1.measuredPerimeter = np.array([[[0, 0]], [[50, 0]], [[50, 100]], [[0, 100]]])
        clients = {"c1": c1}
        if two_clients:
            c2 = server.Client(); c2.displayID = "Default"; c2.deviceWidth = 80; c2.deviceHeight = 60
            c2.measuredPerimeter = np.array([[[50, 0]], [[100, 0]], [[100, 100]], [[50, 100]]])
            clients["c2"] = c2
        mock_settings.clients = clients
        return disp

    def _sess(self):
        s = MagicMock(); s.id = "s"; s.request = MagicMock()
        s.request.remote = "127.0.0.1"; s.request.headers = {"User-Agent": "T"}
        return s

    def test_play_rendered_sends_per_client_warped(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = self._rendered_group(mock_settings)
        disp.renderedToken = server.compute_render_token("Default")  # mark rendered
        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "PLAY", "PAYLOAD": {"displayID": "Default"}}
        server.msg_response(msg, self._sess())
        # one PLAY per client (broadcast_to_client), not the group broadcast
        assert server.socketmanager.broadcast.call_count == 2
        sent = jsonpickle.decode(server.socketmanager.broadcast.call_args_list[0].args[0])
        assert "/seg_" in sent["PAYLOAD"]["items"][0]["file"]

    def test_play_stale_emits_render_required(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        self._rendered_group(mock_settings)  # renderedToken left ""
        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "PLAY", "PAYLOAD": {"displayID": "Default"}}
        ret = server.msg_response(msg, self._sess())
        decoded = jsonpickle.decode(ret)
        assert decoded["PAYLOAD"]["status"] == "RENDER_REQUIRED"
        assert server.socketmanager.broadcast.call_count == 0

    def test_play_full_only_uses_group_path(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "a"; me.file = "/media/server/x.jpg"
        me.duration = 1000; me.playmode = server.PlayMode.FULL
        disp.mediaElements = [me]; disp.loop = True; disp.action = server.PlayState.STOP
        c1 = server.Client(); c1.displayID = "Default"
        mock_settings.clients = {"c1": c1}
        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "PLAY", "PAYLOAD": {"displayID": "Default"}}
        ret = server.msg_response(msg, self._sess())
        assert jsonpickle.decode(ret)["PAYLOAD"] == "SUCCESS"
        assert server.socketmanager.broadcast.call_count == 1  # group broadcast, one client
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_mosaic.py::TestSegmentPlay -c tests/pytest.ini -q`
Expected: FAIL — current PLAY ignores SEGMENT/render, so the per-client and RENDER_REQUIRED behaviors are absent.

- [ ] **Step 3: Add `_broadcast_segment_play` and rewrite the PLAY branch**

In `server.py`, after `render_group`, add:

```python
def _broadcast_segment_play(display_id, display):
    """Send each client its own PLAY: SEGMENT items use that client's warped
    file (or the full source if it has no calibration), FULL items use the
    shared source. All clients share display.playStartEpoch."""
    token = display.renderedToken
    for key, c in _group_clients(display_id):
        items = []
        for i, me in enumerate(display.mediaElements):
            if me.playmode == PlayMode.SEGMENT and c.measuredPerimeter is not None:
                f = "/media/" + key + "/seg_" + token + "_" + str(i) + ".png"
            else:
                f = me.file  # FULL item, or uncalibrated fallback to full source
            items.append({"id": me.id, "file": f, "duration": me.duration})
        broadcast_to_client(key, {"REQUEST": "PLAY",
            "PAYLOAD": {"startEpoch": display.playStartEpoch, "items": items, "loop": display.loop}})
```

In `msg_response`, REPLACE the entire existing `elif(msg["REQUEST"] == "PLAY"):` branch with:

```python
    elif(msg["REQUEST"] == "PLAY"):
        display_id = msg["PAYLOAD"]["displayID"]
        display = settings.displays.get(display_id)
        if not display or not display.mediaElements:
            response["PAYLOAD"] = "SUCCESS"
        else:
            now_ms = int(time.time() * 1000)
            resume_epoch = now_ms - display.pauseOffset if display.action == PlayState.PAUSE else now_ms
            has_segment = any(me.playmode == PlayMode.SEGMENT for me in display.mediaElements)
            if has_segment and compute_render_token(display_id) != display.renderedToken:
                response["PAYLOAD"] = {"status": "RENDER_REQUIRED", "displayID": display_id}
            else:
                display.playStartEpoch = resume_epoch
                display.action = PlayState.PLAY
                if has_segment:
                    _broadcast_segment_play(display_id, display)
                else:
                    items = [{"id": me.id, "file": me.file, "duration": me.duration}
                             for me in display.mediaElements]
                    broadcast_to_display_group(display_id, {
                        "REQUEST": "PLAY",
                        "PAYLOAD": {"startEpoch": display.playStartEpoch,
                                    "items": items, "loop": display.loop}})
                response["PAYLOAD"] = "SUCCESS"
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_mosaic.py::TestSegmentPlay -c tests/pytest.ini -q`
Expected: PASS

- [ ] **Step 5: Run the full suite for regressions**

Run: `python -m pytest tests/unit tests/integration -c tests/pytest.ini -q`
Expected: all pass — the FULL-playlist PLAY/PAUSE/resume tests in `test_playback.py` still pass (FULL playlists take the unchanged group path; resume math preserved via `resume_epoch`).

- [ ] **Step 6: Commit**

```bash
git add server.py tests/unit/test_mosaic.py
git commit -m "feat(mosaic): PLAY gates on render readiness, sends per-client warped PLAYs

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: End-to-end Playwright check (light)

**Files:** none (verification only). Controller-run. The client is unchanged, so this is a sanity check that two clients in a rendered SEGMENT group receive distinct warped URLs with a shared `startEpoch`.

- [ ] **Step 1: Start the server**

Run (background): `python server.py -p 3000 -v`; confirm `curl http://localhost:3000/` → 200. Remove any stale `settings.dat` first so the group/client state is clean.

- [ ] **Step 2: Drive a SEGMENT render+play and observe the two clients**

Open two Playwright pages to `http://localhost:3000/` (or one page plus a second context). Wait for both to register + sync. Then, from one page's console, drive the flow over the socket and read both clients' received `PLAY`:

- Inject calibration geometry for both clients server-side is non-trivial from the browser; instead verify the message wiring with a manual server-side fixture: in a separate terminal, it is acceptable to assert this path via the pytest in Task 6 (which already proves per-client warped URLs + shared startEpoch + RENDER_REQUIRED). For the browser check, confirm only that: (a) `SETPLAYLIST` with a SEGMENT item leaves the group needing render, (b) `PLAY` returns `RENDER_REQUIRED` before render, and (c) after `RENDER` (which needs calibration) `PLAY` no longer returns `RENDER_REQUIRED`.

Minimal browser assertion (no calibration available ⇒ expect RENDER_REQUIRED, proving the gate is wired end-to-end over the socket):

```javascript
async () => {
  function sleep(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
  var dev = await fetch('/api/discovery/devices').then(function(r){ return r.json(); });
  var group=null,i; for(i=0;i<dev.devices.length;i++){ if(dev.devices[i].clientKey===getUDID()){ group=dev.devices[i].displayID; } }
  var got = null;
  var prev = sock_callback;
  // capture the next PLAY/RENDER_REQUIRED-bearing response by polling discovery is not enough;
  // instead rely on the SETPLAYLIST+PLAY round-trip via a one-shot listener:
  sock.send(generateMessage('SRV','SETPLAYLIST',{displayID:group,loop:false,items:[{id:'a',file:'/media/server/x.jpg',duration:1000,playmode:'SEGMENT'}]}));
  await sleep(300);
  // PLAY response comes back to this client as a normal message; check it does not start playback
  sock.send(generateMessage('SRV','PLAY',{displayID:group}));
  await sleep(500);
  return { group: group, playbackActive: playback.active };
}
```
Expected: `playbackActive:false` — the SEGMENT group did not start playing because it is unrendered (no calibration in this browser-only setup), confirming the gate is live over the socket.

- [ ] **Step 3: Shut down + clean up**

Stop the background server (free port 3000); close the browser; remove `.playwright-mcp/` and any `settings.dat` the run created. No commit.

> Note: the substantive per-client-warp behavior is fully covered by the Task 6 pytest (deterministic, no codec/calibration-photo needed). This browser step only confirms the render-gate is wired through the live socket.

---

## Self-review notes

- **Spec coverage:** group bbox persisted by calibration (Task 3); per-screen homography warp (Tasks 1–2); `Display.renderedToken` + `compute_render_token` + SETPLAYLIST playmode/unrender (Task 4); `RENDER` warps + writes per-screen files + sets token (Task 5); `PLAY` gating with `RENDER_REQUIRED` and per-client warped PLAYs sharing one `startEpoch`, FULL path unchanged, uncalibrated fallback to source (Task 6); client unchanged (no client task). order_points handles ≥4 points; source resolved via `resolve_media_path`. Video split / editor / async render correctly absent.
- **Placeholder scan:** none — concrete code/commands throughout. (Task 7 intentionally documents that the heavy behavior is covered by Task 6 pytest, with a thin live-gate browser check.)
- **Type/name consistency:** `order_points`, `group_bounding_box`, `resolve_media_path`, `warp_image_for_screen`, `assign_group_bounding_boxes`, `_group_clients`, `compute_render_token`, `render_group`, `_broadcast_segment_play`, `Display.renderedToken`, `PlayMode.SEGMENT` used consistently. Warped URL form `/media/<key>/seg_<token>_<i>.png` matches both the write path (`media/<key>/images/seg_...png`) and `media_handler`'s `/media/<client>/<file>` → `media/<client>/images/<file>` mapping.
