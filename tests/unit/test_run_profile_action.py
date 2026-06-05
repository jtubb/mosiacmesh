"""Tests for run_profile_action — the new public dispatcher entry point.

Behavioral contract:
  - Resolves profile via client.profileName -> server.settings.profiles[name]
  - Returns (None, "no-profile") and logs a warning when no profile is set
    or the named profile is missing (no crash, fleet-wide robustness)
  - Routes 'start' through LAUNCH_METHODS[profile.launch['method']]
  - Routes login/stop/test/reboot through _exec_ssh(profile.scripts[which])
  - Substitutes template variables in the script body before execution
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import argparse
_orig = argparse.ArgumentParser.parse_args
argparse.ArgumentParser.parse_args = lambda self, *a, **k: argparse.Namespace(Port=3000, Verbose=False)
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

from mosaicmesh.state import Client, ScriptingProfile, Settings
from mosaicmesh.device_scripts import run_profile_action


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _setup_fleet():
    """Replace server.settings with a fresh Settings containing one Client
    pointing at one profile. Returns (clientKey, profile)."""
    server.settings = Settings()
    p = ScriptingProfile()
    p.name = "ipad1-ios5"
    p.scripts = {"login": "echo LOGIN-{ip}", "start": "echo START-{ip}",
                 "stop":  "echo STOP-{ip}",  "test":  "echo TEST-{ip}",
                 "reboot":"echo REBOOT"}
    p.launch = {"method": "shell"}
    p.webclip = {"bundleId": "BID", "title": "T"}
    server.settings.profiles["ipad1-ios5"] = p
    c = Client(); c.ip = "10.0.0.5"; c.clientID = "abc"
    c.profileName = "ipad1-ios5"
    server.settings.clients["abc"] = c
    return "abc", p


def test_no_profile_assigned_logs_and_returns():
    """A client with profileName=None must NOT crash — return a sentinel."""
    server.settings = Settings()
    c = Client(); c.ip = "10.0.0.5"; c.clientID = "x"
    c.profileName = None
    server.settings.clients["x"] = c
    rc, out = _run(run_profile_action("x", "start"))
    assert rc is None
    assert out == "no-profile"


def test_unknown_profile_name_logs_and_returns():
    """profileName points to a profile that doesn't exist — same robust
    no-crash path as no-profile."""
    server.settings = Settings()
    c = Client(); c.ip = "10.0.0.5"; c.clientID = "x"
    c.profileName = "ghost"
    server.settings.clients["x"] = c
    rc, out = _run(run_profile_action("x", "start"))
    assert rc is None
    assert out == "no-profile"


def test_login_routes_through_exec_ssh_with_substitution():
    ckey, _ = _setup_fleet()
    with patch("mosaicmesh.device_scripts._exec_ssh",
               new=AsyncMock(return_value=(0, "ok"))) as mock_exec:
        rc, out = _run(run_profile_action(ckey, "login"))
    assert rc == 0
    # _exec_ssh was called with the login template (NOT yet substituted —
    # _exec_ssh does the substitution internally)
    args = mock_exec.call_args.args
    assert args[1] == "echo LOGIN-{ip}"
    # vars_ dict contains the right substitution values
    assert args[2]["ip"] == "10.0.0.5"


def test_start_routes_through_LAUNCH_METHODS():
    ckey, p = _setup_fleet()
    p.launch = {"method": "shell"}
    with patch("mosaicmesh.device_scripts._exec_ssh",
               new=AsyncMock(return_value=(0, "ok"))) as mock_exec:
        rc, out = _run(run_profile_action(ckey, "start"))
    assert rc == 0
    # 'shell' launch method calls _exec_ssh(profile.scripts['start'])
    assert mock_exec.call_args.args[1] == "echo START-{ip}"


def test_unknown_launch_method_falls_back_to_exec_ssh_with_start():
    """If profile.launch['method'] is unrecognized, dispatcher falls back to
    executing scripts['start'] via SSH — same as 'shell' method."""
    ckey, p = _setup_fleet()
    p.launch = {"method": "wat"}
    with patch("mosaicmesh.device_scripts._exec_ssh",
               new=AsyncMock(return_value=(0, "ok"))) as mock_exec:
        rc, out = _run(run_profile_action(ckey, "start"))
    assert rc == 0
    assert mock_exec.call_args.args[1] == "echo START-{ip}"
