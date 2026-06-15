"""Unit tests for /api/displays — display-group CRUD (PR-12).

Display groups used to be invisible to the admin UI when they had zero
online clients (the discovery endpoint enumerated clients, not groups).
PR-12 introduces /api/displays as a first-class read + write surface;
these tests cover the list/create/delete behaviour including the 409+
refs path that protects in-use groups.
"""
import json
import pytest
from aiohttp.test_utils import make_mocked_request

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import server
from mosaicmesh.state import Settings, Display, Client, Schedule
from mosaicmesh.api.displays import (
    api_displays_list, api_displays_create, api_displays_delete,
)


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev


def _make_client(display_id, online=True):
    c = Client()
    c.displayID = display_id
    c.isOnline = online
    return c


def _make_schedule(sid, display_id):
    s = Schedule()
    s.id = sid
    s.displayID = display_id
    s.playlistName = "pl"
    s.startTime = "09:00"
    s.endTime = "10:00"
    return s


class TestDisplaysList:
    @pytest.mark.asyncio
    async def test_empty(self, fresh_settings):
        resp = await api_displays_list(make_mocked_request('GET', '/api/displays'))
        assert resp.status == 200
        assert json.loads(resp.text) == {"success": True, "displays": []}

    @pytest.mark.asyncio
    async def test_shows_group_with_zero_clients(self, fresh_settings):
        """The whole point of PR-12: a group with no clients is visible."""
        fresh_settings.displays["Lobby"] = Display()
        resp = await api_displays_list(make_mocked_request('GET', '/api/displays'))
        data = json.loads(resp.text)
        assert len(data['displays']) == 1
        d = data['displays'][0]
        assert d['displayID'] == "Lobby"
        assert d['clientCount'] == 0
        assert d['onlineCount'] == 0
        assert d['scheduleCount'] == 0
        assert d['clients'] == []

    @pytest.mark.asyncio
    async def test_counts_clients_and_schedules(self, fresh_settings):
        fresh_settings.displays["Tablet"] = Display()
        fresh_settings.clients["c1"] = _make_client("Tablet", online=True)
        fresh_settings.clients["c2"] = _make_client("Tablet", online=False)
        fresh_settings.clients["c3"] = _make_client("Other", online=True)
        # c1 is calibrated (has a measured perimeter), c2 is not.
        fresh_settings.clients["c1"].measuredPerimeter = [[0, 0], [1, 0], [1, 1], [0, 1]]
        fresh_settings.schedules["s1"] = _make_schedule("s1", "Tablet")
        fresh_settings.schedules["s2"] = _make_schedule("s2", "Tablet")
        fresh_settings.schedules["s3"] = _make_schedule("s3", "Other")
        resp = await api_displays_list(make_mocked_request('GET', '/api/displays'))
        data = json.loads(resp.text)
        tablet = next(d for d in data['displays'] if d['displayID'] == "Tablet")
        assert tablet['clientCount'] == 2
        assert tablet['onlineCount'] == 1
        assert tablet['calibratedCount'] == 1   # only c1 has a measured perimeter
        assert tablet['scheduleCount'] == 2
        assert set(tablet['clients']) == {"c1", "c2"}


class TestDisplaysCreate:
    def _post(self, body):
        req = make_mocked_request('POST', '/api/displays')
        async def _json(): return body
        req.json = _json
        return req

    @pytest.mark.asyncio
    async def test_create_minimal(self, fresh_settings):
        resp = await api_displays_create(self._post({"displayID": "Lobby"}))
        assert resp.status == 201
        assert "Lobby" in fresh_settings.displays
        data = json.loads(resp.text)
        assert data['display']['displayID'] == "Lobby"
        assert data['display']['clientCount'] == 0

    @pytest.mark.asyncio
    async def test_trims_whitespace(self, fresh_settings):
        resp = await api_displays_create(self._post({"displayID": "  Foyer  "}))
        assert resp.status == 201
        assert "Foyer" in fresh_settings.displays

    @pytest.mark.asyncio
    async def test_missing_displayID_400(self, fresh_settings):
        resp = await api_displays_create(self._post({}))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_empty_displayID_400(self, fresh_settings):
        resp = await api_displays_create(self._post({"displayID": "  "}))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_non_string_displayID_400(self, fresh_settings):
        resp = await api_displays_create(self._post({"displayID": 42}))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_duplicate_409(self, fresh_settings):
        fresh_settings.displays["Lobby"] = Display()
        resp = await api_displays_create(self._post({"displayID": "Lobby"}))
        assert resp.status == 409


class TestDisplaysDelete:
    def _delete(self, display_id):
        req = make_mocked_request('DELETE', f'/api/displays/{display_id}',
                                  match_info={"displayID": display_id})
        return req

    @pytest.mark.asyncio
    async def test_delete_empty_group(self, fresh_settings):
        fresh_settings.displays["Lobby"] = Display()
        resp = await api_displays_delete(self._delete("Lobby"))
        assert resp.status == 204
        assert "Lobby" not in fresh_settings.displays

    @pytest.mark.asyncio
    async def test_delete_missing_404(self, fresh_settings):
        resp = await api_displays_delete(self._delete("ghost"))
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_delete_with_clients_409(self, fresh_settings):
        fresh_settings.displays["Tablet"] = Display()
        fresh_settings.clients["c1"] = _make_client("Tablet")
        resp = await api_displays_delete(self._delete("Tablet"))
        assert resp.status == 409
        data = json.loads(resp.text)
        assert data['refs']['clients'] == ["c1"]
        assert data['refs']['schedules'] == []
        # Group still present
        assert "Tablet" in fresh_settings.displays

    @pytest.mark.asyncio
    async def test_delete_with_schedules_409(self, fresh_settings):
        fresh_settings.displays["Tablet"] = Display()
        fresh_settings.schedules["s1"] = _make_schedule("s1", "Tablet")
        resp = await api_displays_delete(self._delete("Tablet"))
        assert resp.status == 409
        data = json.loads(resp.text)
        assert data['refs']['clients'] == []
        assert data['refs']['schedules'] == ["s1"]

    @pytest.mark.asyncio
    async def test_delete_with_both_refs_409(self, fresh_settings):
        fresh_settings.displays["Tablet"] = Display()
        fresh_settings.clients["c1"] = _make_client("Tablet")
        fresh_settings.schedules["s1"] = _make_schedule("s1", "Tablet")
        resp = await api_displays_delete(self._delete("Tablet"))
        assert resp.status == 409
        data = json.loads(resp.text)
        assert data['refs']['clients'] == ["c1"]
        assert data['refs']['schedules'] == ["s1"]
        assert "client(s)" in data['error']
        assert "schedule(s)" in data['error']
