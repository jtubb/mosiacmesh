"""Unit tests for _run_device_script — the public dispatcher entry point
after the PR-3 ScriptingProfile cut-over.

Pre-PR-3 this test file targeted per-Client {login,start,stop,reboot}Script
fields + DEFAULT_DEVICE_SCRIPTS. Those are gone; behavior now flows through
client.profileName -> settings.profiles[name] -> dispatcher."""
import sys, asyncio, argparse
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

_orig = argparse.ArgumentParser.parse_args
argparse.ArgumentParser.parse_args = lambda self, *a, **k: argparse.Namespace(Port=3000, Verbose=False)
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

from mosaicmesh.state import Settings, Client, ScriptingProfile
from mosaicmesh.profile_bootstrap import DEFAULT_PROFILE_IPAD1_IOS5


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _seeded(ckey="a", ip="10.0.0.5"):
    server.settings = Settings()
    import copy
    server.settings.profiles["ipad1-ios5"] = copy.deepcopy(DEFAULT_PROFILE_IPAD1_IOS5)
    c = Client(); c.clientID = ckey; c.ip = ip
    c.profileName = "ipad1-ios5"
    server.settings.clients[ckey] = c
    return ckey


def test_run_device_script_builds_legacy_ssh_command_for_login():
    """Login goes straight through _exec_ssh — verify the SSH command
    shape (legacy crypto opts, ssh key path, user@ip, substituted script
    body) matches what the iPad-1 sshd expects."""
    ckey = _seeded()
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"LOGIN_OK\n", b""))
    fake_proc.returncode = 0
    fake_proc.kill = MagicMock()
    fake_proc.wait = AsyncMock()
    with patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=fake_proc)) as exec_mock:
        rc, out = _run(server._run_device_script(ckey, "login"))
    assert rc == 0
    args = exec_mock.call_args.args
    assert "ssh" in args[0]
    assert "-o" in args and "HostKeyAlgorithms=+ssh-rsa" in args
    assert args[-2] == "root@10.0.0.5"
    # Substituted login script must contain the literal command body (no
    # {tokens} left)
    assert "activator send libactivator.lockscreen.dismiss" in args[-1]
    assert "echo LOGIN_OK" in args[-1]


def test_run_device_script_no_profile_is_noop():
    """A Client with profileName=None returns the sentinel (None, 'no-profile')
    without attempting any subprocess work."""
    server.settings = Settings()
    c = Client(); c.clientID = "x"; c.ip = "10.0.0.5"; c.profileName = None
    server.settings.clients["x"] = c
    with patch("asyncio.create_subprocess_exec",
               new=AsyncMock()) as exec_mock:
        rc, out = _run(server._run_device_script("x", "login"))
    assert rc is None
    assert out == "no-profile"
    assert exec_mock.call_count == 0


def test_run_device_script_unknown_profile_is_noop():
    server.settings = Settings()
    c = Client(); c.clientID = "x"; c.ip = "10.0.0.5"
    c.profileName = "ghost-profile"
    server.settings.clients["x"] = c
    rc, out = _run(server._run_device_script("x", "stop"))
    assert rc is None
    assert out == "no-profile"


def test_run_device_script_start_routes_through_ssh_then_vnc_for_default():
    """The default ipad1-ios5 profile uses launch.method='ssh-then-vnc';
    'start' must therefore go through the ssh-then-vnc path (which runs
    the wakeScript over SSH first, then VNC-taps)."""
    ckey = _seeded()
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))
    fake_proc.returncode = 0
    fake_proc.kill = MagicMock()
    fake_proc.wait = AsyncMock()
    proxy = MagicMock()
    tapped = []
    with patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=fake_proc)) as exec_mock, \
         patch.object(server, "_get_pooled_vnc",
                      new=AsyncMock(return_value=proxy)), \
         patch.object(server, "_do_tap",
                      side_effect=lambda px, x, y: tapped.append((x, y))), \
         patch("asyncio.sleep", new=AsyncMock()):
        result = _run(server._run_device_script(ckey, "start"))
    # tap at the default profile's coordinate
    assert tapped == [(945, 671)]
    # wake step ran over SSH exactly once
    assert exec_mock.call_count == 1
    # success path
    rc, out = result
    assert rc == 0
    assert out == "VNC_TAP_OK"


def test_run_device_script_reboot_runs_reboot_template():
    ckey = _seeded()
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"REBOOTING\n", b""))
    fake_proc.returncode = 0
    fake_proc.kill = MagicMock()
    fake_proc.wait = AsyncMock()
    with patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=fake_proc)) as exec_mock:
        rc, out = _run(server._run_device_script(ckey, "reboot"))
    assert rc == 0
    # The reboot script template ("echo REBOOTING; reboot") goes through
    # _exec_ssh unchanged (no template tokens).
    assert exec_mock.call_args.args[-1] == "echo REBOOTING; reboot"


def test_run_device_script_via_legacy_broadcast_call_site_unchanged():
    """The legacy mosaicmesh/websocket/legacy.py RUN_SCRIPT handler calls
    `server._run_device_script(k, which)` — same arity and entry point —
    so its call sites need NO change post-PR-3. This is a smoke that the
    re-export through server.py is intact."""
    assert hasattr(server, "_run_device_script")
    assert callable(server._run_device_script)
