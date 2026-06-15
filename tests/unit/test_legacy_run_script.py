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
