"""SockJS connection lifecycle + dispatch to the legacy handler.

Owns the connect/disconnect callbacks registered on the sockjs manager.
For each incoming MESSAGE event, dispatches to:
  - mosaicmesh.websocket.legacy.msg_response (REQUEST-based, iPad-1 fleet)

NOT YET WIRED: mosaicmesh.websocket.typed.handle_websocket_message
exists as an intended replacement for the legacy protocol but is
currently called only from direct test invocations
(server.handle_websocket_message via test_websocket_handlers.py). The
typed handler is NOT dispatched from ws_handler in this file —
ws_handler's MESSAGE branch only invokes msg_response. Wiring the
typed protocol in is a future task; until then the re-export in
server.py keeps the test import path stable.

Completes the mosaicmesh/websocket/ subpackage: legacy.py (Task 10) +
typed.py (Task 11) + this file. After this task, server.py's only
remaining websocket touchpoint is the SockJS endpoint registration
in __main__.

Substitutions applied (pure relocation, no semantic changes):
  - bare `settings` -> `server.settings` (lazy import server)
  - bare `_client_ip` -> `server._client_ip` (still in server.py)
  - msg_response imported at module level from mosaicmesh.websocket.legacy
  - handle_client_disconnect called as bare name (defined in this file)
"""
import logging
import time
import jsonpickle
import sockjs

from mosaicmesh.websocket.legacy import msg_response
from mosaicmesh.websocket.session_store import remember_request, forget_request


def handle_client_disconnect(session_id):
    """Enhanced client disconnect handling"""
    import server
    # Find and update client last seen time
    for client_key, client in server.settings.clients.items():
        if client.clientID == session_id:
            client.lastSeen = time.time()
            client.isOnline = False
            client.synced = False
            client.ready = False
            logging.info(f"Client {client.friendlyName or client_key} disconnected")
            break


async def ws_handler(manager, session, msg):
    # sockjs >=0.12 handler signature: (manager, session, msg).
    # Message types are sockjs.MsgType.* and msg carries .type / .data.
    import server
    logging.debug("WS_HANDLER")
    if manager is None:
        return
    if msg.type == sockjs.MsgType.OPEN:
        # Remember the connecting request NOW (it's valid at OPEN); msg_response falls
        # back to it because session.request is None for xhr_send-delivered MESSAGEs.
        remember_request(session)
        # NB: DEVICE_DISCOVERED and JOIN broadcasts were removed here (T1.3).
        # Both fired on EVERY OPEN to ALL sessions, and NOTHING consumed them
        # (no JS/Python listener) — pure reconnect-storm noise (2 × M socket
        # writes per connect × N reconnecting clients). The admin roster
        # updates from the REGISTER-driven CLIENTS_CAME_ONLINE batch instead.

        # Replay current renderStatus to the newly-connected session for any
        # display with a non-empty status. Without this, an admin who
        # refreshes the playlist page during an in-flight render loses the
        # "rendering..." badge (the original broadcast happened before they
        # reconnected). Sent only to this session via session.send() to
        # avoid pestering already-connected clients.
        try:
            if server.settings is not None and getattr(server.settings, "displays", None):
                for _did, _disp in server.settings.displays.items():
                    _st = getattr(_disp, "renderStatus", "")
                    if _st:
                        session.send(jsonpickle.encode({
                            "REQUEST": "RENDER_STATUS",
                            "PAYLOAD": {"displayID": _did, "status": _st}}))
        except Exception as _e:
            logging.debug("ws OPEN: render-status replay failed: %s", _e)


    elif msg.type == sockjs.MsgType.MESSAGE:
        session.send(msg_response(jsonpickle.decode(msg.data),session))
    elif msg.type == sockjs.MsgType.CLOSED:
        forget_request(session.id)
        handle_client_disconnect(session.id)
        # NB: the DISC broadcast was removed here (T1.3) — like JOIN/
        # DEVICE_DISCOVERED on OPEN, it fired to ALL sessions on every
        # disconnect and had no consumer. The admin offline roster comes from
        # process()'s CLIENTS_WENT_OFFLINE sweep, not per-session DISC.
