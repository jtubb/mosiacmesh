"""Async type-based websocket message handler — intended replacement for the
legacy REQUEST-based protocol (mosaicmesh.websocket.legacy.msg_response).

Both protocols coexist per CLAUDE.md's dual-protocol convention. They will
funnel through mosaicmesh/websocket/dispatch.py's ws_handler — but this
handler is NOT YET WIRED INTO ws_handler at all (ws_handler currently calls
only msg_response). Task 12 (dispatch.py) will connect both. The current
state means handle_websocket_message is exercised only by direct test calls
(test_websocket_handlers.py) and the re-export from server.py keeps it
visible in that namespace. Per the spec, the legacy protocol must not be
removed — the 24 iPad-1 production displays use it.

Substitutions applied (pure relocation, no semantic changes):
  - bare `settings` -> `server.settings`
  - bare `socketmanager` -> `server.socketmanager`
  - bare `device_detector` -> `server.device_detector`
  - helpers still in server.py (_client_ip, _device_field, _device_type_str,
    auto_configure_client already in mosaicmesh.api.discovery) -> server.<name>
    or direct import as appropriate
  - broadcast_to_display_group already moved to mosaicmesh.broadcast ->
    imported at module level as bare name
"""
import logging
import time
import jsonpickle

from mosaicmesh.broadcast import broadcast_to_display_group
from mosaicmesh.state import Client
# Note: auto_configure_client is NOT imported at module level even though it
# lives in mosaicmesh.api.discovery (Task 9). It's accessed via
# server.auto_configure_client inside the function body so existing tests
# that use `patch('server.auto_configure_client')` continue to intercept it.
# Same pattern as legacy.py's lazy server._run_device_script (Task 10).
#
# Asymmetry note: legacy.py imports auto_configure_client at module level
# because test_client_management.py patches at a different level (it mocks
# the underlying display dict directly, not auto_configure_client). The two
# files diverge intentionally on this import strategy. If a future test for
# the LEGACY protocol's auto_configure_client path starts using
# patch('server.auto_configure_client'), legacy.py would silently bypass it
# and need to switch to the lazy pattern here.


async def handle_websocket_message(session, message_data):
    """Dispatch a structured ('type'-based) WebSocket message.

    Per-client delivery still flows through the central socketmanager + DEST
    routing used elsewhere; direct replies use session.send.
    """
    import server

    if not isinstance(message_data, dict):
        return  # ignore malformed frames without raising

    msg_type = message_data.get('type')

    if msg_type == 'clientInfo':
        client = server.settings.clients.setdefault(session.id, Client())
        client.clientID = session.id
        client.friendlyName = message_data.get('friendlyName', client.friendlyName)
        client.deviceWidth = message_data.get('deviceWidth', client.deviceWidth)
        client.deviceHeight = message_data.get('deviceHeight', client.deviceHeight)
        client.deviceType = message_data.get('deviceType', client.deviceType)
        client.userAgent = message_data.get('userAgent', client.userAgent)
        if getattr(session, 'request', None) is not None:
            client.ip = server._client_ip(session.request)
        client.lastSeen = time.time()
        client.isOnline = True
        # synced stays False until the client emits TIME_SYNCED (see the
        # REGISTER handler comment) -- REGISTER is page-bootstrap, not
        # clock-sync.
        client.connectionCount += 1
        # Best-effort fingerprinting (fields may be methods or plain values)
        try:
            device = server.device_detector.parse(client.userAgent)
            client.osName = server._device_field(device.os_name) or client.osName
            client.osVersion = server._device_field(device.os_version) or client.osVersion
            client.deviceBrand = server._device_field(device.device_brand) or client.deviceBrand
            client.deviceModel = server._device_field(device.device_model) or client.deviceModel
            detected_type = server._device_type_str(server._device_field(device.device_type))
            if detected_type and not message_data.get('deviceType'):
                client.deviceType = detected_type
        except Exception as e:
            logging.debug(f"Device detection skipped: {e}")
        server.auto_configure_client(session.id, client)

    elif msg_type == 'ready':
        client = server.settings.clients.get(session.id)
        if client:
            client.ready = message_data.get('ready', True)
            client.lastSeen = time.time()

    elif msg_type == 'heartbeat':
        client = server.settings.clients.get(session.id)
        if client:
            client.lastSeen = time.time()
            client.isOnline = True
        await session.send(jsonpickle.encode({"REQUEST": "HEARTBEAT", "PAYLOAD": "ACK"}))

    elif msg_type == 'displayData':
        # Relay to peers in the same display group via the central manager
        display_id = message_data.get('displayID')
        if server.socketmanager is not None and display_id is not None:
            broadcast_to_display_group(display_id, {
                "REQUEST": "displayData",
                "PAYLOAD": message_data.get('data')
            })
        await session.send(jsonpickle.encode({"REQUEST": "displayData", "PAYLOAD": "ACK"}))

    else:
        logging.debug(f"Unknown websocket message type: {msg_type}")
