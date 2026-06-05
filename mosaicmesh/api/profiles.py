"""REST CRUD for ScriptingProfiles plus per-client profile assignment.

A ScriptingProfile is referenced by Clients via Client.profileName.
The dispatcher (mosaicmesh.device_scripts.run_profile_action) resolves
the profile at script-run time and executes the appropriate lifecycle
action over SSH and/or Veency VNC taps.

DELETE returns 409 with a refs list (clientKeys) when the profile is
assigned to any client.
"""
from aiohttp import web

from mosaicmesh.state import ScriptingProfile
from mosaicmesh.persistence import saveSettings
from mosaicmesh.api._concurrency import (
    parse_if_match,
    precondition_required_response,
    precondition_failed_response,
    bump_version,
)

__all__ = [
    "api_profiles_list",
    "api_profiles_create",
    "api_profiles_update",
    "api_profiles_delete",
    "api_clients_assign_profile",
]

_DICT_FIELDS = ("scripts", "launch", "webclip", "ssh")


def _validate_dict_fields(body):
    """Reject non-dict values for the four object-shaped fields up front so
    they don't silently no-op in _apply_fields. Returns (ok, error_msg)."""
    for field in _DICT_FIELDS:
        if field in body and not isinstance(body[field], dict):
            return False, f"{field} must be an object"
    return True, None


def _serialize(p):
    return {
        "name": p.name,
        "label": p.label,
        "matchDeviceType": p.matchDeviceType,
        "scripts": dict(p.scripts or {}),
        "launch": dict(p.launch or {}),
        "webclip": dict(p.webclip or {}),
        "ssh": dict(p.ssh or {}),
        "_serverVersion": int(getattr(p, "_serverVersion", 0)),
    }


def _apply_fields(p, body):
    """Copy provided fields onto the ScriptingProfile. Skips name and
    _serverVersion (name is the key; version is server-managed)."""
    if "label" in body:
        p.label = body["label"]
    if "matchDeviceType" in body:
        p.matchDeviceType = body["matchDeviceType"]
    if "scripts" in body and isinstance(body["scripts"], dict):
        p.scripts = dict(body["scripts"])
    if "launch" in body and isinstance(body["launch"], dict):
        p.launch = dict(body["launch"])
    if "webclip" in body and isinstance(body["webclip"], dict):
        p.webclip = dict(body["webclip"])
    if "ssh" in body and isinstance(body["ssh"], dict):
        p.ssh = dict(body["ssh"])


async def api_profiles_list(request):
    """GET /api/profiles — list every scripting profile."""
    import server
    out = [_serialize(p) for p in server.settings.profiles.values()]
    return web.json_response({"success": True, "profiles": out})


async def api_profiles_create(request):
    """POST /api/profiles — create a new profile. Body: {name, label?, ...}.
    Returns 201; 400 if name missing; 409 if name taken."""
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
    if name in server.settings.profiles:
        return web.json_response({"success": False,
                                  "error": f"profile '{name}' already exists"},
                                 status=409)
    ok, err = _validate_dict_fields(body)
    if not ok:
        return web.json_response({"success": False, "error": err}, status=400)
    p = ScriptingProfile()
    p.name = name
    _apply_fields(p, body)
    p._serverVersion = 1
    server.settings.profiles[name] = p
    saveSettings()
    return web.json_response({"success": True, "profile": _serialize(p)},
                             status=201)


async def api_profiles_update(request):
    """PUT /api/profiles/{name} — update. If-Match required."""
    import server
    name = request.match_info.get("name", "")
    p = server.settings.profiles.get(name)
    if p is None:
        return web.json_response({"success": False,
                                  "error": f"profile '{name}' not found"},
                                 status=404)
    if_match = parse_if_match(request)
    if if_match is None:
        return precondition_required_response("profile")
    current_version = int(getattr(p, "_serverVersion", 0))
    if if_match != current_version:
        return precondition_failed_response("profile", current_version)
    try:
        body = await request.json()
    except Exception as e:
        return web.json_response({"success": False,
                                  "error": f"Invalid JSON: {e}"}, status=400)
    ok, err = _validate_dict_fields(body)
    if not ok:
        return web.json_response({"success": False, "error": err}, status=400)
    _apply_fields(p, body)
    bump_version(p)
    saveSettings()
    return web.json_response({"success": True, "profile": _serialize(p)})


async def api_profiles_delete(request):
    """DELETE /api/profiles/{name} — remove. Returns 204; 404 if missing;
    409 + refs list if any Client.profileName references it."""
    import server
    name = request.match_info.get("name", "")
    if name not in server.settings.profiles:
        return web.json_response({"success": False,
                                  "error": f"profile '{name}' not found"},
                                 status=404)
    refs = [k for k, c in server.settings.clients.items()
            if getattr(c, "profileName", None) == name]
    if refs:
        return web.json_response({
            "success": False,
            "error": f"profile '{name}' is assigned to {len(refs)} client(s)",
            "refs": refs,
        }, status=409)
    del server.settings.profiles[name]
    saveSettings()
    return web.Response(status=204)


async def api_clients_assign_profile(request):
    """POST /api/clients/{clientKey}/profile — set Client.profileName.
    Body: {profileName: '...' | null}. null clears the override.
    Returns 200; 404 if client or profile missing."""
    import server
    ckey = request.match_info.get("clientKey", "")
    client = server.settings.clients.get(ckey)
    if client is None:
        return web.json_response({"success": False,
                                  "error": f"client '{ckey}' not found"},
                                 status=404)
    try:
        body = await request.json()
    except Exception as e:
        return web.json_response({"success": False,
                                  "error": f"Invalid JSON: {e}"}, status=400)
    pname = body.get("profileName")
    if pname is not None:
        if not isinstance(pname, str):
            return web.json_response({"success": False,
                                      "error": "profileName must be a string or null"},
                                     status=400)
        if pname == "":
            return web.json_response({"success": False,
                                      "error": "use null to clear the profile"},
                                     status=400)
        if pname not in server.settings.profiles:
            return web.json_response({"success": False,
                                      "error": f"profile '{pname}' not found"},
                                     status=404)
    client.profileName = pname   # may be None to clear
    saveSettings()
    return web.json_response({
        "success": True,
        "clientKey": ckey,
        "profileName": pname,
    })
