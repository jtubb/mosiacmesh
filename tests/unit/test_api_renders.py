# tests/unit/test_api_renders.py
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

import json
import pytest
from aiohttp.test_utils import make_mocked_request
from mosaicmesh.state import Settings, Display
from mosaicmesh import render as R
from mosaicmesh.api.renders import api_renders_list


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev


@pytest.mark.asyncio
async def test_renders_list_shape(fresh_settings):
    d = Display()
    fresh_settings.displays["G1"] = d
    R._set_render_state(d, "P", R.RENDER_RENDERING, token="t", percent=42, eta=30)
    resp = await api_renders_list(make_mocked_request('GET', '/api/renders'))
    assert resp.status == 200
    data = json.loads(resp.text)
    assert data["success"] is True
    assert "queueDepth" in data
    row = next(r for r in data["renders"] if r["playlist"] == "P")
    assert row["displayID"] == "G1"
    assert row["state"] == "RENDERING"
    assert row["percent"] == 42
