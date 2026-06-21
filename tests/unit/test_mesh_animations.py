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

import numpy as np
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
    c = Client(); c.displayID = did
    c.measuredPerimeter = np.array(quad, dtype="int32").reshape(-1, 1, 2)
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


import json


def _mesh_group(fresh_settings, did="G"):
    d = Display()
    d.boundingBox = [0, 0, 200, 100]
    d.meshGlobal = [1774, 887]
    me = MediaElement(); me.id = "a"; me.file = "plasma"
    me.playmode = PlayMode.SCRIPT; me.duration = 5.0; me.scriptSpan = "mesh"
    d.mediaElements = [me]
    fresh_settings.displays[did] = d
    return d


def _calibrated_client(did="G", quad=None):
    c = Client(); c.displayID = did
    q = quad or [[0, 0], [100, 0], [100, 100], [0, 100]]
    c.measuredPerimeter = np.array(q, dtype="int32").reshape(-1, 1, 2)  # production (4,1,2) shape
    return c


def test_per_client_items_mesh_attaches_quad_for_calibrated(fresh_settings):
    d = _mesh_group(fresh_settings)
    c = _calibrated_client()
    items = R._per_client_items(d, "c1", c)
    assert items[0]["meshGlobal"] == [1774, 887]
    # left-half screen of a 200-wide bbox -> normalized x in {0, 0.5}
    q = items[0]["meshQuad"]
    assert q[0] == [0.0, 0.0] and q[1] == [0.5, 0.0]
    assert q[2] == [0.5, 1.0] and q[3] == [0.0, 1.0]
    # meshQuad coords must be native Python floats so the payload is JSON-serializable
    assert all(type(v) is float for pair in q for v in pair)
    json.dumps(items)  # must not raise (no numpy types leak into the payload)


def test_per_client_items_mesh_black_for_uncalibrated(fresh_settings):
    d = _mesh_group(fresh_settings)
    c = Client(); c.displayID = "G"; c.measuredPerimeter = None  # uncalibrated
    items = R._per_client_items(d, "c1", c)
    assert "meshQuad" not in items[0] and "meshGlobal" not in items[0]


def test_per_client_items_mirror_has_no_mesh_fields(fresh_settings):
    d = _mesh_group(fresh_settings)
    d.mediaElements[0].scriptSpan = "mirror"
    c = _calibrated_client()
    items = R._per_client_items(d, "c1", c)
    assert "meshQuad" not in items[0] and "meshGlobal" not in items[0]


def test_meshglobal_backfills_stale_precomputed_group(fresh_settings):
    # Boot scenario: a group calibrated before meshGlobal existed has a (stale)
    # boundingBox on disk but meshGlobal=None, while its client's
    # measuredPerimeter is persisted. The boot-time assign_group_bounding_boxes()
    # call must populate meshGlobal (and recompute boundingBox) from the stored
    # quad, so mesh animations work after a plain restart with no re-calibrate.
    d = Display()
    d.boundingBox = [0, 0, 999, 999]   # stale value from a prior calibration
    d.meshGlobal = None                # field didn't exist at last calibration
    fresh_settings.displays["G"] = d
    fresh_settings.clients["a"] = _client_with_quad(
        "G", [[0, 0], [100, 0], [100, 100], [0, 100]], 1024, 768)
    CAL.assign_group_bounding_boxes()
    mg = fresh_settings.displays["G"].meshGlobal
    assert mg is not None and mg[0] > 0 and mg[1] > 0
    # boundingBox recomputed from the stored quad — no longer the stale value.
    assert fresh_settings.displays["G"].boundingBox != [0, 0, 999, 999]
