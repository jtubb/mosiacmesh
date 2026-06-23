"""Regression tests for the session.request-None crash in the legacy SockJS handler.

sockjs delivers polling-transport MESSAGEs (the iPad-1 fallback) with
session.request == None; msg_response used to deref it and crash on every message,
stranding the fleet on a server restart. msg_response now falls back to the request
captured at OPEN (session_store) and guards None.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

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

from mosaicmesh.websocket import session_store


def test_session_store_remember_fallback_forget():
    open_sess = MagicMock(); open_sess.id = "s2"
    open_sess.request.headers = {"User-Agent": "UA-remembered"}
    open_sess.request.remote = "10.0.0.5"
    session_store.remember_request(open_sess)

    # Later MESSAGE on the same session: request is None -> fall back to the OPEN request.
    msg_sess = MagicMock(); msg_sess.id = "s2"; msg_sess.request = None
    assert session_store.session_request(msg_sess) is open_sess.request

    session_store.forget_request("s2")
    assert session_store.session_request(msg_sess) is None


def test_session_store_prefers_live_request():
    sess = MagicMock(); sess.id = "s3"
    assert session_store.session_request(sess) is sess.request  # live request wins


def test_msg_response_survives_none_request():
    # The actual bug: a MESSAGE with session.request None (and nothing stashed) must NOT
    # crash msg_response — it should still handle the request. SERVERTIME exercises the
    # top of msg_response (the two debug-log derefs that used to raise AttributeError).
    server.settings = server.Settings()
    server.socketmanager = MagicMock()
    sess = MagicMock(); sess.id = "none1"; sess.request = None
    session_store.forget_request("none1")  # ensure no stash

    import jsonpickle
    resp = server.msg_response(
        {"SRC": "none1", "DEST": "SRV", "REQUEST": "SERVERTIME", "PAYLOAD": {}}, sess)
    decoded = jsonpickle.decode(resp)               # msg_response returns an encoded string
    assert decoded["REQUEST"] == "SERVERTIME"        # handled, no AttributeError crash
    assert isinstance(decoded["PAYLOAD"], int)       # server time emitted
