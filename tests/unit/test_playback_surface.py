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
