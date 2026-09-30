"""Unit tests for the RUN_SCRIPT handler in mosaicmesh/websocket/legacy.py
(the `msg_response` REQUEST-based protocol used by the iPad-1 fleet).

The handler fans out a single RUN_SCRIPT REQUEST to one client, a display
group, or the full fleet — by stubbing `server._run_device_script`, these
tests verify the fanout selection logic without exercising the dispatcher
beneath it. The dispatcher itself is covered by
tests/unit/test_device_scripts.py.

This file was extracted from the pre-PR-3 test_device_scripts.py during
Task 6's cut-over — the tests target websocket-handler behavior, not
script execution, so they don't belong with the script tests."""
import sys, argparse, jsonpickle
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

_orig = argparse.ArgumentParser.parse_args
argparse.ArgumentParser.parse_args = lambda self, *a, **k: argparse.Namespace(Port=3000, Verbose=False)
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig


def _sess():
    s = MagicMock(); s.id = "s"; s.request = MagicMock()
    s.request.remote = "127.0.0.1"; s.request.headers = {"User-Agent": "T"}
    return s


def _dispatch(payload):
    """Call the RUN_SCRIPT handler, capturing which client keys get dispatched."""
    calls = []
    with patch.object(server, "_run_device_script", lambda k, w: calls.append((k, w))), \
         patch("asyncio.ensure_future", lambda coro: coro):
        ret = server.msg_response(
            {"SRC": "admin", "DEST": "SRV", "REQUEST": "RUN_SCRIPT", "PAYLOAD": payload}, _sess())
    return calls, jsonpickle.decode(ret)["PAYLOAD"]


def _three_clients():
    server.settings = server.Settings()
    for k, g in (("a", "G"), ("b", "G"), ("c", "Other")):
        cl = server.Client(); cl.displayID = g; cl.ip = "1.2.3.4"
        server.settings.clients[k] = cl


def test_run_script_single_client():
    _three_clients()
    calls, payload = _dispatch({"clientKey": "a", "script": "stop"})
    assert calls == [("a", "stop")] and payload["count"] == 1


def test_run_script_group_fanout():
    _three_clients()
    calls, payload = _dispatch({"displayID": "G", "script": "start"})
    assert set(k for k, _ in calls) == {"a", "b"}        # only group G, not "c"
    assert all(w == "start" for _, w in calls) and payload["count"] == 2


def test_run_script_all_fleet():
    _three_clients()
    calls, payload = _dispatch({"all": True, "script": "reboot"})
    assert set(k for k, _ in calls) == {"a", "b", "c"} and payload["count"] == 3


def test_run_script_bad_script_rejected():
    _three_clients()
    calls, payload = _dispatch({"all": True, "script": "format-c"})
    assert calls == [] and payload["status"] == "BAD_REQUEST"


# ---------------------------------------------------------------------------
# The log line must name the SENDER and the SCOPE.
#
# WHY (2026-09-30): a fleet-wide `stop -> login -> start`, 24 devices each,
# fired ~40s after two consecutive server restarts. Tracing who sent it took a
# full investigation -- reading every RUN_SCRIPT call site in the admin JS,
# proving `_run_device_script` has exactly one caller (this handler, so nothing
# server-side can self-trigger), enumerating TCP connections to find the admin
# browser, and inferring the scope arithmetically from the count (24 = the
# "OEB Sign 1" group; {all:true} would have been 27).
#
# Every one of those steps was recoverable from a log line that already had the
# information in hand: msg["SRC"] is the SockJS session that sent it, and the
# payload states the scope. Omitting them turned a lookup into an inference, and
# an inference I got wrong once in public -- I first said the burst "wasn't me,
# so it must have been you", then reversed to "it must be automatic" when it
# recurred, before the code proved it needs a human click on a confirm modal.
#
# A 24-way SSH fan-out is also the exact pattern that trips the AP flood
# protection ([[fleet-ssh-no-burst]]), so "who asked for this, and over what
# scope" is operationally important, not merely tidy.

def _dispatch_capture_log(payload, src="admin"):
    """Run the handler and capture what it logged."""
    import logging as _logging
    records = []

    class _Grab(_logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    h = _Grab()
    root = _logging.getLogger()
    root.addHandler(h)
    prev = root.level
    root.setLevel(_logging.DEBUG)
    try:
        with patch.object(server, "_run_device_script", lambda k, w: None), \
             patch("asyncio.ensure_future", lambda coro: coro):
            server.msg_response(
                {"SRC": src, "DEST": "SRV", "REQUEST": "RUN_SCRIPT", "PAYLOAD": payload},
                _sess())
    finally:
        root.removeHandler(h)
        root.setLevel(prev)
    return [m for m in records if "RUN_SCRIPT" in m]


def test_run_script_log_names_the_sender():
    server.settings = MagicMock()
    server.settings.clients = {"a": MagicMock(displayID="G1")}
    lines = _dispatch_capture_log({"clientKey": "a", "script": "start"}, src="fleet-ui-7")
    assert lines, "the handler must log"
    assert "fleet-ui-7" in lines[0], "the SRC session must appear: {0}".format(lines[0])


def test_run_script_log_names_a_group_scope():
    # The scope is what distinguishes "someone clicked Start on OEB Sign 1"
    # from "someone hit the whole fleet"; the device count alone cannot,
    # because a group can happen to contain every eligible client.
    server.settings = MagicMock()
    server.settings.clients = {
        "a": MagicMock(displayID="OEB Sign 1"),
        "b": MagicMock(displayID="OEB Sign 1"),
        "c": MagicMock(displayID="Desktop"),
    }
    lines = _dispatch_capture_log({"displayID": "OEB Sign 1", "script": "stop"})
    assert "OEB Sign 1" in lines[0], lines[0]
    assert "2 device" in lines[0], lines[0]


def test_run_script_log_marks_a_whole_fleet_action():
    server.settings = MagicMock()
    server.settings.clients = {"a": MagicMock(displayID="G1"), "b": MagicMock(displayID="G2")}
    lines = _dispatch_capture_log({"all": True, "script": "reboot"})
    assert "all" in lines[0].lower(), lines[0]
    assert "2 device" in lines[0], lines[0]


def test_run_script_log_names_a_single_client_scope():
    server.settings = MagicMock()
    server.settings.clients = {"a": MagicMock(displayID="G1")}
    lines = _dispatch_capture_log({"clientKey": "a", "script": "test"})
    assert "a" in lines[0], lines[0]
    assert "test" in lines[0], lines[0]
