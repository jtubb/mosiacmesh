# tests/unit/test_render_registry.py
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
from mosaicmesh.state import Settings, Display, migrate_client_objects
from mosaicmesh import render as R


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev


def test_new_display_has_empty_renders():
    assert Display().renders == {}


def test_render_state_constants():
    assert R.RENDER_QUEUED == "QUEUED"
    assert R.RENDER_RENDERING == "RENDERING"
    assert R.RENDER_READY == "READY"
    assert R.RENDER_STALE == "STALE"
    assert R.RENDER_FAILED == "FAILED"


def test_migration_backfills_renders(fresh_settings):
    d = Display()
    del d.renders            # simulate a Display loaded from a pre-feature settings.dat
    fresh_settings.displays["G1"] = d
    migrate_client_objects()
    assert fresh_settings.displays["G1"].renders == {}


def test_encode_group_callable_signature(fresh_settings):
    # _encode_group must accept (media_elements, display_id, token, progress_cb=None)
    import inspect
    sig = inspect.signature(R._encode_group)
    params = list(sig.parameters)
    assert params[:3] == ["media_elements", "display_id", "token"]
    assert "progress_cb" in params


def test_render_group_async_no_clients_returns_ready_token(fresh_settings):
    # A calibrated group with a single FULL (non-renderable) item: nothing to
    # encode, wrapper still returns ready + sets legacy fields.
    from mosaicmesh.state import Display, MediaElement, PlayMode
    import asyncio
    d = Display(); d.boundingBox = [0, 0, 10, 10]
    me = MediaElement(); me.id = 0; me.file = "/m/x.png"; me.playmode = PlayMode.FULL
    d.mediaElements = [me]
    fresh_settings.displays["G1"] = d
    out = asyncio.run(R.render_group_async("G1"))
    assert out["status"] == "ready"
    assert d.renderStatus == "ready"
    assert d.renderedToken == out["token"]


def test_render_playlist_for_group_sets_ready(fresh_settings, monkeypatch):
    from mosaicmesh.state import Display, Playlist, Client
    import asyncio
    d = Display(); d.boundingBox = [0, 0, 10, 10]
    fresh_settings.displays["G1"] = d
    c = Client(); c.displayID = "G1"; c.deviceWidth = 100; c.deviceHeight = 100
    c.measuredPerimeter = [0, 0, 5, 0, 5, 5, 0, 5]
    fresh_settings.clients["c1"] = c
    pl = Playlist(); pl.name = "P"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["P"] = pl

    async def _fake_encode(elements, did, token, progress_cb=None):
        if progress_cb: progress_cb(1, 1)
    monkeypatch.setattr(R, "_encode_group", _fake_encode)

    asyncio.run(R.render_playlist_for_group_async("P", "G1"))
    entry = d.renders["P"]
    assert entry["state"] == R.RENDER_READY
    assert entry["token"] == R.render_token(R._build_media_elements(pl.items), "G1")
    assert entry["percent"] == 100


def test_render_playlist_for_group_failed(fresh_settings, monkeypatch):
    from mosaicmesh.state import Display, Playlist, Client
    import asyncio
    d = Display(); d.boundingBox = [0, 0, 10, 10]
    fresh_settings.displays["G1"] = d
    c = Client(); c.displayID = "G1"; c.deviceWidth = 100; c.deviceHeight = 100
    c.measuredPerimeter = [0, 0, 5, 0, 5, 5, 0, 5]
    fresh_settings.clients["c1"] = c
    pl = Playlist(); pl.name = "P"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["P"] = pl

    async def _boom(elements, did, token, progress_cb=None):
        raise RuntimeError("ffmpeg exploded")
    monkeypatch.setattr(R, "_encode_group", _boom)

    asyncio.run(R.render_playlist_for_group_async("P", "G1"))
    entry = d.renders["P"]
    assert entry["state"] == R.RENDER_FAILED
    assert "ffmpeg exploded" in entry["error"]
    assert "percent" not in entry        # terminal FAILED must not show stale progress
    assert "eta" not in entry


def test_renders_snapshot_lists_entries(fresh_settings):
    from mosaicmesh.state import Display
    import asyncio
    d = Display()
    fresh_settings.displays["G1"] = d
    R._set_render_state(d, "P", R.RENDER_RENDERING, token="t", percent=42, eta=30)
    snap = R.renders_snapshot()
    row = next(r for r in snap if r["playlist"] == "P")
    assert row["displayID"] == "G1"
    assert row["state"] == "RENDERING"
    assert row["percent"] == 42


def test_revalidate_demotes_stale_token(fresh_settings):
    from mosaicmesh.state import Display, Playlist, Client
    d = Display(); d.boundingBox = [0, 0, 10, 10]
    fresh_settings.displays["G1"] = d
    c = Client(); c.displayID = "G1"; c.deviceWidth = 100; c.deviceHeight = 100
    c.measuredPerimeter = [0, 0, 5, 0, 5, 5, 0, 5]
    fresh_settings.clients["c1"] = c
    pl = Playlist(); pl.name = "P"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["P"] = pl
    # Persisted READY with a WRONG token (calibration changed while down).
    R._set_render_state(d, "P", R.RENDER_READY, token="staletoken")
    R.revalidate_renders_on_boot()
    assert d.renders["P"]["state"] == R.RENDER_STALE


def test_revalidate_resets_inflight(fresh_settings):
    from mosaicmesh.state import Display, Playlist, Client
    d = Display(); d.boundingBox = [0, 0, 10, 10]
    fresh_settings.displays["G1"] = d
    c = Client(); c.displayID = "G1"; c.deviceWidth = 100; c.deviceHeight = 100
    c.measuredPerimeter = [0, 0, 5, 0, 5, 5, 0, 5]
    fresh_settings.clients["c1"] = c
    pl = Playlist(); pl.name = "Q"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["Q"] = pl
    R._set_render_state(d, "Q", R.RENDER_RENDERING, token="t")
    R.revalidate_renders_on_boot()
    assert d.renders["Q"]["state"] == R.RENDER_STALE
