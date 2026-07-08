# tests/unit/test_render_precache_hook.py
import types, server
from mosaicmesh import render

def test_ready_triggers_precache_for_cache_capable(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "start_precache",
                        lambda g, t, urls, **kw: calls.append((g, t, dict(urls))), raising=False)
    # minimal fake state: two clients in G1, one cache-capable
    C = lambda cm: types.SimpleNamespace(cacheMode=cm, displayID="G1")
    monkeypatch.setattr(server, "settings",
        types.SimpleNamespace(clients={"a": C("lighttpd-localhost"), "b": C("none")}), raising=False)
    render.notify_precache_on_ready("G1", "T1", {"a": "http://c/seg-a", "b": "http://c/seg-b"})
    assert calls == [("G1", "T1", {"a": "http://c/seg-a"})]   # only 'a' (cache-capable)
