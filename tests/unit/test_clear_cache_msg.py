"""Unit tests for the CLEAR_CACHE handler in mosaicmesh/websocket/legacy.py."""
import sys, argparse, jsonpickle
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

_orig = argparse.ArgumentParser.parse_args
argparse.ArgumentParser.parse_args = lambda self, *a, **k: argparse.Namespace(Port=3000, Verbose=False)
try:
    import server
    import mosaicmesh.websocket.legacy as legacy
finally:
    argparse.ArgumentParser.parse_args = _orig


def _sess():
    s = MagicMock(); s.id = "s"; s.request = MagicMock()
    s.request.remote = "127.0.0.1"; s.request.headers = {"User-Agent": "T"}
    return s


def _three_clients():
    server.settings = server.Settings()
    for k, g, segs in (("a", "G", {"T1_0", "T2_0"}), ("b", "G", {"T1_0"}), ("c", "Other", {"Z_0"})):
        cl = server.Client(); cl.displayID = g; cl.cachedSegments = set(segs)
        server.settings.clients[k] = cl


def _dispatch(payload):
    sent = []
    with patch.object(legacy, "broadcast_to_client",
                      lambda key, msg: sent.append((key, msg.get("REQUEST")))):
        ret = server.msg_response(
            {"SRC": "admin", "DEST": "SRV", "REQUEST": "CLEAR_CACHE", "PAYLOAD": payload}, _sess())
    return sent, jsonpickle.decode(ret)["PAYLOAD"]


def test_clear_cache_group_broadcasts_and_clears():
    _three_clients()
    sent, payload = _dispatch({"displayID": "G"})
    assert set(k for k, _ in sent) == {"a", "b"}                    # only group G, not "c"
    assert all(r == "CLEAR_CACHE" for _, r in sent)
    assert server.settings.clients["a"].cachedSegments == set()
    assert server.settings.clients["b"].cachedSegments == set()
    assert server.settings.clients["c"].cachedSegments == {"Z_0"}   # untouched
    assert payload["count"] == 2


def test_clear_cache_single_client():
    _three_clients()
    sent, payload = _dispatch({"clientKey": "a"})
    assert set(k for k, _ in sent) == {"a"}
    assert server.settings.clients["a"].cachedSegments == set()
    assert payload["count"] == 1
