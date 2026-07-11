# tests/unit/test_cache_pull_msg.py — exercises the handler via a seam, no aiohttp.
import types, mosaicmesh.websocket.legacy as legacy
from mosaicmesh.cache_pull import PrecacheWindow

def _make_server(sent):
    srv = types.SimpleNamespace()
    srv.precache_windows = {"G1": PrecacheWindow(["a", "b", "c"], n=1)}
    srv.precache_urls = {"b": "http://c/seg-b", "c": "http://c/seg-c"}
    srv.precache_group = {"a": "G1", "b": "G1", "c": "G1"}
    srv.precache_token = "T1"
    # the PRECACHE token is the per-client SEGMENT NAME (url basename)
    srv.precache_segtoken = {"a": "seg_T1_0", "b": "seg_T1_0", "c": "seg_T1_0"}
    srv.settings = types.SimpleNamespace(clients={"a": types.SimpleNamespace(cachedSegments=set())})
    srv._send_precache = lambda key, url, token: sent.append((key, url, token))
    return srv

def test_cached_ack_marks_segment_and_advances(monkeypatch):
    sent = []
    srv = _make_server(sent)
    monkeypatch.setattr(legacy, "server", srv, raising=False)
    srv.precache_windows["G1"].start()               # a active; b, c still waiting
    assert sent == []                                # start() doesn't use _send_precache
    legacy.handle_cache_ack({"SRC": "a", "REQUEST": "CACHED", "PAYLOAD": {"token": "seg_T1_0"}})
    # segment marked on the Client (seg_ prefix stripped -> the key _per_client_items checks)
    assert "T1_0" in srv.settings.clients["a"].cachedSegments
    # advance("a") grants "b" next (n=1 window), carrying b's seg-token
    assert len(sent) == 1
    assert sent[0][0] == "b"
    assert sent[0][2] == "seg_T1_0"

def test_cache_failed_advances_window_without_marking(monkeypatch):
    sent = []
    srv = _make_server(sent)
    monkeypatch.setattr(legacy, "server", srv, raising=False)
    srv.precache_windows["G1"].start()               # a active
    legacy.handle_cache_ack({"SRC": "a", "REQUEST": "CACHE_FAILED", "PAYLOAD": {"token": "seg_T1_0"}})
    assert "T1_0" not in srv.settings.clients["a"].cachedSegments   # failed -> not cached
    assert sent[0][0] == "b"                          # still advances to the next client

def test_cache_failed_removes_present_segment(monkeypatch):
    sent = []
    srv = _make_server(sent)
    # Seed the STALE state: the record claims "a" holds T1_0, but the device lost it.
    srv.settings.clients["a"].cachedSegments = {"T1_0"}
    monkeypatch.setattr(legacy, "server", srv, raising=False)
    srv.precache_windows["G1"].start()               # a active
    legacy.handle_cache_ack({"SRC": "a", "REQUEST": "CACHE_FAILED", "PAYLOAD": {"token": "seg_T1_0"}})
    assert "T1_0" not in srv.settings.clients["a"].cachedSegments   # failed pull -> record corrected
    assert sent[0][0] == "b"                                        # still advances the window
