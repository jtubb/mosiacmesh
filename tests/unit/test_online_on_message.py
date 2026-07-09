# tests/unit/test_online_on_message.py — a live client's message marks it online
# (not just REGISTER), so _client_is_push_eligible sees it and the client-pull PRECACHE fires.
import types
import server
import mosaicmesh.websocket.legacy as legacy


def test_message_marks_known_client_online(monkeypatch):
    c = types.SimpleNamespace(isOnline=False, lastSeen=0.0)
    monkeypatch.setattr(server, "settings",
                        types.SimpleNamespace(clients={"k": c}), raising=False)
    # avoid session_request's request-store dependency
    monkeypatch.setattr(legacy, "session_request", lambda s: None, raising=False)
    sess = types.SimpleNamespace(id="s1")
    # unknown REQUEST falls through the dispatch; only the online-refresh runs
    legacy.msg_response({"SRC": "k", "DEST": "SRV", "REQUEST": "__nop__", "PAYLOAD": None}, sess)
    assert c.isOnline is True
    assert c.lastSeen > 0.0


def test_unknown_src_is_a_noop(monkeypatch):
    monkeypatch.setattr(server, "settings",
                        types.SimpleNamespace(clients={}), raising=False)
    monkeypatch.setattr(legacy, "session_request", lambda s: None, raising=False)
    sess = types.SimpleNamespace(id="s1")
    # SRC not a known client -> no crash, no-op
    legacy.msg_response({"SRC": "ghost", "DEST": "SRV", "REQUEST": "__nop__", "PAYLOAD": None}, sess)
