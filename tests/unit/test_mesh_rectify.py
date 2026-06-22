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
    assert CAL.MESH_RECTIFY is True   # rectification is the production default


def _grid_centers(R, C, pitch=100.0, x0=0.0, y0=0.0):
    return [(x0 + c * pitch, y0 + r * pitch) for r in range(R) for c in range(C)]


def test_detect_grid_clean_6x4():
    centers = _grid_centers(4, 6, pitch=100.0)   # 24 points, cell extent ~80
    res = CAL._detect_grid(centers, 80.0, 80.0)
    assert res is not None
    rows, cols, Rn, Cn = res
    assert Rn == 4 and Cn == 6
    assert sorted(zip(rows, cols)) == sorted((r, c) for r in range(4) for c in range(6))


def test_detect_grid_skips_irregular():
    # 23 points: a clean 6x4 minus one cell -> R*C (24) != N (23) -> skip.
    centers = _grid_centers(4, 6, pitch=100.0)[:-1]
    assert CAL._detect_grid(centers, 80.0, 80.0) is None


def test_cluster_1d_bands_by_gap():
    vals = [0, 2, 1, 100, 101, 99, 200, 198, 202]
    bands = CAL._cluster_1d(vals, 40.0)
    assert max(bands) == 2
    assert bands[0] == bands[1] == bands[2] == 0
    assert bands[3] == bands[4] == bands[5] == 1
    assert bands[6] == bands[7] == bands[8] == 2


from unittest.mock import patch


def _client(did, quad, dw=1024, dh=768):
    c = Client(); c.displayID = did
    c.measuredPerimeter = np.array(quad, dtype="int32").reshape(-1, 1, 2)
    c.deviceWidth = dw; c.deviceHeight = dh
    return c


def _keystoned_clients(did="G", R=4, C=6):
    """Regular R×C grid of cell quads pushed through a known perspective (bottom
    enlarged) so the photo coords are keystoned — the exact bug. cell 80×60 on a
    100×100 pitch (gap 20)."""
    import cv2 as _cv
    PITCH, CW, CH = 100.0, 80.0, 60.0
    cells = []
    for r in range(R):
        for c in range(C):
            cx, cy = c * PITCH, r * PITCH
            cells.append([[cx - CW/2, cy - CH/2], [cx + CW/2, cy - CH/2],
                          [cx + CW/2, cy + CH/2], [cx - CW/2, cy + CH/2]])
    xs = [p[0] for cell in cells for p in cell]; ys = [p[1] for cell in cells for p in cell]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    src = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype="float32")
    dst = np.array([[60, 0], [540, 0], [560, 300], [40, 300]], dtype="float32")  # bottom wider+taller
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


def _row_gap_nonuniformity(quads_norm, R=4, C=6):
    """Spread of inter-row gaps relative to the mean gap, from normalized quads.
    Cell center y's sort cleanly into R rows of C (rows never interleave); a
    keystoned layout has uneven row gaps (high), a rectified one ~uniform (~0)."""
    import statistics as _st
    cys = sorted(sum(p[1] for p in q) / 4.0 for q in quads_norm)
    rows_y = [_st.mean(cys[i * C:(i + 1) * C]) for i in range(R)]
    gaps = [rows_y[i + 1] - rows_y[i] for i in range(R - 1)]
    return (max(gaps) - min(gaps)) / _st.mean(gaps)


def test_rectify_centers_keystoned_grid(fresh_settings):
    d = Display()
    clients = _keystoned_clients("G")
    res = CAL.rectify_group_grid(d, clients)
    assert res is True
    assert d.meshGlobalRect and d.meshGlobalRect[0] > 0 and d.meshGlobalRect[1] > 0
    mx, my = _cell_centroid(clients)
    assert abs(mx - 0.5) < 0.02, mx
    assert abs(my - 0.5) < 0.02, my
    q = clients[0].meshCellQuad
    assert len(q) == 4 and all(type(v) is float for pair in q for v in pair)

    # Falsifiable: the raw (bbox-normalized) fixture must have a real keystone
    # bias, and rectification must flatten the inter-row spacing.
    allx, ally = [], []
    for c in clients:
        q = np.array(c.measuredPerimeter, dtype="float64").reshape(-1, 2)
        allx += list(q[:, 0]); ally += list(q[:, 1])
    bx, by = min(allx), min(ally); bw = max(allx) - bx; bh = max(ally) - by
    raw_norm = []
    for c in clients:
        q = np.array(c.measuredPerimeter, dtype="float64").reshape(-1, 2)
        raw_norm.append([[(p[0] - bx) / bw, (p[1] - by) / bh] for p in q])
    raw_nu = _row_gap_nonuniformity(raw_norm)
    rect_nu = _row_gap_nonuniformity([c.meshCellQuad for c in clients])
    assert raw_nu > 0.02, ("fixture not keystoned enough", raw_nu)
    assert rect_nu < 0.01, ("rectified rows not uniform", rect_nu)


def test_rectify_skips_non_grid(fresh_settings):
    d = Display()
    clients = _keystoned_clients("G")[:-1]   # 23 -> not a clean 6x4
    assert CAL.rectify_group_grid(d, clients) is False
    assert d.meshGlobalRect is None
    assert all(c.meshCellQuad is None for c in clients)


def test_assign_calls_rectify_per_flag(fresh_settings):
    for i, c in enumerate(_keystoned_clients("G")):
        fresh_settings.clients["c%d" % i] = c
    with patch.object(CAL, "MESH_RECTIFY", False):          # OFF -> no rectified rect
        CAL.assign_group_bounding_boxes()
    assert fresh_settings.displays["G"].meshGlobalRect is None
    with patch.object(CAL, "MESH_RECTIFY", True):           # ON -> rectified rect computed
        CAL.assign_group_bounding_boxes()
    assert fresh_settings.displays["G"].meshGlobalRect is not None


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
    assert items[0]["meshGlobal"] == [2000, 1000]
    assert items[0]["meshQuad"] == [[0.1, 0.1], [0.4, 0.1], [0.4, 0.9], [0.1, 0.9]]
    json.dumps(items)


def test_per_client_items_raw_when_flag_off(fresh_settings):
    d = _mesh_display(fresh_settings)
    c = _client("G", [[0, 0], [100, 0], [100, 100], [0, 100]])
    c.meshCellQuad = [[0.1, 0.1], [0.4, 0.1], [0.4, 0.9], [0.1, 0.9]]  # present but ignored
    with patch.object(CAL, "MESH_RECTIFY", False):
        items = R._per_client_items(d, "c1", c)
    assert items[0]["meshGlobal"] == [1774, 887]
    assert items[0]["meshQuad"][0] == [0.0, 0.0] and items[0]["meshQuad"][1] == [0.5, 0.0]


def test_rectified_multiple_mesh_items_survive_jsonpickle_refs(fresh_settings):
    """Two mesh items must each carry their OWN meshQuad list. The broadcast is
    jsonpickle-encoded with reference tracking on; if both items referenced the
    SAME c.meshCellQuad object, every occurrence after the first would serialize
    as a {"py/id": N} back-reference. The iPad does JSON.parse (no jsonpickle
    decode), so it would see an object instead of a [u,v] array and mmMeshTransform
    would throw on meshQuad[3][0] -> that screen goes black. Regression for the
    on-wall "2nd+ mesh animation renders black" bug."""
    import jsonpickle
    d = _mesh_display(fresh_settings)
    me2 = MediaElement(); me2.id = "b"; me2.file = "gameOfLife"
    me2.playmode = PlayMode.SCRIPT; me2.duration = 5.0; me2.scriptSpan = "mesh"
    d.mediaElements.append(me2)                      # now: [plasma/mesh, gameOfLife/mesh]
    c = _client("G", [[0, 0], [100, 0], [100, 100], [0, 100]])
    c.meshCellQuad = [[0.1, 0.1], [0.4, 0.1], [0.4, 0.9], [0.1, 0.9]]
    with patch.object(CAL, "MESH_RECTIFY", True):
        items = R._per_client_items(d, "c1", c)
    # Root cause: the two items must not share the same list object.
    assert items[0]["meshQuad"] is not items[1]["meshQuad"]
    # Faithful to the wire: jsonpickle-encode (broadcast) -> plain JSON-decode (client).
    encoded = jsonpickle.encode(items)
    assert "py/id" not in encoded
    decoded = json.loads(encoded)
    for it in decoded:
        q = it["meshQuad"]
        assert isinstance(q, list) and len(q) == 4, "client must see a 4-point array, not a py/id ref"
        for pt in q:
            assert isinstance(pt, list) and len(pt) == 2
            assert all(isinstance(v, (int, float)) for v in pt)


def test_detect_grid_strong_keystone_row_first():
    # Columns overlap in x across rows (top x-compressed, bottom x-expanded) —
    # the real-wall failure mode where independent x-clustering merges columns.
    # Row-first detection (rank x within each row) must still recover a clean 4x6.
    R, C = 4, 6
    centers = []
    for r in range(R):
        for c in range(C):
            sx = 1.0 + 0.6 * r                       # bottom rows wider in x
            x = 1000 + (c - (C - 1) / 2.0) * 120 * sx
            y = r * 400.0                            # rows separate cleanly in y
            centers.append((x, y))
    res = CAL._detect_grid(centers, 100.0, 100.0)
    assert res is not None
    rows, cols, Rn, Cn = res
    assert Rn == 4 and Cn == 6
    assert sorted(zip(rows, cols)) == sorted((r, c) for r in range(4) for c in range(6))
