# Transition Effects Plugin Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An extensible effect plugin framework whose effects bake into per-screen video renders — shipping a working `fade` (video) and `audiofade` (audio) plus a `wipe` placeholder, surfaced dynamically in the editor.

**Architecture:** A new `effects.py` module holds an `Effect` base, a `ParamSpec` schema, an `EFFECTS` registry, and three plugins. `render_group_async`'s video branch resolves each item's `startEffect`/`endEffect` to ffmpeg filter fragments and threads them into the ffmpeg command builders via new `extra_video_filters`/`extra_audio_filters` params. `GET /api/effects` exposes the catalog so the editor builds effect dropdowns + parameter inputs dynamically. `index.html` is untouched (effects are baked into the media it already plays).

**Tech Stack:** Python 3 / aiohttp / ffmpeg (libx264/aac); jQuery 1.x (admin console); pytest (`tests/pytest.ini`, `asyncio_mode=auto`).

---

## Conventions for every task

- **Run tests:** `python -m pytest <path> -c tests/pytest.ini -v`. Full suite: `python pytest_runner.py --unit`.
- **Branch:** stay on `feature/discovery-completion-legacy-compat` (NOT main).
- Commit messages end with: `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`.
- **Regression rule:** after each task, `tests/unit/test_mosaic.py` + `tests/unit/test_playlists.py` stay green.

### Reference (current code)
- `build_ffmpeg_perspective_cmd(src_path, out_path, src_points, out_w, out_h)` (server.py ~470): builds `vf = perspective(...)+",scale=W:H"`, returns `["ffmpeg","-y","-i",src,"-vf",vf,"-c:v","libx264",...,"-c:a","aac","-b:a","128k","-preset","veryfast","-movflags","+faststart",out]`.
- `build_ffmpeg_individual_cmd(src_path, out_path, src_points, out_w, out_h, pad_w, pad_h, pad_x, pad_y, bg_hex)` (~487): same tail, `vf = pad+","+persp+",scale=W:H"`.
- `render_group_async` video branch (~370-403): per item, per client, builds `cmd` via one of the two builders then `await asyncio.create_subprocess_exec(*cmd, ...)`. INDIVIDUAL path computes `pts/out_path` then `build_ffmpeg_individual_cmd(...)`; the `else` (SEGMENT) computes `pts/out_path` then `build_ffmpeg_perspective_cmd(...)`. `int(c.deviceWidth) or 1`, `int(c.deviceHeight) or 1` are the device dims.
- `compute_render_token` per-item tuple (~332): `items.append((me.id, me.file, me.duration, pm, getattr(me, "backgroundColor", "#000000")))`.
- `MediaElement` already has `startEffect=None`/`endEffect=None`; `_build_media_elements` stores `item.get("startEffect")`/`endEffect`; `_media_item_payload` carries them — so `{name, params}` dicts already round-trip with no change.
- Route block (~1715): `app.router.add_route('GET', '/api/media', api_media)` etc.
- Editor `plRenderInspector` (admin.html ~610): the two disabled effect selects are at ~633-636.

---

## Task 1: `effects.py` — framework + three plugins

**Files:**
- Create: `effects.py`
- Test: `tests/unit/test_effects.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_effects.py`:

```python
"""Unit tests for the transition effect plugin framework."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import effects


class TestRegistry:
    def test_three_effects_registered(self):
        names = set(effects.EFFECTS.keys())
        assert {"fade", "audiofade", "wipe"} <= names

    def test_get_effect(self):
        assert effects.get_effect("fade").name == "fade"
        assert effects.get_effect("nope") is None

    def test_catalog_shape(self):
        cat = {e["name"]: e for e in effects.effect_catalog()}
        assert cat["fade"]["label"]
        assert cat["fade"]["params"][0]["key"] == "duration"
        assert cat["fade"]["params"][0]["default"] == 600
        wipe_params = {p["key"]: p for p in cat["wipe"]["params"]}
        assert wipe_params["direction"]["type"] == "choice"
        assert wipe_params["direction"]["choices"] == ["left", "right", "up", "down"]


class TestFade:
    def test_fade_start(self):
        v, a = effects.get_effect("fade").video_filters(
            "start", {"duration": 600}, {"duration_ms": 5000, "out_w": 80, "out_h": 60})
        assert v == ["fade=t=in:st=0:d=0.6"]
        assert a == []

    def test_fade_end_start_time_from_duration(self):
        v, a = effects.get_effect("fade").video_filters(
            "end", {"duration": 600}, {"duration_ms": 5000, "out_w": 80, "out_h": 60})
        assert v == ["fade=t=out:st=4.4:d=0.6"]
        assert a == []

    def test_audiofade_only_audio(self):
        v, a = effects.get_effect("audiofade").video_filters(
            "start", {"duration": 1000}, {"duration_ms": 5000})
        assert v == []
        assert a == ["afade=t=in:st=0:d=1"]

    def test_wipe_is_noop(self):
        v, a = effects.get_effect("wipe").video_filters(
            "start", {"direction": "left", "duration": 600}, {"duration_ms": 5000})
        assert v == [] and a == []

    def test_resolve_applies_defaults(self):
        # missing param falls back to ParamSpec default
        resolved = effects.get_effect("fade").resolve({})
        assert resolved["duration"] == 600
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -v` (no module `effects`).

- [ ] **Step 3: Implement `effects.py`**

```python
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
```

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -v` (all pass).

- [ ] **Step 5: Commit**
```bash
git add effects.py tests/unit/test_effects.py
git commit -m "feat(effects): plugin framework + fade/audiofade/wipe effects"
```

---

## Task 2: `GET /api/effects` catalog endpoint

**Files:**
- Modify: `server.py` — `import effects`; add `api_effects` handler; register the route.
- Test: `tests/unit/test_playlists.py` (has the `make_mocked_request` API-test pattern).

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_playlists.py` (the file already imports `json`, `make_mocked_request`, `pytest`):

```python
class TestEffectsApi:
    @pytest.mark.asyncio
    async def test_api_effects_lists_registered(self):
        resp = await server.api_effects(make_mocked_request('GET', '/api/effects'))
        data = json.loads(resp.text)
        names = {e["name"] for e in data["effects"]}
        assert {"fade", "audiofade", "wipe"} <= names
        fade = next(e for e in data["effects"] if e["name"] == "fade")
        assert fade["params"][0]["key"] == "duration"
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/test_playlists.py::TestEffectsApi -c tests/pytest.ini -v` (`api_effects` not defined).

- [ ] **Step 3: Implement in `server.py`**

Add `import effects` near the other imports (top of the module, with the stdlib/third-party imports).

Add the handler near `api_media`:
```python
async def api_effects(request):
    """List the registered transition effects and their parameter schemas."""
    return web.Response(text=json.dumps({"effects": effects.effect_catalog()}),
                        content_type="application/json")
```

Register the route next to `/api/media`:
```python
        app.router.add_route('GET', '/api/effects', api_effects)
```

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/unit/test_playlists.py::TestEffectsApi -c tests/pytest.ini -v`; then `python -c "import server"` (confirms the `import effects` resolves).

- [ ] **Step 5: Commit**
```bash
git add server.py tests/unit/test_playlists.py
git commit -m "feat(effects): GET /api/effects catalog endpoint"
```

---

## Task 3: ffmpeg builders accept extra filters

**Files:**
- Modify: `server.py` — `build_ffmpeg_perspective_cmd`, `build_ffmpeg_individual_cmd`.
- Test: `tests/unit/test_mosaic.py`

Add optional `extra_video_filters`/`extra_audio_filters` params (default empty → output byte-identical to today). Video filters append to the `-vf` chain; audio filters add an `-af` arg only when present.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mosaic.py`:

```python
class TestBuilderExtraFilters:
    def test_perspective_appends_video_and_audio_filters(self):
        pts = [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]]
        cmd = server.build_ffmpeg_perspective_cmd("in.mp4", "out.mp4", pts, 80, 60,
                                                  extra_video_filters=["fade=t=in:st=0:d=0.6"],
                                                  extra_audio_filters=["afade=t=in:st=0:d=0.6"])
        vf = cmd[cmd.index("-vf") + 1]
        assert vf.endswith(",fade=t=in:st=0:d=0.6")
        assert "scale=80:60" in vf
        assert cmd[cmd.index("-af") + 1] == "afade=t=in:st=0:d=0.6"

    def test_perspective_no_extras_is_unchanged(self):
        pts = [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]]
        cmd = server.build_ffmpeg_perspective_cmd("in.mp4", "out.mp4", pts, 80, 60)
        assert "-af" not in cmd
        assert cmd[cmd.index("-vf") + 1].endswith("scale=80:60")

    def test_individual_appends_filters(self):
        pts = [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]]
        cmd = server.build_ffmpeg_individual_cmd("in.mp4", "out.mp4", pts, 80, 60,
                                                100, 100, 0, 0, "#000000",
                                                extra_video_filters=["fade=t=out:st=4.4:d=0.6"],
                                                extra_audio_filters=[])
        vf = cmd[cmd.index("-vf") + 1]
        assert vf.endswith(",fade=t=out:st=4.4:d=0.6")
        assert "-af" not in cmd   # empty audio list adds no -af
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/test_mosaic.py::TestBuilderExtraFilters -c tests/pytest.ini -v` (unexpected kwargs).

- [ ] **Step 3: Implement in `server.py`**

`build_ffmpeg_perspective_cmd` — add params and thread them in:
```python
def build_ffmpeg_perspective_cmd(src_path, out_path, src_points, out_w, out_h,
                                 extra_video_filters=None, extra_audio_filters=None):
    """ffmpeg arg list: perspective-warp the source quad to fill the frame, scale
    to the screen resolution, encode iPad-compatible H.264 + AAC audio.
    src_points is [TL, TR, BR, BL]; ffmpeg's perspective wants TL, TR, BL, BR.
    extra_video_filters append to -vf; extra_audio_filters add an -af when present."""
    tl, tr, br, bl = src_points
    def n(v):
        return str(int(round(v)))
    persp = ("perspective=" + n(tl[0]) + ":" + n(tl[1]) + ":" + n(tr[0]) + ":" + n(tr[1]) +
             ":" + n(bl[0]) + ":" + n(bl[1]) + ":" + n(br[0]) + ":" + n(br[1]) + ":sense=source")
    vf = persp + ",scale=" + str(out_w) + ":" + str(out_h)
    for f in (extra_video_filters or []):
        vf += "," + f
    cmd = ["ffmpeg", "-y", "-i", src_path,
           "-vf", vf,
           "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k"]
    if extra_audio_filters:
        cmd += ["-af", ",".join(extra_audio_filters)]
    cmd += ["-preset", "veryfast", "-movflags", "+faststart", out_path]
    return cmd
```

`build_ffmpeg_individual_cmd` — same treatment (keep the existing `pad`/hex logic, change only the tail assembly):
```python
def build_ffmpeg_individual_cmd(src_path, out_path, src_points, out_w, out_h,
                                pad_w, pad_h, pad_x, pad_y, bg_hex,
                                extra_video_filters=None, extra_audio_filters=None):
    """... (existing docstring) ..."""
    tl, tr, br, bl = src_points
    def n(v):
        return str(int(round(v)))
    _h = (bg_hex or "#000000").lstrip("#")
    if len(_h) != 6:
        _h = "000000"
    hexcol = "0x" + _h
    pad = ("pad=" + str(int(pad_w)) + ":" + str(int(pad_h)) + ":" +
           str(int(pad_x)) + ":" + str(int(pad_y)) + ":color=" + hexcol)
    persp = ("perspective=" + n(tl[0]) + ":" + n(tl[1]) + ":" + n(tr[0]) + ":" + n(tr[1]) +
             ":" + n(bl[0]) + ":" + n(bl[1]) + ":" + n(br[0]) + ":" + n(br[1]) + ":sense=source")
    vf = pad + "," + persp + ",scale=" + str(out_w) + ":" + str(out_h)
    for f in (extra_video_filters or []):
        vf += "," + f
    cmd = ["ffmpeg", "-y", "-i", src_path,
           "-vf", vf,
           "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k"]
    if extra_audio_filters:
        cmd += ["-af", ",".join(extra_audio_filters)]
    cmd += ["-preset", "veryfast", "-movflags", "+faststart", out_path]
    return cmd
```

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/unit/test_mosaic.py -c tests/pytest.ini` (new tests pass; the existing `test_build_ffmpeg_cmd_has_perspective_h264_and_audio` and `test_build_individual_cmd_*` still pass — no-extras output is unchanged).

- [ ] **Step 5: Commit**
```bash
git add server.py tests/unit/test_mosaic.py
git commit -m "feat(effects): ffmpeg builders accept extra video/audio filters"
```

---

## Task 4: Render hook — bake effect filters; token hashes effects

**Files:**
- Modify: `server.py` — add `_normalize_effect` + `_resolve_effect_filters`; thread filters into both builder calls in `render_group_async`'s video branch; extend `compute_render_token`.
- Test: `tests/unit/test_mosaic.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mosaic.py`:

```python
class TestEffectRenderHook:
    def _video_group(self, mock_settings, playmode, start=None, end=None):
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "v"; me.file = "/media/server/clip.mp4"
        me.duration = 5000; me.playmode = playmode
        me.startEffect = start; me.endEffect = end
        disp.mediaElements = [me]; disp.boundingBox = [0, 0, 100, 100]
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 80; c.deviceHeight = 60
        c.measuredPerimeter = np.array([[[0, 0]], [[100, 0]], [[100, 100]], [[0, 100]]])
        mock_settings.clients = {"c1": c}
        return disp

    async def _run_capture(self, monkeypatch):
        calls = []
        class _Proc:
            returncode = 0
            async def communicate(self): return (b"", b"")
        async def _fake_exec(*args, **kwargs):
            calls.append(list(args)); return _Proc()
        monkeypatch.setattr(server.asyncio, "create_subprocess_exec", _fake_exec)
        monkeypatch.setattr(server, "get_video_dimensions", lambda p: (200, 100))
        await server.render_group_async("Default")
        return calls

    async def test_segment_video_bakes_fade(self, mock_settings, monkeypatch):
        server.settings = mock_settings; server.socketmanager = MagicMock()
        self._video_group(mock_settings, server.PlayMode.SEGMENT,
                          start={"name": "fade", "params": {"duration": 600}},
                          end={"name": "fade", "params": {"duration": 600}})
        calls = await self._run_capture(monkeypatch)
        vf = calls[0][calls[0].index("-vf") + 1]
        assert "perspective=" in vf and "scale=80:60" in vf
        assert "fade=t=in:st=0:d=0.6" in vf and "fade=t=out:st=4.4:d=0.6" in vf
        assert "-af" not in calls[0]   # fade is video-only

    async def test_individual_video_bakes_audiofade(self, mock_settings, monkeypatch):
        server.settings = mock_settings; server.socketmanager = MagicMock()
        self._video_group(mock_settings, server.PlayMode.INDIVIDUAL,
                          start={"name": "audiofade", "params": {"duration": 1000}})
        calls = await self._run_capture(monkeypatch)
        assert calls[0][calls[0].index("-af") + 1] == "afade=t=in:st=0:d=1"

    async def test_wipe_bakes_nothing(self, mock_settings, monkeypatch):
        server.settings = mock_settings; server.socketmanager = MagicMock()
        self._video_group(mock_settings, server.PlayMode.SEGMENT,
                          start={"name": "wipe", "params": {"direction": "left", "duration": 600}})
        calls = await self._run_capture(monkeypatch)
        vf = calls[0][calls[0].index("-vf") + 1]
        assert "fade" not in vf and "-af" not in calls[0]

    def test_token_changes_with_effect(self, mock_settings):
        server.settings = mock_settings
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "a"; me.file = "/media/server/x.jpg"
        me.duration = 1000; me.playmode = server.PlayMode.SEGMENT
        disp.mediaElements = [me]; disp.boundingBox = [0, 0, 100, 100]
        c = server.Client(); c.displayID = "Default"; c.deviceWidth = 80; c.deviceHeight = 60
        c.measuredPerimeter = np.array([[[0, 0]], [[50, 0]], [[50, 100]], [[0, 100]]])
        mock_settings.clients = {"c1": c}
        t1 = server.compute_render_token("Default")
        me.startEffect = {"name": "fade", "params": {"duration": 600}}
        assert server.compute_render_token("Default") != t1

    def test_normalize_effect_tolerates_shapes(self):
        assert server._normalize_effect(None) is None
        assert server._normalize_effect("fade") == {"name": "fade", "params": {}}
        assert server._normalize_effect({"name": "fade", "params": {"duration": 1}})["name"] == "fade"
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/test_mosaic.py::TestEffectRenderHook -c tests/pytest.ini -v`.

- [ ] **Step 3: Implement in `server.py`**

Add helpers above `render_group_async` (after `_is_renderable`):
```python
def _normalize_effect(field):
    """Tolerate an effect field as {name, params} | bare-string name | None."""
    if not field:
        return None
    if isinstance(field, str):
        return {"name": field, "params": {}}
    if isinstance(field, dict) and field.get("name"):
        return field
    return None


def _resolve_effect_filters(me, duration_ms, out_w, out_h):
    """Collect (video_fragments, audio_fragments) for an item's start/end effects."""
    vfs, afs = [], []
    ctx = {"duration_ms": duration_ms, "out_w": out_w, "out_h": out_h}
    for role, field in (("start", getattr(me, "startEffect", None)),
                        ("end", getattr(me, "endEffect", None))):
        spec = _normalize_effect(field)
        if not spec:
            continue
        eff = effects.get_effect(spec.get("name"))
        if eff is None:
            continue
        v, a = eff.video_filters(role, eff.resolve(spec.get("params")), ctx)
        vfs += v
        afs += a
    return vfs, afs
```

In `render_group_async`'s video branch, inside the `for key, c in clients:` loop, compute the filters once (just after `out_dir`/mkdir, before the `if me.playmode == PlayMode.INDIVIDUAL:` split) and pass them to BOTH builder calls:
```python
                    evf, eaf = _resolve_effect_filters(me, me.duration,
                                                       int(c.deviceWidth) or 1, int(c.deviceHeight) or 1)
                    if me.playmode == PlayMode.INDIVIDUAL:
                        ... # existing pad/pts/out_path computation unchanged
                        cmd = build_ffmpeg_individual_cmd(src_path, out_path, pts,
                                                          int(c.deviceWidth) or 1, int(c.deviceHeight) or 1,
                                                          pad_w, pad_h, pad_x, pad_y,
                                                          getattr(me, "backgroundColor", "#000000"),
                                                          extra_video_filters=evf, extra_audio_filters=eaf)
                    else:
                        pts = quad_to_source_points(display.boundingBox, c.measuredPerimeter, sw, sh)
                        out_path = os.path.join(out_dir, "seg_" + token + "_" + str(i) + ".mp4")
                        cmd = build_ffmpeg_perspective_cmd(src_path, out_path, pts,
                                                           int(c.deviceWidth) or 1, int(c.deviceHeight) or 1,
                                                           extra_video_filters=evf, extra_audio_filters=eaf)
```
(Leave the image branch untouched — effects on images are deferred.)

Extend `compute_render_token`'s per-item tuple to include the effect fields:
```python
        items.append((me.id, me.file, me.duration, pm,
                      getattr(me, "backgroundColor", "#000000"),
                      getattr(me, "startEffect", None), getattr(me, "endEffect", None)))
```
(`repr(...)` over the whole structure already handles the dicts.)

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/unit/test_mosaic.py::TestEffectRenderHook -c tests/pytest.ini -v`; then full `python pytest_runner.py --unit` all green (existing SEGMENT/INDIVIDUAL render tests pass — they pass no effects, so `evf/eaf` are empty and builder output is unchanged).

- [ ] **Step 5 (optional opt-in): real-ffmpeg integration.** Append a `TestEffectFfmpegIntegration` mirroring the existing opt-in skip gate in `TestFfmpegIntegration`/`TestIndividualFfmpegIntegration` (reuse its `shutil.which("ffmpeg")`/env mechanism verbatim): synthesize a lavfi clip, set a SEGMENT video item with `start={"name":"fade","params":{"duration":300}}`, run `render_group_async`, assert the `seg_<token>_0.mp4` exists and is non-empty. If the existing gate mechanism is unclear, skip this step.

- [ ] **Step 6: Commit**
```bash
git add server.py tests/unit/test_mosaic.py
git commit -m "feat(effects): bake effect filters into video render; token hashes effects"
```

---

## Task 5: Editor — data-driven effect controls

**Files:**
- Modify: `admin.html` — fetch `/api/effects`; replace the two disabled effect selects in `plRenderInspector` with dynamic dropdowns + param inputs.
- Verify: Playwright (controller-run).

`admin.html` is a desktop console (modern JS fine). `index.html` is NOT touched.

- [ ] **Step 1: Add catalog fetch + helpers**

In the editor `<script>` block, add near `plEditor`:
```javascript
var plEffectCatalog = [];   // [{name,label,params:[{key,type,default,choices?}]}]

function plLoadEffects() {
  $.getJSON('/api/effects', function(data){ plEffectCatalog = (data && data.effects) || []; });
}

function plEffectByName(name) {
  for (var i = 0; i < plEffectCatalog.length; i++) {
    if (plEffectCatalog[i].name === name) { return plEffectCatalog[i]; }
  }
  return null;
}

// Build one labeled effect dropdown + its parameter inputs.
// getFn() returns the item's current field ({name,params}|null); setFn(v) writes it.
function plRenderEffectField($host, label, getFn, setFn) {
  $('<div class="size">' + label + '</div>').appendTo($host);
  var $sel = $('<select>').appendTo($host);
  $('<option>').val("").text("None").appendTo($sel);
  $.each(plEffectCatalog, function(_, e){ $('<option>').val(e.name).text(e.label).appendTo($sel); });
  var cur = getFn();
  $sel.val((cur && cur.name) || "");
  var $params = $('<div>').appendTo($host);

  function renderParams(name) {
    $params.empty();
    var e = plEffectByName(name);
    if (!e) { return; }
    $.each(e.params, function(_, p){
      var field = getFn() || { name: name, params: {} };
      var val = (field.params && field.params[p.key] != null) ? field.params[p.key] : p.default;
      $('<span class="size" style="margin-right:4px;">' + p.key + '</span>').appendTo($params);
      var $inp;
      if (p.type === "choice") {
        $inp = $('<select>');
        $.each(p.choices, function(_, c){ $('<option>').val(c).text(c).appendTo($inp); });
        $inp.val(val);
      } else {
        $inp = $('<input>').attr('type', 'number').css('width', '5em').val(val);
      }
      $inp.on('change', function(){
        var f = getFn() || { name: name, params: {} };
        f.name = name; f.params = f.params || {};
        f.params[p.key] = (p.type === "choice") ? this.value : (parseFloat(this.value) || 0);
        setFn(f);
      });
      $params.append($inp).append('<br>');
    });
  }

  $sel.on('change', function(){
    var name = this.value;
    if (!name) { setFn(null); $params.empty(); return; }
    var e = plEffectByName(name);
    var params = {};
    if (e) { $.each(e.params, function(_, p){ params[p.key] = p.default; }); }
    setFn({ name: name, params: params });
    renderParams(name);
  });

  renderParams((cur && cur.name) || "");
}
```

- [ ] **Step 2: Replace the disabled selects in `plRenderInspector`**

Replace these lines:
```javascript
  $('<div class="size" style="opacity:.5;">Start effect</div>').appendTo($host);
  $('<select disabled><option>None (coming soon)</option></select>').appendTo($host);
  $('<div class="size" style="opacity:.5;">End effect</div>').appendTo($host);
  $('<select disabled><option>None (coming soon)</option></select>').appendTo($host);
```
with:
```javascript
  plRenderEffectField($host, "Start effect",
    function(){ return it.startEffect; }, function(v){ it.startEffect = v; });
  plRenderEffectField($host, "End effect",
    function(){ return it.endEffect; }, function(v){ it.endEffect = v; });
```

- [ ] **Step 3: Fetch the catalog on load**

In the existing `$(function(){ ... })` DOM-ready block (where `plLoadLibrary()` is called), add:
```javascript
  plLoadEffects();
```

- [ ] **Step 4: Verify (controller, Playwright)**

Start `python server.py -p 3000` (background). Navigate to `http://localhost:3000/admin.html`, then:
```javascript
() => {
  return new Promise(function(resolve){
    plLoadEffects();
    setTimeout(function(){
      plNew(); plAddItem('/media/server/videos/v.mp4', false);
      plEditor.selected = 0; plRenderInspector();
      // Start-effect dropdown = the 3rd select in the inspector (duration? no: playmode, [bg color is input], start-effect select, ...)
      var $selects = $('#plInspectorHost select');
      // find the start-effect select: it's the first select whose options include 'fade'
      var $start = $selects.filter(function(){ return $(this).find('option[value=fade]').length > 0; }).first();
      $start.val('fade').trigger('change');
      var afterFade = JSON.parse(JSON.stringify(plEditor.items[0].startEffect));
      var hasDur = $('#plInspectorHost input[type=number]').length >= 2; // duration(item) + duration(effect)
      // switch to wipe -> direction choice select appears
      $start.val('wipe').trigger('change');
      var wipeField = plEditor.items[0].startEffect;
      // back to None
      $start.val('').trigger('change');
      var noneField = plEditor.items[0].startEffect;
      resolve({ catalog: plEffectCatalog.map(function(e){return e.name;}),
                afterFade: afterFade, hasDurInput: hasDur,
                wipeName: wipeField && wipeField.name, wipeDir: wipeField && wipeField.params.direction,
                noneField: noneField });
    }, 400);
  });
}
```
Expected ≈ `{ catalog includes fade/audiofade/wipe, afterFade: {name:"fade", params:{duration:600}}, hasDurInput:true, wipeName:"wipe", wipeDir:"left", noneField:null }`.

- [ ] **Step 5: Commit**
```bash
git add admin.html
git commit -m "feat(effects): data-driven effect dropdowns + params in the editor"
```

---

## Final verification (after all tasks)

- [ ] `python pytest_runner.py --unit` → all green.
- [ ] Playwright: effect dropdowns populate from `/api/effects`; fade→duration input + `{name:"fade",params:{duration:600}}`; wipe→direction select; None→null.
- [ ] Push branch and update PR #1.

## Notes for the implementer

- **DRY:** `_fade_st_d` (effects.py) and `_resolve_effect_filters` (server.py) are the single sources for timing and filter collection — don't inline.
- **YAGNI:** `wipe` bakes nothing this slice; effects on FULL/image items bake nothing (only SEGMENT/INDIVIDUAL **video** is hooked). Do NOT widen `_is_renderable`.
- **No ES5 concern** — `index.html` untouched; `admin.html` is the desktop console.
- The effect fields already round-trip through `_build_media_elements`/`_media_item_payload` as opaque values — no change needed there; only `compute_render_token` (Task 4) must hash them.
- If `render_group_async`'s video branch differs from the reference, STOP and report NEEDS_CONTEXT rather than guessing.
```
