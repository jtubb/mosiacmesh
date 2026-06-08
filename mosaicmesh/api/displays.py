"""REST CRUD for Display groups. Backed by Settings.displays (dict keyed
by displayID — the group name IS its primary key).

Until PR-12 the only way to create a display group was implicitly via
auto_configure_client: when a client of a recognized deviceType
registered, the dispatcher would `setdefault("Mobile"|"Tablet"|"Desktop",
Display())`. Groups with no online clients were effectively invisible
to the admin timeline because hydration enumerated CLIENTS, not groups
(see js/timeline/store.js's old behaviour). PR-12 makes groups first-
class: this module is the read + write surface.

Response shape per group:
  {
    displayID: "Lobby",            # key into settings.displays
    clients:   ["abcd123", ...],   # clientKeys assigned to this group
    clientCount: 3,                # len(clients)
    onlineCount: 1,                # # of clients currently online
    scheduleCount: 2,              # # of schedules targeting this group
  }

DELETE returns 409 with a refs object when the group is in use:
  {success:false, error:"display 'Lobby' is in use", refs:{clients:[...], schedules:[...]}}
Operators must reassign or delete the references before the group can
be removed. There is intentionally no force-delete — losing client
display assignments silently has hurt us before.

No If-Match / _serverVersion. Display objects are server-only state
(playback, render token, cached segments) — the timeline UI never
PUTs to a group, only POSTs (create) and DELETEs.
"""
from aiohttp import web

from mosaicmesh.state import Display
from mosaicmesh.persistence import saveSettings

__all__ = [
    "api_displays_list",
    "api_displays_create",
    "api_displays_delete",
]


def _serialize(display_id, display):
    """Display + its referrers -> dict. Reads server.settings.clients +
    schedules to compute counts; cheap enough at admin scale (handful
    of groups, handful of clients each)."""
    import server
    clients = [k for k, c in server.settings.clients.items()
               if getattr(c, "displayID", None) == display_id]
    online = [k for k in clients
              if getattr(server.settings.clients[k], "isOnline", False)]
    schedules = [s for s in server.settings.schedules.values()
                 if getattr(s, "displayID", None) == display_id]
    return {
        "displayID": display_id,
        "clients": clients,
        "clientCount": len(clients),
        "onlineCount": len(online),
        "scheduleCount": len(schedules),
    }


async def api_displays_list(request):
    """GET /api/displays — every display group, even ones with zero
    clients online (the whole point of PR-12). Order = insertion order
    of settings.displays, which is stable across saves."""
    import server
    out = [_serialize(did, d) for did, d in server.settings.displays.items()]
    return web.json_response({"success": True, "displays": out})


async def api_displays_create(request):
    """POST /api/displays — body {displayID}. 400 if missing/empty/non-
    string, 409 if the group already exists. Returns 201 with the
    fully-serialized group (clientCount + scheduleCount will be 0 for a
    freshly-created group)."""
    import server
    try:
        body = await request.json()
    except Exception as e:
        return web.json_response({"success": False,
                                  "error": f"Invalid JSON: {e}"}, status=400)
    raw = body.get("displayID")
    if not isinstance(raw, str):
        return web.json_response({"success": False,
                                  "error": "displayID must be a string"},
                                 status=400)
    display_id = raw.strip()
    if not display_id:
        return web.json_response({"success": False,
                                  "error": "displayID is required"},
                                 status=400)
    if display_id in server.settings.displays:
        return web.json_response({"success": False,
                                  "error": f"display '{display_id}' already exists"},
                                 status=409)
    server.settings.displays[display_id] = Display()
    saveSettings()
    return web.json_response(
        {"success": True, "display": _serialize(display_id, server.settings.displays[display_id])},
        status=201)


async def api_displays_delete(request):
    """DELETE /api/displays/{displayID}. 404 if missing, 409+refs if in
    use by any client or schedule. Returns 204 on success."""
    import server
    display_id = request.match_info.get("displayID", "")
    if display_id not in server.settings.displays:
        return web.json_response({"success": False,
                                  "error": f"display '{display_id}' not found"},
                                 status=404)
    client_refs = [k for k, c in server.settings.clients.items()
                   if getattr(c, "displayID", None) == display_id]
    schedule_refs = [s.id for s in server.settings.schedules.values()
                     if getattr(s, "displayID", None) == display_id]
    if client_refs or schedule_refs:
        msg_parts = []
        if client_refs:
            msg_parts.append(f"{len(client_refs)} client(s)")
        if schedule_refs:
            msg_parts.append(f"{len(schedule_refs)} schedule(s)")
        return web.json_response({
            "success": False,
            "error": f"display '{display_id}' is in use by " + " and ".join(msg_parts),
            "refs": {"clients": client_refs, "schedules": schedule_refs},
        }, status=409)
    del server.settings.displays[display_id]
    saveSettings()
    return web.Response(status=204)
