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
