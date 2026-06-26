"""Transition effect plugins.

Each effect declares a parameter schema and contributes ffmpeg filter
fragments that are baked into the per-screen render (see render_group_async).
Adding an effect = define an Effect subclass + @register; it then appears in
the editor (via /api/effects) and is honored at render with no other changes.

Visual transitions (fade/wipe) are now handled client-side; only audio fades
are baked into the render (via afade) when the audioFade param is True.
"""


class ParamSpec:
    """One declared effect parameter."""
    def __init__(self, key, ptype, default, choices=None, minimum=None, maximum=None):
        self.key = key
        self.type = ptype          # "number" | "choice" | "boolean"
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


def effect_audio_fade_default(name):
    """The declared default of an effect's 'audioFade' param (bool); False if the
    effect or param doesn't exist. Lets callers resolve a missing audioFade key on
    legacy data without duplicating the schema default."""
    eff = EFFECTS.get(name)
    if eff is None:
        return False
    for ps in eff.params:
        if ps.key == "audioFade":
            return bool(ps.default)
    return False


def _fmt(x):
    return "%g" % x


def _fade_st_d(role, params, ctx):
    """Shared timing: ('st', 'd') strings in seconds for a fade-style effect."""
    d_ms = float(params["duration"])
    d = d_ms / 1000.0
    if role == "start":
        st = 0.0
    else:
        # `or 0` (not a .get default) so a present-but-None duration_ms — an
        # "Auto"-duration item before length resolution — clamps to 0 instead of
        # crashing float(None). Callers should pass the resolved length; defense
        # in depth alongside the call-site fix in render._encode_group.
        st = max(0.0, (float(ctx.get("duration_ms") or 0) - d_ms) / 1000.0)
    return _fmt(st), _fmt(d)


def _afade(role, params, ctx):
    """afade fragment list when audioFade is on for this role, else [].

    Expects resolve()d params so that 'duration' and 'audioFade' are present;
    'audioFade' is evaluated as a plain Python bool (truthy/falsy).
    """
    if not params.get("audioFade"):
        return []
    st, d = _fade_st_d(role, params, ctx)
    typ = "in" if role == "start" else "out"
    return ["afade=t=" + typ + ":st=" + st + ":d=" + d]


@register
class FadeEffect(Effect):
    name = "fade"
    label = "Fade"
    params = [ParamSpec("duration", "number", 600, minimum=0),
              ParamSpec("audioFade", "boolean", True)]

    def video_filters(self, role, params, ctx):
        return ([], _afade(role, params, ctx))     # visual fade is client-side


@register
class WipeEffect(Effect):
    name = "wipe"
    label = "Wipe"
    params = [ParamSpec("direction", "choice", "left", choices=["left", "right", "up", "down"]),
              ParamSpec("scope", "choice", "screen", choices=["screen", "wall"]),
              ParamSpec("duration", "number", 600, minimum=0),
              ParamSpec("audioFade", "boolean", True)]

    def video_filters(self, role, params, ctx):
        return ([], _afade(role, params, ctx))     # visual wipe is client-side


@register
class SlideEffect(Effect):
    name = "slide"
    label = "Slide"
    params = [ParamSpec("direction", "choice", "left", choices=["left", "right", "up", "down"]),
              ParamSpec("scope", "choice", "wall", choices=["screen", "wall"]),
              ParamSpec("duration", "number", 600, minimum=0),
              ParamSpec("audioFade", "boolean", True)]

    def video_filters(self, role, params, ctx):
        return ([], _afade(role, params, ctx))


@register
class ZoomEffect(Effect):
    name = "zoom"
    label = "Zoom"
    params = [ParamSpec("scale", "number", 0.6, minimum=0.05, maximum=1),
              ParamSpec("scope", "choice", "wall", choices=["screen", "wall"]),
              ParamSpec("duration", "number", 600, minimum=0),
              ParamSpec("audioFade", "boolean", True)]

    def video_filters(self, role, params, ctx):
        return ([], _afade(role, params, ctx))


@register
class IrisEffect(Effect):
    name = "iris"
    label = "Iris"
    params = [ParamSpec("scope", "choice", "wall", choices=["screen", "wall"]),
              ParamSpec("duration", "number", 600, minimum=0),
              ParamSpec("audioFade", "boolean", True)]

    def video_filters(self, role, params, ctx):
        return ([], _afade(role, params, ctx))


@register
class DissolveEffect(Effect):
    name = "dissolve"
    label = "Dissolve"
    params = [ParamSpec("blocks", "number", 16, minimum=2, maximum=64),
              ParamSpec("duration", "number", 600, minimum=0),
              ParamSpec("audioFade", "boolean", True)]

    def video_filters(self, role, params, ctx):
        return ([], _afade(role, params, ctx))


@register
class BeerFillEffect(Effect):
    name = "beerfill"
    label = "Beer Fill"
    # Single `duration`: a beerfill instance only ever runs ONE phase (fill when used
    # as an endEffect, drain when used as a startEffect), so one length suffices.
    params = [ParamSpec("beerType", "choice", "pale", choices=["pale", "amber", "stout"]),
              ParamSpec("scope", "choice", "wall", choices=["screen", "wall"]),
              ParamSpec("duration", "number", 2500, minimum=0),
              ParamSpec("audioFade", "boolean", True)]

    def video_filters(self, role, params, ctx):
        return ([], _afade(role, params, ctx))     # visual is client-side; single duration


@register
class ScatterEffect(Effect):
    name = "scatter"
    label = "Scatter"
    params = [ParamSpec("sprite", "string", "hop"),
              ParamSpec("scope", "choice", "wall", choices=["screen", "wall"]),
              ParamSpec("count", "number", 40, minimum=1, maximum=120),
              ParamSpec("fillMs", "number", 2500, minimum=0),
              ParamSpec("drainMs", "number", 2500, minimum=0),
              ParamSpec("audioFade", "boolean", True),
              ParamSpec("giantScale", "number", 0.2, minimum=0, maximum=2)]

    def video_filters(self, role, params, ctx):
        dur = params.get("fillMs") if role == "end" else params.get("drainMs")
        p = dict(params)
        p["duration"] = dur
        return ([], _afade(role, p, ctx))


@register
class KegRollEffect(Effect):
    name = "kegroll"
    label = "Keg Roll"
    params = [ParamSpec("sprite", "string", "keg"),
              ParamSpec("direction", "choice", "right", choices=["left", "right", "up", "down"]),
              ParamSpec("scope", "choice", "wall", choices=["screen", "wall"]),
              ParamSpec("duration", "number", 2000, minimum=0),
              ParamSpec("audioFade", "boolean", True)]

    def video_filters(self, role, params, ctx):
        return ([], _afade(role, params, ctx))     # visual roll is client-side


@register
class FrostCreepEffect(Effect):
    name = "frostcreep"
    label = "Frost Creep"
    # Single `duration`: a frostcreep instance only covers (endEffect) or reveals
    # (startEffect), never both.
    params = [ParamSpec("tint", "choice", "frost", choices=["frost", "blue", "clear"]),
              ParamSpec("sprite", "string", "frostymug"),   # mug that drops in / rises out (any transparent PNG; "" = pure frost)
              ParamSpec("scope", "choice", "wall", choices=["screen", "wall"]),
              ParamSpec("duration", "number", 2200, minimum=0),
              ParamSpec("audioFade", "boolean", True)]

    def video_filters(self, role, params, ctx):
        return ([], _afade(role, params, ctx))     # visual is client-side; single duration


@register
class CoasterFlipEffect(Effect):
    name = "coasterflip"
    label = "Coaster Flip"
    # Single `duration`: a coasterflip instance only folds (endEffect) or opens
    # (startEffect), never both.
    params = [ParamSpec("axis", "choice", "horizontal", choices=["horizontal", "vertical"]),
              ParamSpec("coaster", "choice", "kraft", choices=["kraft", "cork", "slate"]),
              ParamSpec("sprite", "string", "coaster"),   # back-face PNG (any transparent PNG; "" = blank back)
              ParamSpec("flips", "number", 5, minimum=1, maximum=12),   # half-turns in the tumble
              ParamSpec("scope", "choice", "wall", choices=["screen", "wall"]),
              ParamSpec("duration", "number", 1800, minimum=0),
              ParamSpec("audioFade", "boolean", True)]

    def video_filters(self, role, params, ctx):
        return ([], _afade(role, params, ctx))     # visual is client-side; single duration


@register
class WheatPartEffect(Effect):
    name = "wheatpart"
    label = "Wheat Part"
    # Single `duration`: a wheatpart instance only covers (endEffect) or reveals
    # (startEffect), never both.
    params = [ParamSpec("tint", "choice", "golden", choices=["golden", "amber", "pale"]),
              ParamSpec("density", "number", 70, minimum=10, maximum=200),
              ParamSpec("hold", "number", 0.2, minimum=0, maximum=0.5),
              ParamSpec("scope", "choice", "wall", choices=["screen", "wall"]),
              ParamSpec("duration", "number", 2200, minimum=0),
              ParamSpec("audioFade", "boolean", True)]

    def video_filters(self, role, params, ctx):
        return ([], _afade(role, params, ctx))     # visual is client-side; single duration
