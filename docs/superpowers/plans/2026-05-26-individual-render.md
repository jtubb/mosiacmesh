# INDIVIDUAL Render Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `PlayMode.INDIVIDUAL` rendering — the whole media on every screen, rectified to that screen's own measured quad (perspective + rotation), aspect-fit with a `backgroundColor` letterbox, reusing the SEGMENT warp/ffmpeg machinery.

**Architecture:** A new `_is_renderable(me)` predicate (SEGMENT ∪ INDIVIDUAL) replaces the scattered `== SEGMENT` gate checks. `render_group_async` branches per item: SEGMENT keeps the group-bbox region crop; INDIVIDUAL uses the screen's *own* `boundingRect(quad)` + an aspect-fit letterbox onto a `backgroundColor` canvas (images via OpenCV, video via ffmpeg `pad`+`perspective`+`scale`). Per-client PLAY hands each calibrated client its `ind_<token>_<i>` file. The editor's INDIVIDUAL picker option is enabled. `index.html` is untouched.

**Tech Stack:** Python 3 / aiohttp / OpenCV (`cv2`) / numpy / ffmpeg (libx264); pytest (`tests/pytest.ini`, `asyncio_mode=auto`).

---

## Conventions for every task

- **Run tests:** `python -m pytest <path> -c tests/pytest.ini -v` (a bare `pytest` from root misconfigures markers/asyncio). Full unit suite: `python pytest_runner.py --unit`.
- **Branch:** stay on `feature/discovery-completion-legacy-compat` (NOT main).
- Server tests go in `tests/unit/test_mosaic.py` (existing mosaic suite; matches the fixtures/patterns there: `mock_settings` fixture, `np`/`cv` imported, `MagicMock`, `jsonpickle`). Async tests are bare `async def` (the suite runs under `asyncio_mode=auto`).
- Commit messages end with the trailer `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`.
- **Regression rule:** after each task, the FULL existing `tests/unit/test_mosaic.py` and `tests/unit/test_playlists.py` must stay green — these tasks refactor shared code.

### Reference: current code being modified (server.py)

- `render_group_async` (~325): `seg_items = [(i, me) ... if me.playmode == PlayMode.SEGMENT]`; per item, video branch loops clients building an ffmpeg cmd via `build_ffmpeg_perspective_cmd`, image branch loops clients calling `warp_image_for_screen`, writing `seg_<token>_<i>.{mp4,png}` to `media/<key>/{videos,images}`.
- `_broadcast_segment_play(display_id, display)` (~379): per client, per item — if `me.playmode == PlayMode.SEGMENT and c.measuredPerimeter is not None` → `f = "/media/" + key + "/seg_" + token + "_" + str(i) + ext`, else `f = me.file`; builds `_media_item_payload(me)` then overrides `item["file"] = f`.
- PLAY handler (~822): `has_segment = any(me.playmode == PlayMode.SEGMENT ...)`; gates `RENDER_IN_PROGRESS`/`RENDER_REQUIRED`; `if has_segment: _broadcast_segment_play(...)` else group broadcast.
- RENDER handler (~863): validation chain includes `elif not any(me.playmode == PlayMode.SEGMENT ...): {"status":"ERROR","error":"no SEGMENT items"}`.
- ASSIGN_PLAYLIST (~926): `has_segment = any(me.playmode == PlayMode.SEGMENT ...)` → `NOT_CALIBRATED`/`RENDER_REQUIRED`.
- `warp_image_for_screen(source_img, bbox, screen_quad, out_w, out_h)` (~264), `quad_to_source_points(bbox, screen_quad, src_w, src_h)` (~405), `build_ffmpeg_perspective_cmd(...)` (~413), `order_points` (~244).

---

## Task 1: `_is_renderable` predicate + gate/routing swap

**Files:**
- Modify: `server.py` — add `_is_renderable`; PLAY gate, RENDER validation, ASSIGN classification; rename `_broadcast_segment_play` → `_broadcast_per_client_play` and extend its file selection.
- Test: `tests/unit/test_mosaic.py`

This task makes INDIVIDUAL items *gate* and *route* correctly (render output comes in Tasks 2–3). SEGMENT behavior is unchanged.

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_mosaic.py`:

```python
class TestIsRenderable:
    def test_predicate(self):
        for pm, exp in [(server.PlayMode.SEGMENT, True), (server.PlayMode.INDIVIDUAL, True),
                        (server.PlayMode.FULL, False), (server.PlayMode.SCRIPT, False),
                        (server.PlayMode.DEFAULT, False)]:
            me = server.MediaElement(); me.playmode = pm
            assert server._is_renderable(me) is exp

    async def test_render_accepts_individual_only_playlist(self, monkeypatch):
        # RENDER must not reject an INDIVIDUAL-only playlist with "no renderable items".
        # async test (running loop) + stub ensure_future so we don't actually render.
        scheduled = []
        def _capture(coro):
            scheduled.append(coro); coro.close(); return None   # close() avoids un-awaited warning
        monkeypatch.setattr(server.asyncio, "ensure_future", _capture)
        ms = server.Settings()
        ms.displays = {"Default": server.Display()}
        server.settings = ms
        server.socketmanager = MagicMock()
        disp = ms.displays["Default"]
        me = server.MediaElement(); me.file = "/media/server/x.jpg"; me.duration = 1000
        me.playmode = server.PlayMode.INDIVIDUAL
        disp.mediaElements = [me]; disp.boundingBox = [0, 0, 100, 100]
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 80; c.deviceHeight = 60
        c.measuredPerimeter = np.array([[[0, 0]], [[50, 0]], [[50, 100]], [[0, 100]]])
        ms.clients = {"c1": c}
        sess = MagicMock(); sess.id = "s"; sess.request = MagicMock()
        sess.request.remote = "127.0.0.1"; sess.request.headers = {"User-Agent": "T"}
        ret = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "RENDER", "PAYLOAD": {"displayID": "Default"}}, sess))
        assert ret["PAYLOAD"]["status"] == "rendering"   # accepted, not ERROR
        assert len(scheduled) == 1                       # render was scheduled

    def test_play_individual_stale_requires_render(self):
        ms = server.Settings(); ms.displays = {"Default": server.Display()}
        server.settings = ms; server.socketmanager = MagicMock()
        disp = ms.displays["Default"]
        me = server.MediaElement(); me.id = "a"; me.file = "/media/server/x.jpg"
        me.duration = 1000; me.playmode = server.PlayMode.INDIVIDUAL
        disp.mediaElements = [me]; disp.boundingBox = [0, 0, 100, 100]
        disp.action = server.PlayState.STOP
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 80; c.deviceHeight = 60
        c.measuredPerimeter = np.array([[[0, 0]], [[50, 0]], [[50, 100]], [[0, 100]]])
        ms.clients = {"c1": c}
        sess = MagicMock(); sess.id = "s"; sess.request = MagicMock()
        sess.request.remote = "127.0.0.1"; sess.request.headers = {"User-Agent": "T"}
        ret = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "PLAY", "PAYLOAD": {"displayID": "Default"}}, sess))
        assert ret["PAYLOAD"]["status"] == "RENDER_REQUIRED"

    def test_per_client_play_routes_individual_to_ind_file(self):
        ms = server.Settings(); ms.displays = {"Default": server.Display()}
        server.settings = ms; server.socketmanager = MagicMock()
        disp = ms.displays["Default"]
        me = server.MediaElement(); me.id = "a"; me.file = "/media/server/x.jpg"
        me.duration = 1000; me.playmode = server.PlayMode.INDIVIDUAL
        disp.mediaElements = [me]; disp.boundingBox = [0, 0, 100, 100]; disp.loop = True
        disp.action = server.PlayState.STOP
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 80; c.deviceHeight = 60
        c.measuredPerimeter = np.array([[[0, 0]], [[50, 0]], [[50, 100]], [[0, 100]]])
        ms.clients = {"c1": c}
        disp.renderedToken = server.compute_render_token("Default")   # mark rendered
        sess = MagicMock(); sess.id = "s"; sess.request = MagicMock()
        sess.request.remote = "127.0.0.1"; sess.request.headers = {"User-Agent": "T"}
        server.msg_response({"SRC": "a", "DEST": "SRV", "REQUEST": "PLAY",
                             "PAYLOAD": {"displayID": "Default"}}, sess)
        sent = jsonpickle.decode(server.socketmanager.broadcast.call_args_list[0].args[0])
        assert "/ind_" in sent["PAYLOAD"]["items"][0]["file"]
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/test_mosaic.py::TestIsRenderable -c tests/pytest.ini -v` (AttributeError `_is_renderable` / wrong statuses).

- [ ] **Step 3: Implement in `server.py`**

Add the predicate just above `render_group_async`:
```python
def _is_renderable(me):
    """SEGMENT and INDIVIDUAL items require a per-screen server render."""
    return me.playmode in (PlayMode.SEGMENT, PlayMode.INDIVIDUAL)
```

Rename `_broadcast_segment_play` to `_broadcast_per_client_play` and change its file selection to pick the prefix by mode:
```python
def _broadcast_per_client_play(display_id, display):
    """Send each client its own PLAY: renderable items (SEGMENT/INDIVIDUAL) use
    that client's warped file when calibrated, otherwise the plain source."""
    token = display.renderedToken
    for key, c in _group_clients(display_id):
        items = []
        for i, me in enumerate(display.mediaElements):
            if _is_renderable(me) and c.measuredPerimeter is not None:
                prefix = "ind_" if me.playmode == PlayMode.INDIVIDUAL else "seg_"
                ext = ".mp4" if isVideoItem(me.file) else ".png"
                f = "/media/" + key + "/" + prefix + token + "_" + str(i) + ext
            else:
                f = me.file  # FULL item, or uncalibrated fallback to full source
            item = _media_item_payload(me)
            item["file"] = f
            items.append(item)
        broadcast_to_client(key, {"REQUEST": "PLAY",
            "PAYLOAD": {"startEpoch": display.playStartEpoch, "items": items, "loop": display.loop}})
```

In the PLAY handler, replace the `has_segment` block:
```python
            has_renderable = any(_is_renderable(me) for me in display.mediaElements)
            if has_renderable and display.renderStatus == "rendering":
                response["PAYLOAD"] = {"status": "RENDER_IN_PROGRESS", "displayID": display_id}
            elif has_renderable and compute_render_token(display_id) != display.renderedToken:
                response["PAYLOAD"] = {"status": "RENDER_REQUIRED", "displayID": display_id}
            else:
                display.playStartEpoch = resume_epoch
                display.action = PlayState.PLAY
                if has_renderable:
                    _broadcast_per_client_play(display_id, display)
                else:
                    items = [_media_item_payload(me) for me in display.mediaElements]
                    broadcast_to_display_group(display_id, {
                        "REQUEST": "PLAY",
                        "PAYLOAD": {"startEpoch": display.playStartEpoch,
                                    "items": items, "loop": display.loop}})
                response["PAYLOAD"] = "SUCCESS"
```

In the RENDER handler, change the SEGMENT-items check:
```python
        elif not any(_is_renderable(me) for me in display.mediaElements):
            response["PAYLOAD"] = {"status": "ERROR", "error": "no renderable items"}
```

In the ASSIGN_PLAYLIST handler, change the classification predicate:
```python
            has_renderable = any(_is_renderable(me) for me in display.mediaElements)
            if has_renderable and not display.boundingBox:
                status = "NOT_CALIBRATED"
            elif has_renderable and compute_render_token(display_id) != display.renderedToken:
                status = "RENDER_REQUIRED"
            else:
                status = "ok"
```

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/unit/test_mosaic.py tests/unit/test_playlists.py -c tests/pytest.ini` (new TestIsRenderable passes; all existing SEGMENT tests — `test_play_rendered_sends_per_client_warped` etc. — still pass because SEGMENT routes to `seg_` unchanged).

- [ ] **Step 5: Commit**
```bash
git add server.py tests/unit/test_mosaic.py
git commit -m "feat(individual): _is_renderable predicate; gate + per-client routing for INDIVIDUAL"
```

---

## Task 2: INDIVIDUAL image render (letterbox + per-screen warp)

**Files:**
- Modify: `server.py` — add `_hex_to_bgr` + `letterbox_to_aspect`; branch the image path of `render_group_async`.
- Test: `tests/unit/test_mosaic.py`

`render_group_async`'s item filter changes to `_is_renderable`. The image branch dispatches by mode: SEGMENT = existing group-bbox warp; INDIVIDUAL = per-screen own-bbox letterbox warp → `ind_<token>_<i>.png`. The video branch still handles SEGMENT; an INDIVIDUAL **video** raises a clear `RuntimeError` until Task 3 (unreachable today — editor still disables INDIVIDUAL until Task 4).

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_mosaic.py`:

```python
class TestLetterbox:
    def test_letterbox_centers_and_pads_with_bg(self):
        img = np.zeros((50, 100, 3), dtype=np.uint8); img[:] = (0, 0, 255)  # red, 2:1
        out = server.letterbox_to_aspect(img, 100, 100, (255, 0, 0))        # target 1:1, bg blue
        assert out.shape == (100, 100, 3)
        assert tuple(int(v) for v in out[50, 50]) == (0, 0, 255)            # center is the red media
        assert tuple(int(v) for v in out[5, 50]) == (255, 0, 0)            # top margin is bg blue
        assert tuple(int(v) for v in out[95, 50]) == (255, 0, 0)           # bottom margin is bg blue

    def test_hex_to_bgr(self):
        assert server._hex_to_bgr("#ff0000") == (0, 0, 255)   # red hex -> BGR
        assert server._hex_to_bgr("#00ff00") == (0, 255, 0)
        assert server._hex_to_bgr(None) == (0, 0, 0)


class TestIndividualImageRender:
    async def test_individual_image_writes_ind_file_and_rectifies(self, mock_settings, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        src_dir = tmp_path / "media" / "server" / "images"; src_dir.mkdir(parents=True)
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        img[:, :50] = (0, 0, 255)   # left red, right black
        cv.imwrite(str(src_dir / "x.jpg"), img)
        server.settings = mock_settings; server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "a"; me.file = "/media/server/x.jpg"
        me.duration = 1000; me.playmode = server.PlayMode.INDIVIDUAL; me.backgroundColor = "#000000"
        disp.mediaElements = [me]; disp.boundingBox = [0, 0, 100, 100]
        # square-ish full-bbox screen so the whole image lands ~upright
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 100; c.deviceHeight = 100
        c.measuredPerimeter = np.array([[[0, 0]], [[100, 0]], [[100, 100]], [[0, 100]]])
        mock_settings.clients = {"c1": c}

        result = await server.render_group_async("Default")

        assert result["status"] == "ready"
        out_file = tmp_path / "media" / "c1" / "images" / ("ind_" + disp.renderedToken + "_0.png")
        assert out_file.exists()
        out = cv.imread(str(out_file))
        assert out.shape == (100, 100, 3)
        assert out[50, 25][2] > 150 and out[50, 25][0] < 80   # left half still red
        assert out[50, 75][2] < 80                            # right half not red

    async def test_individual_image_letterbox_uses_background_color(self, mock_settings, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        src_dir = tmp_path / "media" / "server" / "images"; src_dir.mkdir(parents=True)
        img = np.zeros((40, 100, 3), dtype=np.uint8); img[:] = (0, 0, 255)  # wide 2.5:1 red
        cv.imwrite(str(src_dir / "w.jpg"), img)
        server.settings = mock_settings; server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "a"; me.file = "/media/server/w.jpg"
        me.duration = 1000; me.playmode = server.PlayMode.INDIVIDUAL; me.backgroundColor = "#00ff00"  # green
        disp.mediaElements = [me]; disp.boundingBox = [0, 0, 100, 100]
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 100; c.deviceHeight = 100
        c.measuredPerimeter = np.array([[[0, 0]], [[100, 0]], [[100, 100]], [[0, 100]]])
        mock_settings.clients = {"c1": c}

        await server.render_group_async("Default")
        out = cv.imread(str(tmp_path / "media" / "c1" / "images" / ("ind_" + disp.renderedToken + "_0.png")))
        # top/bottom margins should be green bg (wide media on square screen letterboxes vertically)
        assert out[5, 50][1] > 150 and out[5, 50][2] < 80    # green, not red
        assert out[95, 50][1] > 150 and out[95, 50][2] < 80
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/test_mosaic.py::TestLetterbox tests/unit/test_mosaic.py::TestIndividualImageRender -c tests/pytest.ini -v`.

- [ ] **Step 3: Implement in `server.py`**

Add helpers near `warp_image_for_screen`:
```python
def _hex_to_bgr(hexstr):
    """'#rrggbb' -> OpenCV (B, G, R) tuple; falls back to black."""
    h = (hexstr or "#000000").lstrip("#")
    if len(h) != 6:
        h = "000000"
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


def letterbox_to_aspect(img, target_w, target_h, bg_bgr):
    """Scale img to fit within target_w x target_h preserving aspect, centered
    on a solid bg_bgr canvas of exactly that size."""
    target_w = max(1, int(target_w)); target_h = max(1, int(target_h))
    h, w = img.shape[:2]
    scale = min(target_w / float(w), target_h / float(h))
    nw = max(1, int(round(w * scale))); nh = max(1, int(round(h * scale)))
    resized = cv.resize(img, (nw, nh))
    canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
    canvas[:] = bg_bgr
    x = (target_w - nw) // 2; y = (target_h - nh) // 2
    canvas[y:y + nh, x:x + nw] = resized
    return canvas
```

In `render_group_async`, change the item filter and the image branch. Replace `seg_items = [(i, me) for i, me in enumerate(display.mediaElements) if me.playmode == PlayMode.SEGMENT]` with:
```python
        seg_items = [(i, me) for i, me in enumerate(display.mediaElements) if _is_renderable(me)]
```
In the **image** branch (the `else:` after `if isVideoItem(me.file):`), replace the per-client loop body so it dispatches by mode:
```python
            else:
                img = cv.imread(src_path) if src_path else None
                if img is None:
                    raise RuntimeError("cannot read source image: " + str(me.file))
                for key, c in clients:
                    if me.playmode == PlayMode.INDIVIDUAL:
                        quad_pts = np.array(c.measuredPerimeter, dtype="int32").reshape(-1, 2)
                        bx, by, bw, bh = [int(v) for v in cv.boundingRect(quad_pts)]
                        bg = _hex_to_bgr(getattr(me, "backgroundColor", "#000000"))
                        canvas = letterbox_to_aspect(img, bw, bh, bg)
                        warped = warp_image_for_screen(canvas, [bx, by, bw, bh], c.measuredPerimeter,
                                                       int(c.deviceWidth) or 1, int(c.deviceHeight) or 1)
                        out_dir = os.path.join("media", key, "images")
                        Path(out_dir).mkdir(parents=True, exist_ok=True)
                        cv.imwrite(os.path.join(out_dir, "ind_" + token + "_" + str(i) + ".png"), warped)
                    else:
                        warped = warp_image_for_screen(img, display.boundingBox, c.measuredPerimeter,
                                                       int(c.deviceWidth) or 1, int(c.deviceHeight) or 1)
                        out_dir = os.path.join("media", key, "images")
                        Path(out_dir).mkdir(parents=True, exist_ok=True)
                        cv.imwrite(os.path.join(out_dir, "seg_" + token + "_" + str(i) + ".png"), warped)
```
In the **video** branch, guard the not-yet-implemented INDIVIDUAL case at the top of that branch (right after `if isVideoItem(me.file):`):
```python
                if me.playmode == PlayMode.INDIVIDUAL:
                    raise RuntimeError("INDIVIDUAL video render not implemented yet")
```
(The existing SEGMENT video code follows unchanged. Task 3 replaces this guard.)

- [ ] **Step 4: Run, expect PASS** — the two new classes pass; then `python -m pytest tests/unit/test_mosaic.py tests/unit/test_playlists.py -c tests/pytest.ini` all green (SEGMENT image/video render tests unaffected).

- [ ] **Step 5: Commit**
```bash
git add server.py tests/unit/test_mosaic.py
git commit -m "feat(individual): per-screen image render with aspect-fit backgroundColor letterbox"
```

---

## Task 3: INDIVIDUAL video render (ffmpeg pad + perspective)

**Files:**
- Modify: `server.py` — add `build_ffmpeg_individual_cmd`; replace the Task-2 video guard with the real INDIVIDUAL video path.
- Test: `tests/unit/test_mosaic.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_mosaic.py`:

```python
class TestIndividualFfmpeg:
    def test_build_individual_cmd_has_pad_perspective_scale_and_bg(self):
        pts = [[0.0, 0.0], [200.0, 0.0], [200.0, 100.0], [0.0, 100.0]]  # TL,TR,BR,BL
        cmd = server.build_ffmpeg_individual_cmd("in.mp4", "out.mp4", pts,
                                                 800, 600, 200, 125, 0, 12, "#00ff00")
        assert cmd[0] == "ffmpeg" and cmd[-1] == "out.mp4"
        vf = cmd[cmd.index("-vf") + 1]
        assert vf.startswith("pad=200:125:0:12:color=0x00ff00")
        assert "perspective=" in vf and "sense=source" in vf
        assert "scale=800:600" in vf
        assert "libx264" in cmd and "-c:a" in cmd and "aac" in cmd and "-an" not in cmd

    async def test_individual_video_invokes_ffmpeg_with_pad(self, mock_settings, monkeypatch):
        server.settings = mock_settings; server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "v"; me.file = "/media/server/clip.mp4"
        me.duration = 5000; me.playmode = server.PlayMode.INDIVIDUAL; me.backgroundColor = "#000000"
        disp.mediaElements = [me]; disp.boundingBox = [0, 0, 100, 100]
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 80; c.deviceHeight = 60
        c.measuredPerimeter = np.array([[[0, 0]], [[100, 0]], [[100, 100]], [[0, 100]]])
        mock_settings.clients = {"c1": c}
        monkeypatch.setattr(server, "get_video_dimensions", lambda p: (200, 100))
        calls = []
        class _Proc:
            returncode = 0
            async def communicate(self): return (b"", b"")
        async def _fake_exec(*args, **kwargs):
            calls.append(args); return _Proc()
        monkeypatch.setattr(server.asyncio, "create_subprocess_exec", _fake_exec)

        result = await server.render_group_async("Default")
        assert result["status"] == "ready"
        assert len(calls) == 1 and calls[0][0] == "ffmpeg"
        vf = list(calls[0])[list(calls[0]).index("-vf") + 1]
        assert vf.startswith("pad=")           # INDIVIDUAL pads before perspective
        # output path is the ind_ prefix
        assert any("ind_" in a for a in calls[0])
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/test_mosaic.py::TestIndividualFfmpeg -c tests/pytest.ini -v` (`build_ffmpeg_individual_cmd` missing / RuntimeError from the Task-2 guard).

- [ ] **Step 3: Implement in `server.py`**

Add the command builder near `build_ffmpeg_perspective_cmd`:
```python
def build_ffmpeg_individual_cmd(src_path, out_path, src_points, out_w, out_h,
                                pad_w, pad_h, pad_x, pad_y, bg_hex):
    """ffmpeg args for INDIVIDUAL: pad the source to the screen bbox aspect with
    backgroundColor, perspective-warp the whole padded frame to the screen quad,
    scale to the device resolution. src_points is [TL, TR, BR, BL]."""
    tl, tr, br, bl = src_points
    def n(v):
        return str(int(round(v)))
    hexcol = "0x" + (bg_hex or "#000000").lstrip("#")
    pad = ("pad=" + str(int(pad_w)) + ":" + str(int(pad_h)) + ":" +
           str(int(pad_x)) + ":" + str(int(pad_y)) + ":color=" + hexcol)
    persp = ("perspective=" + n(tl[0]) + ":" + n(tl[1]) + ":" + n(tr[0]) + ":" + n(tr[1]) +
             ":" + n(bl[0]) + ":" + n(bl[1]) + ":" + n(br[0]) + ":" + n(br[1]) + ":sense=source")
    vf = pad + "," + persp + ",scale=" + str(out_w) + ":" + str(out_h)
    return ["ffmpeg", "-y", "-i", src_path,
            "-vf", vf,
            "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-preset", "veryfast", "-movflags", "+faststart", out_path]
```

In `render_group_async`, **replace** the Task-2 video guard
`if me.playmode == PlayMode.INDIVIDUAL: raise RuntimeError("INDIVIDUAL video render not implemented yet")`
with the real per-client INDIVIDUAL video path (the existing SEGMENT video loop stays in the `else`). The video branch becomes:
```python
            if isVideoItem(me.file):
                dims = get_video_dimensions(src_path) if src_path else None
                if not dims:
                    raise RuntimeError("cannot read source video: " + str(me.file))
                sw, sh = dims
                for key, c in clients:
                    out_dir = os.path.join("media", key, "videos")
                    Path(out_dir).mkdir(parents=True, exist_ok=True)
                    if me.playmode == PlayMode.INDIVIDUAL:
                        quad_pts = np.array(c.measuredPerimeter, dtype="int32").reshape(-1, 2)
                        bx, by, bw, bh = [int(v) for v in cv.boundingRect(quad_pts)]
                        # pad source to the screen bbox aspect, media centered
                        if sw * bh >= sh * bw:                 # source wider/equal -> pad height
                            pad_w = sw; pad_h = int(round(sw * bh / float(bw)))
                        else:                                  # source taller -> pad width
                            pad_h = sh; pad_w = int(round(sh * bw / float(bh)))
                        pad_x = (pad_w - sw) // 2; pad_y = (pad_h - sh) // 2
                        pts = quad_to_source_points([bx, by, bw, bh], c.measuredPerimeter, pad_w, pad_h)
                        out_path = os.path.join(out_dir, "ind_" + token + "_" + str(i) + ".mp4")
                        cmd = build_ffmpeg_individual_cmd(src_path, out_path, pts,
                                                          int(c.deviceWidth) or 1, int(c.deviceHeight) or 1,
                                                          pad_w, pad_h, pad_x, pad_y,
                                                          getattr(me, "backgroundColor", "#000000"))
                    else:
                        pts = quad_to_source_points(display.boundingBox, c.measuredPerimeter, sw, sh)
                        out_path = os.path.join(out_dir, "seg_" + token + "_" + str(i) + ".mp4")
                        cmd = build_ffmpeg_perspective_cmd(src_path, out_path, pts,
                                                           int(c.deviceWidth) or 1, int(c.deviceHeight) or 1)
                    proc = await asyncio.create_subprocess_exec(
                        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    await proc.communicate()
                    if proc.returncode != 0:
                        raise RuntimeError("ffmpeg failed (" + str(proc.returncode) + ")")
```

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/unit/test_mosaic.py::TestIndividualFfmpeg -c tests/pytest.ini -v`; then full `python pytest_runner.py --unit` all green (SEGMENT video render test `test_render_video_invokes_ffmpeg_per_screen` still passes — its item is SEGMENT, takes the `else`).

- [ ] **Step 5 (optional, opt-in): real-ffmpeg integration test.** Append, mirroring `TestFfmpegIntegration`'s opt-in gate (it skips unless an env flag is set — check the existing class for the exact `pytest.mark.skipif`/env name and reuse it verbatim):

```python
class TestIndividualFfmpegIntegration:
    async def test_real_individual_render_produces_nonempty_mp4(self, mock_settings, tmp_path, monkeypatch):
        # reuse the SAME opt-in skip guard as TestFfmpegIntegration (e.g. env MOSAIC_FFMPEG_TEST)
        import shutil, os as _os
        if not _os.environ.get("MOSAIC_FFMPEG_TEST"):
            import pytest; pytest.skip("opt-in: set MOSAIC_FFMPEG_TEST=1 and have ffmpeg on PATH")
        monkeypatch.chdir(tmp_path)
        vid_dir = tmp_path / "media" / "server" / "videos"; vid_dir.mkdir(parents=True)
        src = str(vid_dir / "clip.mp4")
        # synthesize a 1s test video
        import subprocess
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=200x100:duration=1",
                        "-pix_fmt", "yuv420p", src], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        server.settings = mock_settings; server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "v"; me.file = "/media/server/clip.mp4"
        me.duration = 1000; me.playmode = server.PlayMode.INDIVIDUAL; me.backgroundColor = "#000000"
        disp.mediaElements = [me]; disp.boundingBox = [0, 0, 100, 100]
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 80; c.deviceHeight = 60
        c.measuredPerimeter = np.array([[[0, 0]], [[100, 0]], [[100, 100]], [[0, 100]]])
        mock_settings.clients = {"c1": c}
        result = await server.render_group_async("Default")
        assert result["status"] == "ready"
        out = tmp_path / "media" / "c1" / "videos" / ("ind_" + disp.renderedToken + "_0.mp4")
        assert out.exists() and out.stat().st_size > 0
```
(If the existing integration test uses a different opt-in mechanism, match it exactly and adjust the skip line.)

- [ ] **Step 6: Commit**
```bash
git add server.py tests/unit/test_mosaic.py
git commit -m "feat(individual): per-screen video render via ffmpeg pad+perspective"
```

---

## Task 4: Enable INDIVIDUAL in the editor picker

**Files:**
- Modify: `admin.html` — the inspector playmode `<select>` in `plRenderInspector`.
- Verify: Playwright (controller-run).

`admin.html` is a desktop console (modern JS fine). The inspector currently builds the playmode select with INDIVIDUAL `disabled` and labeled `"INDIVIDUAL — soon"`.

- [ ] **Step 1: Implement** — in `plRenderInspector`, the `$.each([... ])` that builds the playmode options. Change the INDIVIDUAL entry so it is enabled and relabeled. Find:
```javascript
  $.each([["FULL","FULL"],["SEGMENT","SEGMENT (mesh)"],
          ["INDIVIDUAL","INDIVIDUAL — soon"],["SCRIPT","SCRIPT"]], function(_, o){
    var $o = $('<option>').val(o[0]).text(o[1]);
    if (o[0] === "INDIVIDUAL") $o.prop('disabled', true);
    $pm.append($o);
  });
```
Replace with:
```javascript
  $.each([["FULL","FULL"],["SEGMENT","SEGMENT (mesh)"],
          ["INDIVIDUAL","INDIVIDUAL"],["SCRIPT","SCRIPT"]], function(_, o){
    $pm.append($('<option>').val(o[0]).text(o[1]));
  });
```

- [ ] **Step 2: Verify (controller, Playwright)** — start `python server.py -p 3000` (background), navigate to `http://localhost:3000/admin.html`, then:
```javascript
() => {
  plNew(); plAddItem('/media/server/images/x.jpg', false);
  plEditor.selected = 0; plRenderInspector();
  var $pm = $('#plInspectorHost select').first();
  var indDisabled = $pm.find('option[value=INDIVIDUAL]').prop('disabled');
  $pm.val('INDIVIDUAL').trigger('change');
  return { individualEnabled: indDisabled === false, itemMode: plEditor.items[0].playmode };
}
```
Expected: `{individualEnabled: true, itemMode: "INDIVIDUAL"}`.

- [ ] **Step 3: Commit**
```bash
git add admin.html
git commit -m "feat(individual): enable INDIVIDUAL option in the editor playmode picker"
```

---

## Final verification (after all tasks)

- [ ] `python pytest_runner.py --unit` → all green (no regressions across mosaic/playlist/playback suites).
- [ ] Playwright: the editor exposes INDIVIDUAL; selecting it writes `playmode: "INDIVIDUAL"`. (Full physical-wall verification needs calibrated hardware — out of scope here; the geometry is unit-tested via the sentinel-pixel + letterbox tests.)
- [ ] Push branch and update PR #1.

## Notes for the implementer

- **DRY:** `_is_renderable` is the single mode gate — do not re-inline `== SEGMENT` / `== INDIVIDUAL` at the call sites changed in Task 1.
- **YAGNI:** always aspect-FIT with letterbox (no fill option); effects remain fields-only.
- **No ES5 concern** anywhere here — `index.html` is untouched; `admin.html` is the desktop console.
- The `bbox` for INDIVIDUAL is each screen's OWN `cv.boundingRect(quad)`, NOT the group `display.boundingBox` (that's SEGMENT). This is the crux — don't mix them up.
- If `cv.boundingRect` / `warp_image_for_screen` / `quad_to_source_points` signatures differ from what's shown, STOP and report NEEDS_CONTEXT rather than guessing.
