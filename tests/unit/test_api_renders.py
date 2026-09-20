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


async def test_api_renders_includes_token(fresh_settings):
    """The token was tracked in state but never serialised, so the endpoint
    reported token=None for every entry -- and that sent a live debugging
    session down the wrong path twice (2026-09-19)."""
    d = Display()
    fresh_settings.displays["G1"] = d
    R._set_render_state(d, "P", R.RENDER_READY, token="abc123def456")
    resp = await api_renders_list(make_mocked_request('GET', '/api/renders'))
    row = next(r for r in json.loads(resp.text)["renders"] if r["playlist"] == "P")
    assert row["token"] == "abc123def456"
    assert "assetsPresent" not in row      # not asked for -> no disk I/O


async def test_api_renders_assets_flag_is_opt_in(fresh_settings, monkeypatch):
    """Assets going missing is the one thing that silently invalidates a READY
    render, and it was only discoverable by hand."""
    d = Display()
    fresh_settings.displays["G1"] = d
    R._set_render_state(d, "P", R.RENDER_READY, token="t")
    monkeypatch.setattr(R, "_render_assets_exist", lambda *a: False)
    resp = await api_renders_list(make_mocked_request('GET', '/api/renders?assets=1'))
    row = next(r for r in json.loads(resp.text)["renders"] if r["playlist"] == "P")
    assert row["assetsPresent"] is False


async def test_api_renders_asset_check_never_500s(fresh_settings, monkeypatch):
    """A stat error on one entry must not take down the whole panel."""
    d = Display()
    fresh_settings.displays["G1"] = d
    R._set_render_state(d, "P", R.RENDER_READY, token="t")
    def boom(*a):
        raise OSError("disk gone")
    monkeypatch.setattr(R, "_render_assets_exist", boom)
    resp = await api_renders_list(make_mocked_request('GET', '/api/renders?assets=1'))
    row = next(r for r in json.loads(resp.text)["renders"] if r["playlist"] == "P")
    assert row["assetsPresent"] is None
