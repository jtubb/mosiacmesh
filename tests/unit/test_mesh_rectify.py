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
