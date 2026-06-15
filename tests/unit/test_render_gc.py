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
