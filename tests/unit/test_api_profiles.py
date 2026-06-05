"""Unit tests for /api/profiles CRUD + POST /api/clients/{key}/profile."""
import json
import pytest
from aiohttp.test_utils import make_mocked_request

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

from mosaicmesh.state import Settings, ScriptingProfile, Client
from mosaicmesh.api.profiles import (
    api_profiles_list, api_profiles_create,
    api_profiles_update, api_profiles_delete,
    api_clients_assign_profile,
)


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev


class TestProfilesList:
    @pytest.mark.asyncio
    async def test_empty_list(self, fresh_settings):
        resp = await api_profiles_list(make_mocked_request('GET', '/api/profiles'))
        assert resp.status == 200
        assert json.loads(resp.text)['profiles'] == []

    @pytest.mark.asyncio
    async def test_lists_existing(self, fresh_settings):
        p = ScriptingProfile()
        p.name = "ipad1-ios5"
        p.label = "iPad 1"
        p.matchDeviceType = "Tablet"
        p._serverVersion = 3
        fresh_settings.profiles["ipad1-ios5"] = p
        resp = await api_profiles_list(make_mocked_request('GET', '/api/profiles'))
        data = json.loads(resp.text)
        assert len(data['profiles']) == 1
        assert data['profiles'][0]['name'] == "ipad1-ios5"
        assert data['profiles'][0]['_serverVersion'] == 3


class TestProfilesCreate:
    def _post(self, body):
        req = make_mocked_request('POST', '/api/profiles')
        async def _json(): return body
        req.json = _json
        return req

    @pytest.mark.asyncio
    async def test_create_minimal(self, fresh_settings):
        resp = await api_profiles_create(self._post({"name": "p1"}))
        assert resp.status == 201
        assert "p1" in fresh_settings.profiles
        assert fresh_settings.profiles["p1"]._serverVersion == 1

    @pytest.mark.asyncio
    async def test_create_with_full_shape(self, fresh_settings):
        body = {
            "name": "ipad1-ios5",
            "label": "iPad 1 — iOS 5.1.1",
            "matchDeviceType": "Tablet",
            "scripts": {"login": "echo LOGIN_OK", "start": "echo START_OK"},
            "launch": {"method": "ssh-then-vnc", "vncPassword": "x",
                       "taps": [{"fbX": 945, "fbY": 671}]},
            "webclip": {"bundleId": "com.apple.webapp-X", "title": "MM"},
            "ssh": {"legacyCrypto": True, "user": "root", "keyPath": "~/.ssh/k"},
        }
        resp = await api_profiles_create(self._post(body))
        assert resp.status == 201
        data = json.loads(resp.text)
        assert data['profile']['scripts']['login'] == "echo LOGIN_OK"
        assert data['profile']['launch']['method'] == "ssh-then-vnc"

    @pytest.mark.asyncio
    async def test_create_requires_name(self, fresh_settings):
        resp = await api_profiles_create(self._post({"label": "x"}))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_create_rejects_duplicate(self, fresh_settings):
        fresh_settings.profiles["dup"] = ScriptingProfile()
        resp = await api_profiles_create(self._post({"name": "dup"}))
        assert resp.status == 409


class TestProfilesUpdateDelete:
    def _put(self, name, body, if_match=None):
        headers = {'If-Match': str(if_match)} if if_match is not None else {}
        req = make_mocked_request('PUT', f'/api/profiles/{name}',
                                  headers=headers,
                                  match_info={"name": name})
        async def _json(): return body
        req.json = _json
        return req

    @pytest.mark.asyncio
    async def test_update_with_if_match(self, fresh_settings):
        p = ScriptingProfile(); p.name = "x"; p._serverVersion = 3
        fresh_settings.profiles["x"] = p
        resp = await api_profiles_update(
            self._put("x", {"label": "renamed"}, if_match=3))
        assert resp.status == 200
        data = json.loads(resp.text)
        assert data['profile']['label'] == "renamed"
        assert data['profile']['_serverVersion'] == 4

    @pytest.mark.asyncio
    async def test_update_stale_412(self, fresh_settings):
        p = ScriptingProfile(); p.name = "x"; p._serverVersion = 5
        fresh_settings.profiles["x"] = p
        resp = await api_profiles_update(
            self._put("x", {"label": "renamed"}, if_match=2))
        assert resp.status == 412

    @pytest.mark.asyncio
    async def test_delete_unreferenced(self, fresh_settings):
        fresh_settings.profiles["x"] = ScriptingProfile()
        req = make_mocked_request('DELETE', '/api/profiles/x',
                                  match_info={"name": "x"})
        resp = await api_profiles_delete(req)
        assert resp.status == 204

    @pytest.mark.asyncio
    async def test_delete_referenced_409(self, fresh_settings):
        fresh_settings.profiles["pr"] = ScriptingProfile()
        c1 = Client(); c1.profileName = "pr"
        c2 = Client(); c2.profileName = "pr"
        c3 = Client(); c3.profileName = "other"
        fresh_settings.clients = {"c1": c1, "c2": c2, "c3": c3}
        req = make_mocked_request('DELETE', '/api/profiles/pr',
                                  match_info={"name": "pr"})
        resp = await api_profiles_delete(req)
        assert resp.status == 409
        assert set(json.loads(resp.text)['refs']) == {"c1", "c2"}


class TestClientsAssignProfile:
    def _post(self, ckey, body):
        req = make_mocked_request('POST',
                                  f'/api/clients/{ckey}/profile',
                                  match_info={"clientKey": ckey})
        async def _json(): return body
        req.json = _json
        return req

    @pytest.mark.asyncio
    async def test_assign(self, fresh_settings):
        fresh_settings.profiles["ipad1"] = ScriptingProfile()
        fresh_settings.clients["c1"] = Client()
        resp = await api_clients_assign_profile(
            self._post("c1", {"profileName": "ipad1"}))
        assert resp.status == 200
        assert fresh_settings.clients["c1"].profileName == "ipad1"

    @pytest.mark.asyncio
    async def test_clear(self, fresh_settings):
        fresh_settings.profiles["ipad1"] = ScriptingProfile()
        c = Client(); c.profileName = "ipad1"
        fresh_settings.clients["c1"] = c
        resp = await api_clients_assign_profile(
            self._post("c1", {"profileName": None}))
        assert resp.status == 200
        assert fresh_settings.clients["c1"].profileName is None

    @pytest.mark.asyncio
    async def test_unknown_client_404(self, fresh_settings):
        resp = await api_clients_assign_profile(
            self._post("ghost", {"profileName": "x"}))
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_unknown_profile_404(self, fresh_settings):
        fresh_settings.clients["c1"] = Client()
        resp = await api_clients_assign_profile(
            self._post("c1", {"profileName": "ghost"}))
        assert resp.status == 404
