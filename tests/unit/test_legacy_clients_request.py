"""The legacy CLIENTS request (audit 1.10).

The handler's condition was inverted, and its guarded branch dereferenced the
very key the condition said was absent:

    logging.debug(msg["PAYLOAD"])           # unguarded deref, before the check
    if 'PAYLOAD' not in msg:
        response["PAYLOAD"] = server.settings.clients[msg["PAYLOAD"]]   # KeyError
    else:
        response["PAYLOAD"] = server.settings.clients                   # always taken

So the single-client lookup was unreachable and every request serialized the
entire clients dict. Nothing in the repo sends CLIENTS (no JS, HTML, tool or
test), so the full-dict response is the only behavior anything has observed --
it is preserved for the no-key case, and the by-key lookup is made to work.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import jsonpickle
import server


def _session(session_id="sess1"):
    s = MagicMock()
    s.id = session_id
    s.request = MagicMock()
    s.request.remote = "127.0.0.1"
    s.request.headers = {"User-Agent": "Test Browser"}
    return s


def _ask(payload, mock_settings, include_payload=True):
    server.settings = mock_settings
    msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "CLIENTS"}
    if include_payload:
        msg["PAYLOAD"] = payload
    return jsonpickle.decode(server.msg_response(msg, _session()))["PAYLOAD"]


def _two_clients(mock_settings):
    for key in ("alpha", "beta"):
        c = server.Client()
        c.friendlyName = key
        mock_settings.clients[key] = c
    return mock_settings


def test_missing_payload_returns_all_clients(mock_settings):
    _two_clients(mock_settings)
    got = _ask(None, mock_settings, include_payload=False)
    assert set(got.keys()) == {"alpha", "beta"}


def test_empty_payload_returns_all_clients(mock_settings):
    _two_clients(mock_settings)
    assert set(_ask("", mock_settings).keys()) == {"alpha", "beta"}


def test_known_key_returns_just_that_client(mock_settings):
    _two_clients(mock_settings)
    got = _ask("alpha", mock_settings)
    assert got.friendlyName == "alpha"


def test_unknown_key_reports_an_error(mock_settings):
    _two_clients(mock_settings)
    got = _ask("nope", mock_settings)
    assert "error" in got


def test_missing_payload_does_not_raise(mock_settings):
    """The old unguarded logging.debug(msg["PAYLOAD"]) raised before dispatch."""
    _two_clients(mock_settings)
    _ask(None, mock_settings, include_payload=False)   # must not raise
