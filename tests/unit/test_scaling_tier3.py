"""Tier-3 O(N)-per-event cleanups: group-broadcast encode-once (T3.1),
recursive static prewarm (T3.4), and the /api/displays single-pass index (T3.8).
All must be behavior-preserving — the tests assert output equivalence, not just
the optimization shape."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import argparse
_orig = argparse.ArgumentParser.parse_args
class _MockArgs:
    Port = 3000
    Verbose = False
argparse.ArgumentParser.parse_args = lambda self, a=None, n=None: _MockArgs()
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

import jsonpickle
from mosaicmesh.state import Settings, Client, Display


# ------------------------------ T3.1 ------------------------------

def test_broadcast_group_encode_once_byte_equivalent(monkeypatch):
    """Each delivered message must be byte-for-byte what a per-client
    jsonpickle.encode (DEST=client_id) would have produced."""
    from mosaicmesh import broadcast
    monkeypatch.setattr(server, "settings", Settings(), raising=False)
    monkeypatch.setattr(server, "socketmanager", object(), raising=False)  # non-None
    for k, did in [("a", "G"), ("b", "G"), ("c", "OTHER")]:
        c = Client(); c.displayID = did; c.clientID = "sess_" + k
        server.settings.clients[k] = c

    captured = {}
    monkeypatch.setattr(broadcast, "_deliver",
                        lambda cid, msg, client: captured.__setitem__(cid, msg))

    payload = {"REQUEST": "PLAY", "PAYLOAD": {"epoch": 123, "url": "/x.mp4"}}
    broadcast.broadcast_to_display_group("G", dict(payload))

    assert set(captured) == {"a", "b"}, "only group-G clients receive it"
    for cid in ("a", "b"):
        expected = jsonpickle.encode({**payload, "DEST": cid})
        assert captured[cid] == expected, \
            "encode-once + DEST-substitute must equal a per-client encode"


def test_broadcast_group_noop_without_socketmanager(monkeypatch):
    from mosaicmesh import broadcast
    monkeypatch.setattr(server, "settings", Settings(), raising=False)
    monkeypatch.setattr(server, "socketmanager", None, raising=False)
    called = []
    monkeypatch.setattr(broadcast, "_deliver",
                        lambda *a, **k: called.append(1))
    broadcast.broadcast_to_display_group("G", {"REQUEST": "PLAY"})
    assert called == []


# ------------------------------ T3.8 ------------------------------

def test_displays_index_single_pass_counts(monkeypatch):
    from mosaicmesh.api import displays
    monkeypatch.setattr(server, "settings", Settings(), raising=False)
    d = Display(); server.settings.displays["G"] = d
    spec = [("a", True, True), ("b", True, False), ("c", False, True)]
    for k, on, cal in spec:
        c = Client(); c.displayID = "G"; c.isOnline = on
        c.measuredPerimeter = [0, 0, 1, 0, 1, 1, 0, 1] if cal else None
        server.settings.clients[k] = c

    idx = displays._index_clients_by_display()
    assert sorted(idx["G"]["clients"]) == ["a", "b", "c"]
    assert idx["G"]["online"] == 2
    assert idx["G"]["calibrated"] == 2

    s = displays._serialize("G", d, idx)
    assert s["clientCount"] == 3 and s["onlineCount"] == 2 and s["calibratedCount"] == 2
    # standalone (no shared index) must produce the same result
    assert displays._serialize("G", d) == s


# ------------------------------ T3.4 ------------------------------

def test_prewarm_recurses_into_js_timeline(monkeypatch, tmp_path):
    from mosaicmesh import cache
    (tmp_path / "index.html").write_text("x")
    tl = tmp_path / "js" / "timeline"
    tl.mkdir(parents=True)
    (tl / "index.js").write_text("// module")
    (tmp_path / "js" / "mosiacmesh.js").write_text("// root")
    monkeypatch.chdir(tmp_path)

    saved = dict(cache.file_cache)
    cache.file_cache.clear()
    try:
        cache.prewarm_static_cache()
        norm = {os.path.normpath(k) for k in cache.file_cache}
        assert os.path.normpath("js/timeline/index.js") in norm, \
            "recursive prewarm must cache js/timeline modules"
        assert os.path.normpath("js/mosiacmesh.js") in norm
        assert os.path.normpath("index.html") in norm
    finally:
        cache.file_cache.clear()
        cache.file_cache.update(saved)
