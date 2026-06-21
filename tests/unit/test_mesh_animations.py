# tests/unit/test_mesh_animations.py
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
from unittest.mock import MagicMock
from mosaicmesh.state import Settings, Display, Client, MediaElement, PlayMode
from mosaicmesh import render as R


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    server.socketmanager = MagicMock()
    yield server.settings
    server.settings = prev


def test_mediaelement_defaults_scriptspan_mirror():
    assert MediaElement().scriptSpan == 'mirror'


def test_display_defaults_meshglobal_none():
    assert Display().meshGlobal is None


def test_build_media_elements_reads_scriptspan():
    els = R._build_media_elements([
        {"id": "a", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh"},
        {"id": "b", "file": "plasma", "playmode": "SCRIPT"},  # no scriptSpan -> mirror
    ])
    assert els[0].scriptSpan == "mesh"
    assert els[1].scriptSpan == "mirror"


def test_media_item_payload_echoes_scriptspan():
    me = MediaElement(); me.id = "a"; me.file = "plasma"
    me.playmode = PlayMode.SCRIPT; me.duration = 1.0; me.scriptSpan = "mesh"
    assert R._media_item_payload(me)["scriptSpan"] == "mesh"


def test_media_item_payload_scriptspan_defaults_mirror_on_old_object():
    me = MediaElement(); me.id = "a"; me.file = "plasma"
    me.playmode = PlayMode.SCRIPT; me.duration = 1.0
    del me.scriptSpan  # simulate an object from an older settings.dat
    assert R._media_item_payload(me)["scriptSpan"] == "mirror"


from mosaicmesh import calibration as CAL


def _client_with_quad(did, quad, dw, dh):
    c = Client(); c.displayID = did; c.measuredPerimeter = quad
    c.deviceWidth = dw; c.deviceHeight = dh
    return c


def test_meshglobal_preserves_bbox_aspect_and_scales(fresh_settings):
    # Two screens side by side, each quad occupying 100x100 photo px.
    # Each screen is 1024x768 device px.
    # ratio per screen = sqrt((1024/100)*(768/100)) = sqrt(78.64) ~= 8.868
    # cv2.boundingRect gives bw=201, bh=101 for points spanning [0..200]x[0..100].
    # GW = bw*k, GH = bh*k -> aspect stays ~2:1.
    fresh_settings.clients["a"] = _client_with_quad(
        "G", [[0, 0], [100, 0], [100, 100], [0, 100]], 1024, 768)
    fresh_settings.clients["b"] = _client_with_quad(
        "G", [[100, 0], [200, 0], [200, 100], [100, 100]], 1024, 768)
    CAL.assign_group_bounding_boxes()
    d = fresh_settings.displays["G"]
    bx, by, bw, bh = d.boundingBox
    gw, gh = d.meshGlobal
    assert gw > 0 and gh > 0
    assert abs(gw / float(gh) - 2.0) < 0.02          # aspect preserved
    assert abs(gh - round(bh * 8.868)) <= 2          # scaled by median ratio


def test_meshglobal_fallback_when_no_resolution(fresh_settings):
    # Clients calibrated but with no device resolution -> k=1 -> meshGlobal == bbox dims.
    fresh_settings.clients["a"] = _client_with_quad(
        "G", [[0, 0], [100, 0], [100, 100], [0, 100]], 0, 0)
    CAL.assign_group_bounding_boxes()
    d = fresh_settings.displays["G"]
    bx, by, bw, bh = d.boundingBox
    assert d.meshGlobal == [bw, bh]
