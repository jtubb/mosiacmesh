"""Unit tests for image split/mosaic (geometry, render, play gating)."""
import os
import sys
from pathlib import Path
import numpy as np
import cv2 as cv
import pytest
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

    def test_quad_to_source_points_preserves_marker_order(self):
        # The quad's stored order carries the panel's orientation (from the
        # marker). A 180°-rotated corner order must NOT be re-sorted geometrically
        # — else a 180°-mounted screen renders upside down.
        bbox = [0, 0, 100, 100]
        upright = [[0, 0], [100, 0], [100, 100], [0, 100]]      # TL,TR,BR,BL
        rotated = [[100, 100], [0, 100], [0, 0], [100, 0]]      # same quad, 180° order
        assert server.quad_to_source_points(bbox, upright, 200, 200)[0] == [0.0, 0.0]
        # first source point follows the FIRST given corner (200,200), not a
        # geometric top-left (which would be [0,0] if it re-sorted).
        assert server.quad_to_source_points(bbox, rotated, 200, 200)[0] == [200.0, 200.0]


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

    def test_token_changes_with_background_color(self, mock_settings):
        server.settings = mock_settings
        disp, c = self._seg_group(mock_settings)
        t1 = server.compute_render_token("Default")
        disp.mediaElements[0].backgroundColor = "#123456"
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

    async def test_render_uses_calibrated_device_res_even(self, mock_settings, tmp_path, monkeypatch):
        # Output is the CALIBRATED device screen resolution (the stable per-screen
        # target), rounded to even (libx264) — NOT the volatile browser canvas. The
        # canvas below is bogus on purpose to prove it's ignored. Device 80x60 ->
        # 80x60 output (warp PNG shape (h=60, w=80)).
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
        c.canvasWidth = 61; c.canvasHeight = 121   # bogus viewport -> ignored (calibrated wins)
        c.measuredPerimeter = np.array([[[0, 0]], [[50, 0]], [[50, 100]], [[0, 100]]])
        mock_settings.clients = {"c1": c}

        result = await server.render_group_async("Default")

        assert result["status"] == "ready"
        out_png = tmp_path / "media" / "c1" / "images" / ("seg_" + disp.renderedToken + "_0.png")
        assert out_png.exists()
        assert cv.imread(str(out_png)).shape == (60, 80, 3)   # calibrated device res (h=60, w=80)

    def test_render_output_dims_uses_calibrated_device_res(self):
        # Renders to the CALIBRATED device screen resolution, IGNORING the volatile
        # browser canvas/viewport (regression ee4e1c2 keyed output on canvas, which
        # desynced from the device-keyed render_token and letterboxed when the
        # viewport != the screen). Canvas values below are bogus on purpose.
        c = server.Client(); c.deviceWidth = 768; c.deviceHeight = 1024
        c.canvasWidth = 980; c.canvasHeight = 1185           # ignored
        assert server._render_output_dims(c) == (768, 1024)
        c2 = server.Client(); c2.deviceWidth = 2560; c2.deviceHeight = 1440
        c2.canvasWidth = 1278; c2.canvasHeight = 1260        # ignored
        assert server._render_output_dims(c2) == (2560, 1440)
        # odd device dims -> floored to even for libx264
        c3 = server.Client(); c3.deviceWidth = 61; c3.deviceHeight = 121
        assert server._render_output_dims(c3) == (60, 120)

    async def test_render_video_invokes_ffmpeg_per_screen(self, mock_settings, monkeypatch):
        import mosaicmesh.render as _render
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = self._video_group(mock_settings)
        monkeypatch.setattr(_render, "get_video_dimensions", lambda p: (1920, 1080))

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
        import mosaicmesh.render as _render
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = self._video_group(mock_settings)
        monkeypatch.setattr(_render, "get_video_dimensions", lambda p: (1920, 1080))
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
        # Resume-from-pause path: direct per-client PLAY with warped segment URLs
        from mosaicmesh import render as R
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = self._rendered_group(mock_settings)
        disp.renderedToken = server.compute_render_token("Default")  # mark rendered
        disp.action = server.PlayState.PAUSE  # resume path bypasses coordinated prepare
        # Satisfy the render gate: register a named playlist + READY entry so
        # is_playlist_ready returns True and the PLAY handler proceeds.
        pl = server.Playlist(); pl.name = "P"
        pl.items = [{"id": "a", "file": "/media/server/x.jpg", "playmode": "SEGMENT",
                     "duration": 1000, "backgroundColor": "#000000",
                     "startEffect": None, "endEffect": None}]
        mock_settings.playlists["P"] = pl
        tok = server.compute_render_token("Default")
        R._set_render_state(disp, "P", R.RENDER_READY, token=tok)
        disp.currentPlaylistName = "P"
        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "PLAY", "PAYLOAD": {"displayID": "Default"}}
        server.msg_response(msg, self._sess())
        # one PLAY per client (broadcast_to_client) + one PLAYBACK_CHANGED state update
        assert server.socketmanager.broadcast.call_count == 3
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

    def test_play_full_uses_shared_central_asset(self, mock_settings):
        # PT-T5: FULL is now renderable + render-gated. Once a READY registry entry
        # exists, PLAY routes through per-client broadcast (_broadcast_per_client_play)
        # and each client receives the shared central full_<token>_<i> URL — never
        # the raw source file, never a per-client seg_ URL.
        # Using PAUSE resume path (bypasses coordinated _begin_prepare) so the PLAY
        # broadcast fires synchronously and we can inspect the payload.
        from mosaicmesh import render as R
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "a"; me.file = "/media/server/x.jpg"
        me.duration = 1000; me.playmode = server.PlayMode.FULL
        disp.mediaElements = [me]; disp.loop = True
        disp.action = server.PlayState.PAUSE  # resume path: direct per-client PLAY
        c1 = server.Client(); c1.displayID = "Default"
        mock_settings.clients = {"c1": c1}
        # Save the playlist so is_playlist_ready can resolve it.
        pl = server.Playlist(); pl.name = "Full"
        pl.items = [{"id": "a", "file": "/media/server/x.jpg", "playmode": "FULL",
                     "duration": 1000, "backgroundColor": "#000000",
                     "startEffect": None, "endEffect": None}]
        mock_settings.playlists["Full"] = pl
        # Seed a READY registry entry so the render gate passes.
        elements = R._build_media_elements(pl.items)
        tok = R.render_token(elements, "Default")
        R._set_render_state(disp, "Full", R.RENDER_READY, token=tok)
        disp.currentPlaylistName = "Full"
        disp.renderedToken = tok  # sync so _per_client_items resolves correctly
        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "PLAY", "PAYLOAD": {"displayID": "Default"}}
        server.msg_response(msg, self._sess())
        # Per-client broadcast: one PLAY per client (c1) + one PLAYBACK_CHANGED state broadcast
        assert server.socketmanager.broadcast.call_count == 2
        sent = jsonpickle.decode(server.socketmanager.broadcast.call_args_list[0].args[0])
        f = sent["PAYLOAD"]["items"][0]["file"]
        assert "/full_" in f                             # shared central asset
        assert f.endswith(".png")                        # image → .png
        assert f != me.file                              # never raw source


class TestReloadCommand:
    """RELOAD admin command: group-scoped (per-client DEST) or global (DEST=ALL)."""

    def _sess(self):
        s = MagicMock(); s.id = "s"; s.request = MagicMock()
        s.request.remote = "127.0.0.1"; s.request.headers = {"User-Agent": "T"}
        return s

    def test_reload_scoped_to_group_targets_only_its_members(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        a = server.Client(); a.displayID = "Desktop"
        b = server.Client(); b.displayID = "Desktop"
        other = server.Client(); other.displayID = "Mobile"
        mock_settings.clients = {"a": a, "b": b, "other": other}
        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "RELOAD",
               "PAYLOAD": {"displayID": "Desktop"}}
        ret = server.msg_response(msg, self._sess())
        assert jsonpickle.decode(ret)["PAYLOAD"] == "SUCCESS"
        # one broadcast per group member, none for the Mobile client
        assert server.socketmanager.broadcast.call_count == 2
        dests = set()
        for call in server.socketmanager.broadcast.call_args_list:
            sent = jsonpickle.decode(call.args[0])
            assert sent["REQUEST"] == "RELOAD"
            dests.add(sent["DEST"])
        assert dests == {"a", "b"}

    def test_reload_without_group_broadcasts_to_all(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        mock_settings.clients = {"a": server.Client(), "b": server.Client()}
        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "RELOAD", "PAYLOAD": "NONE"}
        ret = server.msg_response(msg, self._sess())
        assert jsonpickle.decode(ret)["PAYLOAD"] == "SUCCESS"
        assert server.socketmanager.broadcast.call_count == 1
        sent = jsonpickle.decode(server.socketmanager.broadcast.call_args_list[0].args[0])
        assert sent["REQUEST"] == "RELOAD" and sent["DEST"] == "ALL"


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
        # Square pixels: without setsar the scale inherits a 16:9 source's display
        # aspect -> the iPad shows the portrait frame letterboxed to 16:9.
        assert vf.endswith("setsar=1")
        assert "libx264" in cmd and "baseline" in cmd and "yuv420p" in cmd
        assert "-c:a" in cmd and "aac" in cmd
        assert "-an" not in cmd  # audio is kept

    def test_build_ffmpeg_cmds_use_ios5_compatible_plain_encode(self):
        # iPad-1 / iOS-5 (UIWebView) rejects VBV (HRD params land in the SPS ->
        # MEDIA_ERR_SRC_NOT_SUPPORTED) and can't decode all-intra's bitrate. Segments
        # must be plain Constrained Baseline (no -maxrate/-bufsize) but DO carry a
        # regular keyframe grid (force_key_frames every KEYFRAME_GRID_SEC + scenecut
        # off) so iOS-5 keyframe-accurate seeks land on a shared grid for sync.
        pts = [[10.0, 20.0], [110.0, 20.0], [110.0, 220.0], [10.0, 220.0]]
        pcmd = server.build_ffmpeg_perspective_cmd("in.mp4", "out.mp4", pts, 800, 600)
        icmd = server.build_ffmpeg_individual_cmd("in.mp4", "out.mp4", pts, 80, 60, 10, 10, 0, 0, "#000000")
        for cmd in (pcmd, icmd):
            assert "baseline" in cmd and "libx264" in cmd
            assert "-maxrate" not in cmd and "-bufsize" not in cmd
            assert "-force_key_frames" in cmd
            j = cmd.index("-force_key_frames")
            assert ("n_forced*" + str(server.KEYFRAME_GRID_SEC)) in cmd[j + 1]
            assert "scenecut=0" in cmd[cmd.index("-x264-params") + 1]

    def test_is_video_item(self):
        assert server.isVideoItem("/media/server/clip.mp4") is True
        assert server.isVideoItem("/media/server/pic.jpg") is False
        assert server.isVideoItem("/media/server/clip.MP4?t=1") is True

    def test_is_video_item_other_formats(self):
        # .mov etc. must be recognized as video (else the renderer cv.imread's
        # a video and crashes with 'cannot read source image')
        assert server.isVideoItem("/media/server/videos/big_buck_bunny.mov") is True
        assert server.isVideoItem("/media/server/clip.MOV") is True
        assert server.isVideoItem("/media/server/clip.webm") is True
        assert server.isVideoItem("/media/server/clip.m4v") is True
        # .mkv (matroska) is a common upload container; ffmpeg transcodes it to
        # an iPad-compatible .mp4 like any other source, so it must classify as
        # video (a real "Video Test" render failed cv.imread-ing a .mkv).
        assert server.isVideoItem("/media/server/videos/movie.mkv") is True
        assert server.isVideoItem("/media/server/clip.MKV") is True
        assert server.isVideoItem("/media/server/clip.avi") is False  # not browser/listed

    def test_get_video_dimensions(self, monkeypatch):
        class FakeCap:
            def get(self, prop):
                import cv2
                return 1920.0 if prop == cv2.CAP_PROP_FRAME_WIDTH else 1080.0
            def release(self): pass
        monkeypatch.setattr(server.cv, "VideoCapture", lambda p: FakeCap())
        assert server.get_video_dimensions("x.mp4") == (1920, 1080)


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
        # Updated for registry-based gating (Task 13): old renderStatus field replaced
        # by Display.renders[name]["state"] == RENDER_RENDERING.
        import jsonpickle
        from mosaicmesh import render as R
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = self._video_group(mock_settings)
        # Set up a playlist name and registry entry in RENDERING state.
        disp.currentPlaylistName = "Vid"
        R._set_render_state(disp, "Vid", R.RENDER_RENDERING, token="tok")
        ret = server.msg_response({"SRC": "a", "DEST": "SRV", "REQUEST": "PLAY",
                                   "PAYLOAD": {"displayID": "Default"}}, self._sess())
        assert jsonpickle.decode(ret)["PAYLOAD"]["status"] == "RENDER_IN_PROGRESS"
        assert server.socketmanager.broadcast.call_count == 0

    def test_play_rendered_video_sends_mp4_urls(self, mock_settings):
        # Resume-from-pause path: direct per-client PLAY with segmented mp4 URLs
        import jsonpickle
        from mosaicmesh import render as R
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = self._video_group(mock_settings)
        disp.renderStatus = "ready"
        disp.renderedToken = server.compute_render_token("Default")
        disp.action = server.PlayState.PAUSE  # resume path bypasses coordinated prepare
        # Satisfy the render gate: register a named playlist + READY entry so
        # is_playlist_ready returns True and the PLAY handler proceeds.
        pl = server.Playlist(); pl.name = "P"
        pl.items = [{"id": "v", "file": "/media/server/clip.mp4", "playmode": "SEGMENT",
                     "duration": 5000, "backgroundColor": "#000000",
                     "startEffect": None, "endEffect": None}]
        mock_settings.playlists["P"] = pl
        tok = server.compute_render_token("Default")
        R._set_render_state(disp, "P", R.RENDER_READY, token=tok)
        disp.currentPlaylistName = "P"
        server.msg_response({"SRC": "a", "DEST": "SRV", "REQUEST": "PLAY",
                             "PAYLOAD": {"displayID": "Default"}}, self._sess())
        # one per-client PLAY broadcast + one PLAYBACK_CHANGED state update
        assert server.socketmanager.broadcast.call_count == 2
        sent = jsonpickle.decode(server.socketmanager.broadcast.call_args_list[0].args[0])
        assert sent["PAYLOAD"]["items"][0]["file"].endswith(".mp4")
        assert "/seg_" in sent["PAYLOAD"]["items"][0]["file"]


import shutil
import pytest


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
class TestFfmpegIntegration:
    async def test_real_render_produces_nonempty_mp4(self, mock_settings, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        src_dir = tmp_path / "media" / "server" / "videos"; src_dir.mkdir(parents=True)
        src = str(src_dir / "clip.mp4")
        import subprocess
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi",
                        "-i", "testsrc=size=320x240:rate=10:duration=1",
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


class TestIsRenderable:
    def test_predicate(self):
        # FULL is now renderable: device encode/downscale required (PT-T3).
        for pm, exp in [(server.PlayMode.SEGMENT, True), (server.PlayMode.INDIVIDUAL, True),
                        (server.PlayMode.FULL, True), (server.PlayMode.SCRIPT, False),
                        (server.PlayMode.DEFAULT, False)]:
            me = server.MediaElement(); me.playmode = pm
            assert server._is_renderable(me) is exp

    def test_render_handler_enqueues_failed_individual_playlist(self, monkeypatch):
        # RENDER with {displayID, name} enqueues a FAILED render — new contract.
        # INDIVIDUAL-only playlists are accepted (no "nothing to render" guard).
        import mosaicmesh.render as _R
        enq = []
        monkeypatch.setattr("mosaicmesh.render_queue.enqueue",
                            lambda name, did: enq.append((name, did)) or True)
        ms = server.Settings()
        ms.displays = {"Default": server.Display()}
        server.settings = ms
        server.socketmanager = MagicMock()
        disp = ms.displays["Default"]
        disp.boundingBox = [0, 0, 100, 100]
        pl = server.Playlist(); pl.name = "Ind"
        pl.items = [{"id": 0, "file": "/media/server/x.jpg", "playmode": "INDIVIDUAL",
                     "duration": 1000}]
        ms.playlists["Ind"] = pl
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 80; c.deviceHeight = 60
        c.measuredPerimeter = np.array([[[0, 0]], [[50, 0]], [[50, 100]], [[0, 100]]])
        ms.clients = {"c1": c}
        _R._set_render_state(disp, "Ind", _R.RENDER_FAILED, token="old", error="boom")
        sess = MagicMock(); sess.id = "s"; sess.request = MagicMock()
        sess.request.remote = "127.0.0.1"; sess.request.headers = {"User-Agent": "T"}
        ret = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "RENDER",
             "PAYLOAD": {"displayID": "Default", "name": "Ind"}}, sess))
        assert ret["PAYLOAD"]["status"] == "QUEUED"   # accepted, not ERROR
        assert ("Ind", "Default") in enq

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
        # Resume-from-pause path: direct per-client PLAY with individual-crop URLs.
        # Updated for registry-based gating (Task 13): must set currentPlaylistName
        # + a READY registry entry (replaces old direct renderedToken assignment).
        from mosaicmesh import render as R
        ms = server.Settings(); ms.displays = {"Default": server.Display()}
        server.settings = ms; server.socketmanager = MagicMock()
        disp = ms.displays["Default"]
        me = server.MediaElement(); me.id = "a"; me.file = "/media/server/x.jpg"
        me.duration = 1000; me.playmode = server.PlayMode.INDIVIDUAL
        disp.mediaElements = [me]; disp.boundingBox = [0, 0, 100, 100]; disp.loop = True
        disp.action = server.PlayState.PAUSE  # resume path bypasses coordinated prepare
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 80; c.deviceHeight = 60
        c.measuredPerimeter = np.array([[[0, 0]], [[50, 0]], [[50, 100]], [[0, 100]]])
        ms.clients = {"c1": c}
        # Registry-based readiness: add playlist, set READY entry, sync renderedToken.
        pl = server.Playlist(); pl.name = "Ind"
        pl.items = [{"id": "a", "file": "/media/server/x.jpg", "playmode": "INDIVIDUAL",
                     "duration": 1000}]
        ms.playlists["Ind"] = pl
        tok = server.compute_render_token("Default")
        R._set_render_state(disp, "Ind", R.RENDER_READY, token=tok)
        disp.currentPlaylistName = "Ind"
        disp.renderedToken = tok   # sync so _per_client_items uses the right token
        sess = MagicMock(); sess.id = "s"; sess.request = MagicMock()
        sess.request.remote = "127.0.0.1"; sess.request.headers = {"User-Agent": "T"}
        server.msg_response({"SRC": "a", "DEST": "SRV", "REQUEST": "PLAY",
                             "PAYLOAD": {"displayID": "Default"}}, sess)
        sent = jsonpickle.decode(server.socketmanager.broadcast.call_args_list[0].args[0])
        assert "/ind_" in sent["PAYLOAD"]["items"][0]["file"]


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

    def test_build_individual_cmd_normalizes_bad_hex(self):
        pts = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]
        cmd = server.build_ffmpeg_individual_cmd("in.mp4", "out.mp4", pts, 80, 60, 10, 10, 0, 0, "#abc")
        vf = cmd[cmd.index("-vf") + 1]
        assert "color=0x000000" in vf   # malformed 3-digit hex falls back to black

    async def test_individual_video_invokes_ffmpeg_with_pad(self, mock_settings, monkeypatch):
        import mosaicmesh.render as _render
        server.settings = mock_settings; server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "v"; me.file = "/media/server/clip.mp4"
        me.duration = 5000; me.playmode = server.PlayMode.INDIVIDUAL; me.backgroundColor = "#000000"
        disp.mediaElements = [me]; disp.boundingBox = [0, 0, 100, 100]
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 80; c.deviceHeight = 60
        c.measuredPerimeter = np.array([[[0, 0]], [[100, 0]], [[100, 100]], [[0, 100]]])
        mock_settings.clients = {"c1": c}
        monkeypatch.setattr(_render, "get_video_dimensions", lambda p: (200, 100))
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
        assert vf.startswith("pad=")
        assert any("ind_" in str(a) for a in calls[0])


class TestIndividualDegenerateQuad:
    async def test_zero_area_quad_raises_clear_error(self, mock_settings, monkeypatch):
        import mosaicmesh.render as _render
        server.settings = mock_settings; server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "v"; me.file = "/media/server/clip.mp4"
        me.duration = 5000; me.playmode = server.PlayMode.INDIVIDUAL; me.backgroundColor = "#000000"
        disp.mediaElements = [me]; disp.boundingBox = [0, 0, 100, 100]
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 80; c.deviceHeight = 60
        # all four corners identical -> zero-area quad
        c.measuredPerimeter = np.array([[[10, 10]], [[10, 10]], [[10, 10]], [[10, 10]]])
        mock_settings.clients = {"c1": c}
        monkeypatch.setattr(_render, "get_video_dimensions", lambda p: (200, 100))
        result = await server.render_group_async("Default")
        assert result["status"] == "error"
        assert "degenerate" in result.get("error", "").lower()


class TestBuilderExtraFilters:
    def test_perspective_appends_video_and_audio_filters(self):
        pts = [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]]
        cmd = server.build_ffmpeg_perspective_cmd("in.mp4", "out.mp4", pts, 80, 60,
                                                  extra_video_filters=["fade=t=in:st=0:d=0.6"],
                                                  extra_audio_filters=["afade=t=in:st=0:d=0.6"])
        vf = cmd[cmd.index("-vf") + 1]
        assert ",fade=t=in:st=0:d=0.6," in vf          # extra video filter present
        assert vf.endswith("setsar=1")                  # square-pixels filter is last
        assert "scale=80:60" in vf
        assert cmd[cmd.index("-af") + 1] == "afade=t=in:st=0:d=0.6"

    def test_perspective_no_extras_is_unchanged(self):
        pts = [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]]
        cmd = server.build_ffmpeg_perspective_cmd("in.mp4", "out.mp4", pts, 80, 60)
        assert "-af" not in cmd
        assert cmd[cmd.index("-vf") + 1].endswith("scale=80:60,setsar=1")

    def test_individual_appends_filters(self):
        pts = [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]]
        cmd = server.build_ffmpeg_individual_cmd("in.mp4", "out.mp4", pts, 80, 60,
                                                100, 100, 0, 0, "#000000",
                                                extra_video_filters=["fade=t=out:st=4.4:d=0.6"],
                                                extra_audio_filters=[])
        vf = cmd[cmd.index("-vf") + 1]
        assert ",fade=t=out:st=4.4:d=0.6," in vf        # extra video filter present
        assert vf.endswith("setsar=1")                  # square-pixels filter is last
        assert "-af" not in cmd   # empty audio list adds no -af


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
class TestIndividualFfmpegIntegration:
    async def test_real_individual_render_produces_nonempty_mp4(self, mock_settings, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        src_dir = tmp_path / "media" / "server" / "videos"; src_dir.mkdir(parents=True)
        src = str(src_dir / "clip.mp4")
        import subprocess
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi",
                        "-i", "testsrc=size=320x240:rate=10:duration=1",
                        "-pix_fmt", "yuv420p", src], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "v"; me.file = "/media/server/clip.mp4"
        me.duration = 1000; me.playmode = server.PlayMode.INDIVIDUAL; me.backgroundColor = "#000000"
        disp.mediaElements = [me]; disp.boundingBox = [0, 0, 320, 240]
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 160; c.deviceHeight = 120
        c.measuredPerimeter = np.array([[[0, 0]], [[160, 0]], [[160, 240]], [[0, 240]]])
        mock_settings.clients = {"c1": c}

        result = await server.render_group_async("Default")

        assert result["status"] == "ready"
        out = tmp_path / "media" / "c1" / "videos" / ("ind_" + disp.renderedToken + "_0.mp4")
        assert out.exists() and out.stat().st_size > 0


class TestEffectRenderHook:
    def _video_group(self, mock_settings, playmode, start=None, end=None):
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "v"; me.file = "/media/server/clip.mp4"
        me.duration = 5; me.playmode = playmode   # 5 SECONDS (model stores seconds; _duration_ms -> 5000ms)
        me.startEffect = start; me.endEffect = end
        disp.mediaElements = [me]; disp.boundingBox = [0, 0, 100, 100]
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 80; c.deviceHeight = 60
        c.measuredPerimeter = np.array([[[0, 0]], [[100, 0]], [[100, 100]], [[0, 100]]])
        mock_settings.clients = {"c1": c}
        return disp

    async def _run_capture(self, monkeypatch):
        import mosaicmesh.render as _render
        calls = []
        class _Proc:
            returncode = 0
            async def communicate(self): return (b"", b"")
        async def _fake_exec(*args, **kwargs):
            calls.append(list(args)); return _Proc()
        monkeypatch.setattr(server.asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(_render, "get_video_dimensions", lambda p: (200, 100))
        await server.render_group_async("Default")
        return calls

    async def test_segment_video_fade_bakes_audio_not_video(self, mock_settings, monkeypatch):
        server.settings = mock_settings; server.socketmanager = MagicMock()
        self._video_group(mock_settings, server.PlayMode.SEGMENT,
                          start={"name": "fade", "params": {"duration": 600, "audioFade": True}},
                          end={"name": "fade", "params": {"duration": 600, "audioFade": True}})
        calls = await self._run_capture(monkeypatch)
        vf = calls[0][calls[0].index("-vf") + 1]
        assert "perspective=" in vf and "scale=80:60" in vf
        assert "fade=" not in vf   # visual fade is client-side, never baked
        af = calls[0][calls[0].index("-af") + 1]
        assert "afade=t=in:st=0:d=0.6" in af    # start audio fade baked
        assert "afade=t=out:st=4.4:d=0.6" in af  # end audio fade baked (chained in same -af)

    async def test_individual_video_fade_bakes_audio(self, mock_settings, monkeypatch):
        server.settings = mock_settings; server.socketmanager = MagicMock()
        self._video_group(mock_settings, server.PlayMode.INDIVIDUAL,
                          start={"name": "fade", "params": {"duration": 1000, "audioFade": True}})
        calls = await self._run_capture(monkeypatch)
        assert calls[0][calls[0].index("-af") + 1] == "afade=t=in:st=0:d=1"

    async def test_wipe_bakes_nothing(self, mock_settings, monkeypatch):
        server.settings = mock_settings; server.socketmanager = MagicMock()
        self._video_group(mock_settings, server.PlayMode.SEGMENT,
                          start={"name": "wipe", "params": {"direction": "left", "duration": 600, "audioFade": False}})
        calls = await self._run_capture(monkeypatch)
        vf = calls[0][calls[0].index("-vf") + 1]
        assert "fade" not in vf and "-af" not in calls[0]

    def test_token_changes_with_effect(self, mock_settings):
        server.settings = mock_settings
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "a"; me.file = "/media/server/x.jpg"
        me.duration = 1000; me.playmode = server.PlayMode.SEGMENT
        disp.mediaElements = [me]; disp.boundingBox = [0, 0, 100, 100]
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 80; c.deviceHeight = 60
        c.measuredPerimeter = np.array([[[0, 0]], [[50, 0]], [[50, 100]], [[0, 100]]])
        mock_settings.clients = {"c1": c}
        t1 = server.compute_render_token("Default")
        me.startEffect = {"name": "fade", "params": {"duration": 600}}
        assert server.compute_render_token("Default") != t1

    def _token_setup(self, mock_settings):
        """Shared display+client setup for audio-fade-sig token tests."""
        server.settings = mock_settings
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "a"; me.file = "/media/server/x.jpg"
        me.duration = 1000; me.playmode = server.PlayMode.SEGMENT
        disp.mediaElements = [me]; disp.boundingBox = [0, 0, 100, 100]
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 80; c.deviceHeight = 60
        c.measuredPerimeter = np.array([[[0, 0]], [[50, 0]], [[50, 100]], [[0, 100]]])
        mock_settings.clients = {"c1": c}
        return me

    def test_token_unchanged_by_visual_only_effect_change(self, mock_settings):
        me = self._token_setup(mock_settings)
        me.startEffect = {"name": "fade", "params": {"duration": 600, "audioFade": True}}
        t1 = server.compute_render_token("Default")
        # Switch to a wipe with same audioFade + same duration — only visual params differ.
        me.startEffect = {"name": "wipe", "params": {"direction": "up", "scope": "wall",
                                                      "duration": 600, "audioFade": True}}
        t2 = server.compute_render_token("Default")
        assert t1 == t2, "visual-only param changes should not invalidate the render token"

    def test_token_unchanged_by_kegroll_visual_param_change(self, mock_settings):
        me = self._token_setup(mock_settings)
        me.startEffect = {"name": "kegroll", "params": {"sprite": "keg", "direction": "right",
                                                        "scope": "wall", "duration": 2000, "audioFade": True}}
        t1 = server.compute_render_token("Default")
        # Change only visual params (sprite and direction); audioFade and duration remain unchanged.
        me.startEffect = {"name": "kegroll", "params": {"sprite": "bottlecap", "direction": "left",
                                                        "scope": "wall", "duration": 2000, "audioFade": True}}
        t2 = server.compute_render_token("Default")
        assert t1 == t2, "kegroll visual-only param changes should not invalidate the render token"

    def test_token_unchanged_by_frostcreep_visual_param_change(self, mock_settings):
        me = self._token_setup(mock_settings)
        me.startEffect = {"name": "frostcreep", "params": {"tint": "frost", "sprite": "frostymug",
                                                           "scope": "wall", "duration": 2200, "audioFade": True}}
        t1 = server.compute_render_token("Default")
        # Change only visual params (tint, sprite, scope); audioFade and duration unchanged.
        me.startEffect = {"name": "frostcreep", "params": {"tint": "blue", "sprite": "hop",
                                                           "scope": "screen", "duration": 2200, "audioFade": True}}
        t2 = server.compute_render_token("Default")
        assert t1 == t2, "frostcreep visual-only param changes should not invalidate the render token"

    def test_token_unchanged_by_coasterflip_visual_param_change(self, mock_settings):
        me = self._token_setup(mock_settings)
        me.startEffect = {"name": "coasterflip", "params": {"axis": "horizontal", "coaster": "kraft",
                                                            "sprite": "coaster", "flips": 5,
                                                            "scope": "wall", "duration": 1800, "audioFade": True}}
        t1 = server.compute_render_token("Default")
        # Change only visual params (axis, coaster, sprite, flips, scope); audioFade + duration unchanged.
        me.startEffect = {"name": "coasterflip", "params": {"axis": "vertical", "coaster": "slate",
                                                            "sprite": "hop", "flips": 3,
                                                            "scope": "screen", "duration": 1800, "audioFade": True}}
        t2 = server.compute_render_token("Default")
        assert t1 == t2, "coasterflip visual-only param changes should not invalidate the render token"

    def test_token_unchanged_by_wheatpart_visual_param_change(self, mock_settings):
        me = self._token_setup(mock_settings)
        me.startEffect = {"name": "wheatpart", "params": {"tint": "golden", "density": 70, "scope": "wall",
                                                          "duration": 2200, "audioFade": True}}
        t1 = server.compute_render_token("Default")
        # Change only visual params (tint, density); audioFade + duration unchanged.
        me.startEffect = {"name": "wheatpart", "params": {"tint": "amber", "density": 200, "scope": "wall",
                                                          "duration": 2200, "audioFade": True}}
        t2 = server.compute_render_token("Default")
        assert t1 == t2, "wheatpart visual-only param changes should not invalidate the render token"

    def test_token_changes_when_audioFade_toggled(self, mock_settings):
        me = self._token_setup(mock_settings)
        me.startEffect = {"name": "fade", "params": {"duration": 600, "audioFade": True}}
        t1 = server.compute_render_token("Default")
        me.startEffect = {"name": "fade", "params": {"duration": 600, "audioFade": False}}
        t2 = server.compute_render_token("Default")
        assert t1 != t2, "toggling audioFade must change the render token"

    def test_token_changes_when_audio_duration_changes(self, mock_settings):
        me = self._token_setup(mock_settings)
        me.startEffect = {"name": "fade", "params": {"duration": 600, "audioFade": True}}
        t1 = server.compute_render_token("Default")
        me.startEffect = {"name": "fade", "params": {"duration": 1200, "audioFade": True}}
        t2 = server.compute_render_token("Default")
        assert t1 != t2, "changing duration when audioFade is on must change the render token"

    def test_token_unchanged_by_visual_duration_when_audio_off(self, mock_settings):
        me = self._token_setup(mock_settings)
        me.startEffect = {"name": "fade", "params": {"duration": 600, "audioFade": False}}
        t1 = server.compute_render_token("Default")
        me.startEffect = {"name": "fade", "params": {"duration": 1200, "audioFade": False}}
        t2 = server.compute_render_token("Default")
        assert t1 == t2, "duration change when audioFade is off should not change the render token"

    def test_normalize_effect_tolerates_shapes(self):
        assert server._normalize_effect(None) is None
        assert server._normalize_effect("fade") == {"name": "fade", "params": {}}
        assert server._normalize_effect({"name": "fade", "params": {"duration": 1}})["name"] == "fade"
        assert server._normalize_effect("audiofade") == {"name": "fade", "params": {"audioFade": True}}
        assert server._normalize_effect("") is None


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
class TestEffectFfmpegIntegration:
    async def test_segment_fade_effect_render_produces_nonempty_mp4(self, mock_settings, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        src_dir = tmp_path / "media" / "server" / "videos"; src_dir.mkdir(parents=True)
        src = str(src_dir / "clip.mp4")
        import subprocess
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi",
                        "-i", "testsrc=size=320x240:rate=10:duration=1",
                        "-pix_fmt", "yuv420p", src], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "v"; me.file = "/media/server/clip.mp4"
        me.duration = 1000; me.playmode = server.PlayMode.SEGMENT
        me.startEffect = {"name": "fade", "params": {"duration": 300, "audioFade": False}}
        disp.mediaElements = [me]; disp.boundingBox = [0, 0, 320, 240]
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 160; c.deviceHeight = 120
        c.measuredPerimeter = np.array([[[0, 0]], [[160, 0]], [[160, 240]], [[0, 240]]])
        mock_settings.clients = {"c1": c}

        result = await server.render_group_async("Default")

        assert result["status"] == "ready"
        out = tmp_path / "media" / "c1" / "videos" / ("seg_" + disp.renderedToken + "_0.mp4")
        assert out.exists() and out.stat().st_size > 0


class TestScreenQuad:
    def test_axis_aligned_marker_extrapolates_full_screen(self):
        # marker 300px centered in a 1000x800 canvas, photographed axis-aligned
        # at scale 1 (1px canvas == 1px photo): marker corners are at
        # (350,250),(650,250),(650,550),(350,550) and the screen quad must be
        # the full canvas rectangle.
        marker_quad = [[350,250],[650,250],[650,550],[350,550]]
        q = server.reconstruct_screen_quad(marker_quad, 1000, 800).reshape(4,2)
        assert abs(q[0][0]-0) <= 1 and abs(q[0][1]-0) <= 1       # TL
        assert abs(q[1][0]-1000) <= 1 and abs(q[1][1]-0) <= 1    # TR
        assert abs(q[2][0]-1000) <= 1 and abs(q[2][1]-800) <= 1  # BR
        assert abs(q[3][0]-0) <= 1 and abs(q[3][1]-800) <= 1     # BL

    def test_scaled_marker(self):
        # marker photographed at half scale centered at photo (500,400):
        # corners +/-75 -> screen should be 500x400 centered at (500,400).
        marker_quad = [[425,325],[575,325],[575,475],[425,475]]
        q = server.reconstruct_screen_quad(marker_quad, 1000, 800).reshape(4,2)
        assert abs(q[0][0]-250) <= 1 and abs(q[0][1]-200) <= 1   # TL ~ (250,200)
        assert abs(q[2][0]-750) <= 1 and abs(q[2][1]-600) <= 1   # BR ~ (750,600)


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

    def test_unvalidated_band_keeps_fiducial(self):
        # band can't validate either orientation (tiny, unrelated) -> keep the
        # marker-derived fiducial and flag it; never output the band geometry.
        border = [[100,100],[200,100],[200,150],[100,150]]  # tiny, unrelated
        quad, src = server.reconcile_screen_quad(self.MARKER, border, 1000, 800)
        assert src == "unverified"
        fid = server.reconstruct_screen_quad(self.MARKER, 1000, 800)
        assert quad.reshape(4,2).tolist() == fid.reshape(4,2).tolist()

    def test_degenerate_band_ignored(self):
        # a zero-area / collinear band must not be trusted: it can't validate, so
        # the marker fiducial geometry is kept and flagged "no-band" (unusable band).
        quad, src = server.reconcile_screen_quad(self.MARKER, [[0,0],[10,0],[20,0]], 1000, 800)
        assert src == "no-band"
        fid = server.reconstruct_screen_quad(self.MARKER, 1000, 800)
        assert quad.reshape(4,2).tolist() == fid.reshape(4,2).tolist()

    def test_no_border_uses_fiducial(self):
        # no band quad at all -> fiducial geometry, flagged "no-band".
        quad, src = server.reconcile_screen_quad(self.MARKER, None, 1000, 800)
        assert src == "no-band"
        fid = server.reconstruct_screen_quad(self.MARKER, 1000, 800)
        assert quad.reshape(4,2).tolist() == fid.reshape(4,2).tolist()
