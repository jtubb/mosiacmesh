"""Template-variable substitution machinery for the ScriptingProfile
dispatcher (PR-3 of the admin-timeline-redesign spec).

Profile script strings (e.g. `profile.scripts["start"]`) contain
literal `{tokens}` such as `{webclipBundleId}` and `{displayUrl}`.
The dispatcher calls `str.format_map(SafeDict(build_vars(...)))` to
substitute them at run time. Unknown tokens are left literal — this
is intentional: operators may use template strings that include
shell variables (`$HOME`, etc.) or other content the substitution
layer should not try to interpret. See spec §7 "Template variables".
"""

__all__ = ["SafeDict", "build_vars"]


class SafeDict(dict):
    """A dict that returns `{key}` (the literal placeholder) on missing
    keys, so `str.format_map(SafeDict(...))` never raises KeyError.

    This is the canonical pattern recommended in PEP 3101 §"Format
    String Syntax" for safe partial substitution.
    """

    def __missing__(self, key):
        return "{" + key + "}"


def build_vars(client, profile, **extra):
    """Construct the substitution dict the dispatcher hands to
    `str.format_map(SafeDict(...))`. Pulls fields from the Client and
    ScriptingProfile per spec §7's table; extra keyword args (typically
    `displayUrl` from server config) merge in last and win on conflict.

    Returns a plain dict — wrap in SafeDict at the call site if you
    want missing-key tolerance during format_map.

    Robust to None / missing nested-dict fields: an unconfigured
    webclip or launch dict yields empty strings rather than raising.
    """
    webclip = getattr(profile, "webclip", None) or {}
    launch = getattr(profile, "launch", None) or {}
    vars_ = {
        "clientID":        getattr(client, "clientID", "") or "",
        "ip":              getattr(client, "ip", "") or "",
        "friendlyName":    getattr(client, "friendlyName", "") or "",
        "displayId":       getattr(client, "displayID", "") or "",
        "cacheMode":       getattr(client, "cacheMode", "") or "",
        "webclipBundleId": webclip.get("bundleId", "") or "",
        "webclipTitle":    webclip.get("title", "") or "",
        "vncPassword":     launch.get("vncPassword", "") or "",
    }
    vars_.update(extra)
    return vars_
