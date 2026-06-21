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
    assert CAL.MESH_RECTIFY is False   # opt-in: off by default


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


def test_assign_calls_rectify_only_when_flag_on(fresh_settings):
    for i, c in enumerate(_keystoned_clients("G")):
        fresh_settings.clients["c%d" % i] = c
    CAL.assign_group_bounding_boxes()                       # flag OFF (default)
    assert fresh_settings.displays["G"].meshGlobalRect is None
    with patch.object(CAL, "MESH_RECTIFY", True):
        CAL.assign_group_bounding_boxes()
    assert fresh_settings.displays["G"].meshGlobalRect is not None
