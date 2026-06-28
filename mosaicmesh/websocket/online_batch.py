"""Batches CLIENTS_CAME_ONLINE broadcasts during a reconnect storm.

Each REGISTER used to fire its own `CLIENTS_CAME_ONLINE {devices:[one]}`
broadcast to ALL sockjs sessions. At fleet scale a reconnect storm (N iPads
reloading at once) becomes N broadcasts × M sessions socket writes — the loop
saturates for seconds and every display parses N admin-only messages it
ignores.

queue_client_online() buffers each (re)connecting device, deduped by
clientKey, and schedules ONE consolidated broadcast a short debounce later.
The admin consumer (js/timeline/timeline/sockjs-status.js) already iterates
`payload.devices`, so a multi-device payload needs no client change.

Latency tradeoff: the admin's online indicator updates up to
_ONLINE_BATCH_SECS late instead of instantly. ~0.5 s is imperceptible while
collapsing the storm from N broadcasts to ~1.

No running loop (unit tests call msg_response directly) -> emit immediately,
preserving the pre-batch behavior so existing tests still observe the send.
"""
import logging
import jsonpickle

_ONLINE_BATCH_SECS = 0.5

# clientKey -> device dict; dict (not list) so a client that bounces several
# times inside one window collapses to a single entry.
_pending_online = {}
_flush_scheduled = False


def queue_client_online(device):
    """Buffer one device for the next batched CLIENTS_CAME_ONLINE broadcast.

    `device` is the per-client dict {clientKey, displayID, isOnline,
    friendlyName}. Never raises into the REGISTER handler."""
    global _flush_scheduled
    try:
        key = device.get("clientKey")
        if not key:
            return
        _pending_online[key] = device
        if _flush_scheduled:
            return
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop (direct test call) -> behave like the old inline
            # broadcast: flush now.
            _flush_online()
            return
        _flush_scheduled = True
        loop.call_later(_ONLINE_BATCH_SECS, _flush_online)
    except Exception as e:
        logging.debug("queue_client_online failed (continuing): %s", e)


def _flush_online():
    """Emit the buffered devices as ONE CLIENTS_CAME_ONLINE broadcast."""
    global _flush_scheduled
    _flush_scheduled = False
    if not _pending_online:
        return
    devices = list(_pending_online.values())
    _pending_online.clear()
    import server
    if server.socketmanager is None:
        return
    try:
        server.socketmanager.broadcast(jsonpickle.encode({
            "REQUEST": "CLIENTS_CAME_ONLINE",
            "PAYLOAD": {"devices": devices},
        }))
    except Exception as e:
        logging.debug("CLIENTS_CAME_ONLINE batch broadcast failed: %s", e)
