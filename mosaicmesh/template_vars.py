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

    **Known limitation — format specs on missing keys.** A template like
    `{ip:>15}` with `ip` missing will substitute the literal `{ip}` and
    then *apply the format spec to that string*, producing
    `'          {ip}'` instead of the literal `{ip:>15}`. The fix is a
    custom `string.Formatter` subclass — out of scope for v1 because the
    project's actual templates (profile scripts) contain no format specs.
    Documented + pinned by test_safedict_format_spec_on_missing_key in
    tests/unit/test_template_vars.py so the behavior is intentional, not
    accidental.
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
    # fbX/fbY are spec §7 template variables but they're per-tap context
    # (each entry in launch.taps has its own coords), not call-time scalars.
    # We stub them here so any operator-written template referencing them
    # gets an empty string rather than the literal `{fbX}` showing up in a
    # shell command. The dispatcher's _vnc_tap_sequence reads coords
    # directly from launch.taps and doesn't route them through this map.
    vars_ = {
        "clientID":        getattr(client, "clientID", "") or "",
        "ip":              getattr(client, "ip", "") or "",
        "friendlyName":    getattr(client, "friendlyName", "") or "",
        "displayId":       getattr(client, "displayID", "") or "",
        "cacheMode":       getattr(client, "cacheMode", "") or "",
        "webclipBundleId": webclip.get("bundleId", "") or "",
        "webclipTitle":    webclip.get("title", "") or "",
        "vncPassword":     launch.get("vncPassword", "") or "",
        "fbX":             "",
        "fbY":             "",
    }
    vars_.update(extra)
    return vars_
