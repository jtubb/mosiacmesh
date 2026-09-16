"""SockJS broadcast helpers. Reference the shared socketmanager and the
global settings via the server module (lazy at call time) so the
circular import between server.py and mosaicmesh.broadcast resolves
cleanly."""
import asyncio
import logging


def _send_to_session(session_id, encoded_message):
    """Look up a sockjs Session by its id and call .send() directly. Returns
    True if delivered, False if no such session.

    Why we need this: the previous broadcast_to_*() helpers called
    socketmanager.broadcast() once PER addressed client, which sent each
    message to ALL connected sessions and relied on the iPad-side DEST
    filter (index.html line 688: `if DEST == getUDID() || DEST == 'ALL'`).
    For a 24-iPad group on a 24-iPad fleet (each iPad ~~ 1-2 sockjs
    sessions due to xhr_streaming fallback), one logical PLAY/STOP/PAUSE
    command became 24 broadcasts x ~40 sessions = ~960 serialized socket
    writes through the event loop -- visible to operators as command lag.
    Targeted send via socketmanager.get(session_id) skips every session
    that isn't the intended recipient: O(N) instead of O(N*M)."""
    import server
    if server.socketmanager is None or not session_id:
        return False
    sess = server.socketmanager.get(session_id, default=None)
    if sess is None:
        return False  # session has since disconnected/expired
    try:
        sess.send(encoded_message)
        return True
    except Exception as e:
        logging.debug("_send_to_session(%s) failed: %s", session_id, e)
        return False


# How long a targeted send keeps chasing a reconnecting client before giving up
# and using the fleet-broadcast last resort. attempts x delay should comfortably
# cover the observed reconnect gap (100-500ms typical, 1-2s under load).
_GAP_RETRY_ATTEMPTS = 12
_GAP_RETRY_DELAY = 0.25


def _current_session_id(client_id, client):
    """Re-read the client's session id: REGISTER swaps it during the gap."""
    import server
    settings = getattr(server, "settings", None)
    fresh = settings.clients.get(client_id) if settings is not None else None
    return getattr(fresh if fresh is not None else client, "clientID", "")


async def _retry_then_fallback(client_id, encoded_message, client):
    """Chase the reconnecting client with TARGETED sends, then fall back once."""
    import server
    for _ in range(_GAP_RETRY_ATTEMPTS):
        await asyncio.sleep(_GAP_RETRY_DELAY)
        if _send_to_session(_current_session_id(client_id, client), encoded_message):
            return
    if server.socketmanager is not None:
        logging.warning("_deliver: %s never reappeared; fleet-broadcast fallback",
                        client_id)
        server.socketmanager.broadcast(encoded_message)


def _deliver(client_id, encoded_message, client):
    """Try a targeted session send; chase the client across a reconnect gap.

    Why a gap exists: after a Safari restart (post-reboot, ?tdbg switch, or
    crash) the iPad opens a NEW SockJS session with a fresh session.id, but
    client.clientID still points at the OLD (dead) session until that
    connection's REGISTER arrives. During the gap a targeted send drops every
    message addressed to that iPad -- in the field, 19 of 22 reconnected iPads
    missed PREPARE this way.

    Why we no longer broadcast on the FIRST miss: socketmanager.broadcast sends
    one client's per-client message to EVERY session. That is tolerable for a
    single straggler, but a reconnect storm puts many clients in the gap at once,
    so a single group PLAY/PREPARE becomes K full-fleet blasts and each iPad
    absorbs K x the payload in a burst. That is precisely the mmws
    rx-reassembly overflow -> ws teardown -> reconnect-loop path, and it feeds
    itself: the overflow causes more reconnects (audit 1.2).

    So a miss schedules targeted RETRIES instead, re-reading clientID each time
    so the send lands as soon as REGISTER swaps the session in. The fleet
    broadcast remains only as a last resort for a client that never returns.
    With no running loop (synchronous callers) the original immediate fallback
    is kept, since there is nothing to schedule a retry onto."""
    import server
    if _send_to_session(getattr(client, "clientID", ""), encoded_message):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        if server.socketmanager is not None:
            server.socketmanager.broadcast(encoded_message)
        return
    loop.create_task(_retry_then_fallback(client_id, encoded_message, client))


def broadcast_all(response_dict):
    """Fleet-wide broadcast: encode ONCE and hand the same bytes to every
    session via socketmanager.broadcast. Used by the TIMEBEAT clock beat,
    where the payload is identical for every client (no per-client DEST) —
    a single serialization + fan-out is both cheapest and maximally
    common-mode (every screen aligns to the identical bytes)."""
    import server
    import jsonpickle
    if server.socketmanager is None:
        return
    response_dict["DEST"] = "ALL"
    encoded = jsonpickle.encode(response_dict)
    server.socketmanager.broadcast(encoded)


def broadcast_to_client(client_id, response_dict):
    """Send a message to a single client (identified by its clientKey, i.e.
    the cookie-based UDID). Routes to the iPad's specific sockjs session
    when possible; falls back to broadcast (filtered by client-side DEST)
    when the session lookup misses -- typically during the reconnect gap."""
    import server
    import jsonpickle
    client = server.settings.clients.get(client_id)
    if not client:
        return
    response_dict["DEST"] = client_id
    _deliver(client_id, jsonpickle.encode(response_dict), client)


# Sentinel DEST used to encode a group message once and substitute each
# client's DEST by string replace (T3.1). Null-byte-wrapped so it can't collide
# with any real payload content or UDID.
_DEST_SENTINEL = "\x00MM_DEST\x00"


def broadcast_to_display_group(display_id, response_dict):
    """Send a per-client message to every client in a display group. Each
    iPad gets the message addressed to its own DEST (the contract the
    client-side filter expects). Targeted per-session in the steady state;
    falls back to broadcast for any iPad whose session is in the reconnect
    gap.

    T3.1: the message is identical for every client except DEST, so encode it
    ONCE with a sentinel DEST and substitute each client's DEST by string
    replace — saving N-1 jsonpickle.encode calls on the (latency-sensitive)
    PLAY/PREPARE/STOP fan-out. Both the sentinel and each replacement are
    produced by jsonpickle.encode, so the JSON escaping is identical and the
    substituted message is byte-for-byte what a per-client encode would emit."""
    import server
    import jsonpickle
    if server.socketmanager is None:
        return
    response_dict["DEST"] = _DEST_SENTINEL
    template = jsonpickle.encode(response_dict)
    sentinel_json = jsonpickle.encode(_DEST_SENTINEL)
    for client_id, client in server.settings.clients.items():
        if client.displayID != display_id:
            continue
        msg = template.replace(sentinel_json, jsonpickle.encode(client_id))
        _deliver(client_id, msg, client)
