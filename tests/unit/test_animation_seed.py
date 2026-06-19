# tests/unit/test_animation_seed.py
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
from unittest.mock import MagicMock, patch
from mosaicmesh.state import Settings, Display, Client, MediaElement, PlayMode, PlayState
from mosaicmesh import render as R


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    server.socketmanager = MagicMock()
    yield server.settings
    server.settings = prev


def _synced_group(fresh_settings, did="G1"):
    d = Display(); d.boundingBox = [0, 0, 10, 10]
    me = MediaElement(); me.id = "a"; me.file = "bouncingBalls"; me.playmode = PlayMode.SCRIPT
    me.duration = 1000
    d.mediaElements = [me]
    fresh_settings.displays[did] = d
    c = Client(); c.displayID = did; c.isOnline = True; c.synced = True
    fresh_settings.clients["c1"] = c
    return d, c


def test_begin_prepare_mints_32bit_seed(fresh_settings):
    d, _ = _synced_group(fresh_settings)
    with patch.object(server, "broadcast_to_client"):
        server._begin_prepare("G1")
    assert isinstance(d.playSeed, int)
    assert 0 <= d.playSeed < 2**32
    assert d.playSeed != 0


def test_prepare_payload_carries_seed(fresh_settings):
    d, _ = _synced_group(fresh_settings)
    with patch.object(server, "broadcast_to_client") as bc:
        server._begin_prepare("G1")
    payload = bc.call_args[0][1]["PAYLOAD"]
    assert payload["seed"] == d.playSeed


def test_play_payload_carries_seed(fresh_settings):
    d, _ = _synced_group(fresh_settings)
    d.playSeed = 0xABCD1234
    with patch.object(server, "broadcast_to_display_group") as bg:
        R._start_group_playback("G1")
    assert bg.call_args[0][1]["PAYLOAD"]["seed"] == 0xABCD1234


def test_start_group_playback_does_not_remint(fresh_settings):
    d, _ = _synced_group(fresh_settings)
    d.playSeed = 555
    d.action = PlayState.PAUSE; d.pauseOffset = 0
    with patch.object(server, "broadcast_to_display_group"):
        R._start_group_playback("G1")
    assert d.playSeed == 555


def test_late_join_play_carries_seed(fresh_settings):
    from mosaicmesh.api.discovery import sync_new_client_to_group
    d, c = _synced_group(fresh_settings)
    d.playSeed = 0x0BADF00D
    d.action = PlayState.PLAY
    with patch("mosaicmesh.api.discovery.broadcast_to_client") as bc:
        sync_new_client_to_group("c1", c)
    play = [call.args[1] for call in bc.call_args_list if call.args[1]["REQUEST"] == "PLAY"][0]
    assert play["PAYLOAD"]["seed"] == 0x0BADF00D
