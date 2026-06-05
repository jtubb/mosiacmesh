"""SockJS connection lifecycle + dispatch to legacy or typed handlers.

Owns the connect/disconnect callbacks registered on the sockjs manager.
For each message, dispatches to either:
  - mosaicmesh.websocket.legacy.msg_response (REQUEST-based, iPad-1 fleet)
  - mosaicmesh.websocket.typed.handle_websocket_message (type-based, newer)

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
        # Enhanced discovery notification with client info
        client_info = {
            "sessionId": session.id,
            "ip": server._client_ip(session.request) if hasattr(session, 'request') else "unknown",
            "userAgent": session.request.headers.get('User-Agent', '') if hasattr(session, 'request') else "",
            "timestamp": time.time()
        }
        discovery_announcement = {
            "REQUEST": "DEVICE_DISCOVERED",
            "PAYLOAD": client_info
        }
        manager.broadcast(jsonpickle.encode(discovery_announcement))

        # Also send traditional JOIN for backward compatibility
        manager.broadcast(jsonpickle.encode({"REQUEST": "JOIN", "PAYLOAD":session.id}))

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
        # Enhanced disconnect notification
        handle_client_disconnect(session.id)
        manager.broadcast(jsonpickle.encode({"REQUEST": "DISC", "PAYLOAD":session.id}))
