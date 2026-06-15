"""REST CRUD for Schedules. Backed by Settings.schedules (dict keyed by id).

Validation is stricter than Playlists because Schedules have foreign-key-
style links (playlistName -> Playlists, displayID -> Displays) plus
recurrence-rule semantics that must be parseable by dateutil.rrule (the
same code path mosaicmesh.scheduling.schedule_active_at uses).

Schedule.id is server-generated on POST (uuid4) — clients never need to
mint one.
"""
import logging
import uuid

from aiohttp import web

from mosaicmesh.state import Schedule
from mosaicmesh.persistence import saveSettings
from mosaicmesh.api._concurrency import (
    parse_if_match,
    precondition_required_response,
    precondition_failed_response,
    bump_version,
)

__all__ = [
    "api_schedules_list",
    "api_schedules_get",
    "api_schedules_create",
    "api_schedules_update",
    "api_schedules_delete",
]

_VALID_FREQ = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}
_VALID_END_TYPES = {"never", "until", "count"}


def _serialize(s):
    return {
        "id": s.id,
        "name": s.name,
        "playlistName": s.playlistName,
        "displayID": s.displayID,
        "priority": int(s.priority),
        "enabled": bool(s.enabled),
        "freq": s.freq,
        "interval": int(s.interval),
        "byweekday": list(s.byweekday or []),
        "dtstart": s.dtstart,
        "end": dict(s.end or {"type": "never"}),
        "exdates": list(s.exdates or []),
        "startTime": s.startTime,
        "endTime": s.endTime,
        "_serverVersion": int(getattr(s, "_serverVersion", 0)),
    }


def _validate_time_str(s):
    """HH:MM with 0-23 hours and 0-59 minutes. Returns (ok, error_msg)."""
    if not isinstance(s, str) or len(s) != 5 or s[2] != ':':
        return False, f"time '{s}' must be HH:MM"
    try:
        hh, mm = int(s[:2]), int(s[3:])
    except ValueError:
        return False, f"time '{s}' must be HH:MM with numeric values"
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return False, f"time '{s}' out of range (00:00 to 23:59)"
    return True, None


def _validate_end_dict(end):
    """The Schedule.end dict has three shapes: {"type":"never"},
    {"type":"until","untilDate":"YYYY-MM-DD"}, or {"type":"count","count":N}.
    Reject early so dateutil.rrule (used by mosaicmesh.scheduling at evaluation
    time) doesn't blow up on partially-formed input. Returns (ok, error_msg)."""
    if not isinstance(end, dict):
        return False, "end must be an object"
    et = end.get("type")
    if et not in _VALID_END_TYPES:
        return False, f"end.type must be one of {sorted(_VALID_END_TYPES)}"
    if et == "until":
        ud = end.get("untilDate")
        if not isinstance(ud, str) or not ud:
            return False, "end.untilDate is required when end.type='until'"
    elif et == "count":
        c = end.get("count")
        try:
            if int(c) < 1:
                return False, "end.count must be >= 1"
        except (TypeError, ValueError):
            return False, "end.count must be an integer"
    return True, None


def _validate_fields(body, settings, partial=False):
    """Validate body fields against Schedule's contract.
    partial=True (PUT) skips presence checks for playlistName/displayID, but
    still validates foreign-key existence whenever those fields appear in the
    body. partial=False (POST) requires both. Returns (ok, error_msg)."""
    if not partial:
        if not body.get("playlistName"):
            return False, "playlistName is required"
        if not body.get("displayID"):
            return False, "displayID is required"
    if "playlistName" in body and body["playlistName"] not in settings.playlists:
        return False, f"playlist '{body['playlistName']}' not found"
    if "displayID" in body and body["displayID"] not in settings.displays:
        return False, f"display '{body['displayID']}' not found"
    if "freq" in body and body["freq"] not in _VALID_FREQ:
        return False, f"freq '{body['freq']}' must be one of {sorted(_VALID_FREQ)}"
    if "interval" in body:
        try:
            if int(body["interval"]) < 1:
                return False, "interval must be >= 1"
        except (TypeError, ValueError):
            return False, "interval must be an integer"
    if "byweekday" in body:
        if not isinstance(body["byweekday"], list):
            return False, "byweekday must be a list of integers 0-6"
        for d in body["byweekday"]:
            if not isinstance(d, int) or not (0 <= d <= 6):
                return False, "byweekday entries must be integers 0-6 (Mon=0)"
    if "startTime" in body:
        ok, err = _validate_time_str(body["startTime"])
        if not ok:
            return False, err
    if "endTime" in body:
        ok, err = _validate_time_str(body["endTime"])
        if not ok:
            return False, err
    if "end" in body:
        ok, err = _validate_end_dict(body["end"])
        if not ok:
            return False, err
    return True, None


def _apply_fields(s, body):
    """Copy provided fields from body to the Schedule object. Skips id and
    _serverVersion (those are managed server-side).

    list/dict fields are shallow-copied so that subsequent mutations of the
    request body (or future PUTs sharing a reused dict) don't alias the
    stored Schedule's collections."""
    for field in ("name", "playlistName", "displayID", "priority",
                  "enabled", "freq", "interval", "byweekday",
                  "dtstart", "end", "exdates", "startTime", "endTime"):
        if field not in body:
            continue
        v = body[field]
        if field in ("byweekday", "exdates"):
            setattr(s, field, list(v))
        elif field == "end":
            setattr(s, field, dict(v))
        else:
            setattr(s, field, v)


async def api_schedules_list(request):
    """GET /api/schedules — list every schedule."""
    import server
    out = [_serialize(s) for s in server.settings.schedules.values()]
    return web.json_response({"success": True, "schedules": out})


async def api_schedules_get(request):
    """GET /api/schedules/{id} — fetch a single schedule by id.
    Returns 200 + {schedule}; 404 if missing. Used by the 412 refetch path
    in js/timeline/api.js (refetchSchedule) so a conflict resolver can pull
    the current server state without re-listing all schedules."""
    import server
    sid = request.match_info.get("id", "")
    s = server.settings.schedules.get(sid)
    if s is None:
        return web.json_response({"success": False,
                                  "error": f"schedule '{sid}' not found"},
                                 status=404)
    return web.json_response({"success": True, "schedule": _serialize(s)})


async def api_schedules_create(request):
    """POST /api/schedules — create a new schedule. id auto-generated.
    Body: at minimum {playlistName, displayID}; other fields take Schedule
    defaults. Returns 201 + {schedule}; 400 on validation; 404 if
    referenced playlist/display missing."""
    import server
    try:
        body = await request.json()
    except Exception as e:
        return web.json_response({"success": False,
                                  "error": f"Invalid JSON: {e}"}, status=400)
    ok, err = _validate_fields(body, server.settings, partial=False)
    if not ok:
        return web.json_response({"success": False, "error": err}, status=400)
    s = Schedule()
    s.id = uuid.uuid4().hex[:16]
    while s.id in server.settings.schedules:   # 64-bit hex; collision guard is paranoia
        s.id = uuid.uuid4().hex[:16]
    _apply_fields(s, body)
    s._serverVersion = 1
    server.settings.schedules[s.id] = s
    saveSettings()
    return web.json_response({"success": True, "schedule": _serialize(s)},
                             status=201)


async def api_schedules_update(request):
    """PUT /api/schedules/{id} — update any subset of fields. If-Match required.
    Returns 200 + {schedule}; 404 if missing; 412 if stale; 428 if no If-Match."""
    import server
    sid = request.match_info.get("id", "")
    s = server.settings.schedules.get(sid)
    if s is None:
        return web.json_response({"success": False,
                                  "error": f"schedule '{sid}' not found"},
                                 status=404)
    if_match = parse_if_match(request)
    if if_match is None:
        return precondition_required_response("schedule")
    current_version = int(getattr(s, "_serverVersion", 0))
    if if_match != current_version:
        return precondition_failed_response("schedule", current_version)
    try:
        body = await request.json()
    except Exception as e:
        return web.json_response({"success": False,
                                  "error": f"Invalid JSON: {e}"}, status=400)
    ok, err = _validate_fields(body, server.settings, partial=True)
    if not ok:
        return web.json_response({"success": False, "error": err}, status=400)
    _apply_fields(s, body)
    bump_version(s)
    saveSettings()
    return web.json_response({"success": True, "schedule": _serialize(s)})


async def api_schedules_delete(request):
    """DELETE /api/schedules/{id} — remove. Returns 204; 404 if missing.
    No reference check (schedules aren't referenced by other entities)."""
    import server
    sid = request.match_info.get("id", "")
    if sid not in server.settings.schedules:
        return web.json_response({"success": False,
                                  "error": f"schedule '{sid}' not found"},
                                 status=404)
    del server.settings.schedules[sid]
    saveSettings()
    return web.Response(status=204)
