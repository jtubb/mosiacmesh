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
        assert server.socketmanager.broadcast.call_count == 1
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
        assert len(scheduled) == 1

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
