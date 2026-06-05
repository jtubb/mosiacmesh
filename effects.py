"""Transition effect plugins.

Each effect declares a parameter schema and contributes ffmpeg filter
fragments that are baked into the per-screen render (see render_group_async).
Adding an effect = define an Effect subclass + @register; it then appears in
the editor (via /api/effects) and is honored at render with no other changes.
"""


class ParamSpec:
    """One declared effect parameter."""
    def __init__(self, key, ptype, default, choices=None, minimum=None, maximum=None):
        self.key = key
        self.type = ptype          # "number" | "choice"
        self.default = default
        self.choices = choices
        self.minimum = minimum
        self.maximum = maximum

    def to_dict(self):
        d = {"key": self.key, "type": self.type, "default": self.default}
        if self.choices is not None:
            d["choices"] = self.choices
        if self.minimum is not None:
            d["min"] = self.minimum
        if self.maximum is not None:
            d["max"] = self.maximum
        return d


class Effect:
    name = ""
    label = ""
    params = []

    def resolve(self, params):
        """Merge user-supplied params over declared defaults."""
        out = {}
        for p in self.params:
            out[p.key] = (params or {}).get(p.key, p.default)
        return out

    def video_filters(self, role, params, ctx):
        """role: 'start' | 'end'. params: resolved dict. ctx: {'duration_ms', ...}.
        Returns (video_fragments, audio_fragments): lists of ffmpeg filter strings."""
        return ([], [])


EFFECTS = {}


def register(cls):
    EFFECTS[cls.name] = cls()
    return cls


def get_effect(name):
    return EFFECTS.get(name)


def effect_catalog():
    return [{"name": e.name, "label": e.label,
             "params": [p.to_dict() for p in e.params]}
            for e in EFFECTS.values()]


def _fmt(x):
    return "%g" % x


def _fade_st_d(role, params, ctx):
    """Shared timing: ('st', 'd') strings in seconds for a fade-style effect."""
    d_ms = float(params["duration"])
    d = d_ms / 1000.0
    if role == "start":
        st = 0.0
    else:
        st = max(0.0, (float(ctx.get("duration_ms", 0)) - d_ms) / 1000.0)
    return _fmt(st), _fmt(d)


@register
class FadeEffect(Effect):
    name = "fade"
    label = "Fade (video)"
    params = [ParamSpec("duration", "number", 600, minimum=0)]

    def video_filters(self, role, params, ctx):
        st, d = _fade_st_d(role, params, ctx)
        typ = "in" if role == "start" else "out"
        return (["fade=t=" + typ + ":st=" + st + ":d=" + d], [])


@register
class AudioFadeEffect(Effect):
    name = "audiofade"
    label = "Audio fade"
    params = [ParamSpec("duration", "number", 600, minimum=0)]

    def video_filters(self, role, params, ctx):
        st, d = _fade_st_d(role, params, ctx)
        typ = "in" if role == "start" else "out"
        return ([], ["afade=t=" + typ + ":st=" + st + ":d=" + d])


@register
class WipeEffect(Effect):
    name = "wipe"
    label = "Wipe (coming soon)"
    params = [ParamSpec("direction", "choice", "left", choices=["left", "right", "up", "down"]),
              ParamSpec("duration", "number", 600, minimum=0)]

    def video_filters(self, role, params, ctx):
        return ([], [])   # placeholder: geometry baked in a later slice
