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


def parse_if_match(request):
    """Return the integer If-Match version from the request headers, or
    None if the header is absent or non-integer. Handlers decide how
    to respond (428 for missing, 412 for stale, 400 for malformed).

    Note: HTTP allows If-Match to wrap the value in quotes (RFC 9110).
    We accept both '42' and '"42"' for friendliness.
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
    """428 response for a mutating request that didn't send If-Match."""
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
    """Increment obj._serverVersion by 1. Used after every successful PUT
    so subsequent If-Match comparisons reflect the new state."""
    obj._serverVersion = int(getattr(obj, '_serverVersion', 0)) + 1
