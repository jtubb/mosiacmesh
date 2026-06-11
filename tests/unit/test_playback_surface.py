"""Playback-state surface: Display.currentPlaylistName, /api/playback, mapping, broadcast."""
import json
import jsonpickle
from unittest.mock import MagicMock, patch

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import server
from mosaicmesh.state import Display, PlayState


class TestCurrentPlaylistNameField:
    def test_fresh_display_has_none(self):
        d = Display()
        assert d.currentPlaylistName is None

    def test_survives_jsonpickle_roundtrip(self):
        d = Display()
        d.currentPlaylistName = "Lunch Menu"
        d2 = jsonpickle.decode(jsonpickle.encode(d))
        assert d2.currentPlaylistName == "Lunch Menu"


class TestPlaybackStateMapping:
    def _disp(self, action, playlist="P", media=True):
        d = Display()
        d.action = action
        d.currentPlaylistName = playlist
        d.mediaElements = [object()] if media else []
        d.playStartEpoch = 123
        d.renderStatus = "ready"
        return d

    def test_play_is_playing(self):
        assert server._playback_state(self._disp(PlayState.PLAY)) == "playing"

    def test_preparing_is_playing(self):
        assert server._playback_state(self._disp(PlayState.PREPARING)) == "playing"

    def test_pause_is_paused(self):
        assert server._playback_state(self._disp(PlayState.PAUSE)) == "paused"

    def test_stop_with_playlist_is_stopped(self):
        assert server._playback_state(self._disp(PlayState.STOP)) == "stopped"

    def test_noaction_without_playlist_is_idle(self):
        assert server._playback_state(self._disp(PlayState.NOACTION, playlist=None)) == "idle"

    def test_row_shape(self):
        row = server._playback_row("Lobby", self._disp(PlayState.PLAY))
        assert row == {
            "displayID": "Lobby", "state": "playing",
            "currentPlaylist": "P", "startedEpoch": 123, "renderStatus": "ready",
        }


class TestSetClearPlaylistName:
    def _settings_with_group(self, display_id="Lobby"):
        s = server.Settings()
        s.displays[display_id] = Display()
        return s

    def test_apply_playlist_sets_name(self):
        from mosaicmesh.render import _apply_playlist
        from mosaicmesh.state import Playlist
        server.settings = self._settings_with_group()
        pl = Playlist()
        pl.name = "Lunch Menu"
        pl.items = []
        pl.loop = True
        _apply_playlist("Lobby", pl)
        assert server.settings.displays["Lobby"].currentPlaylistName == "Lunch Menu"

    def test_stop_clears_name(self):
        from mosaicmesh.render import _stop_group_playback
        server.settings = self._settings_with_group()
        server.settings.displays["Lobby"].currentPlaylistName = "Lunch Menu"
        _stop_group_playback("Lobby")
        assert server.settings.displays["Lobby"].currentPlaylistName is None


import pytest
from aiohttp.test_utils import make_mocked_request


class TestApiPlayback:
    @pytest.mark.asyncio
    async def test_returns_group_rows(self):
        s = server.Settings()
        d = Display()
        d.action = PlayState.PLAY
        d.currentPlaylistName = "Lunch Menu"
        d.playStartEpoch = 999
        d.mediaElements = [object()]
        s.displays["Lobby"] = d
        s.displays["Idle Group"] = Display()  # NOACTION + no playlist -> idle
        server.settings = s

        req = make_mocked_request("GET", "/api/playback")
        resp = await server.api_playback(req)
        assert resp.status == 200
        body = json.loads(resp.text)
        assert body["success"] is True
        rows = {r["displayID"]: r for r in body["groups"]}
        assert rows["Lobby"]["state"] == "playing"
        assert rows["Lobby"]["currentPlaylist"] == "Lunch Menu"
        assert rows["Lobby"]["startedEpoch"] == 999
        assert rows["Idle Group"]["state"] == "idle"
