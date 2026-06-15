"""Unit tests for /api/schedules CRUD + validation."""
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

from mosaicmesh.state import Settings, Schedule, Playlist, Display
from mosaicmesh.api.schedules import (
    api_schedules_list, api_schedules_create,
    api_schedules_update, api_schedules_delete,
)


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    server.settings.playlists["Morning"] = Playlist()
    server.settings.playlists["Morning"].name = "Morning"
    server.settings.displays["Lobby"] = Display()
    yield server.settings
    server.settings = prev


class TestSchedulesList:
    @pytest.mark.asyncio
    async def test_empty_list(self, fresh_settings):
        resp = await api_schedules_list(make_mocked_request('GET', '/api/schedules'))
        assert resp.status == 200
        assert json.loads(resp.text)['schedules'] == []

    @pytest.mark.asyncio
    async def test_lists_existing(self, fresh_settings):
        s = Schedule()
        s.id = "abc-123"
        s.playlistName = "Morning"
        s.displayID = "Lobby"
        s.freq = "WEEKLY"
        s.byweekday = [0, 1, 2, 3, 4]
        s.startTime = "08:00"
        s.endTime = "11:00"
        s._serverVersion = 2
        fresh_settings.schedules[s.id] = s
        resp = await api_schedules_list(make_mocked_request('GET', '/api/schedules'))
        data = json.loads(resp.text)
        assert len(data['schedules']) == 1
        out = data['schedules'][0]
        assert out['id'] == "abc-123"
        assert out['_serverVersion'] == 2
        assert out['byweekday'] == [0, 1, 2, 3, 4]


class TestSchedulesCreate:
    def _post(self, body):
        req = make_mocked_request('POST', '/api/schedules')
        async def _json(): return body
        req.json = _json
        return req

    @pytest.mark.asyncio
    async def test_minimal_create(self, fresh_settings):
        resp = await api_schedules_create(self._post(
            {"playlistName": "Morning", "displayID": "Lobby"}))
        assert resp.status == 201
        data = json.loads(resp.text)
        sid = data['schedule']['id']
        assert sid and len(sid) == 16
        assert data['schedule']['_serverVersion'] == 1
        assert sid in fresh_settings.schedules

    @pytest.mark.asyncio
    async def test_missing_playlist_400(self, fresh_settings):
        resp = await api_schedules_create(self._post({"displayID": "Lobby"}))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_missing_display_400(self, fresh_settings):
        resp = await api_schedules_create(self._post({"playlistName": "Morning"}))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_unknown_playlist_400(self, fresh_settings):
        resp = await api_schedules_create(self._post(
            {"playlistName": "Ghost", "displayID": "Lobby"}))
        assert resp.status == 400
        assert "Ghost" in json.loads(resp.text)['error']

    @pytest.mark.asyncio
    async def test_unknown_display_400(self, fresh_settings):
        resp = await api_schedules_create(self._post(
            {"playlistName": "Morning", "displayID": "Ghost"}))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_invalid_freq_400(self, fresh_settings):
        resp = await api_schedules_create(self._post(
            {"playlistName": "Morning", "displayID": "Lobby", "freq": "HOURLY"}))
        assert resp.status == 400
        assert "HOURLY" in json.loads(resp.text)['error']

    @pytest.mark.asyncio
    async def test_invalid_time_400(self, fresh_settings):
        resp = await api_schedules_create(self._post(
            {"playlistName": "Morning", "displayID": "Lobby",
             "startTime": "25:00"}))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_invalid_byweekday_400(self, fresh_settings):
        resp = await api_schedules_create(self._post(
            {"playlistName": "Morning", "displayID": "Lobby",
             "freq": "WEEKLY", "byweekday": [0, 7]}))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_end_until_roundtrip(self, fresh_settings):
        end = {"type": "until", "untilDate": "2026-12-31"}
        resp = await api_schedules_create(self._post(
            {"playlistName": "Morning", "displayID": "Lobby", "end": end}))
        assert resp.status == 201
        data = json.loads(resp.text)
        assert data['schedule']['end'] == end

    @pytest.mark.asyncio
    async def test_end_count_roundtrip(self, fresh_settings):
        end = {"type": "count", "count": 5}
        resp = await api_schedules_create(self._post(
            {"playlistName": "Morning", "displayID": "Lobby", "end": end}))
        assert resp.status == 201
        data = json.loads(resp.text)
        assert data['schedule']['end'] == end

    @pytest.mark.asyncio
    async def test_end_until_missing_date_400(self, fresh_settings):
        resp = await api_schedules_create(self._post(
            {"playlistName": "Morning", "displayID": "Lobby",
             "end": {"type": "until"}}))
        assert resp.status == 400
        assert "untilDate" in json.loads(resp.text)['error']

    @pytest.mark.asyncio
    async def test_end_count_missing_count_400(self, fresh_settings):
        resp = await api_schedules_create(self._post(
            {"playlistName": "Morning", "displayID": "Lobby",
             "end": {"type": "count"}}))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_end_unknown_type_400(self, fresh_settings):
        resp = await api_schedules_create(self._post(
            {"playlistName": "Morning", "displayID": "Lobby",
             "end": {"type": "forever"}}))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_apply_fields_does_not_alias_end_or_byweekday(self, fresh_settings):
        end_in = {"type": "count", "count": 3}
        bwd_in = [0, 2, 4]
        resp = await api_schedules_create(self._post(
            {"playlistName": "Morning", "displayID": "Lobby",
             "freq": "WEEKLY", "byweekday": bwd_in, "end": end_in}))
        assert resp.status == 201
        sid = json.loads(resp.text)['schedule']['id']
        s = fresh_settings.schedules[sid]
        # mutate the caller's dict/list; the stored Schedule must be unaffected
        end_in["count"] = 999
        bwd_in.append(6)
        assert s.end == {"type": "count", "count": 3}
        assert s.byweekday == [0, 2, 4]


class TestSchedulesUpdate:
    def _put(self, sid, body, if_match=None):
        headers = {'If-Match': str(if_match)} if if_match is not None else {}
        req = make_mocked_request('PUT', f'/api/schedules/{sid}',
                                  headers=headers,
                                  match_info={"id": sid})
        async def _json(): return body
        req.json = _json
        return req

    @pytest.mark.asyncio
    async def test_update_partial_with_if_match(self, fresh_settings):
        s = Schedule(); s.id = "abc"; s._serverVersion = 1
        s.playlistName = "Morning"; s.displayID = "Lobby"
        fresh_settings.schedules["abc"] = s
        resp = await api_schedules_update(
            self._put("abc", {"enabled": False}, if_match=1))
        assert resp.status == 200
        data = json.loads(resp.text)
        assert data['schedule']['enabled'] is False
        assert data['schedule']['_serverVersion'] == 2

    @pytest.mark.asyncio
    async def test_update_stale_412(self, fresh_settings):
        s = Schedule(); s.id = "abc"; s._serverVersion = 5
        fresh_settings.schedules["abc"] = s
        resp = await api_schedules_update(
            self._put("abc", {"enabled": False}, if_match=2))
        assert resp.status == 412

    @pytest.mark.asyncio
    async def test_update_missing_if_match_428(self, fresh_settings):
        s = Schedule(); s.id = "abc"; s._serverVersion = 1
        fresh_settings.schedules["abc"] = s
        resp = await api_schedules_update(self._put("abc", {"enabled": False}))
        assert resp.status == 428

    @pytest.mark.asyncio
    async def test_update_unknown_playlist_400(self, fresh_settings):
        s = Schedule(); s.id = "abc"; s._serverVersion = 1
        s.playlistName = "Morning"; s.displayID = "Lobby"
        fresh_settings.schedules["abc"] = s
        resp = await api_schedules_update(
            self._put("abc", {"playlistName": "Ghost"}, if_match=1))
        assert resp.status == 400


class TestSchedulesDelete:
    @pytest.mark.asyncio
    async def test_delete_existing(self, fresh_settings):
        s = Schedule(); s.id = "abc"
        fresh_settings.schedules["abc"] = s
        req = make_mocked_request('DELETE', '/api/schedules/abc',
                                  match_info={"id": "abc"})
        resp = await api_schedules_delete(req)
        assert resp.status == 204
        assert "abc" not in fresh_settings.schedules

    @pytest.mark.asyncio
    async def test_delete_missing_404(self, fresh_settings):
        req = make_mocked_request('DELETE', '/api/schedules/nope',
                                  match_info={"id": "nope"})
        resp = await api_schedules_delete(req)
        assert resp.status == 404
