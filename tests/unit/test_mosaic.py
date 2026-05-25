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
