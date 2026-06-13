# tests/unit/test_render_token.py
"""render_token / compute_render_token stability + readiness predicate."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import argparse
_orig = argparse.ArgumentParser.parse_args
class _MockArgs:
    Port = 3000
    Verbose = False
argparse.ArgumentParser.parse_args = lambda self, a=None, n=None: _MockArgs()
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

import pytest
from mosaicmesh.state import Settings, Display, Client, MediaElement, PlayMode
from mosaicmesh import render as R


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev


def _seg_elem(i=0, file="/media/server/videos/a.mp4"):
    me = MediaElement()
    me.id = i
    me.file = file
    me.duration = 5000
    me.playmode = PlayMode.SEGMENT
    return me


def _calibrated_group(settings, did="G1"):
    d = Display()
    d.boundingBox = [0, 0, 100, 100]
    settings.displays[did] = d
    c = Client()
    c.displayID = did
    c.deviceWidth = 1024
    c.deviceHeight = 768
    c.measuredPerimeter = [0, 0, 10, 0, 10, 10, 0, 10]
    settings.clients["c1"] = c
    return d


def test_render_token_matches_compute_for_applied(fresh_settings):
    d = _calibrated_group(fresh_settings)
    d.mediaElements = [_seg_elem()]
    assert R.render_token(d.mediaElements, "G1") == R.compute_render_token("G1")


def test_render_token_varies_with_items(fresh_settings):
    _calibrated_group(fresh_settings)
    t1 = R.render_token([_seg_elem(file="/media/server/videos/a.mp4")], "G1")
    t2 = R.render_token([_seg_elem(file="/media/server/videos/b.mp4")], "G1")
    assert t1 != t2


def test_render_token_empty_for_unknown_group(fresh_settings):
    assert R.render_token([_seg_elem()], "NOPE") == ""


def test_na_playlist_always_ready(fresh_settings):
    from mosaicmesh.state import Playlist
    _calibrated_group(fresh_settings)
    pl = Playlist(); pl.name = "P"; pl.items = [{"id": 0, "file": "/m/x.png", "playmode": "FULL"}]
    fresh_settings.playlists["P"] = pl
    assert R.is_playlist_ready("P", "G1") is True


def test_renderable_not_ready_without_entry(fresh_settings):
    from mosaicmesh.state import Playlist
    _calibrated_group(fresh_settings)
    pl = Playlist(); pl.name = "P"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["P"] = pl
    assert R.is_playlist_ready("P", "G1") is False


def test_renderable_ready_with_current_token(fresh_settings):
    from mosaicmesh.state import Playlist
    d = _calibrated_group(fresh_settings)
    pl = Playlist(); pl.name = "P"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["P"] = pl
    tok = R.render_token(R._build_media_elements(pl.items), "G1")
    R._set_render_state(d, "P", R.RENDER_READY, token=tok)
    assert R.is_playlist_ready("P", "G1") is True
    # stale token => not ready
    R._set_render_state(d, "P", R.RENDER_READY, token="deadbeef")
    assert R.is_playlist_ready("P", "G1") is False
