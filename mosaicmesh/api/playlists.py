"""REST CRUD for Playlists. Backed by Settings.playlists (dict keyed by name).

All endpoints follow the project's {success, ...} response shape. Mutating
endpoints use If-Match for optimistic concurrency (see
mosaicmesh/api/_concurrency.py).

A Playlist is referenced by Schedules via Schedule.playlistName. DELETE
returns 409 with a refs list when the playlist is in use by any schedule,
so the admin UI can show 'Used by N schedules' before forcing the user
to disconnect them.
"""
import logging

from aiohttp import web

from mosaicmesh.state import Playlist
from mosaicmesh.persistence import saveSettings
from mosaicmesh.api._concurrency import (
    parse_if_match,
    precondition_required_response,
    precondition_failed_response,
    bump_version,
)

__all__ = [
    "api_playlists_list",
    "api_playlists_get",
    "api_playlists_create",
    "api_playlists_update",
    "api_playlists_delete",
]


def _serialize(p):
    """Playlist -> dict. Mirrors what jsonpickle would emit but stripped of
    pickle metadata, which the timeline-UI client doesn't need."""
    return {
        "name": p.name,
        "items": list(p.items),
        "loop": bool(p.loop),
        "_serverVersion": int(getattr(p, "_serverVersion", 0)),
    }


async def api_playlists_list(request):
    """GET /api/playlists — list every playlist with its current version."""
    import server
    out = [_serialize(p) for p in server.settings.playlists.values()]
    return web.json_response({"success": True, "playlists": out})


async def api_playlists_get(request):
    """GET /api/playlists/{name} — fetch a single playlist by name.
    Returns 200 + {playlist}; 404 if missing. Used by the 412 refetch path
    in js/timeline/api.js (refetchPlaylist) so a conflict resolver can pull
    the current server state without re-listing all playlists."""
    import server
    name = request.match_info.get("name", "")
    p = server.settings.playlists.get(name)
    if p is None:
        return web.json_response({"success": False,
                                  "error": f"playlist '{name}' not found"},
                                 status=404)
    return web.json_response({"success": True, "playlist": _serialize(p)})


async def api_playlists_create(request):
    """POST /api/playlists — create a new playlist. Body: {name, items?, loop?}.
    Returns 201 + {playlist}; 409 if a playlist with the same name exists."""
    import server
    try:
        body = await request.json()
    except Exception as e:
        return web.json_response({"success": False,
                                  "error": f"Invalid JSON: {e}"}, status=400)
    name = (body.get("name") or "").strip()
    if not name:
        return web.json_response({"success": False,
                                  "error": "name is required"}, status=400)
    if name in server.settings.playlists:
        return web.json_response({"success": False,
                                  "error": f"playlist '{name}' already exists"},
                                 status=409)
    p = Playlist()
    p.name = name
    p.items = list(body.get("items") or [])
    p.loop = bool(body.get("loop", False))
    p._serverVersion = 1   # first persistence
    server.settings.playlists[name] = p
    saveSettings()
    from mosaicmesh import render_queue
    render_queue.schedule_autorender(name)
    return web.json_response({"success": True, "playlist": _serialize(p)},
                             status=201)


async def api_playlists_update(request):
    """PUT /api/playlists/{name} — update items + loop. If-Match required.
    Returns 200 + {playlist}; 404 if missing; 412 if stale; 428 if no If-Match."""
    import server
    name = request.match_info.get("name", "")
    p = server.settings.playlists.get(name)
    if p is None:
        return web.json_response({"success": False,
                                  "error": f"playlist '{name}' not found"},
                                 status=404)
    if_match = parse_if_match(request)
    if if_match is None:
        return precondition_required_response("playlist")
    current_version = int(getattr(p, "_serverVersion", 0))
    if if_match != current_version:
        return precondition_failed_response("playlist", current_version)
    try:
        body = await request.json()
    except Exception as e:
        return web.json_response({"success": False,
                                  "error": f"Invalid JSON: {e}"}, status=400)
    if "items" in body:
        p.items = list(body["items"])
    if "loop" in body:
        p.loop = bool(body["loop"])
    bump_version(p)
    saveSettings()
    from mosaicmesh import render_queue
    render_queue.schedule_autorender(name)
    return web.json_response({"success": True, "playlist": _serialize(p)})


async def api_playlists_delete(request):
    """DELETE /api/playlists/{name} — remove. Returns 204; 404 if missing;
    409 with refs list if any schedule references the playlist."""
    import server
    name = request.match_info.get("name", "")
    if name not in server.settings.playlists:
        return web.json_response({"success": False,
                                  "error": f"playlist '{name}' not found"},
                                 status=404)
    refs = [s.id for s in server.settings.schedules.values()
            if getattr(s, "playlistName", "") == name]
    if refs:
        return web.json_response({
            "success": False,
            "error": f"playlist '{name}' is referenced by {len(refs)} schedule(s)",
            "refs": refs,
        }, status=409)
    del server.settings.playlists[name]
    saveSettings()
    return web.Response(status=204)
