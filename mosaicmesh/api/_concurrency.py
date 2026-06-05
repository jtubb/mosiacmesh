"""Shared optimistic-concurrency helpers for the PR-2 REST endpoints.

All mutating endpoints on versioned resources (Playlists, Schedules,
ScriptingProfiles) follow the same If-Match protocol:
  - PUT requires an If-Match header carrying the current _serverVersion.
  - Missing header -> 428 Precondition Required.
  - Stale version  -> 412 Precondition Failed.
  - Success        -> bump_version() increments the object's _serverVersion.

Centralizing the parsing + response shape keeps the three resource
modules trivially consistent. The response bodies follow the project's
{success: false, error: ...} convention.
"""
from aiohttp import web

__all__ = [
    "parse_if_match",
    "precondition_required_response",
    "precondition_failed_response",
    "bump_version",
]


def parse_if_match(request):
    """Return the integer If-Match version from the request headers, or
    None if the header is absent OR malformed (non-integer).

    Note: HTTP allows If-Match to wrap the value in quotes (RFC 9110).
    We accept both '42' and '"42"' for friendliness.

    DESIGN NOTE: this function intentionally conflates "header missing"
    and "header malformed" into a single None return. PR-2 handlers
    treat None as "If-Match required" and emit 428 — the practical
    difference between missing and malformed is small (both reflect
    a confused client) and 428 with a clear error string lets the
    client recover by sending a fresh If-Match value. If a future
    handler needs to distinguish the two cases (e.g. to log
    malformed-header attacks separately), split the return into
    (None | int | <malformed-sentinel>) here rather than re-parsing
    the header in each handler.
    """
    raw = request.headers.get('If-Match')
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def precondition_required_response(resource_kind):
    """428 response for a mutating request that didn't send If-Match
    (or sent one we couldn't parse — see parse_if_match design note)."""
    return web.json_response({
        "success": False,
        "error": f"If-Match header required for {resource_kind} update",
    }, status=428)


def precondition_failed_response(resource_kind, current_version):
    """412 response for a mutating request with a stale If-Match. Returns
    the current_version in the body so the client can resync."""
    return web.json_response({
        "success": False,
        "error": f"{resource_kind} was modified by another writer",
        "currentVersion": current_version,
    }, status=412)


def bump_version(obj):
    """Mutates obj in place: obj._serverVersion = int(_serverVersion or 0) + 1.
    Returns None. The int() cast handles legacy objects loaded from older
    settings.dat where the field may be a string after jsonpickle roundtrip.
    The getattr default of 0 handles objects without the attribute at all
    (e.g. fresh resources from a future class that opts in to versioning)."""
    obj._serverVersion = int(getattr(obj, '_serverVersion', 0)) + 1
