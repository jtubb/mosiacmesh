# tests/unit/test_cache_pull_msg.py — exercises the handler via a seam, no aiohttp.
import types, mosaicmesh.websocket.legacy as legacy
from mosaicmesh.cache_pull import CacheState, PrecacheWindow

def _make_server(sent):
    srv = types.SimpleNamespace()
    srv.cache_state = CacheState()
    srv.precache_windows = {"G1": PrecacheWindow(["a", "b", "c"], n=1)}
    srv.precache_urls = {"c": "http://c/seg-c"}     # url to grant next
    srv.precache_group = {"a": "G1", "b": "G1", "c": "G1"}
    srv.precache_token = "T1"
    srv._send_precache = lambda key, url, token: sent.append((key, url, token))
    return srv

def test_cached_ack_advances_window_and_grants_next(monkeypatch):
    sent = []
    srv = _make_server(sent)
    monkeypatch.setattr(legacy, "server", srv, raising=False)
    srv.precache_windows["G1"].start()               # a active; b, c still waiting
    assert sent == []                                # nothing sent yet (start() doesn't use _send_precache)
    legacy.handle_cache_ack({"SRC": "a", "REQUEST": "CACHED", "PAYLOAD": {"token": "T1"}})
    assert srv.cache_state.is_cached("a", "T1") is True
    # advance("a") should grant "b" as next (n=1 window)
    assert len(sent) == 1
    assert sent[0][0] == "b"                        # b is the next client granted
    assert sent[0][2] == "T1"                       # correct token

def test_cache_failed_advances_window_and_records_failure(monkeypatch):
    sent = []
    srv = _make_server(sent)
    monkeypatch.setattr(legacy, "server", srv, raising=False)
    srv.precache_windows["G1"].start()               # a active
    legacy.handle_cache_ack({"SRC": "a", "REQUEST": "CACHE_FAILED", "PAYLOAD": {"token": "T1"}})
    assert srv.cache_state.is_cached("a", "T1") is False
    assert sent[0][0] == "b"                        # still advances to next client
