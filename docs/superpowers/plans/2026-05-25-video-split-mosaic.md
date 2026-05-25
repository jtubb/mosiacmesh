# Video Split / Mosaic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `SEGMENT` mode to `.mp4` — each calibrated screen gets a perspective-warped, audio-bearing H.264 slice produced by ffmpeg — and unify rendering into one asynchronous job with a status the PLAY gate respects.

**Architecture:** `RENDER` schedules an async job (`render_group_async`) that warps image items inline (OpenCV) and video items by awaiting an ffmpeg `perspective`+`scale` subprocess per screen (H.264 baseline, audio kept). `Display.renderStatus` (rendering/ready/error) is broadcast as `RENDER_STATUS`; `PLAY` of a SEGMENT playlist returns `RENDER_IN_PROGRESS`/`RENDER_REQUIRED` until ready, then sends per-client warped PLAYs. The client changes only to play video unmuted.

**Tech Stack:** Python 3 / aiohttp / asyncio subprocess / OpenCV / ffmpeg (system binary, libx264). pytest. Vanilla ES5 client (1-line change).

**Spec:** `docs/superpowers/specs/2026-05-25-video-split-mosaic-design.md`

**Conventions:** `server.py` imports cleanly (`asyncio`, `cv2 as cv`, `numpy as np`, `os`, `Path`, `hashlib`, `jsonpickle`, `logging` all present). Tests: `python -m pytest tests/unit -c tests/pytest.ini -q` (`asyncio_mode=auto` is set, so `async def test_*` run). New tests append to `tests/unit/test_mosaic.py`. Commit trailer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`. Place new helpers in `server.py` near the existing mosaic helpers (after `_broadcast_segment_play`).

> This slice **refactors** the image slice's synchronous `render_group` into an async `render_group_async` and moves RENDER's input validation into the RENDER message handler. Task 2 updates the existing `TestRender` tests accordingly.

---

## Task 1: ffmpeg command + source-quad + dimensions helpers

**Files:** Modify `server.py`; Test `tests/unit/test_mosaic.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_mosaic.py`:

```python
class TestFfmpegHelpers:
    def test_quad_to_source_points(self):
        # screen quad covers the right half of a 100x100 bbox; source video 200x100
        quad = np.array([[[50, 0]], [[100, 0]], [[100, 100]], [[50, 100]]])
        pts = server.quad_to_source_points([0, 0, 100, 100], quad, 200, 100)
        # ordered [TL,TR,BR,BL] in source px: x in {100,200}, y in {0,100}
        assert pts == [[100.0, 0.0], [200.0, 0.0], [200.0, 100.0], [100.0, 100.0]]

    def test_build_ffmpeg_cmd_has_perspective_h264_and_audio(self):
        pts = [[10.0, 20.0], [110.0, 20.0], [110.0, 220.0], [10.0, 220.0]]  # TL,TR,BR,BL
        cmd = server.build_ffmpeg_perspective_cmd("in.mp4", "out.mp4", pts, 800, 600)
        assert cmd[0] == "ffmpeg"
        assert "in.mp4" in cmd and cmd[-1] == "out.mp4"
        vf = cmd[cmd.index("-vf") + 1]
        # ffmpeg perspective order is TL,TR,BL,BR -> x2,y2 must be the BL corner (10,220)
        assert vf.startswith("perspective=10:20:110:20:10:220:110:220:sense=source")
        assert "scale=800:600" in vf
        assert "libx264" in cmd and "baseline" in cmd and "yuv420p" in cmd
        assert "-c:a" in cmd and "aac" in cmd
        assert "-an" not in cmd  # audio is kept

    def test_is_video_item(self):
        assert server.isVideoItem("/media/server/clip.mp4") is True
        assert server.isVideoItem("/media/server/pic.jpg") is False
        assert server.isVideoItem("/media/server/clip.MP4?t=1") is True

    def test_get_video_dimensions(self, monkeypatch):
        class FakeCap:
            def get(self, prop):
                import cv2
                return 1920.0 if prop == cv2.CAP_PROP_FRAME_WIDTH else 1080.0
            def release(self): pass
        monkeypatch.setattr(server.cv, "VideoCapture", lambda p: FakeCap())
        assert server.get_video_dimensions("x.mp4") == (1920, 1080)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_mosaic.py::TestFfmpegHelpers -c tests/pytest.ini -q`
Expected: FAIL — helpers not defined.

- [ ] **Step 3: Implement the helpers**

In `server.py`, after `_broadcast_segment_play`, add:

```python
def isVideoItem(file):
    """True if a media file is a video (.mp4), mirroring the client's isVideoItem.
    Tolerates a trailing ?query."""
    return str(file or "").lower().split("?")[0].endswith(".mp4")


def quad_to_source_points(bbox, screen_quad, src_w, src_h):
    """Ordered [TL, TR, BR, BL] corners of the screen's quad expressed in source
    media pixel coords (the source is stretched to fill the group bbox)."""
    bx, by, bw, bh = bbox
    ordered = order_points(screen_quad)  # [TL, TR, BR, BL] in photo coords
    return [[(float(px) - bx) / bw * src_w, (float(py) - by) / bh * src_h] for (px, py) in ordered]


def build_ffmpeg_perspective_cmd(src_path, out_path, src_points, out_w, out_h):
    """ffmpeg arg list: perspective-warp the source quad to fill the frame, scale
    to the screen resolution, encode iPad-compatible H.264 + AAC audio.
    src_points is [TL, TR, BR, BL]; ffmpeg's perspective wants TL, TR, BL, BR."""
    tl, tr, br, bl = src_points
    def n(v):
        return str(int(round(v)))
    persp = ("perspective=" + n(tl[0]) + ":" + n(tl[1]) + ":" + n(tr[0]) + ":" + n(tr[1]) +
             ":" + n(bl[0]) + ":" + n(bl[1]) + ":" + n(br[0]) + ":" + n(br[1]) + ":sense=source")
    vf = persp + ",scale=" + str(out_w) + ":" + str(out_h)
    return ["ffmpeg", "-y", "-i", src_path,
            "-vf", vf,
            "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-preset", "veryfast", "-movflags", "+faststart", out_path]


def get_video_dimensions(path):
    """Return (width, height) of a video via OpenCV, or None if unreadable."""
    cap = cv.VideoCapture(path)
    try:
        w = int(cap.get(cv.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv.CAP_PROP_FRAME_HEIGHT))
    finally:
        cap.release()
    if w <= 0 or h <= 0:
        return None
    return (w, h)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_mosaic.py::TestFfmpegHelpers -c tests/pytest.ini -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_mosaic.py
git commit -m "feat(mosaic): ffmpeg perspective command + source-quad + video dims helpers

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Unified async render (`render_group_async`, status, RENDER handler)

**Files:** Modify `server.py`; Test `tests/unit/test_mosaic.py`.

- [ ] **Step 1: Add the `renderStatus` field**

In `server.py` `class Display.__init__`, add after `self.renderedToken = ""`:

```python
        self.renderedToken = ""   # token of the last successful SEGMENT render
        self.renderStatus = ""    # "" | "rendering" | "ready" | "error"
```

- [ ] **Step 2: Replace the `TestRender` class with async tests**

In `tests/unit/test_mosaic.py`, REPLACE the entire existing `class TestRender:` with:

```python
class TestRender:
    def _video_group(self, mock_settings):
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "v"; me.file = "/media/server/clip.mp4"
        me.duration = 5000; me.playmode = server.PlayMode.SEGMENT
        disp.mediaElements = [me]
        disp.boundingBox = [0, 0, 100, 100]
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 80; c.deviceHeight = 60
        c.measuredPerimeter = np.array([[[0, 0]], [[50, 0]], [[50, 100]], [[0, 100]]])
        mock_settings.clients = {"c1": c}
        return disp

    async def test_render_image_writes_files_and_sets_ready(self, mock_settings, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        src_dir = tmp_path / "media" / "server" / "images"; src_dir.mkdir(parents=True)
        img = np.zeros((100, 100, 3), dtype=np.uint8); img[:, :50] = (0, 0, 255)
        cv.imwrite(str(src_dir / "x.jpg"), img)
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "a"; me.file = "/media/server/x.jpg"
        me.duration = 1000; me.playmode = server.PlayMode.SEGMENT
        disp.mediaElements = [me]; disp.boundingBox = [0, 0, 100, 100]
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 80; c.deviceHeight = 60
        c.measuredPerimeter = np.array([[[0, 0]], [[50, 0]], [[50, 100]], [[0, 100]]])
        mock_settings.clients = {"c1": c}

        result = await server.render_group_async("Default")

        assert result["status"] == "ready"
        assert disp.renderStatus == "ready"
        assert disp.renderedToken == server.compute_render_token("Default")
        assert (tmp_path / "media" / "c1" / "images" / ("seg_" + disp.renderedToken + "_0.png")).exists()

    async def test_render_video_invokes_ffmpeg_per_screen(self, mock_settings, monkeypatch):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = self._video_group(mock_settings)
        monkeypatch.setattr(server, "get_video_dimensions", lambda p: (1920, 1080))

        calls = []
        class _Proc:
            returncode = 0
            async def communicate(self): return (b"", b"")
        async def _fake_exec(*args, **kwargs):
            calls.append(args)
            return _Proc()
        monkeypatch.setattr(server.asyncio, "create_subprocess_exec", _fake_exec)

        result = await server.render_group_async("Default")

        assert result["status"] == "ready"
        assert len(calls) == 1                      # one ffmpeg per (video item x screen)
        assert calls[0][0] == "ffmpeg"
        assert disp.renderedToken == server.compute_render_token("Default")
        # RENDER_STATUS broadcast at least twice (rendering, ready)
        assert server.socketmanager.broadcast.call_count >= 2

    async def test_render_video_ffmpeg_failure_sets_error(self, mock_settings, monkeypatch):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = self._video_group(mock_settings)
        monkeypatch.setattr(server, "get_video_dimensions", lambda p: (1920, 1080))
        class _Proc:
            returncode = 1
            async def communicate(self): return (b"", b"boom")
        async def _fake_exec(*args, **kwargs): return _Proc()
        monkeypatch.setattr(server.asyncio, "create_subprocess_exec", _fake_exec)

        result = await server.render_group_async("Default")
        assert result["status"] == "error"
        assert disp.renderStatus == "error"
        assert disp.renderedToken == ""           # unchanged on failure

    def test_render_handler_validation_errors_without_calibration(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.file = "/media/server/x.jpg"; me.duration = 1000
        me.playmode = server.PlayMode.SEGMENT
        disp.mediaElements = [me]; disp.boundingBox = [0, 0, 100, 100]
        mock_settings.clients = {}  # no calibrated screens
        sess = MagicMock(); sess.id = "s"; sess.request = MagicMock()
        sess.request.remote = "127.0.0.1"; sess.request.headers = {"User-Agent": "T"}
        import jsonpickle
        ret = server.msg_response({"SRC": "a", "DEST": "SRV", "REQUEST": "RENDER",
                                   "PAYLOAD": {"displayID": "Default"}}, sess)
        assert jsonpickle.decode(ret)["PAYLOAD"]["status"] == "ERROR"
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest tests/unit/test_mosaic.py::TestRender -c tests/pytest.ini -q`
Expected: FAIL — `render_group_async` not defined / `renderStatus` missing / RENDER handler still synchronous.

- [ ] **Step 4: Implement the async render + status broadcast + replace the RENDER handler**

In `server.py`, REMOVE the existing synchronous `render_group(display_id)` function and add in its place:

```python
def _broadcast_render_status(display_id, status):
    if socketmanager is not None:
        socketmanager.broadcast(jsonpickle.encode(
            {"REQUEST": "RENDER_STATUS", "PAYLOAD": {"displayID": display_id, "status": status}}))


async def render_group_async(display_id):
    """Async render of a group's SEGMENT items: images warped inline (OpenCV),
    videos warped by awaiting one ffmpeg subprocess per screen. Sets renderStatus
    and (on success) renderedToken; broadcasts RENDER_STATUS on each change."""
    display = settings.displays.get(display_id)
    if not display:
        return {"status": "error"}
    display.renderStatus = "rendering"
    _broadcast_render_status(display_id, "rendering")
    token = compute_render_token(display_id)
    try:
        seg_items = [(i, me) for i, me in enumerate(display.mediaElements)
                     if me.playmode == PlayMode.SEGMENT]
        clients = [(k, c) for k, c in _group_clients(display_id) if c.measuredPerimeter is not None]
        for i, me in seg_items:
            src_path = resolve_media_path(me.file)
            if isVideoItem(me.file):
                dims = get_video_dimensions(src_path) if src_path else None
                if not dims:
                    raise RuntimeError("cannot read source video: " + str(me.file))
                sw, sh = dims
                for key, c in clients:
                    pts = quad_to_source_points(display.boundingBox, c.measuredPerimeter, sw, sh)
                    out_dir = os.path.join("media", key, "videos")
                    Path(out_dir).mkdir(parents=True, exist_ok=True)
                    out_path = os.path.join(out_dir, "seg_" + token + "_" + str(i) + ".mp4")
                    cmd = build_ffmpeg_perspective_cmd(src_path, out_path, pts,
                                                       int(c.deviceWidth) or 1, int(c.deviceHeight) or 1)
                    proc = await asyncio.create_subprocess_exec(
                        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    await proc.communicate()
                    if proc.returncode != 0:
                        raise RuntimeError("ffmpeg failed (" + str(proc.returncode) + ")")
            else:
                img = cv.imread(src_path) if src_path else None
                if img is None:
                    raise RuntimeError("cannot read source image: " + str(me.file))
                for key, c in clients:
                    warped = warp_image_for_screen(img, display.boundingBox, c.measuredPerimeter,
                                                   int(c.deviceWidth) or 1, int(c.deviceHeight) or 1)
                    out_dir = os.path.join("media", key, "images")
                    Path(out_dir).mkdir(parents=True, exist_ok=True)
                    cv.imwrite(os.path.join(out_dir, "seg_" + token + "_" + str(i) + ".png"), warped)
        display.renderedToken = token
        display.renderStatus = "ready"
        _broadcast_render_status(display_id, "ready")
        return {"status": "ready", "token": token}
    except Exception as e:
        logging.error("render failed for %s: %s", display_id, e)
        display.renderStatus = "error"
        _broadcast_render_status(display_id, "error")
        return {"status": "error", "error": str(e)}
```

REPLACE the existing `elif(msg["REQUEST"] == "RENDER"):` branch in `msg_response` with the validating, scheduling version:

```python
    elif(msg["REQUEST"] == "RENDER"):
        display_id = msg["PAYLOAD"]["displayID"]
        display = settings.displays.get(display_id)
        if not display or not display.mediaElements:
            response["PAYLOAD"] = {"status": "ERROR", "error": "no playlist"}
        elif not display.boundingBox:
            response["PAYLOAD"] = {"status": "ERROR", "error": "no calibration"}
        elif not any(me.playmode == PlayMode.SEGMENT for me in display.mediaElements):
            response["PAYLOAD"] = {"status": "ERROR", "error": "no SEGMENT items"}
        elif not [c for k, c in _group_clients(display_id) if c.measuredPerimeter is not None]:
            response["PAYLOAD"] = {"status": "ERROR", "error": "no calibrated screens"}
        elif display.renderStatus == "rendering":
            response["PAYLOAD"] = {"status": "rendering"}
        else:
            asyncio.ensure_future(render_group_async(display_id))
            response["PAYLOAD"] = {"status": "rendering"}
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/unit/test_mosaic.py::TestRender -c tests/pytest.ini -q`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add server.py tests/unit/test_mosaic.py
git commit -m "feat(mosaic): unify rendering into an async job (images + ffmpeg video) with status

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: PLAY gating (RENDER_IN_PROGRESS) + video warped-file URLs

**Files:** Modify `server.py`; Test `tests/unit/test_mosaic.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_mosaic.py`:

```python
class TestVideoSegmentPlay:
    def _video_group(self, mock_settings):
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "v"; me.file = "/media/server/clip.mp4"
        me.duration = 5000; me.playmode = server.PlayMode.SEGMENT
        disp.mediaElements = [me]; disp.loop = True; disp.boundingBox = [0, 0, 100, 100]
        disp.action = server.PlayState.STOP
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 80; c.deviceHeight = 60
        c.measuredPerimeter = np.array([[[0, 0]], [[50, 0]], [[50, 100]], [[0, 100]]])
        mock_settings.clients = {"c1": c}
        return disp

    def _sess(self):
        s = MagicMock(); s.id = "s"; s.request = MagicMock()
        s.request.remote = "127.0.0.1"; s.request.headers = {"User-Agent": "T"}
        return s

    def test_play_while_rendering_emits_in_progress(self, mock_settings):
        import jsonpickle
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = self._video_group(mock_settings)
        disp.renderStatus = "rendering"
        ret = server.msg_response({"SRC": "a", "DEST": "SRV", "REQUEST": "PLAY",
                                   "PAYLOAD": {"displayID": "Default"}}, self._sess())
        assert jsonpickle.decode(ret)["PAYLOAD"]["status"] == "RENDER_IN_PROGRESS"
        assert server.socketmanager.broadcast.call_count == 0

    def test_play_rendered_video_sends_mp4_urls(self, mock_settings):
        import jsonpickle
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = self._video_group(mock_settings)
        disp.renderStatus = "ready"
        disp.renderedToken = server.compute_render_token("Default")
        server.msg_response({"SRC": "a", "DEST": "SRV", "REQUEST": "PLAY",
                             "PAYLOAD": {"displayID": "Default"}}, self._sess())
        assert server.socketmanager.broadcast.call_count == 1  # one client
        sent = jsonpickle.decode(server.socketmanager.broadcast.call_args_list[0].args[0])
        assert sent["PAYLOAD"]["items"][0]["file"].endswith(".mp4")
        assert "/seg_" in sent["PAYLOAD"]["items"][0]["file"]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_mosaic.py::TestVideoSegmentPlay -c tests/pytest.ini -q`
Expected: FAIL — PLAY has no `rendering` check; `_broadcast_segment_play` always emits `.png`.

- [ ] **Step 3: Add the extension choice and the in-progress gate**

In `server.py` `_broadcast_segment_play`, change the SEGMENT file line to pick the extension by source type. Replace:

```python
            if me.playmode == PlayMode.SEGMENT and c.measuredPerimeter is not None:
                f = "/media/" + key + "/seg_" + token + "_" + str(i) + ".png"
```

with:

```python
            if me.playmode == PlayMode.SEGMENT and c.measuredPerimeter is not None:
                ext = ".mp4" if isVideoItem(me.file) else ".png"
                f = "/media/" + key + "/seg_" + token + "_" + str(i) + ext
```

In `msg_response`'s PLAY branch, add the `rendering` check ahead of the token check. Replace:

```python
            if has_segment and compute_render_token(display_id) != display.renderedToken:
                response["PAYLOAD"] = {"status": "RENDER_REQUIRED", "displayID": display_id}
```

with:

```python
            if has_segment and display.renderStatus == "rendering":
                response["PAYLOAD"] = {"status": "RENDER_IN_PROGRESS", "displayID": display_id}
            elif has_segment and compute_render_token(display_id) != display.renderedToken:
                response["PAYLOAD"] = {"status": "RENDER_REQUIRED", "displayID": display_id}
```

- [ ] **Step 4: Run to verify pass + full regression**

Run: `python -m pytest tests/unit/test_mosaic.py::TestVideoSegmentPlay -c tests/pytest.ini -q`
Expected: PASS
Then: `python -m pytest tests/unit tests/integration -c tests/pytest.ini -q`
Expected: all pass (the image SEGMENT play test still passes — image sources still yield `.png` URLs; FULL/PAUSE paths unchanged).

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_mosaic.py
git commit -m "feat(mosaic): PLAY emits RENDER_IN_PROGRESS; per-client video uses .mp4 URLs

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Client unmute + ffmpeg dependency docs

**Files:** Modify `index.html`; Modify `requirements.txt`; Modify `CLAUDE.md`.

- [ ] **Step 1: Unmute video in the client**

In `index.html`'s `showItem`, change the video element from muted to unmuted. Replace the line `v.muted = true;` with:

```javascript
			v.muted = false; // every screen plays audio (see video-split spec); needs a gesture to autoplay
```

(ES5, one line. The existing `v.play()` with its swallowed promise rejection stays — audio starts once the device is armed by a gesture, e.g. SSH device prep.)

- [ ] **Step 2: Document the ffmpeg dependency**

In `requirements.txt`, append a comment line at the end:

```
# System dependency (not pip-installable): ffmpeg with libx264 must be on PATH
# (used by the server to render perspective-warped per-screen video for SEGMENT/mosaic playback)
```

In `CLAUDE.md`, under the runtime/commands area, add a line noting the system dependency:

```
- **System dependency:** `ffmpeg` (with libx264) must be on PATH for video split/mosaic rendering (`SEGMENT` `.mp4` items). Image mosaic and all other features work without it.
```

- [ ] **Step 3: Syntax sanity check**

Run: `python -c "import ast; ast.parse(open('server.py').read()); print('server ok')"` (server unchanged here, just a guard) and visually confirm the `index.html` edit is `v.muted = false;` with no other change.
Expected: `server ok`; the only `index.html` diff is the one line.

- [ ] **Step 4: Commit**

```bash
git add index.html requirements.txt CLAUDE.md
git commit -m "feat(mosaic): play video unmuted; document ffmpeg server dependency

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Verification — opt-in real ffmpeg + Playwright gate

**Files:** Test `tests/unit/test_mosaic.py` (opt-in integration test); plus a controller-run Playwright check.

- [ ] **Step 1: Add the opt-in real-ffmpeg integration test**

Append to `tests/unit/test_mosaic.py`:

```python
import shutil
import pytest


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
class TestFfmpegIntegration:
    async def test_real_render_produces_nonempty_mp4(self, mock_settings, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        src_dir = tmp_path / "media" / "server" / "videos"; src_dir.mkdir(parents=True)
        src = str(src_dir / "clip.mp4")
        # generate a 1s test clip with ffmpeg itself
        import subprocess
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=1",
                        "-pix_fmt", "yuv420p", src], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "v"; me.file = "/media/server/clip.mp4"
        me.duration = 1000; me.playmode = server.PlayMode.SEGMENT
        disp.mediaElements = [me]; disp.boundingBox = [0, 0, 320, 240]
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 160; c.deviceHeight = 120
        c.measuredPerimeter = np.array([[[0, 0]], [[160, 0]], [[160, 240]], [[0, 240]]])
        mock_settings.clients = {"c1": c}

        result = await server.render_group_async("Default")

        assert result["status"] == "ready"
        out = tmp_path / "media" / "c1" / "videos" / ("seg_" + disp.renderedToken + "_0.mp4")
        assert out.exists() and out.stat().st_size > 0
```

- [ ] **Step 2: Run the test (or confirm skip)**

Run: `python -m pytest tests/unit/test_mosaic.py::TestFfmpegIntegration -c tests/pytest.ini -q`
Expected: PASS if ffmpeg is installed (this also validates the `perspective` corner-order/`sense` for real); SKIP otherwise. **If it errors rather than skips/passes, the `perspective` filter args need adjusting — fix `build_ffmpeg_perspective_cmd` until a real warped clip is produced.**

- [ ] **Step 3: Commit the integration test**

```bash
git add tests/unit/test_mosaic.py
git commit -m "test(mosaic): opt-in real-ffmpeg render integration test

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 4: Controller Playwright gate check**

Start the server (background; remove stale `settings.dat` first), open `http://localhost:3000/`, wait for register. Then over the socket: `SETPLAYLIST` a SEGMENT `.mp4` item, `PLAY` → confirm playback does not start (gate returns `RENDER_REQUIRED`, since uncalibrated/unrendered). Also confirm `showItem` builds an unmuted element by injecting a video item and reading `document.querySelector('#canvas video').muted === false`. Shut down server, close browser, remove `.playwright-mcp/` and `settings.dat`. No commit.

---

## Self-review notes

- **Spec coverage:** ffmpeg `perspective`+`scale` H.264 baseline with audio (Task 1 builder; Task 5 real validation); source-quad math + dims (Task 1); unified async `render_group_async` with `renderStatus`/`renderedToken` + `RENDER_STATUS` broadcast + async-scheduling RENDER handler with validation (Task 2); PLAY `RENDER_IN_PROGRESS`/`RENDER_REQUIRED`/ready + per-client `.mp4` URLs (Task 3); client unmute + ffmpeg dependency docs (Task 4); audio kept (`-an` absent, `-c:a aac`) asserted (Task 1) and played unmuted (Task 4). Image SEGMENT/FULL/PAUSE paths preserved (Task 3 regression run). Deferred items (SCRIPT, editor, scheduling, per-screen audio, GC) correctly absent.
- **Placeholder scan:** none — concrete code/commands throughout.
- **Type/name consistency:** `quad_to_source_points`, `build_ffmpeg_perspective_cmd`, `get_video_dimensions`, `render_group_async`, `_broadcast_render_status`, `Display.renderStatus`, `RENDER_STATUS`, `RENDER_IN_PROGRESS` used consistently. Warped video URL `/media/<key>/seg_<token>_<i>.mp4` matches the write path (`media/<key>/videos/…`) and `media_handler`'s `.mp4` → `videos/` routing. The old synchronous `render_group` is removed and its tests replaced (Task 2), so no dangling references remain.
