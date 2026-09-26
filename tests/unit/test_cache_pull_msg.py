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


def test_progress_does_not_advance_the_window(monkeypatch):
    """CACHE_PROGRESS is liveness, NOT completion. If it advanced the window the
    server would hand the slot to the next client while the current one was still
    mid-transfer, so n clients would pull concurrently and the whole point of the
    throttle (bounded peak WiFi) would be lost -- on a 93MB segment that is the
    difference between 3 and 24 simultaneous transfers."""
    sent = []
    srv = _make_server(sent)
    srv.PRECACHE_STALL_TIMEOUT = 90.0
    monkeypatch.setattr(legacy, "server", srv, raising=False)
    srv.precache_windows["G1"].start(now=0.0)          # a active; b, c waiting
    legacy.handle_cache_ack({"SRC": "a", "REQUEST": "CACHE_PROGRESS",
                             "PAYLOAD": {"token": "seg_T1_0", "bytes": 3145728}})
    assert sent == [], "progress must not grant the next client"
    # and it must not be mistaken for completion either
    assert "T1_0" not in srv.settings.clients["a"].cachedSegments


def test_progress_refreshes_the_deadline(monkeypatch):
    """The reported progress has to actually postpone expiry, else a slow-but-alive
    transfer is still killed."""
    sent = []
    srv = _make_server(sent)
    srv.PRECACHE_STALL_TIMEOUT = 90.0
    monkeypatch.setattr(legacy, "server", srv, raising=False)
    win = PrecacheWindow(["a", "b"], n=1, timeout_for=lambda k: 600.0)
    srv.precache_windows["G1"] = win
    win.start(now=0.0)
    legacy.handle_cache_ack({"SRC": "a", "REQUEST": "CACHE_PROGRESS",
                             "PAYLOAD": {"token": "seg_T1_0", "bytes": 1048576}})
    # the grant's clock restarted at ~now, and its deadline is now the stall window
    import time as _t
    assert win.sweep_timeouts(now=_t.time() + 30.0) == ([], []), "30s after progress: alive"
    stale, granted = win.sweep_timeouts(now=_t.time() + 200.0)
    assert stale == ["a"], "200s of silence after progress: dead, free the slot"
    assert granted == ["b"]


def test_progress_for_an_unknown_client_is_harmless(monkeypatch):
    sent = []
    srv = _make_server(sent)
    monkeypatch.setattr(legacy, "server", srv, raising=False)
    legacy.handle_cache_ack({"SRC": "nobody", "REQUEST": "CACHE_PROGRESS",
                             "PAYLOAD": {"token": "seg_T1_0", "bytes": 1}})
    assert sent == []
