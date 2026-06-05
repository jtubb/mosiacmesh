"""Unit tests for the named-playlist store, CRUD, assign, and media API."""
import sys, json, os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import make_mocked_request

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import server
import jsonpickle


def _make_session(session_id="sess1"):
    s = MagicMock()
    s.id = session_id
    s.request = MagicMock()
    s.request.remote = "127.0.0.1"
    s.request.headers = {"User-Agent": "Test Browser"}
    return s


class TestDataModel:
    def test_playmode_has_individual(self):
        assert server.PlayMode.INDIVIDUAL.name == "INDIVIDUAL"

    def test_media_element_defaults(self):
        me = server.MediaElement()
        assert me.backgroundColor == "#000000"
        assert me.startEffect is None
        assert me.endEffect is None

    def test_playlist_round_trips_jsonpickle(self):
        pl = server.Playlist()
        pl.name = "Lobby"
        pl.items = [{"id": "a", "file": "/media/server/images/x.jpg",
                     "duration": 5, "playmode": "FULL",
                     "backgroundColor": "#222222", "startEffect": None, "endEffect": None}]
        pl.loop = True
        decoded = jsonpickle.decode(jsonpickle.encode(pl))
        assert decoded.name == "Lobby"
        assert decoded.loop is True
        assert decoded.items[0]["backgroundColor"] == "#222222"

    def test_settings_has_playlists(self):
        assert isinstance(server.Settings().playlists, dict)

    def test_migrate_backfills_playlists(self, mock_settings):
        del mock_settings.playlists          # simulate an older settings.dat
        server.settings = mock_settings
        server.migrate_client_objects()
        assert mock_settings.playlists == {}


class TestSetPlaylistFields:
    def test_setplaylist_stores_new_fields_with_defaults(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        client = server.Client()
        client.displayID = "Default"
        mock_settings.clients["c1"] = client
        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "SETPLAYLIST",
               "PAYLOAD": {"displayID": "Default", "loop": False, "items": [
                   {"id": "a", "file": "/media/server/images/x.jpg", "duration": 5},
                   {"id": "b", "file": "/media/server/images/y.jpg", "duration": 5,
                    "playmode": "FULL", "backgroundColor": "#abcdef",
                    "startEffect": "wipe", "endEffect": "fade"},
               ]}}
        server.msg_response(msg, _make_session())
        me0, me1 = mock_settings.displays["Default"].mediaElements
        assert me0.backgroundColor == "#000000"   # default applied
        assert me0.startEffect is None
        assert me1.backgroundColor == "#abcdef"
        assert me1.startEffect == "wipe"
        assert me1.endEffect == "fade"
        # PRELOAD broadcast carries normalized items (defaults applied)
        sent = jsonpickle.decode(server.socketmanager.broadcast.call_args[0][0])
        assert sent["PAYLOAD"]["items"][0]["backgroundColor"] == "#000000"
        assert sent["PAYLOAD"]["items"][1]["startEffect"] == "wipe"

    def test_setplaylist_maps_individual(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        client = server.Client(); client.displayID = "Default"
        mock_settings.clients["c1"] = client
        server.msg_response({"SRC": "a", "DEST": "SRV", "REQUEST": "SETPLAYLIST",
            "PAYLOAD": {"displayID": "Default", "loop": False, "items": [
                {"id": "a", "file": "/m/x.jpg", "duration": 5, "playmode": "INDIVIDUAL"}]}},
            _make_session())
        assert mock_settings.displays["Default"].mediaElements[0].playmode is server.PlayMode.INDIVIDUAL

    def test_play_payload_carries_new_fields(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        me = server.MediaElement()
        me.id = "a"; me.file = "/media/server/images/x.jpg"; me.duration = 1000
        me.playmode = server.PlayMode.FULL; me.backgroundColor = "#123456"
        disp.mediaElements = [me]
        client = server.Client(); client.displayID = "Default"
        mock_settings.clients["c1"] = client
        with patch("time.time", return_value=1000.0):
            server.msg_response({"SRC": "admin", "DEST": "SRV", "REQUEST": "PLAY",
                                 "PAYLOAD": {"displayID": "Default"}}, _make_session())
        sent = jsonpickle.decode(server.socketmanager.broadcast.call_args[0][0])
        item = sent["PAYLOAD"]["items"][0]
        assert item["backgroundColor"] == "#123456"
        assert item["startEffect"] is None and item["endEffect"] is None


class TestPlaylistCRUD:
    def _save(self, mock_settings, name="Lobby", loop=True):
        items = [{"id": "a", "file": "/media/server/images/x.jpg", "duration": 5,
                  "playmode": "SEGMENT", "backgroundColor": "#000000",
                  "startEffect": None, "endEffect": None}]
        server.msg_response({"SRC": "admin", "DEST": "SRV", "REQUEST": "SAVE_PLAYLIST",
            "PAYLOAD": {"name": name, "items": items, "loop": loop}}, _make_session())
        return items

    def test_save_then_get(self, mock_settings):
        server.settings = mock_settings
        items = self._save(mock_settings)
        assert "Lobby" in mock_settings.playlists
        resp = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "GET_PLAYLIST",
             "PAYLOAD": {"name": "Lobby"}}, _make_session()))
        assert resp["PAYLOAD"]["name"] == "Lobby"
        assert resp["PAYLOAD"]["loop"] is True
        assert resp["PAYLOAD"]["items"] == items

    def test_save_upserts(self, mock_settings):
        server.settings = mock_settings
        self._save(mock_settings, loop=True)
        self._save(mock_settings, loop=False)
        assert len(mock_settings.playlists) == 1
        assert mock_settings.playlists["Lobby"].loop is False

    def test_list_returns_summaries(self, mock_settings):
        server.settings = mock_settings
        self._save(mock_settings)
        resp = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "LIST_PLAYLISTS",
             "PAYLOAD": {}}, _make_session()))
        row = resp["PAYLOAD"][0]
        assert row == {"name": "Lobby", "itemCount": 1, "hasSegment": True}

    def test_get_unknown_returns_error(self, mock_settings):
        server.settings = mock_settings
        resp = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "GET_PLAYLIST",
             "PAYLOAD": {"name": "nope"}}, _make_session()))
        assert resp["PAYLOAD"] == {"error": "not found"}

    def test_save_requires_name(self, mock_settings):
        server.settings = mock_settings
        resp = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "SAVE_PLAYLIST",
             "PAYLOAD": {"name": "", "items": [], "loop": False}}, _make_session()))
        assert resp["PAYLOAD"] == {"error": "name required"}
        assert mock_settings.playlists == {}

    def test_delete(self, mock_settings):
        server.settings = mock_settings
        self._save(mock_settings)
        server.msg_response({"SRC": "a", "DEST": "SRV", "REQUEST": "DELETE_PLAYLIST",
            "PAYLOAD": {"name": "Lobby"}}, _make_session())
        assert "Lobby" not in mock_settings.playlists

    def test_list_hassegment_true_for_individual(self, mock_settings):
        server.settings = mock_settings
        items = [{"id": "a", "file": "/media/server/images/x.jpg", "duration": 5,
                  "playmode": "INDIVIDUAL", "backgroundColor": "#000000",
                  "startEffect": None, "endEffect": None}]
        server.msg_response({"SRC": "a", "DEST": "SRV", "REQUEST": "SAVE_PLAYLIST",
            "PAYLOAD": {"name": "Ind", "items": items, "loop": False}}, _make_session())
        resp = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "LIST_PLAYLISTS", "PAYLOAD": {}}, _make_session()))
        assert resp["PAYLOAD"][0]["hasSegment"] is True

    def test_list_tolerates_none_items(self, mock_settings):
        server.settings = mock_settings
        pl = server.Playlist(); pl.name = "Bad"; pl.items = None; pl.loop = False
        mock_settings.playlists["Bad"] = pl
        resp = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "LIST_PLAYLISTS",
             "PAYLOAD": {}}, _make_session()))
        assert resp["PAYLOAD"][0] == {"name": "Bad", "itemCount": 0, "hasSegment": False}


class TestAssignPlaylist:
    def _save(self, mock_settings, name, items, loop=False):
        server.msg_response({"SRC": "a", "DEST": "SRV", "REQUEST": "SAVE_PLAYLIST",
            "PAYLOAD": {"name": name, "items": items, "loop": loop}}, _make_session())

    def _assign(self, name, display_id="Default"):
        return jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "ASSIGN_PLAYLIST",
             "PAYLOAD": {"name": name, "displayID": display_id}}, _make_session()))

    def test_assign_ok_no_segment(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        self._save(mock_settings, "Imgs", [
            {"id": "a", "file": "/media/server/images/x.jpg", "duration": 5,
             "playmode": "FULL", "backgroundColor": "#000000",
             "startEffect": None, "endEffect": None}])
        resp = self._assign("Imgs")
        assert resp["PAYLOAD"]["status"] == "ok"
        assert len(mock_settings.displays["Default"].mediaElements) == 1
        assert mock_settings.displays["Default"].loop is False
        assert mock_settings.displays["Default"].renderedToken == ""

    def test_assign_segment_not_calibrated(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        mock_settings.displays["Default"].boundingBox = None
        self._save(mock_settings, "Seg", [
            {"id": "a", "file": "/media/server/videos/v.mp4", "duration": 5,
             "playmode": "SEGMENT", "backgroundColor": "#000000",
             "startEffect": None, "endEffect": None}])
        resp = self._assign("Seg")
        assert resp["PAYLOAD"]["status"] == "NOT_CALIBRATED"

    def test_assign_segment_render_required(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        disp.boundingBox = [[0, 0], [10, 0], [10, 10], [0, 10]]
        disp.renderedToken = "stale"
        self._save(mock_settings, "Seg", [
            {"id": "a", "file": "/media/server/videos/v.mp4", "duration": 5,
             "playmode": "SEGMENT", "backgroundColor": "#000000",
             "startEffect": None, "endEffect": None}])
        resp = self._assign("Seg")
        assert resp["PAYLOAD"]["status"] == "RENDER_REQUIRED"

    def test_assign_unknown_playlist(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        resp = self._assign("ghost")
        assert resp["PAYLOAD"]["status"] == "error"


class TestMediaApi:
    @pytest.mark.asyncio
    async def test_api_media_lists_images_and_videos(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("media/server/images"); os.makedirs("media/server/videos")
        open("media/server/images/a.jpg", "w").close()
        open("media/server/videos/b.mp4", "w").close()
        resp = await server.api_media(make_mocked_request('GET', '/api/media'))
        data = json.loads(resp.text)
        assert "/media/server/images/a.jpg" in data["images"]
        assert "/media/server/videos/b.mp4" in data["videos"]

    @pytest.mark.asyncio
    async def test_api_media_missing_dirs_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        resp = await server.api_media(make_mocked_request('GET', '/api/media'))
        data = json.loads(resp.text)
        assert data["images"] == [] and data["videos"] == []
        assert data.get("videoDurations") == {}

    def test_process_video_moves_into_library(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs("cache")
        open("cache/clip.mp4", "w").close()
        server.processVideo("cache", "clip.mp4")
        assert os.path.exists("media/server/videos/clip.mp4")


class TestDurationUnits:
    """Durations are authored in seconds but the client/effects consume ms."""

    def test_duration_ms_converts_seconds(self):
        me = server.MediaElement(); me.duration = 5
        assert server._duration_ms(me) == 5000

    def test_duration_ms_fractional(self):
        me = server.MediaElement(); me.duration = 596.5
        assert server._duration_ms(me) == 596500

    def test_duration_ms_none_is_zero(self):
        me = server.MediaElement(); me.duration = None
        assert server._duration_ms(me) == 0

    def test_payload_duration_is_milliseconds(self):
        me = server.MediaElement()
        me.id = "i1"; me.file = "/media/server/videos/v.mp4"; me.duration = 10
        me.playmode = server.PlayMode.FULL
        assert server._media_item_payload(me)["duration"] == 10000


class TestEffectsApi:
    @pytest.mark.asyncio
    async def test_api_effects_lists_registered(self):
        resp = await server.api_effects(make_mocked_request('GET', '/api/effects'))
        data = json.loads(resp.text)
        names = {e["name"] for e in data["effects"]}
        assert {"fade", "audiofade", "wipe"} <= names
        fade = next(e for e in data["effects"] if e["name"] == "fade")
        assert fade["params"][0]["key"] == "duration"
