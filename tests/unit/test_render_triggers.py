# tests/unit/test_render_triggers.py
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
from mosaicmesh.state import Settings, Playlist


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev


@pytest.mark.asyncio
async def test_rest_create_schedules_autorender(fresh_settings, monkeypatch):
    scheduled = []
    monkeypatch.setattr("mosaicmesh.render_queue.schedule_autorender",
                        lambda name: scheduled.append(name))
    from mosaicmesh.api.playlists import api_playlists_create
    req = make_mocked_request('POST', '/api/playlists')
    async def _json():
        return {"name": "P", "items": [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]}
    req.json = _json
    resp = await api_playlists_create(req)
    assert resp.status == 201
    assert scheduled == ["P"]


@pytest.mark.asyncio
async def test_rest_update_schedules_autorender(fresh_settings, monkeypatch):
    p = Playlist(); p.name = "P"; p._serverVersion = 1
    fresh_settings.playlists["P"] = p
    scheduled = []
    monkeypatch.setattr("mosaicmesh.render_queue.schedule_autorender",
                        lambda name: scheduled.append(name))
    from mosaicmesh.api.playlists import api_playlists_update
    req = make_mocked_request('PUT', '/api/playlists/P', match_info={'name': 'P'}, headers={'If-Match': '1'})
    async def _json():
        return {"items": [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]}
    req.json = _json
    resp = await api_playlists_update(req)
    assert resp.status == 200
    assert scheduled == ["P"]
