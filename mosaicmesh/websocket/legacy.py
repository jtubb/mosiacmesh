"""Legacy SockJS REQUEST-based message dispatch (msg_response).

This is the protocol surface for the iPad-1 ES5 display clients in
production. It MUST NOT change semantics -- clients in production
depend on byte-identical behavior (CLAUDE.md: 'do not remove').
Moved here byte-identically from server.py; the only changes from
the original are the substitutions documented below.

The 'typed' async protocol that's the intended replacement lives in
mosaicmesh/websocket/typed.py -- coexistence per the dual-protocol
convention. Both protocols funnel through mosaicmesh/websocket/
dispatch.py's ws_handler.

Substitutions applied (pure relocation, no semantic changes):
  - bare `settings` -> `server.settings`
  - bare `socketmanager` -> `server.socketmanager`
  - helpers still in server.py (identifyDisplays, generateAruco,
    _client_ip, _engine_str, _device_type_str, _is_legacy_ipad_signal,
    _maybe_release, _auto_arm_client) -> `server.<name>`
  - all other helpers were already moved to mosaicmesh.* modules and
    are imported at module top as bare names
"""
import logging
import time
import asyncio
import datetime
import uuid
import jsonpickle

from device_detector import DeviceDetector

from mosaicmesh.state import (
    Client,
    Display,
    Playlist,
    Schedule,
    PlayState,
)
from mosaicmesh.persistence import (
    saveSettings,
    save_settings_incremental,
    cleanup_old_clients,
    request_save,
)
from mosaicmesh.broadcast import (
    broadcast_to_client,
    broadcast_to_display_group,
)
from mosaicmesh.websocket.online_batch import queue_client_online
from mosaicmesh.api.discovery import (
    auto_configure_client,
    get_discovered_devices,
    sync_new_client_to_group,
)
# Note: _run_device_script is NOT imported at module level here.
# Tests patch server._run_device_script; the RUN_SCRIPT handler accesses
# it via server._run_device_script so that patch.object(server, ...) works.
# See the lazy `import server` at the top of msg_response.
from mosaicmesh.scheduling import (
    schedule_active_at,
    _FREQ_MAP,
    _parse_date,
    _hhmm_to_min,
)
from mosaicmesh.render import (
    _broadcast_per_client_preload,
    _build_media_elements,
    _is_renderable,
    _begin_prepare,
    render_group_async,
    _start_group_playback,
    _stop_group_playback,
    _apply_playlist,
    _group_is_calibrated,
    render_token,
    _set_render_state,
    is_playlist_ready,
    RENDER_QUEUED,
    RENDER_RENDERING,
)
from mosaicmesh.calibration import (
    _group_clients,
)
from mosaicmesh.websocket.session_store import session_request


def handle_cache_ack(msg):
    """CACHED / CACHE_FAILED from a client: record state, advance that group's
    throttle window, and grant PRECACHE to the next waiting client."""
    import sys
    _server = globals().get("server") or sys.modules.get("server")
    if _server is None:
        import server as _server
    src = msg.get("SRC")
    token = (msg.get("PAYLOAD") or {}).get("token")
    group = getattr(_server, "precache_group", {}).get(src)
    if msg["REQUEST"] == "CACHED":
        # Mark the segment cached on the Client so _per_client_items rewrites its item to
        # the local http://127.0.0.1:8080/<segname>.mp4 URL (the play-from-local path).
        # cachedSegments holds seg keys: 'seg_<rt>_<i>' -> '<rt>_<i>' (strip 'seg_');
        # 'full_<rt>_<i>' stays verbatim (that's the key _per_client_items checks).
        settings = getattr(_server, "settings", None)
        client = settings.clients.get(src) if settings else None
        if client is not None and token:
            segkey = token[4:] if token.startswith("seg_") else token
            cs = getattr(client, "cachedSegments", None)
            if not isinstance(cs, set):
                cs = set(cs) if cs else set()
                client.cachedSegments = cs
            cs.add(segkey)
    win = getattr(_server, "precache_windows", {}).get(group)
    if win is not None:
        nxt = win.advance(src, time.time())
        if nxt is not None:
            url = getattr(_server, "precache_urls", {}).get(nxt)
            _server._send_precache(nxt, url, getattr(_server, "precache_segtoken", {}).get(nxt))


def client_warmable(client):
    """True if the device can pre-decode a second <video> for clip warming.
    False for the legacy iPad-1 class (iOS/Safari <= 5 / old WebKit), which cannot
    sustain two concurrent inline decodes (verified 2026-07-05). Unknown => True
    (assume modern; the client warm path no-ops safely if it can't actually warm)."""
    def _major(v):
        try:
            return int(str(v or "").split(".")[0])
        except (ValueError, TypeError):
            return None
    os_name = (getattr(client, "osName", "") or "").lower()
    os_major = _major(getattr(client, "osVersion", ""))
    if os_name in ("ios", "iphone os") and os_major is not None and os_major <= 5:
        return False
    return True


def apply_cache_capability(client, payload):
    """Client-reported cache capability on REGISTER (replaces the deleted SSH probe).
    Maps the client's mmCache backend NAME to a server cacheMode:
      - cacheBackend == "modern"  -> "service-worker": the modern client keeps the
        central /media/ URL and its Service Worker serves it from the Cache-API.
        (It does NOT serve at 127.0.0.1:8080 — only the mmvideo tweak does.)
      - else if cacheCapable truthy (mmvideo tweak, or a legacy client that reports
        only cacheCapable) -> "lighttpd-localhost": _per_client_items rewrites its
        play URL to http://127.0.0.1:8080/<name>, which the mmvideo tweak maps to
        the pulled local file.
    Only upgrades from the default 'none'; a later ANNOUNCE_CACHE_MODE 'none'
    (client-side local-play failure) remains authoritative."""
    p = payload or {}
    if client is None or getattr(client, "cacheMode", "none") != "none":
        return
    if p.get("cacheBackend") == "modern":
        client.cacheMode = "service-worker"
    elif p.get("cacheCapable"):
        client.cacheMode = "lighttpd-localhost"


def msg_response(msg,session):
    import server
    clientid = session.id
    # Any message from a known client refreshes its online status (not just REGISTER):
    # a client actively communicating IS online, even if a SockJS/ws reconnect resumed
    # without re-REGISTERing. Without this, isOnline is set True ONLY at REGISTER, so a
    # live-but-not-recently-registered client reads offline -> _client_is_push_eligible
    # False -> empty seg_push_targets -> NO client-pull PRECACHE. The REGISTER handler
    # also sets these (here it's a harmless no-op until the client row exists, then real
    # on every subsequent message).
    _src = msg.get("SRC") if isinstance(msg, dict) else None
    if _src:
        _c = server.settings.clients.get(_src)
        if _c is not None:
            _c.isOnline = True
            _c.lastSeen = time.time()
    # session.request is None for xhr_send-delivered MESSAGEs (the iPad-1 polling
    # transport), so fall back to the request captured at OPEN. Still guard None — a
    # session whose OPEN had no request must not crash the handler before dispatch.
    req = session_request(session)
    if req is not None:
        logging.debug(req.headers.get('User-Agent'))

    response = {"DEST":clientid,"REQUEST": msg["REQUEST"], "PAYLOAD": {}}

    if req is not None:
        logging.debug(req.remote)
    logging.debug(msg["SRC"])

    if(msg["REQUEST"] == "SERVERTIME"):
        response["PAYLOAD"] = int(time.time()*1000)

    elif(msg["REQUEST"] == "DISPLAYS"):
        response["PAYLOAD"] = server.settings.displays

    elif(msg["REQUEST"] == "IDENTIFYDISPLAY"):
        server.identifyDisplays(msg["PAYLOAD"]['group'],msg["PAYLOAD"]['id'])

    elif(msg["REQUEST"] == "UPDATEDISPLAY"):
        # Cache client reference to avoid repeated dictionary lookups
        client_id = msg["PAYLOAD"]["clientID"]
        client = server.settings.clients.get(client_id)
        if client:
            if('friendlyName' in msg["PAYLOAD"]):
                client.friendlyName = msg["PAYLOAD"]["friendlyName"]
                client.nameIsCustom = True   # user-set name: DNS won't override it
            if('displayID' in msg["PAYLOAD"]):
                client.displayID = msg["PAYLOAD"]["displayID"]

    elif(msg["REQUEST"] == "UPDATEDISPLAYGROUP"):
        if(msg["PAYLOAD"]["newID"] != 'Default'):
            if(msg["PAYLOAD"]["newID"] is not None):
                server.settings.displays.setdefault(msg["PAYLOAD"]["newID"], Display())
                if(msg["PAYLOAD"]["oldID"] in server.settings.displays):
                    server.settings.displays[msg["PAYLOAD"]["newID"]] = server.settings.displays.pop(msg["PAYLOAD"]["oldID"])
            else:
                server.settings.displays.pop(msg["PAYLOAD"]["oldID"])
                for key in server.settings.clients.keys():
                    if(server.settings.clients[key].displayID == msg["PAYLOAD"]["oldID"]):
                        server.settings.clients[key].displayID = 'Default'

    elif(msg["REQUEST"] == "CLIENTS"):
        logging.debug(msg["PAYLOAD"])
        if 'PAYLOAD' not in msg:
            response["PAYLOAD"] = server.settings.clients[msg["PAYLOAD"]]
        else:
            response["PAYLOAD"] = server.settings.clients

    elif(msg["REQUEST"] == "SYN"):
        client = server.settings.clients.get(msg["PAYLOAD"])
        if client:
            client.synced = False
        response["PAYLOAD"] = "ACK"

    elif(msg["REQUEST"] == "SYNACK"):
        client = server.settings.clients.get(msg["PAYLOAD"])
        if client:
            client.synced = True
        response["PAYLOAD"] = "SYNACK"

    elif(msg["REQUEST"] == "ANNOUNCE_CACHE_MODE"):
        # Client-announced cache capability. The client side knows whether
        # it has a working Service Worker, lighttpd, or neither; it tells
        # us so we can route PLAY payload URLs correctly. The whitelist
        # below prevents a malicious or bug-induced client from setting
        # an arbitrary string (which would break _resolve_media_url's
        # logic in subtle ways).
        client = server.settings.clients.get(msg["SRC"])
        mode = (msg.get("PAYLOAD") or {}).get("mode")
        if client and mode in ("none", "lighttpd-localhost", "service-worker"):
            client.cacheMode = mode
        response["PAYLOAD"] = {"cacheMode": getattr(client, "cacheMode", "none")}

    elif(msg["REQUEST"] == "CACHED" or msg["REQUEST"] == "CACHE_FAILED"):
        handle_cache_ack(msg)

    elif(msg["REQUEST"] == "REMOVE_CLIENT"):
        # Admin-initiated removal of a single device. The device re-registers
        # (as new) if it ever reconnects — this only clears the stale record.
        payload = msg.get("PAYLOAD") or {}
        target = payload.get("clientID") if isinstance(payload, dict) else payload
        removed = server.settings.clients.pop(target, None)
        response["PAYLOAD"] = {"removed": target if removed is not None else None}
        if removed is not None:
            saveSettings()
            logging.info(f"Removed client {target} (admin request)")
            server.socketmanager.broadcast(jsonpickle.encode(
                {"REQUEST": "DEVICE_REMOVED", "PAYLOAD": {"clientKey": target}}))

    elif(msg["REQUEST"] == "CLEAR_OFFLINE_CLIENTS"):
        # Bulk-purge every currently-offline device (max_age 0 = any age).
        count = cleanup_old_clients(max_age_seconds=0)
        response["PAYLOAD"] = {"removed": count}
        if count:
            server.socketmanager.broadcast(jsonpickle.encode(
                {"REQUEST": "DEVICE_REMOVED", "PAYLOAD": {"cleared": count}}))

    elif(msg["REQUEST"] == "REGISTER"):
        is_new_client = msg["SRC"] not in server.settings.clients
        server.settings.clients.setdefault(msg["SRC"], Client())
        # Cache client reference to avoid repeated dictionary lookups
        client = server.settings.clients[msg["SRC"]]

        # Enhanced registration with discovery tracking
        client.clientID = clientid
        client.userAgent = req.headers.get('User-Agent', '') if req is not None else ''
        client.deviceWidth = msg["PAYLOAD"]["width"]
        client.deviceHeight = msg["PAYLOAD"]["height"]
        # Rendered viewport (older clients omit it -> fall back to device res)
        client.canvasWidth = msg["PAYLOAD"].get("canvasWidth") or client.deviceWidth
        client.canvasHeight = msg["PAYLOAD"].get("canvasHeight") or client.deviceHeight
        _new_ip = server._client_ip(req) if req is not None else getattr(client, 'ip', '')
        if _new_ip != getattr(client, 'ip', ''):
            client.hostnameResolved = False   # new IP -> re-resolve its hostname
        client.ip = _new_ip
        client.lastSeen = time.time()
        client.isOnline = True
        # NB: do NOT set client.synced=True here -- synced means "clock-
        # sync handshake stable" (GoTime first WhenSynced callback fired),
        # not "page registered". REGISTER is much earlier than clock
        # stability. The client emits TIME_SYNCED separately when its
        # clock is stable; the TIME_SYNCED handler is the one that sets
        # client.synced=True.
        #
        # DO reset synced=False on REGISTER, though: a fresh REGISTER means
        # a fresh page load, so GoTime is starting its sync probes over,
        # and any prior synced flag is stale. Symmetric with SYN (which
        # also flips synced=False as the JS announces its new sync round).
        # Without this reset, the test harness's wait-for-fresh-sync gate
        # can't observe the kill+reload cycle because the synced flag
        # never flips false (handle_client_disconnect runs ~14s late on
        # the OLD session_id while REGISTER already overwrote clientID
        # to the NEW one -- the lookup fails, the flag stays True).
        client.synced = False
        client.connectionCount += 1

        # Device detection and fingerprinting. T2.2: DeviceDetector.parse() is
        # regex-heavy and runs INLINE on the event loop (msg_response is sync).
        # Skip the re-parse when this client's UA is unchanged from the last
        # detection — the overwhelmingly common reconnect case — so a 200-iPad
        # reconnect storm doesn't run 200 redundant parses back-to-back. A
        # genuinely new device (or a UA change) still parses once. Determinism:
        # parse() is a pure function of the UA, so caching by UA is exact.
        _ua = client.userAgent or ''
        if _ua != getattr(client, "_detectedUA", None) or not getattr(client, "osName", ""):
            device = DeviceDetector(_ua).parse()
            client.osName = device.os_name()
            client.osVersion = device.os_version()
            client.engine = server._engine_str(device.engine())
            client.deviceBrand = device.device_brand()
            client.deviceModel = device.device_model()
            client.deviceType = server._device_type_str(device.device_type())
            client._detectedUA = _ua

        # Recover legacy iPads that present a Mac user-agent (e.g. Safari
        # "Request Desktop Website"). The iPad identity is absent from such a
        # UA, so we reclassify from client-reported touch + screen-size signals
        # BEFORE auto_configure_client runs (it groups by deviceType).
        client.touch = bool(msg["PAYLOAD"].get("touch", False))
        if server._is_legacy_ipad_signal(client.deviceBrand, client.deviceType,
                                  client.deviceWidth, client.deviceHeight, client.touch):
            client.deviceType = "tablet"
            if not client.deviceModel:
                client.deviceModel = "iPad"
            logging.info(f"Reclassified {msg['SRC']} as iPad (Apple+desktop UA, "
                         f"touch, {client.deviceWidth}x{client.deviceHeight})")

        # Apply client-reported cache capability (replaces the SSH probe for capable clients).
        apply_cache_capability(client, msg.get("PAYLOAD"))

        # Auto-configuration for new clients
        if is_new_client:
            client.discoveryTime = time.time()
            auto_configure_client(msg["SRC"], client)

            # Notify admin interface of new device
            new_device_notification = {
                "REQUEST": "NEW_DEVICE_CONFIGURED",
                "PAYLOAD": {
                    "clientKey": msg["SRC"],
                    "friendlyName": client.friendlyName,
                    "deviceType": client.deviceType,
                    "displayID": client.displayID,
                    "autoConfigured": True
                }
            }
            server.socketmanager.broadcast(jsonpickle.encode(new_device_notification))

        # Sync EVERY (re)connecting client to its group, not just first-timers: a
        # reload/reconnect mid-playback must resume (re-send PRELOAD + PLAY with the
        # in-progress epoch). Idempotent — no-op unless the group is currently PLAY.
        sync_new_client_to_group(msg["SRC"], client)

        # PR-27 (2026-06-09): notify admin UI that a client came online.
        # Symmetric with CLIENTS_WENT_OFFLINE in server.process(): both now
        # use {devices: [...]} as the payload shape so the admin's
        # sockjs-status.js can handle them uniformly. Without this
        # broadcast the timeline's per-track online count stuck at its
        # last-hydrated value — an iPad reconnecting bumped client.isOnline
        # server-side but the admin saw nothing until a manual reload.
        # Batched: a reconnect storm queues N devices into ONE broadcast a
        # short debounce later (online_batch), instead of N broadcasts ×
        # M sessions. Never raises into REGISTER.
        queue_client_online({
            "clientKey": msg["SRC"],
            "displayID": client.displayID,
            "isOnline": True,
            "friendlyName": client.friendlyName,
        })

        # Enhanced success response with configuration info
        response["PAYLOAD"] = {
            "status": "SUCCESS",
            "displayID": client.displayID,
            "friendlyName": client.friendlyName,
            "autoConfigured": client.autoConfigured,
            "capabilities": client.capabilities
        }

        # Tell the client whether it may double-buffer video (clip warming). Per-client
        # (device-derived), so it can't ride the group-wide PREPARE payload.
        broadcast_to_client(msg["SRC"], {"REQUEST": "CONFIG",
                                         "PAYLOAD": {"warmable": client_warmable(client)}})

    elif(msg["REQUEST"] == "UPDATECLIENT"):
        # Cache client reference to avoid repeated dictionary lookups
        client = server.settings.clients.get(msg["SRC"])
        if client:
            for settingKey in msg["PAYLOAD"]:
                setattr(client, settingKey, msg["PAYLOAD"][settingKey])
            client.clientID = clientid
            response["PAYLOAD"] = client

    elif(msg["REQUEST"] == "GENERATEARUCO"):
        server.generateAruco(msg["PAYLOAD"]["id"])

    elif(msg["REQUEST"] == "READY"):
        # Client signals its media is cached and it is ready to display
        client = server.settings.clients.get(msg["SRC"])
        if client:
            client.ready = True
        did = getattr(client, "displayID", None) if client else None
        display = server.settings.displays.get(did)
        if display and display.action == PlayState.PREPARING \
                and (msg.get("PAYLOAD") or {}).get("prepareId") == display.prepareId:
            display.readyClients.add(msg["SRC"])
            display.armPending.discard(msg["SRC"])   # armed now (was awaiting a tap)
            server._maybe_release(did)
        response["PAYLOAD"]="SUCCESS"

    elif(msg["REQUEST"] == "NEEDS_ARM"):
        client = server.settings.clients.get(msg["SRC"])
        display = server.settings.displays.get(getattr(client, "displayID", None)) if client else None
        if display and display.action == PlayState.PREPARING \
                and (msg.get("PAYLOAD") or {}).get("prepareId") == display.prepareId:
            # Mark this client as awaiting a HUMAN arming tap so the GO timeout won't
            # release the wall without it (see _release_expired_prepares).
            display.armPending.add(msg["SRC"])
            asyncio.ensure_future(server._auto_arm_client(msg["SRC"]))

    elif(msg["REQUEST"] == "CLIENTLOG"):
        # Client-side debug stream (opt-in via ?tdbg). Surfaced in the server log
        # so device state (video error/readyState/events) is visible without the
        # operator relaying an on-screen HUD. No state change.
        logging.warning("CLIENTLOG %s %s", msg.get("SRC"), msg.get("PAYLOAD"))

    elif(msg["REQUEST"] == "DISCOVERY_STATUS"):
        # Return discovery information for all clients
        response["PAYLOAD"] = get_discovered_devices()

    elif(msg["REQUEST"] == "RECONFIGURE_CLIENT"):
        # Force reconfiguration of a client
        client = server.settings.clients.get(msg["PAYLOAD"]["clientKey"])
        if client:
            client.autoConfigured = False
            auto_configure_client(msg["PAYLOAD"]["clientKey"], client)
            response["PAYLOAD"] = {"status": "SUCCESS", "reconfigured": True}
        else:
            response["PAYLOAD"] = {"status": "ERROR", "message": "Client not found"}

    elif(msg["REQUEST"] == "BULK_CONFIGURE"):
        # Configure multiple clients at once
        configured_count = 0
        for client_key in msg["PAYLOAD"]["clientKeys"]:
            client = server.settings.clients.get(client_key)
            if client:
                client.autoConfigured = False
                auto_configure_client(client_key, client)
                configured_count += 1
        response["PAYLOAD"] = {"status": "SUCCESS", "configured": configured_count}

    elif(msg["REQUEST"] == "REPORT_CANVAS"):
        # Client re-reporting its viewport size (e.g. after going full screen for
        # calibration). Keep canvasWidth/Height fresh so calibrate() reconstructs
        # the screen quad from the marker using the dims actually photographed.
        client = server.settings.clients.get(msg["SRC"])
        if client is not None:
            payload = msg.get("PAYLOAD") or {}
            cw = payload.get("canvasWidth")
            ch = payload.get("canvasHeight")
            if cw and ch:
                client.canvasWidth = int(cw)
                client.canvasHeight = int(ch)
                # Coalesced: a fleet going fullscreen for calibration fires
                # REPORT_CANVAS from N devices in a burst — one flush absorbs it.
                request_save()

    elif(msg["REQUEST"] == "SETPLAYLIST"):
        payload = msg["PAYLOAD"]
        display_id = payload["displayID"]
        display = server.settings.displays.setdefault(display_id, Display())
        display.mediaElements = _build_media_elements(payload.get("items", []))
        display.loop = bool(payload.get("loop", False))
        display.renderedToken = ""  # playlist changed -> needs (re)render
        _broadcast_per_client_preload(display_id, display.mediaElements)
        response["PAYLOAD"] = "SUCCESS"

    elif(msg["REQUEST"] == "PLAY"):
        display_id = msg["PAYLOAD"]["displayID"]
        display = server.settings.displays.get(display_id)
        if not display or not display.mediaElements:
            response["PAYLOAD"] = "SUCCESS"
        else:
            now_ms = int(time.time() * 1000)
            resume_epoch = now_ms - display.pauseOffset if display.action == PlayState.PAUSE else now_ms
            name = getattr(display, "currentPlaylistName", None)
            has_renderable = any(_is_renderable(me) for me in display.mediaElements)
            entry = (getattr(display, "renders", {}) or {}).get(name) if name else None
            state = entry.get("state") if entry else None
            if has_renderable and state in (RENDER_QUEUED, RENDER_RENDERING):
                response["PAYLOAD"] = {"status": "RENDER_IN_PROGRESS", "displayID": display_id}
            elif has_renderable and not is_playlist_ready(name, display_id):
                response["PAYLOAD"] = {"status": "RENDER_REQUIRED", "displayID": display_id}
            else:
                if display.action == PlayState.PAUSE:
                    _start_group_playback(display_id, resume_epoch)   # resume: direct, today's path
                else:
                    _begin_prepare(display_id)                        # fresh start: coordinated
                response["PAYLOAD"] = "SUCCESS"

    elif(msg["REQUEST"] == "STOP"):
        display_id = msg["PAYLOAD"]["displayID"]
        _stop_group_playback(display_id)
        response["PAYLOAD"] = "SUCCESS"

    elif(msg["REQUEST"] == "PAUSE"):
        display_id = msg["PAYLOAD"]["displayID"]
        display = server.settings.displays.get(display_id)
        if display and display.action == PlayState.PLAY:
            display.pauseOffset = int(time.time() * 1000) - display.playStartEpoch
            display.action = PlayState.PAUSE
        broadcast_to_display_group(display_id, {
            "REQUEST": "PAUSE", "PAYLOAD": {"displayID": display_id}
        })
        server._broadcast_playback_state(display_id)
        response["PAYLOAD"] = "SUCCESS"

    elif(msg["REQUEST"] == "RELOAD"):
        # Admin command: tell display clients to hard-reload so they pick up new
        # client JS/HTML. Three scopes:
        #   PAYLOAD.clientKey -> only that one iPad (single-device probe)
        #   PAYLOAD.displayID -> only that group's members
        #   otherwise        -> every connected client via DEST="ALL"
        # The client reloads only on a RELOAD addressed to its own UDID or to
        # "ALL", so the control console isn't reloaded by a group reload.
        payload = msg.get("PAYLOAD")
        client_key = payload.get("clientKey") if isinstance(payload, dict) else None
        display_id = payload.get("displayID") if isinstance(payload, dict) else None
        if client_key:
            broadcast_to_client(client_key, {"REQUEST": "RELOAD", "PAYLOAD": "NONE"})
        elif display_id:
            broadcast_to_display_group(display_id, {"REQUEST": "RELOAD", "PAYLOAD": "NONE"})
        else:
            server.socketmanager.broadcast(jsonpickle.encode(
                {"DEST": "ALL", "REQUEST": "RELOAD", "PAYLOAD": "NONE"}))
        response["PAYLOAD"] = "SUCCESS"

    elif(msg["REQUEST"] == "RUN_SCRIPT"):
        # Admin command: run a device lifecycle script over SSH. PAYLOAD =
        # {script: "login"|"start"|"stop"|"reboot"} plus a target: {clientKey} for
        # one device, {displayID} for a whole group, or {all:true} for the fleet.
        payload = msg.get("PAYLOAD") or {}
        which = payload.get("script")
        if which not in ("login", "start", "stop", "reboot", "test"):
            response["PAYLOAD"] = {"status": "BAD_REQUEST"}
        else:
            ck = payload.get("clientKey")
            did = payload.get("displayID")
            if ck:
                keys = [ck] if ck in server.settings.clients else []
            elif did:
                keys = [k for k, c in server.settings.clients.items()
                        if getattr(c, "displayID", None) == did]
            elif payload.get("all"):
                keys = list(server.settings.clients.keys())
            else:
                keys = []
            for k in keys:
                asyncio.ensure_future(server._run_device_script(k, which))
            logging.warning("RUN_SCRIPT %s -> %d device(s)", which, len(keys))
            response["PAYLOAD"] = {"status": "SUCCESS", "script": which, "count": len(keys)}

    elif(msg["REQUEST"] == "RENDER"):
        # Manual retry of a FAILED (playlist, group) render — the ONLY manual
        # render affordance left. PAYLOAD = {displayID, name}.
        from mosaicmesh import render_queue
        payload = msg["PAYLOAD"]
        display_id = payload.get("displayID")
        name = payload.get("name")
        display = server.settings.displays.get(display_id)
        if not display or name not in server.settings.playlists:
            response["PAYLOAD"] = {"status": "ERROR", "error": "unknown playlist/group"}
        elif not _group_is_calibrated(display_id):
            response["PAYLOAD"] = {"status": "ERROR", "error": "group not calibrated"}
        else:
            elements = _build_media_elements(server.settings.playlists[name].items)
            _set_render_state(display, name, RENDER_QUEUED,
                              token=render_token(elements, display_id))
            render_queue.enqueue(name, display_id)
            response["PAYLOAD"] = {"status": "QUEUED", "displayID": display_id, "name": name}

    elif(msg["REQUEST"] == "LIST_PLAYLISTS"):
        rows = []
        for name, pl in server.settings.playlists.items():
            items = pl.items or []
            has_segment = any(it.get("playmode") in ("SEGMENT", "INDIVIDUAL") for it in items)
            rows.append({"name": name, "itemCount": len(items),
                         "hasSegment": has_segment})
        response["PAYLOAD"] = rows

    elif(msg["REQUEST"] == "GET_PLAYLIST"):
        pl = server.settings.playlists.get(msg["PAYLOAD"].get("name"))
        if pl is None:
            response["PAYLOAD"] = {"error": "not found"}
        else:
            response["PAYLOAD"] = {"name": pl.name, "items": pl.items, "loop": pl.loop}

    elif(msg["REQUEST"] == "SAVE_PLAYLIST"):
        payload = msg["PAYLOAD"]
        name = (payload.get("name") or "").strip()
        if not name:
            response["PAYLOAD"] = {"error": "name required"}
        else:
            pl = server.settings.playlists.setdefault(name, Playlist())
            pl.name = name
            pl.items = payload.get("items", [])
            pl.loop = bool(payload.get("loop", False))
            from mosaicmesh import render_queue
            render_queue.schedule_autorender(name)
            response["PAYLOAD"] = "SUCCESS"

    elif(msg["REQUEST"] == "DELETE_PLAYLIST"):
        _dp_name = msg["PAYLOAD"].get("name")
        server.settings.playlists.pop(_dp_name, None)
        from mosaicmesh import render as _render
        _render.cleanup_playlist_renders(_dp_name)
        response["PAYLOAD"] = "SUCCESS"

    elif(msg["REQUEST"] == "ASSIGN_PLAYLIST"):
        payload = msg["PAYLOAD"]
        display_id = payload.get("displayID")
        name = payload.get("name")
        pl = server.settings.playlists.get(name)
        if pl is None or display_id is None:
            response["PAYLOAD"] = {"status": "error", "displayID": display_id}
        else:
            _apply_playlist(display_id, pl)
            display = server.settings.displays.get(display_id)
            has_renderable = any(_is_renderable(me) for me in display.mediaElements)
            if has_renderable and not _group_is_calibrated(display_id):
                status = "NOT_CALIBRATED"
            elif has_renderable and not is_playlist_ready(name, display_id):
                status = "RENDER_REQUIRED"
            else:
                status = "ok"
            response["PAYLOAD"] = {"status": status, "displayID": display_id}

    elif(msg["REQUEST"] == "LIST_SCHEDULES"):
        now = datetime.datetime.now()
        rows = []
        for sid, s in server.settings.schedules.items():
            rows.append({"id": s.id, "name": s.name, "playlistName": s.playlistName,
                         "displayID": s.displayID, "priority": s.priority, "enabled": s.enabled,
                         "activeNow": bool(getattr(s, "enabled", True)) and schedule_active_at(s, now)})
        response["PAYLOAD"] = rows

    elif(msg["REQUEST"] == "GET_SCHEDULE"):
        s = server.settings.schedules.get(msg["PAYLOAD"].get("id"))
        if s is None:
            response["PAYLOAD"] = {"error": "not found"}
        else:
            response["PAYLOAD"] = {"id": s.id, "name": s.name, "playlistName": s.playlistName,
                                   "displayID": s.displayID, "priority": s.priority, "enabled": s.enabled,
                                   "freq": s.freq, "interval": s.interval, "byweekday": s.byweekday,
                                   "dtstart": s.dtstart, "end": s.end, "exdates": s.exdates,
                                   "startTime": s.startTime, "endTime": s.endTime}

    elif(msg["REQUEST"] == "SAVE_SCHEDULE"):
        p = msg["PAYLOAD"]
        ok = True; err = None
        try:
            if _hhmm_to_min(p.get("startTime", "00:00")) >= _hhmm_to_min(p.get("endTime", "23:59")):
                ok = False; err = "endTime must be after startTime"
        except Exception:
            ok = False; err = "bad time"
        if ok and p.get("freq") not in _FREQ_MAP:
            ok = False; err = "bad frequency"
        if ok and p.get("freq") == "WEEKLY" and not p.get("byweekday"):
            ok = False; err = "weekly schedule needs at least one weekday"
        if ok:
            try:
                _parse_date(p.get("dtstart"))
            except Exception:
                ok = False; err = "bad start date"
        if ok:
            probe = Schedule()
            for k in ("freq", "interval", "byweekday", "dtstart", "end"):
                setattr(probe, k, p.get(k, getattr(probe, k)))
            probe.startTime = p.get("startTime", "00:00"); probe.endTime = p.get("endTime", "23:59")
            try:
                schedule_active_at(probe, datetime.datetime.now())  # compiles the rrule
            except Exception as e:
                ok = False; err = "bad recurrence: " + str(e)
        if not ok:
            response["PAYLOAD"] = {"error": err}
        else:
            sid = p.get("id") or ("sch_" + uuid.uuid4().hex[:10])
            s = server.settings.schedules.setdefault(sid, Schedule())
            s.id = sid
            for k in ("name", "playlistName", "displayID", "priority", "enabled",
                      "freq", "interval", "byweekday", "dtstart", "end", "exdates",
                      "startTime", "endTime"):
                if k in p:
                    setattr(s, k, p[k])
            try:
                s.priority = int(s.priority)
            except (TypeError, ValueError):
                s.priority = 0
            response["PAYLOAD"] = {"id": sid}

    elif(msg["REQUEST"] == "DELETE_SCHEDULE"):
        server.settings.schedules.pop(msg["PAYLOAD"].get("id"), None)
        response["PAYLOAD"] = "SUCCESS"

    elif(msg["REQUEST"] == "GET_GROUP_DEFAULTS"):
        response["PAYLOAD"] = [{"displayID": did, "defaultPlaylistName": getattr(d, "defaultPlaylistName", None)}
                               for did, d in server.settings.displays.items()]

    elif(msg["REQUEST"] == "SET_GROUP_DEFAULT"):
        p = msg["PAYLOAD"]
        display = server.settings.displays.get(p.get("displayID"))
        if display is not None:
            display.defaultPlaylistName = (p.get("playlistName") or "").strip() or None
        response["PAYLOAD"] = "SUCCESS"

    else:
        response["PAYLOAD"] = msg["PAYLOAD"]    #echo anything that isn't a registered command

    return jsonpickle.encode(response)
