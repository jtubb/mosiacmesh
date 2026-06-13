# tests/unit/test_render_gating.py
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
from unittest.mock import MagicMock
from mosaicmesh.state import Settings, Display, Playlist, Client
from mosaicmesh import render as R


# Reuse the mock-session shape msg_response needs (.id, .request.remote,
# .request.headers["User-Agent"]). Copied from tests/unit/test_render_triggers.py.
def _MockSession():
    s = MagicMock(); s.id = "s"; s.request = MagicMock()
    s.request.remote = "127.0.0.1"; s.request.headers = {"User-Agent": "T"}
    return s


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev


def _calibrated_group_with_seg_playlist(settings):
    d = Display(); d.boundingBox = [0, 0, 10, 10]
    settings.displays["G1"] = d
    c = Client(); c.displayID = "G1"; c.deviceWidth = 100; c.deviceHeight = 100
    c.measuredPerimeter = [0, 0, 5, 0, 5, 5, 0, 5]
    settings.clients["c1"] = c
    pl = Playlist(); pl.name = "P"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    settings.playlists["P"] = pl
    return d, pl


def test_play_rejects_unready(fresh_settings):
    d, pl = _calibrated_group_with_seg_playlist(fresh_settings)
    R._apply_playlist("G1", pl)   # applied but never rendered
    import jsonpickle
    out = jsonpickle.decode(server.msg_response(
        {"REQUEST": "PLAY", "PAYLOAD": {"displayID": "G1"},
         "SRC": "a", "DEST": "SRV"}, _MockSession()))
    assert out["PAYLOAD"]["status"] == "RENDER_REQUIRED"


def test_play_allows_ready(fresh_settings, monkeypatch):
    d, pl = _calibrated_group_with_seg_playlist(fresh_settings)
    R._apply_playlist("G1", pl)
    tok = R.render_token(R._build_media_elements(pl.items), "G1")
    R._set_render_state(d, "P", R.RENDER_READY, token=tok)
    R._apply_playlist("G1", pl)   # re-apply to sync renderedToken from READY entry
    started = []
    # legacy.py imports _begin_prepare directly at module level
    # ("from mosaicmesh.render import ... _begin_prepare"), so patching
    # mosaicmesh.render._begin_prepare does NOT intercept calls in legacy.py;
    # we must patch the name in legacy's own namespace.
    monkeypatch.setattr("mosaicmesh.websocket.legacy._begin_prepare",
                        lambda did: started.append(did))
    import jsonpickle
    out = jsonpickle.decode(server.msg_response(
        {"REQUEST": "PLAY", "PAYLOAD": {"displayID": "G1"},
         "SRC": "a", "DEST": "SRV"}, _MockSession()))
    assert out["PAYLOAD"] == "SUCCESS"
    assert started == ["G1"]


def test_assign_reports_render_required(fresh_settings):
    d, pl = _calibrated_group_with_seg_playlist(fresh_settings)
    import jsonpickle
    out = jsonpickle.decode(server.msg_response(
        {"REQUEST": "ASSIGN_PLAYLIST", "PAYLOAD": {"displayID": "G1", "name": "P"},
         "SRC": "a", "DEST": "SRV"}, _MockSession()))
    assert out["PAYLOAD"]["status"] == "RENDER_REQUIRED"
