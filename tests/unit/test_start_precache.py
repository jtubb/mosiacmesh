# tests/unit/test_start_precache.py
import types, server

def test_start_precache_sends_only_window(monkeypatch):
    sent = []
    monkeypatch.setattr(server, "_send_precache", lambda k, u, t: sent.append(k), raising=False)
    server.precache_windows = {}
    server.start_precache("G1", "T1", {"a": "u-a", "b": "u-b", "c": "u-c"}, n=2)
    assert len(sent) == 2                         # only the window's initial grant
    assert server.precache_token == "T1"
    assert server.precache_group["a"] == "G1"
    assert server.precache_urls["a"] == "u-a"
