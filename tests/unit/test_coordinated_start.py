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


def _online_client(server, key, display_id):
    c = server.Client()
    c.displayID = display_id
    c.isOnline = True
    server.settings.clients[key] = c
    return c

def test_release_when_all_online_ready():
    disp = _display_with_items(server)
    _online_client(server, "a", "g1")
    _online_client(server, "b", "g1")
    with patch.object(server, "broadcast_to_display_group"):
        server._begin_prepare("g1")
    with patch.object(server, "_start_group_playback") as sgp:
        server._maybe_release("g1")                    # not all ready yet
        assert sgp.call_count == 0
        disp.readyClients = {"a", "b"}
        server._maybe_release("g1")                    # now all ready -> release
        assert sgp.call_count == 1
        epoch = sgp.call_args[0][1]                     # released with a FUTURE epoch
        assert epoch > int(time.time() * 1000)
    assert disp.prepareId is None


def test_timeout_release_helper():
    disp = _display_with_items(server)
    _online_client(server, "a", "g1")
    with patch.object(server, "broadcast_to_display_group"):
        server._begin_prepare("g1")
    disp.prepareDeadline = int(time.time() * 1000) - 1   # already past
    with patch.object(server, "_start_group_playback") as sgp:
        server._release_expired_prepares()
        assert sgp.call_count == 1
    assert disp.prepareId is None


import asyncio

def test_auto_arm_invokes_vncdo_with_center_coords():
    server.settings = server.Settings()
    c = server.Client()
    c.displayID = "g1"; c.isOnline = True; c.ip = "192.168.1.50"
    c.deviceWidth = 1024; c.deviceHeight = 768
    server.settings.clients["a"] = c

    called = {}
    async def fake_exec(*args, **kwargs):
        called["args"] = args
        class P:
            async def wait(self): return 0
        return P()

    server.AUTO_ARM = True
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(server._auto_arm_client("a"))
        finally:
            loop.close()
    assert "vncdo" in called["args"][0]
    assert "192.168.1.50::5900" in called["args"]
    assert "512" in called["args"] and "384" in called["args"]   # center of 1024x768
