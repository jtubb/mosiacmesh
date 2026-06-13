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
