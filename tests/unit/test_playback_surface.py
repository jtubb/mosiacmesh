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
