"""Unit tests for /api/playlists CRUD."""
import json
import pytest
from aiohttp.test_utils import make_mocked_request

# Boilerplate to import server with parse_args mocked (matches the existing
# pattern from test_api_endpoints.py).
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import argparse
_orig = argparse.ArgumentParser.parse_args

class _MockArgs:
    Port = 3000
    Verbose = False

argparse.ArgumentParser.parse_args = lambda self, args=None, namespace=None: _MockArgs()
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

from mosaicmesh.state import Settings, Playlist, Schedule
from mosaicmesh.api.playlists import (
    api_playlists_list, api_playlists_create,
    api_playlists_update, api_playlists_delete,
)


@pytest.fixture
def fresh_settings():
    """Replace server.settings with a fresh Settings object for each test."""
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev


class TestPlaylistsList:
    @pytest.mark.asyncio
    async def test_empty_list(self, fresh_settings):
        resp = await api_playlists_list(make_mocked_request('GET', '/api/playlists'))
        assert resp.status == 200
        data = json.loads(resp.text)
        assert data['success'] is True
        assert data['playlists'] == []

    @pytest.mark.asyncio
    async def test_lists_existing(self, fresh_settings):
        p = Playlist()
        p.name = "Morning News"
        p.items = [{"file": "/media/a.mp4"}]
        p.loop = True
        p._serverVersion = 5
        fresh_settings.playlists["Morning News"] = p
        resp = await api_playlists_list(make_mocked_request('GET', '/api/playlists'))
        data = json.loads(resp.text)
        assert len(data['playlists']) == 1
        out = data['playlists'][0]
        assert out['name'] == "Morning News"
        assert out['loop'] is True
        assert out['_serverVersion'] == 5
        assert out['items'] == [{"file": "/media/a.mp4"}]


class TestPlaylistsCreate:
    @pytest.mark.asyncio
    async def test_create_minimal(self, fresh_settings):
        req = make_mocked_request('POST', '/api/playlists')
        async def _json():
            return {"name": "Lunch Menu"}
        req.json = _json
        resp = await api_playlists_create(req)
        assert resp.status == 201
        data = json.loads(resp.text)
        assert data['success'] is True
        assert data['playlist']['name'] == "Lunch Menu"
        assert data['playlist']['_serverVersion'] == 1
        assert "Lunch Menu" in fresh_settings.playlists

    @pytest.mark.asyncio
    async def test_create_with_items_and_loop(self, fresh_settings):
        req = make_mocked_request('POST', '/api/playlists')
        async def _json():
            return {"name": "X", "items": [{"file": "/m/a.mp4"}], "loop": True}
        req.json = _json
        resp = await api_playlists_create(req)
        assert resp.status == 201
        data = json.loads(resp.text)
        assert data['playlist']['loop'] is True
        assert data['playlist']['items'] == [{"file": "/m/a.mp4"}]

    @pytest.mark.asyncio
    async def test_create_requires_name(self, fresh_settings):
        req = make_mocked_request('POST', '/api/playlists')
        async def _json():
            return {"loop": True}
        req.json = _json
        resp = await api_playlists_create(req)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_create_rejects_duplicate(self, fresh_settings):
        fresh_settings.playlists["dup"] = Playlist()
        req = make_mocked_request('POST', '/api/playlists')
        async def _json():
            return {"name": "dup"}
        req.json = _json
        resp = await api_playlists_create(req)
        assert resp.status == 409


class TestPlaylistsUpdate:
    def _req(self, name, body, if_match=None):
        headers = {'If-Match': str(if_match)} if if_match is not None else {}
        req = make_mocked_request('PUT', f'/api/playlists/{name}',
                                  headers=headers,
                                  match_info={"name": name})
        async def _json():
            return body
        req.json = _json
        return req

    @pytest.mark.asyncio
    async def test_update_with_matching_if_match(self, fresh_settings):
        p = Playlist()
        p.name = "x"
        p._serverVersion = 3
        fresh_settings.playlists["x"] = p
        resp = await api_playlists_update(self._req("x", {"loop": True}, if_match=3))
        assert resp.status == 200
        data = json.loads(resp.text)
        assert data['playlist']['_serverVersion'] == 4
        assert data['playlist']['loop'] is True

    @pytest.mark.asyncio
    async def test_update_missing_if_match_returns_428(self, fresh_settings):
        p = Playlist()
        p._serverVersion = 1
        fresh_settings.playlists["x"] = p
        resp = await api_playlists_update(self._req("x", {"loop": True}))
        assert resp.status == 428

    @pytest.mark.asyncio
    async def test_update_stale_if_match_returns_412(self, fresh_settings):
        p = Playlist()
        p._serverVersion = 5
        fresh_settings.playlists["x"] = p
        resp = await api_playlists_update(self._req("x", {"loop": True}, if_match=2))
        assert resp.status == 412
        data = json.loads(resp.text)
        assert data['currentVersion'] == 5

    @pytest.mark.asyncio
    async def test_update_missing_returns_404(self, fresh_settings):
        resp = await api_playlists_update(self._req("nope", {"loop": True}, if_match=1))
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_update_replaces_items(self, fresh_settings):
        p = Playlist()
        p.name = "x"
        p.items = [{"file": "/m/old.mp4"}]
        p._serverVersion = 2
        fresh_settings.playlists["x"] = p
        new_items = [{"file": "/m/a.mp4"}, {"file": "/m/b.mp4"}]
        resp = await api_playlists_update(
            self._req("x", {"items": new_items}, if_match=2))
        assert resp.status == 200
        data = json.loads(resp.text)
        assert data['playlist']['items'] == new_items
        assert data['playlist']['_serverVersion'] == 3
        assert fresh_settings.playlists["x"].items == new_items


class TestPlaylistsDelete:
    @pytest.mark.asyncio
    async def test_delete_existing(self, fresh_settings):
        fresh_settings.playlists["x"] = Playlist()
        req = make_mocked_request('DELETE', '/api/playlists/x',
                                  match_info={"name": "x"})
        resp = await api_playlists_delete(req)
        assert resp.status == 204
        assert "x" not in fresh_settings.playlists

    @pytest.mark.asyncio
    async def test_delete_missing_returns_404(self, fresh_settings):
        req = make_mocked_request('DELETE', '/api/playlists/nope',
                                  match_info={"name": "nope"})
        resp = await api_playlists_delete(req)
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_delete_referenced_returns_409_with_refs(self, fresh_settings):
        fresh_settings.playlists["pl"] = Playlist()
        s1 = Schedule(); s1.id = "sch-1"; s1.playlistName = "pl"
        s2 = Schedule(); s2.id = "sch-2"; s2.playlistName = "pl"
        s3 = Schedule(); s3.id = "sch-other"; s3.playlistName = "other"
        fresh_settings.schedules = {"sch-1": s1, "sch-2": s2, "sch-other": s3}
        req = make_mocked_request('DELETE', '/api/playlists/pl',
                                  match_info={"name": "pl"})
        resp = await api_playlists_delete(req)
        assert resp.status == 409
        data = json.loads(resp.text)
        assert set(data['refs']) == {"sch-1", "sch-2"}
        assert "pl" in fresh_settings.playlists   # not removed
