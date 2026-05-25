"""Unit tests for synchronized playback (playlist_index math + WS handlers)."""
import sys
from pathlib import Path
import argparse

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Import server cleanly (arg parsing is under __main__, so no patch needed)
import server


class TestPlaylistIndex:
    def test_empty_playlist_returns_none(self):
        assert server.playlist_index(0, [], False) is None

    def test_zero_total_duration_returns_none(self):
        assert server.playlist_index(100, [0, 0], False) is None

    def test_first_item(self):
        assert server.playlist_index(0, [1000, 2000], False) == {"index": 0, "offsetMs": 0}

    def test_within_second_item(self):
        assert server.playlist_index(1000, [1000, 2000], False) == {"index": 1, "offsetMs": 0}
        assert server.playlist_index(2500, [1000, 2000], False) == {"index": 1, "offsetMs": 1500}

    def test_non_loop_past_end_returns_none(self):
        assert server.playlist_index(3000, [1000, 2000], False) is None

    def test_loop_wraps(self):
        assert server.playlist_index(3000, [1000, 2000], True) == {"index": 0, "offsetMs": 0}
        assert server.playlist_index(4200, [1000, 2000], True) == {"index": 1, "offsetMs": 200}

    def test_negative_elapsed_clamps_to_start(self):
        assert server.playlist_index(-50, [1000, 2000], False) == {"index": 0, "offsetMs": 0}


import pytest
from unittest.mock import MagicMock


def _make_session(session_id="sess1"):
    s = MagicMock()
    s.id = session_id
    s.request = MagicMock()
    s.request.remote = "127.0.0.1"
    s.request.headers = {"User-Agent": "Test Browser"}
    return s


class TestSetPlaylist:
    def test_setplaylist_stores_items_and_broadcasts_preload(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        client = server.Client()
        client.displayID = "Default"
        mock_settings.clients["c1"] = client

        msg = {
            "SRC": "admin", "DEST": "SRV", "REQUEST": "SETPLAYLIST",
            "PAYLOAD": {
                "displayID": "Default",
                "loop": True,
                "items": [
                    {"id": "a", "file": "/media/server/a.jpg", "duration": 1000},
                    {"id": "b", "file": "/media/server/b.jpg", "duration": 2000},
                ],
            },
        }
        server.msg_response(msg, _make_session())

        disp = mock_settings.displays["Default"]
        assert disp.loop is True
        assert len(disp.mediaElements) == 2
        assert disp.mediaElements[0].file == "/media/server/a.jpg"
        assert disp.mediaElements[1].duration == 2000
        assert server.socketmanager.broadcast.call_count == 1
