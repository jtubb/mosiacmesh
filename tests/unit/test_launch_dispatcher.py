"""Unit tests for the PR-3 launch dispatcher in mosaicmesh/device_scripts.py.

Each test stubs `asyncio.create_subprocess_exec` (or the VNC pool) and
verifies the dispatcher sends the right command/tap to the right place.
The real subprocess + VNC integration is exercised in a manual smoke
on a live iPad before PR-3 merges.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import argparse
_orig = argparse.ArgumentParser.parse_args
argparse.ArgumentParser.parse_args = lambda self, *a, **k: argparse.Namespace(Port=3000, Verbose=False)
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

from mosaicmesh.state import Client, ScriptingProfile
from mosaicmesh.device_scripts import (
    LAUNCH_METHODS, _exec_ssh, _vnc_tap_sequence, _ssh_then_vnc,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _profile(method, **launch):
    p = ScriptingProfile()
    p.name = "test"
    p.scripts = {"login": "echo LOGIN", "start": "echo START",
                 "stop": "echo STOP", "test": "echo TEST", "reboot": "echo REBOOT"}
    p.launch = {"method": method, **launch}
    p.webclip = {"bundleId": "BID", "title": "T"}
    p.ssh = {"legacyCrypto": True, "user": "root", "keyPath": "/tmp/k"}
    return p


def _client(ip="10.0.0.5"):
    c = Client(); c.ip = ip; c.clientID = "cid"
    return c


def test_launch_methods_table_has_three_entries():
    assert set(LAUNCH_METHODS.keys()) == {"shell", "vnc-tap", "ssh-then-vnc"}
    for k, fn in LAUNCH_METHODS.items():
        assert callable(fn), f"{k} entry is not callable"


def test_exec_ssh_builds_command_with_substituted_template():
    """_exec_ssh runs a script via ssh, substituting template variables
    into the script body before execution."""
    c = _client("192.168.1.99")
    p = _profile("shell")
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"OK", b""))
    fake_proc.returncode = 0
    fake_proc.kill = MagicMock()
    fake_proc.wait = AsyncMock()
    with patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=fake_proc)) as exec_mock:
        rc, out = _run(_exec_ssh(c, "echo hello-{ip}",
                                 {"ip": "192.168.1.99"}))
    assert rc == 0
    assert out == "OK"
    args = exec_mock.call_args.args
    assert "ssh" in args[0]
    assert "192.168.1.99" in args[-2]   # user@ip
    assert args[-1] == "echo hello-192.168.1.99"   # substituted script


def test_vnc_tap_sequence_taps_each_coord_in_order():
    """_vnc_tap_sequence iterates launch_cfg['taps'] and calls _do_tap
    for each coordinate."""
    c = _client()
    launch_cfg = {"method": "vnc-tap", "vncPassword": "pw",
                  "taps": [{"fbX": 100, "fbY": 200},
                           {"fbX": 300, "fbY": 400}]}
    proxy = MagicMock()
    server._veency_pool["cid"] = proxy   # pre-seed pool so _get_pooled_vnc returns instantly
    tapped = []
    try:
        with patch.object(server, "_get_pooled_vnc",
                          new=AsyncMock(return_value=proxy)), \
             patch.object(server, "_do_tap",
                          side_effect=lambda px, x, y: tapped.append((x, y))):
            ok = _run(_vnc_tap_sequence(c, launch_cfg, {}))
    finally:
        server._veency_pool.pop("cid", None)
    assert ok is True
    assert tapped == [(100, 200), (300, 400)]


def test_ssh_then_vnc_runs_wakeScript_then_taps():
    """_ssh_then_vnc executes profile.launch['wakeScript'] over SSH
    first (best-effort), then calls _vnc_tap_sequence."""
    c = _client()
    p = _profile("ssh-then-vnc", vncPassword="pw",
                 wakeScript="activator send libactivator.lockscreen.dismiss",
                 taps=[{"fbX": 945, "fbY": 671}])
    fake_proc = MagicMock()
    fake_proc.wait = AsyncMock()
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))
    fake_proc.returncode = 0
    fake_proc.kill = MagicMock()
    proxy = MagicMock()
    tapped = []
    with patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=fake_proc)) as exec_mock, \
         patch.object(server, "_get_pooled_vnc",
                      new=AsyncMock(return_value=proxy)), \
         patch.object(server, "_do_tap",
                      side_effect=lambda px, x, y: tapped.append((x, y))), \
         patch("asyncio.sleep", new=AsyncMock()):
        ok = _run(_ssh_then_vnc(c, p, {}))
    assert ok is True
    assert tapped == [(945, 671)]
    # The SSH wake step ran exactly once with the wakeScript body
    assert exec_mock.call_count == 1
    assert exec_mock.call_args.args[-1] == \
        "activator send libactivator.lockscreen.dismiss"


def test_ssh_then_vnc_falls_back_to_ssh_when_tap_fails():
    """If the VNC tap raises, _ssh_then_vnc falls back to running
    profile.scripts['start'] via SSH (the same fallback path the old
    _run_device_script uses today)."""
    c = _client()
    p = _profile("ssh-then-vnc", vncPassword="pw",
                 wakeScript="wake",
                 taps=[{"fbX": 945, "fbY": 671}])
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"FALLBACK_OK", b""))
    fake_proc.returncode = 0
    fake_proc.wait = AsyncMock()
    fake_proc.kill = MagicMock()
    proxy = MagicMock()
    with patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=fake_proc)) as exec_mock, \
         patch.object(server, "_get_pooled_vnc",
                      new=AsyncMock(return_value=proxy)), \
         patch.object(server, "_do_tap",
                      side_effect=RuntimeError("VNC unreachable")), \
         patch.object(server, "_drop_pooled_vnc", new=AsyncMock()), \
         patch("asyncio.sleep", new=AsyncMock()):
        ok = _run(_ssh_then_vnc(c, p, {}))
    # tap failed → fell back to ssh-exec scripts['start'] → _exec_ssh
    # returns (rc, output) from the mocked subprocess
    assert ok == (0, "FALLBACK_OK")
    # The exec_mock should now have TWO calls: the wake step + the fallback start
    assert exec_mock.call_count == 2
