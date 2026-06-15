# tests/unit/test_render_gc.py
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
from mosaicmesh.state import Settings, Display
from mosaicmesh import render as R


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev


def test_token_is_live_true_for_registry_token(fresh_settings):
    d = Display()
    fresh_settings.displays["G1"] = d
    R._set_render_state(d, "P", R.RENDER_READY, token="abc123abc123")
    assert R._token_is_live("abc123abc123") is True


def test_token_is_live_true_for_rendered_token(fresh_settings):
    d = Display()
    d.renderedToken = "deadbeef0000"
    fresh_settings.displays["G1"] = d
    assert R._token_is_live("deadbeef0000") is True


def test_token_is_live_false_for_unreferenced(fresh_settings):
    d = Display()
    R._set_render_state(d, "P", R.RENDER_READY, token="abc123abc123")
    fresh_settings.displays["G1"] = d
    assert R._token_is_live("999999999999") is False


def test_token_is_live_false_for_empty(fresh_settings):
    assert R._token_is_live("") is False
    assert R._token_is_live(None) is False


def _seed_asset(tmp_path, key, sub, name):
    d = tmp_path / "media" / key / sub
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text("x")
    return f


def test_delete_token_assets_removes_per_client_and_full(fresh_settings, tmp_path, monkeypatch):
    from mosaicmesh.state import Display, Client
    monkeypatch.chdir(tmp_path)
    d = Display(); fresh_settings.displays["G1"] = d
    c = Client(); c.displayID = "G1"
    fresh_settings.clients["c1"] = c
    tok = "aaaaaaaaaaaa"
    seg = _seed_asset(tmp_path, "c1", "videos", f"seg_{tok}_0.mp4")
    ind = _seed_asset(tmp_path, "c1", "images", f"ind_{tok}_1.png")
    full = _seed_asset(tmp_path, "server", "videos", f"full_{tok}_0.mp4")
    R._delete_token_assets(tok, "G1")
    assert not seg.exists()
    assert not ind.exists()
    assert not full.exists()


def test_delete_token_assets_leaves_other_token(fresh_settings, tmp_path, monkeypatch):
    from mosaicmesh.state import Display, Client
    monkeypatch.chdir(tmp_path)
    d = Display(); fresh_settings.displays["G1"] = d
    c = Client(); c.displayID = "G1"
    fresh_settings.clients["c1"] = c
    keep = _seed_asset(tmp_path, "c1", "videos", "seg_bbbbbbbbbbbb_0.mp4")
    R._delete_token_assets("aaaaaaaaaaaa", "G1")
    assert keep.exists()


import asyncio


def _calibrated_group(fresh_settings, did, ckey):
    from mosaicmesh.state import Display, Client
    d = Display(); d.boundingBox = [0, 0, 10, 10]
    fresh_settings.displays[did] = d
    c = Client(); c.displayID = did; c.deviceWidth = 100; c.deviceHeight = 100
    c.measuredPerimeter = [0, 0, 5, 0, 5, 5, 0, 5]
    fresh_settings.clients[ckey] = c
    return d


def test_rerender_deletes_previous_token_assets(fresh_settings, tmp_path, monkeypatch):
    from mosaicmesh.state import Playlist
    monkeypatch.chdir(tmp_path)
    d = _calibrated_group(fresh_settings, "G1", "c1")
    pl = Playlist(); pl.name = "P"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["P"] = pl
    # Pretend a previous render produced files under an OLD token (12 hex chars).
    old_tok = "0123456789ab"
    old = _seed_asset(tmp_path, "c1", "videos", f"seg_{old_tok}_0.mp4")
    R._set_render_state(d, "P", R.RENDER_READY, token=old_tok)

    async def _fake_encode(elements, did, token, progress_cb=None):
        if progress_cb: progress_cb(1, 1)
    monkeypatch.setattr(R, "_encode_group", _fake_encode)

    asyncio.run(R.render_playlist_for_group_async("P", "G1"))
    new_tok = R.render_token(R._build_media_elements(pl.items), "G1")
    assert d.renders["P"]["state"] == R.RENDER_READY
    assert d.renders["P"]["token"] == new_tok
    assert new_tok != old_tok
    assert not old.exists()                     # previous token's files reclaimed


def test_rerender_keeps_shared_old_token(fresh_settings, tmp_path, monkeypatch):
    from mosaicmesh.state import Playlist
    monkeypatch.chdir(tmp_path)
    d = _calibrated_group(fresh_settings, "G1", "c1")
    pl = Playlist(); pl.name = "P"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["P"] = pl
    old_tok = "abcdefabcdef"
    old = _seed_asset(tmp_path, "c1", "videos", f"seg_{old_tok}_0.mp4")
    R._set_render_state(d, "P", R.RENDER_READY, token=old_tok)
    # A SECOND playlist entry on the same group still references the old token.
    R._set_render_state(d, "Q", R.RENDER_READY, token=old_tok)

    async def _fake_encode(elements, did, token, progress_cb=None):
        if progress_cb: progress_cb(1, 1)
    monkeypatch.setattr(R, "_encode_group", _fake_encode)

    asyncio.run(R.render_playlist_for_group_async("P", "G1"))
    assert old.exists()                          # shared token still live -> NOT deleted


def test_failed_rerender_keeps_previous_assets(fresh_settings, tmp_path, monkeypatch):
    from mosaicmesh.state import Playlist
    monkeypatch.chdir(tmp_path)
    d = _calibrated_group(fresh_settings, "G1", "c1")
    pl = Playlist(); pl.name = "P"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["P"] = pl
    old_tok = "0123456789ab"
    old = _seed_asset(tmp_path, "c1", "videos", f"seg_{old_tok}_0.mp4")
    R._set_render_state(d, "P", R.RENDER_READY, token=old_tok)

    async def _boom(elements, did, token, progress_cb=None):
        raise RuntimeError("ffmpeg exploded")
    monkeypatch.setattr(R, "_encode_group", _boom)

    asyncio.run(R.render_playlist_for_group_async("P", "G1"))
    assert d.renders["P"]["state"] == R.RENDER_FAILED
    assert old.exists()                          # failed re-render must not delete old


def test_sweep_removes_only_orphan_tokens(fresh_settings, tmp_path, monkeypatch):
    from mosaicmesh.state import Display, Client
    monkeypatch.chdir(tmp_path)
    d = Display(); fresh_settings.displays["G1"] = d
    c = Client(); c.displayID = "G1"
    fresh_settings.clients["c1"] = c
    live_tok = "fedcbafedcba"          # 12 hex chars
    R._set_render_state(d, "P", R.RENDER_READY, token=live_tok)

    live = _seed_asset(tmp_path, "c1", "videos", f"seg_{live_tok}_0.mp4")
    orphan = _seed_asset(tmp_path, "c1", "videos", "seg_111111111111_0.mp4")
    orphan_full = _seed_asset(tmp_path, "server", "images", "full_111111111111_2.png")
    # Non-matching files must be untouched.
    upload = _seed_asset(tmp_path, "server", "videos", "myvideo.mp4")
    aruco = _seed_asset(tmp_path, "c1", "images", "aruco.png")

    removed = R.sweep_orphan_render_assets()

    assert live.exists()
    assert upload.exists()
    assert aruco.exists()
    assert not orphan.exists()
    assert not orphan_full.exists()
    assert removed == 2


def test_sweep_empty_media_is_noop(fresh_settings, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert R.sweep_orphan_render_assets() == 0
