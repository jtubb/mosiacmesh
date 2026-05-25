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
