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
    calibratedCount: 2,           # # of clients with a measured perimeter
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


def _index_clients_by_display():
    """Bucket every client by its displayID in ONE pass (T3.8), counting
    clients/online/calibrated per group. api_displays_list used to rescan the
    whole client dict 3× PER group — O(G×N). This scans once: O(N) total."""
    import server
    idx = {}
    for k, c in server.settings.clients.items():
        did = getattr(c, "displayID", None)
        e = idx.get(did)
        if e is None:
            e = idx[did] = {"clients": [], "online": 0, "calibrated": 0}
        e["clients"].append(k)
        if getattr(c, "isOnline", False):
            e["online"] += 1
        if getattr(c, "measuredPerimeter", None) is not None:
            e["calibrated"] += 1
    return idx


def _serialize(display_id, display, client_index=None):
    """Display + its referrers -> dict. `client_index` (from
    _index_clients_by_display) lets the list handler share one client scan
    across all groups; if omitted, a per-call index is built (single group)."""
    import server
    if client_index is None:
        client_index = _index_clients_by_display()
    info = client_index.get(display_id) or {"clients": [], "online": 0, "calibrated": 0}
    schedules = sum(1 for s in server.settings.schedules.values()
                    if getattr(s, "displayID", None) == display_id)
    return {
        "displayID": display_id,
        "clients": info["clients"],
        "clientCount": len(info["clients"]),
        "onlineCount": info["online"],
        # calibratedCount = screens with a measured perimeter (renderable).
        "calibratedCount": info["calibrated"],
        "scheduleCount": schedules,
    }


async def api_displays_list(request):
    """GET /api/displays — every display group, even ones with zero
    clients online (the whole point of PR-12). Order = insertion order
    of settings.displays, which is stable across saves."""
    import server
    idx = _index_clients_by_display()
    out = [_serialize(did, d, idx) for did, d in server.settings.displays.items()]
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
    from mosaicmesh import render as _render
    _render.cleanup_group_renders(display_id)
    del server.settings.displays[display_id]
    saveSettings()
    return web.Response(status=204)
