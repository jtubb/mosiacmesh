"""Discovery REST API + the client-registration / group-sync helpers.

The REST handlers (api_discovery_devices, etc.) are aiohttp request
handlers — bound to /api/discovery/* routes by server.py at startup.
The non-handler helpers (auto_configure_client, sync_new_client_to_group,
get_discovered_devices, the _expected_*/_propagation_* propagation
calculators) are called from both the REST surface AND from
mosaicmesh.websocket.legacy.msg_response (the iPad-facing protocol).
"""
import asyncio
import logging
import time

from aiohttp import web

from mosaicmesh.state import Display, PlayState, PlayMode
from mosaicmesh.render import _is_renderable, isVideoItem, _per_client_items
from mosaicmesh.broadcast import broadcast_to_client
from mosaicmesh.persistence import saveSettings
from mosaicmesh.cache import cache_stats, file_cache
from mosaicmesh.calibration import _group_clients

__all__ = [
    "auto_match_profile",
    "auto_configure_client",
    "get_discovered_devices",
    "_expected_seg_keys_for_display",
    "_expected_segments_for_client",
    "_propagation_percent_for_client",
    "sync_new_client_to_group",
    "api_discovery_devices",
    "api_discovery_stats",
    "api_discovery_configure",
]


def auto_match_profile(client, settings):
    """Return the name of the first profile whose matchDeviceType equals
    client.deviceType (case-insensitive), or None if no profile matches.
    A profile with matchDeviceType='' is treated as manual-only and never
    matched.

    Case-insensitive comparison because device_detector emits lowercase
    deviceType ('tablet', 'smartphone', 'desktop') but profile labels
    written by humans through the REST API or admin UI usually capitalize
    ('Tablet'). Spec §7's example default uses 'Tablet'; production
    Client.deviceType is 'tablet'. Normalizing on both sides removes
    the trap.

    Per spec §7: 'assigned at REGISTER from first profile whose
    matchDeviceType matches client.deviceType; admin can override'.

    First-match-wins is deterministic on Python 3.7+ thanks to dict
    insertion-order preservation. If multiple profiles share the same
    matchDeviceType, the earliest-created one wins. Operators who need
    differentiated routing should give each profile a unique
    matchDeviceType (or use the empty string + manual assignment)."""
    dt = (getattr(client, "deviceType", "") or "").lower()
    if not dt:
        return None
    for name, prof in (settings.profiles or {}).items():
        match = (getattr(prof, "matchDeviceType", "") or "").lower()
        if match and match == dt:
            return name
    return None


def auto_configure_client(client_key, client):
    """Automatically configure new clients based on device characteristics"""
    import server
    if client.autoConfigured:
        return

    # Auto-assign display group based on device type
    if client.deviceType == "smartphone":
        client.displayID = "Mobile"
        server.settings.displays.setdefault("Mobile", Display())
    elif client.deviceType == "tablet":
        client.displayID = "Tablet"
        server.settings.displays.setdefault("Tablet", Display())
    elif client.deviceType == "desktop":
        client.displayID = "Desktop"
        server.settings.displays.setdefault("Desktop", Display())
    else:
        client.displayID = "Default"
        server.settings.displays.setdefault("Default", Display())

    # Generate friendly name if not set
    if not client.friendlyName:
        device_name = client.deviceModel or client.deviceBrand or "Unknown"
        client.friendlyName = f"{device_name}_{client_key[:8]}"

    # Set capabilities based on device characteristics
    client.capabilities = []
    if client.deviceWidth >= 1920 and client.deviceHeight >= 1080:
        client.capabilities.append("HD")
    if client.deviceType in ["smartphone", "tablet"]:
        client.capabilities.append("touch")
        client.capabilities.append("mobile")
    if client.deviceType == "desktop":
        client.capabilities.append("keyboard")
        client.capabilities.append("mouse")

    # PR-3: auto-assign a ScriptingProfile on first connect. Only fires
    # when profileName is still None (operator overrides via
    # POST /api/clients/{key}/profile take precedence forever after).
    if not getattr(client, "profileName", None):
        client.profileName = auto_match_profile(client, server.settings)

    client.autoConfigured = True
    client.discoverySource = "websocket"

    logging.info(f"Auto-configured client {client.friendlyName} -> {client.displayID}")


def _expected_seg_keys_for_display(display):
    """Set of seg_KEY strings (token_n) the display CURRENTLY expects
    to be cached on any lighttpd-localhost iPad in its group. Driven
    by the display's renderedToken (so a stale cache for a previous
    render isn't counted as 'have'). Empty set if the display has no
    renderedToken or no renderable SEGMENT items."""
    if not display or not getattr(display, "renderedToken", None):
        return set()
    token = display.renderedToken
    keys = set()
    for i, me in enumerate(getattr(display, "mediaElements", []) or []):
        if not (_is_renderable(me) and isVideoItem(me.file)):
            continue
        if me.playmode == PlayMode.SEGMENT:
            keys.add("%s_%d" % (token, i))
        elif me.playmode == PlayMode.FULL:
            keys.add("full_%s_%d" % (token, i))   # FULL device-cache key (seam 5)
    return keys


def _expected_segments_for_client(client):
    """Number of seg_ items this client SHOULD have cached given the
    current rendered token of its display group. Operator-facing
    convenience; the denominator of propagationPercent."""
    import server
    did = getattr(client, "displayID", None)
    if not did:
        return 0
    return len(_expected_seg_keys_for_display(server.settings.displays.get(did)))


def _propagation_percent_for_client(client):
    """0-100. Fraction of currently-expected segments this client has
    in cachedSegments. Returns 100.0 for clients in displays with no
    renderable segments (vacuously cached -- nothing to propagate).
    Returns 100.0 for non-iPad / cacheMode=none clients too: the bar
    is meaningful only for lighttpd-localhost iPads, but we don't want
    a noisy 0% to drag down aggregates."""
    import server
    if getattr(client, "cacheMode", "none") != "lighttpd-localhost":
        return 100.0
    did = getattr(client, "displayID", None)
    if not did:
        return 100.0
    expected = _expected_seg_keys_for_display(server.settings.displays.get(did))
    if not expected:
        return 100.0
    cached = getattr(client, "cachedSegments", set()) or set()
    have = sum(1 for k in expected if k in cached)
    return round(100.0 * have / len(expected), 1)


def get_discovered_devices():
    """Get all discovered devices with discovery metadata"""
    import server
    discovered = []
    current_time = time.time()

    for client_key, client in server.settings.clients.items():
        device_info = {
            "clientKey": client_key,
            "friendlyName": client.friendlyName,
            "displayID": client.displayID,
            "deviceType": client.deviceType,
            "deviceBrand": client.deviceBrand,
            "deviceModel": client.deviceModel,
            "resolution": f"{client.deviceWidth}x{client.deviceHeight}",
            "canvas": f"{getattr(client, 'canvasWidth', 0)}x{getattr(client, 'canvasHeight', 0)}",
            "ip": client.ip,
            "hostname": getattr(client, "hostname", ""),
            "osName": client.osName,
            "osVersion": client.osVersion,
            "engine": getattr(client, "engine", ""),
            "userAgent": getattr(client, "userAgent", ""),
            "discoveryTime": client.discoveryTime,
            "lastSeen": client.lastSeen,
            "isOnline": client.isOnline,
            "synced": client.synced,
            "readyToDisplay": client.ready,
            # Derived calibration flag: the Fleet UI's calibrationSummary needs
            # to know a screen is calibrated without shipping the full
            # measuredPerimeter coordinate array on every discovery poll.
            "calibrated": getattr(client, "measuredPerimeter", None) is not None,
            "timeSinceLastSeen": current_time - client.lastSeen,
            "capabilities": client.capabilities,
            "autoConfigured": client.autoConfigured,
            "discoverySource": client.discoverySource,
            "connectionCount": client.connectionCount,
            # Media-cache state (2026-06-03). cachedSegments is a Python
            # set in memory; serialize as a sorted list for the API so
            # operators see a stable order. getattr guards against
            # Clients in settings.dat that pre-dated these fields and
            # somehow slipped through migrate_client_objects.
            "cacheMode": getattr(client, "cacheMode", "none"),
            "cachedSegments": sorted(list(getattr(client, "cachedSegments", set()) or set())),
            # cachePushProgress is always None (SSH push retired; client-pull
            # is the sole cache path). Kept in the response for API schema
            # backward compat; callers may see null here.
            "cachePushProgress": getattr(client, "cachePushProgress", None),
            "expectedSegments": _expected_segments_for_client(client),
            "propagationPercent": _propagation_percent_for_client(client),
            "cacheProbedMs": getattr(client, "cacheProbedMs", None),
        }
        discovered.append(device_info)

    # Sort by most recently seen
    discovered.sort(key=lambda x: x["lastSeen"], reverse=True)
    return discovered


def sync_new_client_to_group(client_key, client):
    """If the client's display group is currently playing, send that one client
    PRELOAD + PLAY so it joins the in-progress playlist in sync."""
    import server
    display = server.settings.displays.get(client.displayID)
    if not display or display.action != PlayState.PLAY or not display.mediaElements:
        return
    # Per-client URLs (this client's rendered segment), not the generic source —
    # else a reconnecting renderable client gets the undecodable full source.
    items = _per_client_items(display, client_key, client)
    broadcast_to_client(client_key, {"REQUEST": "PRELOAD", "PAYLOAD": {"items": items}})
    broadcast_to_client(client_key, {
        "REQUEST": "PLAY",
        "PAYLOAD": {"startEpoch": display.playStartEpoch, "items": items, "loop": display.loop,
                    "seed": getattr(display, "playSeed", 0)}
    })


async def api_discovery_devices(request):
    """REST: list all discovered devices."""
    devices = get_discovered_devices()
    return web.json_response({
        "success": True,
        "devices": devices,
        "total": len(devices),
        "online": len([d for d in devices if d["isOnline"]]),
    })


async def api_discovery_stats(request):
    """REST: aggregate discovery + cache statistics."""
    import server
    devices = get_discovered_devices()
    display_groups = {}
    for d in devices:
        gid = d["displayID"] or "default"
        display_groups[gid] = display_groups.get(gid, 0) + 1
    total = cache_stats['hits'] + cache_stats['misses']

    # Per-display-group cache propagation: counts each lighttpd-
    # localhost iPad in the group as one of {fullyCached, idle} (or pushing/
    # stalled if cachePushProgress is set, though those buckets are always 0
    # now that the SSH push has been retired). Empty for groups without a
    # renderedToken or without renderable SEGMENT items -- the bar is
    # meaningless there and the admin UI uses absence to hide the widget.
    # T3.5: bucket clients by displayID ONCE rather than rescanning the whole
    # client dict for every display group (was O(G×N)).
    _by_group = {}
    for c in server.settings.clients.values():
        _by_group.setdefault(getattr(c, "displayID", None), []).append(c)
    group_prop = {}
    for did, display in server.settings.displays.items():
        expected_keys = _expected_seg_keys_for_display(display)
        if not expected_keys:
            continue
        total_g = 0
        full = pushing = stalled = idle = 0
        for c in _by_group.get(did, ()):
            if getattr(c, "cacheMode", "none") != "lighttpd-localhost":
                continue
            total_g += 1
            cached = getattr(c, "cachedSegments", set()) or set()
            if expected_keys.issubset(cached):
                full += 1
            elif getattr(c, "cachePushProgress", None):
                if c.cachePushProgress.get("status") == "stalled":
                    stalled += 1
                else:
                    pushing += 1
            else:
                idle += 1
        if total_g > 0:
            group_prop[did] = {
                "total": total_g, "fullyCached": full,
                "pushing": pushing, "stalled": stalled, "idle": idle,
                "percent": round(100.0 * full / total_g, 1),
            }

    return web.json_response({
        "success": True,
        "totalDevices": len(devices),
        "onlineDevices": len([d for d in devices if d["isOnline"]]),
        "autoConfiguredDevices": len([d for d in devices if d["autoConfigured"]]),
        "displayGroups": display_groups,
        "displayGroupPropagation": group_prop,
        "cacheStats": {
            "hits": cache_stats['hits'],
            "misses": cache_stats['misses'],
            "cachedFiles": len(file_cache),
            "hitRatio": (cache_stats['hits'] / total) if total else 0,
        },
    })


async def api_discovery_configure(request):
    """REST: configure client(s). Supports five payload styles:

      - {"clientKey", "displayID"?, "friendlyName"?}      -> update fields
      - {"action": "reconfigure", "clientKey"}            -> re-run auto-config
      - {"action": "bulk_reconfigure", "clientKeys": [...]}-> re-run for many
      - {"action": "swap_orientation", "clientKey"}        -> swap canvas dims +
        clear measuredPerimeter (force a re-calibrate at the new orientation)
      - {"action": "set_cache_mode", "clientKey", "mode"} -> set cacheMode to
        "none", "lighttpd-localhost", or "service-worker"
      - {"action": "force_push", ...} -> RETIRED (410): SSH segment-push was
        removed; client-pull (PRECACHE) is the sole cache path.

    (The action-based forms preserve the contract that discovery.html uses.)
    """
    import server
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)

    action = data.get("action")

    if action == "bulk_reconfigure":
        configured = 0
        for key in data.get("clientKeys", []):
            client = server.settings.clients.get(key)
            if client:
                client.autoConfigured = False
                auto_configure_client(key, client)
                configured += 1
        saveSettings()
        return web.json_response({"success": True, "configured": configured})

    if action == "bulk_assign":
        # PR-15: move many clients to one display group in a single call.
        # Atomic: we validate the target group exists FIRST, then walk
        # the clientKeys list. Skips any keys that don't exist in
        # settings.clients (logs them in `missing`). Returns counts so
        # the UI can toast "Moved 22 of 24 to Lobby (2 unknown)".
        target = data.get("displayID")
        if not isinstance(target, str) or not target.strip():
            return web.json_response({"success": False,
                                      "error": "displayID must be a non-empty string"},
                                     status=400)
        if target not in server.settings.displays:
            return web.json_response({"success": False,
                                      "error": f"display group '{target}' not found — create it first"},
                                     status=404)
        keys = data.get("clientKeys") or []
        if not isinstance(keys, list) or not keys:
            return web.json_response({"success": False,
                                      "error": "clientKeys must be a non-empty array"},
                                     status=400)
        moved, missing = [], []
        for key in keys:
            client = server.settings.clients.get(key)
            if client is None:
                missing.append(key)
                continue
            client.displayID = target
            client.autoConfigured = False   # explicit operator move; don't let auto-config undo it
            moved.append(key)
        saveSettings()
        return web.json_response({"success": True, "displayID": target,
                                  "moved": moved, "missing": missing,
                                  "movedCount": len(moved)})

    if action == "clear_cache":
        # Operator/test helper: drop a client's server-side
        # cachedSegments record so the next force_push (or
        # subsequent render) re-pushes the segments. Doesn't touch
        # the actual files on the iPad (lighttpd will keep serving
        # stale-but-byte-identical content until the push lands a
        # newer copy). With no clientKey, clears all
        # lighttpd-localhost clients (handy for re-running an
        # acceptance test from scratch).
        ck = data.get("clientKey")
        cleared = 0
        if ck:
            c = server.settings.clients.get(ck)
            if not c:
                return web.json_response(
                    {"success": False, "error": "Client not found"}, status=404)
            c.cachedSegments = set()
            cleared = 1
        else:
            for c in server.settings.clients.values():
                if getattr(c, "cacheMode", "none") == "lighttpd-localhost":
                    c.cachedSegments = set()
                    cleared += 1
        saveSettings()
        logging.info("clear_cache: cleared cachedSegments on %d client(s)", cleared)
        return web.json_response({"success": True, "cleared": cleared})

    if action == "force_push":
        # The SSH segment-push has been retired (refactor/cache, 2026-07-09).
        # Client-pull (PRECACHE) is now the sole cache path. force_push is a
        # no-op; returning 410 so callers know the action is gone rather than
        # silently doing nothing.
        return web.json_response(
            {"success": False,
             "error": "force_push retired: SSH segment-push removed; "
                      "client-pull (PRECACHE) is the sole cache path"},
            status=410)

    client_key = data.get("clientKey")
    if not client_key:
        return web.json_response({"success": False, "error": "clientKey required"}, status=400)
    client = server.settings.clients.get(client_key)
    if not client:
        return web.json_response({"success": False, "error": "Client not found"}, status=404)

    if action == "reconfigure":
        client.autoConfigured = False
        auto_configure_client(client_key, client)
    elif action == "swap_orientation":
        # Manual override for cases where calibrate's auto-rotation detection
        # got it wrong (e.g. an iPad whose band quad wasn't detected so neither
        # the IoU nor aspect signal had a chance to fire, or borderline cases
        # where both signals were noisy). Swaps reported canvas dims AND
        # clears measuredPerimeter so the next calibration photo re-projects
        # the screen with the corrected orientation. The user is expected to
        # re-upload a calibration image after calling this.
        cw = int(getattr(client, "canvasWidth", 0) or 0)
        ch = int(getattr(client, "canvasHeight", 0) or 0)
        client.canvasWidth, client.canvasHeight = ch, cw
        client.measuredPerimeter = None
        logging.info("swap_orientation: %s canvas %sx%s -> %sx%s",
                     client_key, cw, ch, ch, cw)
    elif action == "set_cache_mode":
        mode = data.get("mode")
        if mode not in ("none", "lighttpd-localhost", "service-worker"):
            return web.json_response({"success": False,
                                      "error": f"invalid mode {mode!r}"},
                                     status=400)
        client.cacheMode = mode
        logging.info("set_cache_mode: %s -> %s", client_key, mode)
    else:
        if "displayID" in data:
            new_did = data["displayID"]
            # PR-14: reject unknown displayIDs. Without this guard, typing
            # "Tablt" instead of "Tablet" silently sets client.displayID
            # to a string no group has — the client vanishes from the
            # timeline because /api/displays only enumerates known
            # groups. Operators have to create the group first
            # (POST /api/displays) or pick an existing one.
            if not isinstance(new_did, str) or not new_did.strip():
                return web.json_response({"success": False,
                                          "error": "displayID must be a non-empty string"},
                                         status=400)
            if new_did not in server.settings.displays:
                return web.json_response({"success": False,
                                          "error": f"display group '{new_did}' not found — create it first"},
                                         status=404)
            client.displayID = new_did
        if "friendlyName" in data:
            client.friendlyName = data["friendlyName"]
            client.nameIsCustom = True   # user-set name: DNS won't override it

    saveSettings()
    return web.json_response({"success": True})
