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
