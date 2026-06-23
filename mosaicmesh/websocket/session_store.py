"""Per-SockJS-session request stash.

sockjs only sets ``Session.request`` while a transport request is actively being
handled. For the polling transports the iPad-1 fleet falls back to (its websocket
handshake is the old Hixie-76 form aiohttp rejects), a MESSAGE is delivered via a
separate ``xhr_send`` request, so ``session.request`` is ``None`` at the moment the
handler runs. ``msg_response`` needs the *connecting* request (User-Agent, remote IP)
for every message — most importantly REGISTER — so we remember the request at OPEN
(where it is valid) and fall back to it whenever ``session.request`` is ``None``.

Without this, ``session.request.headers[...]`` in ``msg_response`` raised
``AttributeError: 'NoneType'`` on every message, which crashed the handler before any
REQUEST was dispatched — so on a server restart the whole fleet failed to
re-REGISTER and playback never resumed.

Lifecycle: ``remember_request`` at OPEN, ``forget_request`` at CLOSE, keyed by
``session.id``.
"""

# session.id -> the aiohttp request captured at OPEN
_open_requests = {}


def remember_request(session):
    """Stash the OPEN request for this session (no-op if it has none)."""
    req = getattr(session, "request", None)
    sid = getattr(session, "id", None)
    if req is not None and sid is not None:
        _open_requests[sid] = req


def forget_request(session_id):
    """Drop the stashed request for a closed session (no-op if absent)."""
    _open_requests.pop(session_id, None)


def session_request(session):
    """The best available request for this session: the live ``session.request`` if
    present, else the one remembered at OPEN, else ``None``. Callers must still treat
    ``None`` gracefully (a session whose OPEN had no request)."""
    req = getattr(session, "request", None)
    if req is not None:
        return req
    return _open_requests.get(getattr(session, "id", None))
