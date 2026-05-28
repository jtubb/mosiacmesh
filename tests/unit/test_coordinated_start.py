import sys, time
from pathlib import Path
import argparse
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
_orig = argparse.ArgumentParser.parse_args
argparse.ArgumentParser.parse_args = lambda self, *a, **k: argparse.Namespace(Port=3000, Verbose=False)
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig


def test_prepare_state_and_constants_exist():
    assert hasattr(server.PlayState, "PREPARING")
    assert server.RELEASE_LEAD_MS > 0
    assert server.PREPARE_TIMEOUT_MS > 0
    d = server.Display()
    assert d.prepareId is None
    assert d.readyClients == set()
    assert d.prepareDeadline == 0


def _display_with_items(server, display_id="g1", n=2):
    server.settings = server.Settings()
    disp = server.Display()
    disp.mediaElements = [server.MediaElement() for _ in range(n)]
    for me in disp.mediaElements:
        me.duration = 1000
    server.settings.displays[display_id] = disp
    return disp

def test_begin_prepare_broadcasts_prepare_and_sets_state():
    disp = _display_with_items(server)
    with patch.object(server, "broadcast_to_display_group") as bc:
        server._begin_prepare("g1")
    assert disp.action == server.PlayState.PREPARING
    assert disp.prepareId
    assert disp.readyClients == set()
    assert disp.prepareDeadline > 0
    req = bc.call_args[0][1]
    assert req["REQUEST"] == "PREPARE"
    assert req["PAYLOAD"]["prepareId"] == disp.prepareId
    assert len(req["PAYLOAD"]["items"]) == 2
