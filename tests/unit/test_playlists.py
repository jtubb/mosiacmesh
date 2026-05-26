"""Unit tests for the named-playlist store, CRUD, assign, and media API."""
import sys, json
from pathlib import Path
from unittest.mock import MagicMock, patch

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
