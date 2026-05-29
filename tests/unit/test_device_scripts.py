import sys, asyncio, argparse, jsonpickle
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
_orig = argparse.ArgumentParser.parse_args
argparse.ArgumentParser.parse_args = lambda self, *a, **k: argparse.Namespace(Port=3000, Verbose=False)
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig


def _client(ip="192.168.1.50"):
    c = server.Client()
    c.ip = ip
    return c


def test_apply_default_scripts_backfills_only_unset():
    c = server.Client()
    assert c.loginScript is None and c.startScript is None
    c.startScript = "custom-start"          # operator-set: must be preserved
    server._apply_default_scripts(c)
    assert c.startScript == "custom-start"  # not overridden
    assert c.loginScript == server.DEFAULT_DEVICE_SCRIPTS["loginScript"]
    assert c.stopScript == server.DEFAULT_DEVICE_SCRIPTS["stopScript"]
    assert c.rebootScript == server.DEFAULT_DEVICE_SCRIPTS["rebootScript"]


def test_default_start_opens_display_url_in_safari():
    s = server.DEFAULT_DEVICE_SCRIPTS["startScript"]
    assert "uiopen" in s and server.DISPLAY_URL in s
    assert "MobileSafari" in server.DEFAULT_DEVICE_SCRIPTS["stopScript"]
    assert "reboot" in server.DEFAULT_DEVICE_SCRIPTS["rebootScript"]


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_run_device_script_builds_legacy_ssh_command():
    server.settings = server.Settings()
    server.settings.clients["a"] = _client("192.168.1.50")
    server._apply_default_scripts(server.settings.clients["a"])
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        class P:
            returncode = 0
            async def communicate(self_): return (b"START_OK", b"")
        return P()

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        rc, out = _run(server._run_device_script("a", "start"))

    args = captured["args"]
    assert args[0] == "ssh"
    assert "-i" in args and server.SSH_KEY_PATH in args
    assert "-o" in args and "HostKeyAlgorithms=+ssh-rsa" in args   # legacy crypto
    assert "root@192.168.1.50" in args
    assert args[-1] == server.DEFAULT_DEVICE_SCRIPTS["startScript"]  # the script runs last
    assert rc == 0 and "START_OK" in out


def test_run_device_script_prefers_per_device_override():
    server.settings = server.Settings()
    c = _client("10.0.0.9"); c.rebootScript = "ldrestart"
    server.settings.clients["b"] = c
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        class P:
            returncode = 0
            async def communicate(self_): return (b"", b"")
        return P()

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        _run(server._run_device_script("b", "reboot"))
    assert captured["args"][-1] == "ldrestart"   # used the override, not the default


def test_run_device_script_no_ip_is_noop():
    server.settings = server.Settings()
    server.settings.clients["c"] = server.Client()   # no ip
    with patch("asyncio.create_subprocess_exec") as ex:
        rc, out = _run(server._run_device_script("c", "start"))
    ex.assert_not_called()
    assert rc is None


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
