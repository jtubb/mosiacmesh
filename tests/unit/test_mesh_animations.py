# tests/unit/test_mesh_animations.py
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
from mosaicmesh.state import Settings, Display, Client, MediaElement, PlayMode
from mosaicmesh import render as R


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    server.socketmanager = MagicMock()
    yield server.settings
    server.settings = prev


def test_mediaelement_defaults_scriptspan_mirror():
    assert MediaElement().scriptSpan == 'mirror'


def test_display_defaults_meshglobal_none():
    assert Display().meshGlobal is None


def test_build_media_elements_reads_scriptspan():
    els = R._build_media_elements([
        {"id": "a", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh"},
        {"id": "b", "file": "plasma", "playmode": "SCRIPT"},  # no scriptSpan -> mirror
    ])
    assert els[0].scriptSpan == "mesh"
    assert els[1].scriptSpan == "mirror"


def test_media_item_payload_echoes_scriptspan():
    me = MediaElement(); me.id = "a"; me.file = "plasma"
    me.playmode = PlayMode.SCRIPT; me.duration = 1.0; me.scriptSpan = "mesh"
    assert R._media_item_payload(me)["scriptSpan"] == "mesh"


def test_media_item_payload_scriptspan_defaults_mirror_on_old_object():
    me = MediaElement(); me.id = "a"; me.file = "plasma"
    me.playmode = PlayMode.SCRIPT; me.duration = 1.0
    del me.scriptSpan  # simulate an object from an older settings.dat
    assert R._media_item_payload(me)["scriptSpan"] == "mirror"
