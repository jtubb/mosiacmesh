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
