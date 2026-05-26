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
