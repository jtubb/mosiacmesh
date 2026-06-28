"""Tests for the CLIENTS_CAME_ONLINE reconnect-storm batcher (T1.3).

A reconnect storm used to fire one broadcast per REGISTER to all sessions.
queue_client_online() now buffers devices (deduped by clientKey) and emits a
single consolidated broadcast a debounce later. With no running loop (direct
test calls) it flushes immediately, preserving the pre-batch behavior.
"""
import asyncio
import jsonpickle
import mosaicmesh.websocket.online_batch as ob


class _FakeLoop:
    def __init__(self):
        self.scheduled = []

    def call_later(self, delay, cb):
        self.scheduled.append((delay, cb))


def _reset():
    ob._pending_online.clear()
    ob._flush_scheduled = False


def _capture_broadcasts(monkeypatch):
    sent = []
    mgr = type("M", (), {"broadcast": lambda self, m: sent.append(m)})()
    import server
    monkeypatch.setattr(server, "socketmanager", mgr, raising=False)
    return sent


def _dev(k, g="G"):
    return {"clientKey": k, "displayID": g, "isOnline": True, "friendlyName": k}


def test_no_loop_flushes_immediately(monkeypatch):
    _reset()
    sent = _capture_broadcasts(monkeypatch)

    def _no_loop():
        raise RuntimeError("no running loop")
    monkeypatch.setattr(asyncio, "get_running_loop", _no_loop)

    ob.queue_client_online(_dev("a"))
    assert len(sent) == 1, "no-loop path emits immediately (pre-batch parity)"
    payload = jsonpickle.decode(sent[0])
    assert payload["REQUEST"] == "CLIENTS_CAME_ONLINE"
    assert [d["clientKey"] for d in payload["PAYLOAD"]["devices"]] == ["a"]


def test_storm_batches_into_one_broadcast_with_loop(monkeypatch):
    _reset()
    sent = _capture_broadcasts(monkeypatch)
    loop = _FakeLoop()
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: loop)

    for k in ["a", "b", "c", "d"]:
        ob.queue_client_online(_dev(k))

    assert len(loop.scheduled) == 1, "many queues -> ONE debounce timer"
    assert sent == [], "nothing broadcast until the debounce fires"

    loop.scheduled[0][1]()                      # fire the flush
    assert len(sent) == 1, "storm collapses to a single broadcast"
    devs = jsonpickle.decode(sent[0])["PAYLOAD"]["devices"]
    assert sorted(d["clientKey"] for d in devs) == ["a", "b", "c", "d"]


def test_dedupes_repeated_client_in_window(monkeypatch):
    _reset()
    sent = _capture_broadcasts(monkeypatch)
    loop = _FakeLoop()
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: loop)

    for k in ["a", "a", "a"]:                   # one flaky client bouncing
        ob.queue_client_online(_dev(k))
    loop.scheduled[0][1]()

    devs = jsonpickle.decode(sent[0])["PAYLOAD"]["devices"]
    assert [d["clientKey"] for d in devs] == ["a"], "deduped to one entry"


def test_missing_client_key_is_ignored(monkeypatch):
    _reset()
    sent = _capture_broadcasts(monkeypatch)
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: _FakeLoop())
    ob.queue_client_online({"displayID": "G"})   # no clientKey
    assert ob._pending_online == {}
    assert sent == []


def test_flag_resets_so_next_window_schedules_again(monkeypatch):
    _reset()
    _capture_broadcasts(monkeypatch)
    loop = _FakeLoop()
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: loop)

    ob.queue_client_online(_dev("a"))
    loop.scheduled[0][1]()                       # flush window 1
    assert ob._flush_scheduled is False
    ob.queue_client_online(_dev("b"))            # a later reconnect
    assert len(loop.scheduled) == 2, "a new window schedules a fresh timer"
