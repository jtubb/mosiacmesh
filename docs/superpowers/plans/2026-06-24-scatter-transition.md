# Scatter Transition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a PNG-driven `scatter` transition — a giant sprite grows at center while N copies erupt outward (continuous, no pause) over a clean cover disc, then reverses to reveal — with the sprite being any transparent PNG in the media library.

**Architecture:** Mirrors the `beerfill` effect: a server `Effect` subclass (audio-only bake) + pure ES5 helpers and a canvas draw in `js/transitions.js`, wired into `index.html`'s mesh in-canvas + overlay cover paths. The sprite is chosen from transparent PNGs in `media/server/images/` (alpha detected via the PNG header color-type byte; `/api/media` exposes a `transparent` flag). Per-frame work is `drawImage` of a once-decoded PNG + `fillRect`.

**Tech Stack:** Python (aiohttp, pytest), ES5 JavaScript (iPad-1 / Safari 5.1), Node `--test` for pure JS helpers.

## Global Constraints

- Client code in `js/transitions.js` and `index.html` is **ES5 only** — no `let`/`const`, arrows, template literals, `class`, `Promise`.
- Per-frame canvas work: `drawImage`, `arc`+`fill`, `fillRect` only. **No `clip()`, no `destination-*` compositing.**
- Effects register via `@register`; visual transitions bake **audio only** (`_afade`), never video filters.
- `scatter` params: `sprite` (string, default `"hop"`), `scope` (choice `screen`/`wall`, default `wall`), `count` (number, default `40`, min `1`, max `120`), `fillMs` (number, `2500`, min `0`), `drainMs` (number, `2500`, min `0`), `audioFade` (boolean, `true`).
- `video_filters` uses `fillMs` for role `'end'`, `drainMs` for role `'start'`.
- Locked visual constants (not params): giant peak `GIANT_PEAK = 1.43` (× region height); giant rotates continuously; eruption distance is **monotonic non-decreasing** within each phase and continuous across the cover→reveal handoff (the no-pause requirement); backing disc = item `backgroundColor`.
- **`front` = local phase progress, 0→1 (0 at phase start → 1 at phase end).** `mmTransitionState` gives `p` that counts UP on the start/`in` window (0→1) but DOWN on the end/`out` window (1→0); the scatter branch stores `front = (role === 'out' ? 1 - p : p)` so every `mmScatter*` helper receives a clean 0→1 local progress. (A cover must grow as the item ends — storing raw `p` would run it backwards.)
- Sprite resolution: a bare name `hop` → `/media/server/images/hop.png`; a value starting with `/` is used as-is.
- Run tests: `python pytest_runner.py --unit` (Python) and `node --test tests/unit/js/<file>` (JS). Bare `pytest` won't load config.

---

### Task 1: Server — `ScatterEffect` in `effects.py`

**Files:**
- Modify: `effects.py` (add subclass after `BeerFillEffect`)
- Test: `tests/unit/test_effects.py`, `tests/unit/test_playlists.py`

**Interfaces:**
- Consumes: `Effect`, `ParamSpec`, `register`, `_afade` (existing).
- Produces: effect `name="scatter"` with the params from Global Constraints; `video_filters(role, params, ctx)` → `([], <afade with fillMs on end / drainMs on start>)`.

- [ ] **Step 1: Write failing tests** — append to `tests/unit/test_effects.py`:

```python
def test_scatter_params():
    e = next(e for e in effects.effect_catalog() if e["name"] == "scatter")
    by = {p["key"]: p for p in e["params"]}
    assert by["sprite"]["type"] == "string" and by["sprite"]["default"] == "hop"
    assert by["scope"]["choices"] == ["screen", "wall"] and by["scope"]["default"] == "wall"
    assert by["count"]["type"] == "number" and by["count"]["default"] == 40
    assert by["count"]["min"] == 1 and by["count"]["max"] == 120
    assert by["fillMs"]["default"] == 2500 and by["drainMs"]["default"] == 2500
    assert by["audioFade"]["type"] == "boolean" and by["audioFade"]["default"] is True


def test_scatter_audio_uses_fillMs_on_end_drainMs_on_start():
    sc = effects.get_effect("scatter")
    ctx = {"duration_ms": 6000}
    v, a = sc.video_filters("start", sc.resolve({"drainMs": 2000, "audioFade": True}), ctx)
    assert v == [] and a == ["afade=t=in:st=0:d=2"]
    v2, a2 = sc.video_filters("end", sc.resolve({"fillMs": 1500, "audioFade": True}), ctx)
    assert v2 == [] and a2 == ["afade=t=out:st=4.5:d=1.5"]
```

Update the catalog-set assertion in the same file:

```python
def test_catalog_has_all_effects():
    names = {e["name"] for e in effects.effect_catalog()}
    assert names == {"fade", "wipe", "slide", "zoom", "iris", "dissolve", "beerfill", "scatter"}
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -v`
Expected: FAIL — `scatter` not in catalog.

- [ ] **Step 3: Implement** — add to `effects.py` after `BeerFillEffect`:

```python
@register
class ScatterEffect(Effect):
    name = "scatter"
    label = "Scatter"
    params = [ParamSpec("sprite", "string", "hop"),
              ParamSpec("scope", "choice", "wall", choices=["screen", "wall"]),
              ParamSpec("count", "number", 40, minimum=1, maximum=120),
              ParamSpec("fillMs", "number", 2500, minimum=0),
              ParamSpec("drainMs", "number", 2500, minimum=0),
              ParamSpec("audioFade", "boolean", True)]

    def video_filters(self, role, params, ctx):
        dur = params.get("fillMs") if role == "end" else params.get("drainMs")
        p = dict(params)
        p["duration"] = dur
        return ([], _afade(role, p, ctx))
```

Note: `ParamSpec("sprite", "string", "hop")` — confirm `to_dict()` emits `{"key","type","default"}` for a `"string"` type (no choices/min/max). It does (see `ParamSpec.to_dict`).

- [ ] **Step 4: Run, verify PASS**

Run: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Update `test_playlists.py` effects-list assertion**

Search `tests/unit/test_playlists.py` for the set `{"fade", "wipe", "slide", "zoom", "iris", "dissolve", "beerfill"}` and add `"scatter"`:

```python
        assert names == {"fade", "wipe", "slide", "zoom", "iris", "dissolve", "beerfill", "scatter"}
```

- [ ] **Step 6: Full unit suite**

Run: `python pytest_runner.py --unit`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add effects.py tests/unit/test_effects.py tests/unit/test_playlists.py
git commit -m "feat(transitions): scatter server effect (audio-only, fillMs/drainMs by role)"
```

---

### Task 2: Server — transparent-PNG detection + `/api/media` `transparent` flag

**Files:**
- Modify: `mosaicmesh/api/media.py`
- Test: `tests/unit/test_media_api.py` (create) — or append to an existing media test if present (`ls tests/unit | grep media`); create the file if none.

**Interfaces:**
- Produces: `mosaicmesh.api.media._png_has_alpha(path) -> bool` (True if the file is a PNG whose IHDR color-type has alpha — type 4 gray+alpha or 6 RGBA; False for non-PNG, missing, or opaque color types 0/2/3). Result cached by `(path, mtime)`.
- `GET /api/media` JSON gains `"transparent": { "<image url>": true|false, ... }` for every image in the list.

- [ ] **Step 1: Write failing tests** — create `tests/unit/test_media_api.py`:

```python
import os, struct, zlib, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from mosaicmesh.api import media


def _png(path, color_type):
    # minimal 1x1 PNG with the given IHDR color type (6=RGBA, 2=RGB)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, color_type, 0, 0, 0)
    def chunk(typ, data):
        return struct.pack(">I", len(data)) + typ + data + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00")) + chunk(b"IEND", b""))


def test_png_has_alpha_rgba_true(tmp_path):
    p = tmp_path / "a.png"; _png(str(p), 6)              # RGBA
    assert media._png_has_alpha(str(p)) is True


def test_png_has_alpha_rgb_false(tmp_path):
    p = tmp_path / "b.png"; _png(str(p), 2)              # RGB (no alpha)
    assert media._png_has_alpha(str(p)) is False


def test_png_has_alpha_missing_or_nonpng_false(tmp_path):
    assert media._png_has_alpha(str(tmp_path / "nope.png")) is False
    j = tmp_path / "c.jpg"; j.write_bytes(b"\xff\xd8\xff\xe0not a png")
    assert media._png_has_alpha(str(j)) is False
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest tests/unit/test_media_api.py -c tests/pytest.ini -v`
Expected: FAIL — `_png_has_alpha` not defined.

- [ ] **Step 3: Implement** — in `mosaicmesh/api/media.py`, add near the top (after imports):

```python
# (path, mtime) -> bool cache for PNG alpha detection (cheap header read)
_png_alpha_cache = {}


def _png_has_alpha(path):
    """True iff `path` is a PNG whose IHDR color type carries alpha (4=gray+alpha,
    6=RGBA). Reads only the 8-byte signature + IHDR (color type at byte 25) — no
    pixel decode. Cached by (path, mtime). Non-PNG / missing -> False."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return False
    key = (path, mtime)
    if key in _png_alpha_cache:
        return _png_alpha_cache[key]
    result = False
    try:
        with open(path, "rb") as f:
            head = f.read(26)
        if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 26:
            color_type = head[25]
            result = color_type in (4, 6)
    except OSError:
        result = False
    _png_alpha_cache[key] = result
    return result
```

Then in `api_media`, after building `videos`/`durations`, compute the flag for images and add it to the body:

```python
    images = _list("images")
    transparent = {}
    for url in images:
        disk = os.path.join("media", "server", "images", os.path.basename(url))
        transparent[url] = _png_has_alpha(disk)
    body = json.dumps({"images": images, "videos": videos,
                       "videoDurations": durations, "transparent": transparent})
```

(Replace the existing `body = json.dumps({"images": _list("images"), ...})` line; reuse the new `images` variable.)

- [ ] **Step 4: Write a flag test** — append to `tests/unit/test_media_api.py`:

```python
import asyncio
from unittest.mock import MagicMock

def test_api_media_marks_transparent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    imgs = tmp_path / "media" / "server" / "images"; imgs.mkdir(parents=True)
    (tmp_path / "media" / "server" / "videos").mkdir(parents=True)
    _png(str(imgs / "hop.png"), 6)        # transparent
    _png(str(imgs / "flat.png"), 2)       # opaque
    import server
    server.settings = server.Settings()
    req = MagicMock()
    resp = asyncio.get_event_loop().run_until_complete(media.api_media(req))
    import json as _j
    data = _j.loads(resp.text)
    assert data["transparent"]["/media/server/images/hop.png"] is True
    assert data["transparent"]["/media/server/images/flat.png"] is False
```

- [ ] **Step 5: Run, verify PASS**

Run: `python -m pytest tests/unit/test_media_api.py -c tests/pytest.ini -v`
Expected: PASS (4 tests). If `import server` is heavy, the test still works — it's the established pattern.

- [ ] **Step 6: Full unit suite + commit**

Run: `python pytest_runner.py --unit` → all pass.
```bash
git add mosaicmesh/api/media.py tests/unit/test_media_api.py
git commit -m "feat(media): detect transparent PNGs (header color-type) + /api/media transparent flag"
```

---

### Task 3: Pure scatter helpers in `js/transitions.js`

**Files:**
- Modify: `js/transitions.js` (add functions + exports before the `root.*` block)
- Test: `tests/unit/js/test_scatter.js` (create)

**Interfaces:**
- Consumes: `_mmLcg(seed)` (existing).
- Produces (all pure, exported on `root`):
  - `mmScatterPhase(role)` → `'cover'` if `role === 'out'` else `'reveal'`.
  - `mmScatterDuration(params, role)` → `fillMs` if `role === 'out'` else `drainMs`; missing/≤0 → 2500.
  - **In all three below, the second arg is the descriptor's `front` = local phase progress (0→1).**
  - `mmScatterCover(phase, lp)` → cover amount `c∈[0,1]`: `'cover'`→clamp(lp) (grows 0→1), else clamp(1−lp) (shrinks 1→0). Drives disc radius + giant scale.
  - `mmScatterDist(phase, lp)` → eruption distance factor: `'cover'`→`pow(clamp(lp),0.72)` (0→1), `'reveal'`→`1 + clamp(lp)*1.4` (1→2.4). Monotonic non-decreasing in `lp` per phase; continuous at the handoff (cover@1 = reveal@0 = 1).
  - `mmScatterGiantAngle(phase, lp)` → radians: `'cover'`→`lp*2π`, `'reveal'`→`(1+lp)*2π` (keeps spinning).
  - `mmScatterSpriteUrl(sprite)` → if `sprite` starts with `'/'` return it, else `'/media/server/images/' + sprite + '.png'`.

- [ ] **Step 1: Write failing test** — create `tests/unit/js/test_scatter.js`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/transitions.js');
const g = globalThis;

test('mmScatterPhase: out=cover, in=reveal', () => {
  assert.equal(g.mmScatterPhase('out'), 'cover');
  assert.equal(g.mmScatterPhase('in'), 'reveal');
});
test('mmScatterDuration: fillMs on out, drainMs on in, default 2500', () => {
  assert.equal(g.mmScatterDuration({ fillMs: 1500, drainMs: 3000 }, 'out'), 1500);
  assert.equal(g.mmScatterDuration({ fillMs: 1500, drainMs: 3000 }, 'in'), 3000);
  assert.equal(g.mmScatterDuration({}, 'out'), 2500);
});
test('mmScatterCover: cover rises, reveal falls, clamped', () => {
  assert.equal(g.mmScatterCover('cover', 0), 0);
  assert.equal(g.mmScatterCover('cover', 1), 1);
  assert.equal(g.mmScatterCover('reveal', 0), 1);
  assert.equal(g.mmScatterCover('reveal', 1), 0);
  assert.equal(g.mmScatterCover('cover', -1), 0);
});
test('mmScatterDist: monotonic per phase, continuous at handoff', () => {
  let prev = -1;
  for (let i = 0; i <= 10; i++) { const d = g.mmScatterDist('cover', i / 10); assert.ok(d >= prev - 1e-9); prev = d; }
  assert.ok(Math.abs(g.mmScatterDist('cover', 1) - 1) < 1e-9);
  assert.ok(Math.abs(g.mmScatterDist('reveal', 0) - 1) < 1e-9);   // continuous: cover@1 == reveal@0
  prev = -1;
  for (let i = 0; i <= 10; i++) { const d = g.mmScatterDist('reveal', i / 10); assert.ok(d >= prev - 1e-9); prev = d; }
  assert.ok(g.mmScatterDist('reveal', 1) > 2);
});
test('mmScatterGiantAngle: full turn by cover end, keeps turning on reveal', () => {
  assert.ok(Math.abs(g.mmScatterGiantAngle('cover', 1) - 2 * Math.PI) < 1e-9);
  assert.ok(g.mmScatterGiantAngle('reveal', 1) > 2 * Math.PI);
});
test('mmScatterSpriteUrl: name vs path', () => {
  assert.equal(g.mmScatterSpriteUrl('hop'), '/media/server/images/hop.png');
  assert.equal(g.mmScatterSpriteUrl('/media/server/images/x.png'), '/media/server/images/x.png');
});
```

- [ ] **Step 2: Run, verify FAIL**

Run: `node --test tests/unit/js/test_scatter.js`
Expected: FAIL — helpers undefined.

- [ ] **Step 3: Implement** — add to `js/transitions.js` before the export block:

```javascript
  function _clamp01(x) { return x < 0 ? 0 : (x > 1 ? 1 : x); }
  function mmScatterPhase(role) { return role === 'out' ? 'cover' : 'reveal'; }
  function mmScatterDuration(params, role) {
    var ms = +(role === 'out' ? (params && params.fillMs) : (params && params.drainMs));
    return ms > 0 ? ms : 2500;
  }
  function mmScatterCover(phase, p) { return _clamp01(phase === 'cover' ? p : 1 - p); }
  function mmScatterDist(phase, p) {
    var c = _clamp01(p);
    return phase === 'cover' ? Math.pow(c, 0.72) : (1 + c * 1.4);
  }
  function mmScatterGiantAngle(phase, p) {
    var c = _clamp01(p);
    return (phase === 'cover' ? c : 1 + c) * 6.283185307;
  }
  function mmScatterSpriteUrl(sprite) {
    if (!sprite) { sprite = 'hop'; }
    return (String(sprite).charAt(0) === '/') ? sprite : ('/media/server/images/' + sprite + '.png');
  }
```

Add exports in the `root.*` block:

```javascript
  root.mmScatterPhase = mmScatterPhase;
  root.mmScatterDuration = mmScatterDuration;
  root.mmScatterCover = mmScatterCover;
  root.mmScatterDist = mmScatterDist;
  root.mmScatterGiantAngle = mmScatterGiantAngle;
  root.mmScatterSpriteUrl = mmScatterSpriteUrl;
```

- [ ] **Step 4: Run, verify PASS**

Run: `node --test tests/unit/js/test_scatter.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_scatter.js
git commit -m "feat(transitions): scatter pure helpers (phase/duration/cover/dist/giant-angle/url)"
```

---

### Task 4: `mmScatterParticles` + `_dur`/`mmTransitionState` scatter branch

**Files:**
- Modify: `js/transitions.js` (`_dur` line 6; `mmTransitionState` ~lines 87-120; add `mmScatterParticles` + export)
- Test: `tests/unit/js/test_scatter.js` (append)

**Interfaces:**
- Consumes: `_mmLcg`, `mmScatterPhase`, `mmScatterDuration` (Task 3).
- Produces:
  - `mmScatterParticles(seed, count)` → array of `count` objects `{ang, sp, rot0, rps}` (`ang∈[0,2π)`, `sp∈[0.6,1.5)`, `rot0∈[0,2π)`, `rps∈[-0.7,0.7)`), deterministic from `seed`.
  - `mmTransitionState(...)` for a `scatter` effect returns `{role, opacity:1, wipe:null, effect:{name:'scatter', family:'mask', front:<localProgress>, scope, params, phase}}` where `phase = mmScatterPhase(role)` and **`front = (role === 'out' ? 1 - p : p)`** — local phase progress 0→1 (see Global Constraints). `_dur` returns `mmScatterDuration(eff.params, role)` for `scatter`.

- [ ] **Step 1: Write failing test** — append to `tests/unit/js/test_scatter.js`:

```javascript
test('mmScatterParticles: deterministic per seed, ranges, count', () => {
  const a = g.mmScatterParticles(9, 40), b = g.mmScatterParticles(9, 40), c = g.mmScatterParticles(10, 40);
  assert.equal(a.length, 40);
  assert.deepEqual(a, b);
  assert.notDeepEqual(a, c);
  a.forEach(p => {
    assert.ok(p.ang >= 0 && p.ang < 6.2832);
    assert.ok(p.sp >= 0.6 && p.sp < 1.5);
    assert.ok(p.rot0 >= 0 && p.rot0 < 6.2832);
    assert.ok(p.rps >= -0.7 && p.rps < 0.7);
  });
});
test('mmTransitionState: scatter end=cover, start=reveal (mask family)', () => {
  const S = g.mmTransitionState;
  const end = { name: 'scatter', params: { fillMs: 2000, drainMs: 2000, scope: 'wall' } };
  // offset 4500 of 6000 with ed=2000 -> raw p=(6000-4500)/2000=0.75 -> front=1-0.75=0.25
  // (distinguishes local-progress from raw p; near the end of the item, cover is only 25% in)
  let st = S(null, end, 4500, 6000, null, null);
  assert.equal(st.role, 'out');
  assert.equal(st.effect.name, 'scatter');
  assert.equal(st.effect.family, 'mask');
  assert.equal(st.effect.phase, 'cover');
  assert.ok(Math.abs(st.effect.front - 0.25) < 1e-9);   // local progress, NOT raw p (0.75)
  assert.equal(st.effect.scope, 'wall');
  const start = { name: 'scatter', params: { fillMs: 2000, drainMs: 2000 } };
  st = S(start, null, 500, 6000, null, null);       // in-window: raw p=0.25, front=0.25
  assert.equal(st.effect.phase, 'reveal');
  assert.ok(Math.abs(st.effect.front - 0.25) < 1e-9);
  assert.equal(st.effect.scope, 'wall');            // default
});
```

- [ ] **Step 2: Run, verify FAIL**

Run: `node --test tests/unit/js/test_scatter.js`
Expected: FAIL — `mmScatterParticles` undefined / scatter falls through to the fade branch (no `.effect`).

- [ ] **Step 3: Implement** — add `mmScatterParticles` near the other scatter helpers:

```javascript
  function mmScatterParticles(seed, count) {
    var rnd = _mmLcg(seed >>> 0), arr = [], i;
    for (i = 0; i < count; i++) {
      arr.push({ ang: rnd() * 6.283185307, sp: 0.6 + rnd() * 0.9,
                 rot0: rnd() * 6.283185307, rps: (rnd() - 0.5) * 1.4 });
    }
    return arr;
  }
```

Export it (`root.mmScatterParticles = mmScatterParticles;`). Extend `_dur` (line 6) — it is already role-aware for `beerfill` after the beerfill task; add scatter:

```javascript
  function _dur(eff, role) {
    if (!eff || !eff.params) { return 0; }
    if (eff.name === 'beerfill') { return mmBeerDuration(eff.params, role); }
    if (eff.name === 'scatter') { return mmScatterDuration(eff.params, role); }
    return (+eff.params.duration) || 0;
  }
```

In `mmTransitionState`, add a `scatter` branch (next to the `beerfill` branch, before the `slide/zoom/iris/dissolve` branch):

```javascript
    if (eff.name === 'scatter') {
      var ssc = (eff.params && eff.params.scope) || 'wall';
      // front = LOCAL phase progress 0->1. mmTransitionState's `p` counts down on
      // the 'out' window (1->0), so invert there; 'in' already counts up.
      var slp = (role === 'out') ? (1 - p) : p;
      return { role: role, opacity: 1, wipe: null,
               effect: { name: 'scatter', family: 'mask', front: slp,
                         scope: ssc, params: eff.params || {}, phase: mmScatterPhase(role) } };
    }
```

(`p` is the clamped phase progress computed at the top of `mmTransitionState`; `role` is `'in'`/`'out'`. `front` carries the inverted-for-`out` local progress so the `mmScatter*` helpers get a clean 0→1.)

- [ ] **Step 4: Run, verify PASS (+ no regression)**

Run: `node --test tests/unit/js/test_scatter.js tests/unit/js/test_beerfill.js tests/unit/js/test_transitions.js tests/unit/js/transition-effects.test.js`
Expected: all PASS (the `_dur` 2nd arg stays ignored for non-beerfill/scatter effects).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_scatter.js
git commit -m "feat(transitions): mmScatterParticles + mmTransitionState scatter branch (mask, role->phase)"
```

---

### Task 5: `mmDrawScatter` canvas draw

**Files:**
- Modify: `js/transitions.js` (add `mmDrawScatter` + export)
- Test: `tests/unit/js/test_scatter.js` (append; inline recording ctx + fake sprite)

**Interfaces:**
- Consumes: `_mmMaskRegion` (existing), `mmScatterCover`, `mmScatterDist`, `mmScatterGiantAngle`, `mmScatterParticles` (Tasks 3-4).
- Produces: `mmDrawScatter(ctx, params, phase, p, GW, GH, quad, scope, seed, img, bg)`:
  - region `reg = _mmMaskRegion(scope, quad, GW, GH)`; center `(cx,cy)`; `maxR = hypot(reg.w/2, reg.h/2)`.
  - cover amount `c = mmScatterCover(phase, p)`; backing disc: `fillStyle = bg`, `arc(cx, cy, c*maxR, ...)`, fill (clean coverage; no-op when `c*maxR < 0.5`).
  - if `img` is loaded (`img && img.width`): stamp `count = params.count||40` copies via `mmScatterParticles(seed, count)` — each at distance `mmScatterDist(phase, p) * maxR * part.sp`, angle `part.ang`, size `spriteH = (reg.h*0.12)*(0.55 + 0.5*c)`, rotation `part.rot0 + p*part.rps*6`; then the giant at center, height `reg.h * 1.43 * c`, rotation `mmScatterGiantAngle(phase, p)`.
  - `drawImage`/`arc`+`fill` only. No-op stamps until `img` loaded (disc still draws → clean cover).

- [ ] **Step 1: Write failing test** — append to `tests/unit/js/test_scatter.js`:

```javascript
function recCtx() {
  return { rects: [], imgs: 0, arcs: 0, fills: 0, fillStyle: '#000',
    save(){}, restore(){}, translate(){}, rotate(){}, scale(){},
    beginPath(){}, arc(){ this.arcs++; }, fill(){ this.fills++; },
    fillRect(x,y,w,h){ this.rects.push({x,y,w,h}); },
    drawImage(){ this.imgs++; } };
}
const fakeImg = { width: 100, height: 120 };          // "loaded"
const noImg = { width: 0, height: 0 };                 // not yet decoded

test('mmDrawScatter: disc only when sprite not loaded', () => {
  const c = recCtx();
  g.mmDrawScatter(c, { count: 40 }, 'cover', 0.5, 300, 200, null, 'wall', 7, noImg, '#140d06');
  assert.equal(c.imgs, 0);          // no stamps
  assert.ok(c.arcs >= 1);           // backing disc drawn
});
test('mmDrawScatter: stamps count copies + giant when loaded', () => {
  const c = recCtx();
  g.mmDrawScatter(c, { count: 40 }, 'cover', 0.6, 300, 200, null, 'wall', 7, fakeImg, '#140d06');
  assert.equal(c.imgs, 41);         // 40 copies + 1 giant
});
test('mmDrawScatter: cover=0 draws nothing visible', () => {
  const c = recCtx();
  g.mmDrawScatter(c, { count: 40 }, 'reveal', 1, 300, 200, null, 'wall', 7, fakeImg, '#140d06');  // c=0
  assert.equal(c.arcs, 0);          // no disc at c=0
});
```

- [ ] **Step 2: Run, verify FAIL**

Run: `node --test tests/unit/js/test_scatter.js`
Expected: FAIL — `mmDrawScatter` undefined.

- [ ] **Step 3: Implement** — add to `js/transitions.js`:

```javascript
  // Draw the scatter cover: backing disc (clean coverage) + erupting sprite copies + giant center.
  // drawImage/arc only; no clip/composite. No-op stamps until img is decoded.
  function mmDrawScatter(ctx, params, phase, p, GW, GH, quad, scope, seed, img, bg) {
    var reg = _mmMaskRegion(scope, quad, GW, GH);
    var cx = reg.x + reg.w / 2, cy = reg.y + reg.h / 2;
    var maxR = Math.sqrt((reg.w / 2) * (reg.w / 2) + (reg.h / 2) * (reg.h / 2));
    var c = mmScatterCover(phase, p);
    // backing disc (item bg) — guarantees a clean, gap-free cover
    if (c * maxR >= 0.5) {
      ctx.fillStyle = bg || '#000000';
      ctx.beginPath(); ctx.arc(cx, cy, c * maxR, 0, 6.283185307); ctx.fill();
    }
    if (!img || !img.width) { return; }                 // sprite not decoded yet -> disc only
    var count = (params && params.count) || 40;
    var dist = mmScatterDist(phase, p) * maxR;
    var parts = mmScatterParticles(seed >>> 0, count), i, pt, d, sz, sc;
    var baseH = reg.h * 0.12;
    for (i = 0; i < parts.length; i++) {
      pt = parts[i]; d = dist * pt.sp; sz = baseH * (0.55 + 0.5 * c); sc = sz / img.height;
      ctx.save();
      ctx.translate(cx + Math.cos(pt.ang) * d, cy + Math.sin(pt.ang) * d);
      ctx.rotate(pt.rot0 + p * pt.rps * 6); ctx.scale(sc, sc);
      ctx.drawImage(img, -img.width / 2, -img.height / 2);
      ctx.restore();
    }
    // giant center
    var gh = reg.h * 1.43 * c;
    if (gh > 2) {
      var gsc = gh / img.height;
      ctx.save(); ctx.translate(cx, cy); ctx.rotate(mmScatterGiantAngle(phase, p)); ctx.scale(gsc, gsc);
      ctx.drawImage(img, -img.width / 2, -img.height / 2); ctx.restore();
    }
  }
```

Export it (`root.mmDrawScatter = mmDrawScatter;`).

- [ ] **Step 4: Run, verify PASS**

Run: `node --test tests/unit/js/test_scatter.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_scatter.js
git commit -m "feat(transitions): mmDrawScatter (backing disc + erupting copies + giant)"
```

---

### Task 6: Wire `scatter` into `index.html` (sprite cache + cover gate + mesh/overlay branches)

**Files:**
- Modify: `index.html`

**Interfaces:**
- Consumes: `mmDrawScatter`, `mmScatterSpriteUrl` (Tasks 3, 5); the `mmTransitionState` scatter descriptor `{name:'scatter', family:'mask', front, scope, params, phase}` (Task 4).
- Produces: a sprite `Image` cache `mmSprite(url)`; `scatter` drawn on mesh SCRIPT items (in-canvas) and media/element items (overlay). No new exports.

This is glue for already-tested helpers; verification is the on-wall sign-off (Task 7). No unit test for this task.

- [ ] **Step 1: Read the real code at the touch-points.** Open `index.html` and locate (grep): the `_cvEff` gate (`function _cvEff` near line 938), the mesh in-canvas mask branch (the `stc.effect.family === 'mask'` block with the `beerfill` case, ~lines 651-665), and the `applyTransitionNow` overlay mask branch (the `st.effect.family === 'mask'` block with the `beerfill` case, ~lines 1039-1060). Reuse the EXACT surrounding variable names there (`ctx`, `it.meshGlobal`, `it.meshQuad`, `playback.seed`, `cmx`, `GWm`, `GHm`, `quad`, `item.backgroundColor`).

- [ ] **Step 2: Add the sprite cache.** Near the top of the `<script>` (by other helpers), add (ES5):

```javascript
		var _mmSpriteCache = {};
		function mmSprite(url) {
			if (!_mmSpriteCache[url]) { var im = new Image(); im.src = url; _mmSpriteCache[url] = im; }
			return _mmSpriteCache[url];
		}
```

- [ ] **Step 3: Extend the cover gate** (`_cvEff`, ~line 938) to include `'scatter'` so media items get an overlay cover:

```javascript
		var _cvEff = function (x) { return x && (x.name === 'wipe' || x.name === 'iris' || x.name === 'dissolve' || x.name === 'beerfill' || x.name === 'scatter'); };
```

- [ ] **Step 4: Mesh in-canvas branch.** In `runScriptLoop`'s `stc.effect.family === 'mask'` block, add a `scatter` case beside the `beerfill` case (before the generic `mmDrawMaskInCanvas` fallback):

```javascript
								} else if (stc.effect.name === 'scatter' && typeof mmDrawScatter === 'function') {
									mmDrawScatter(ctx, stc.effect.params, stc.effect.phase, stc.effect.front,
										it.meshGlobal[0], it.meshGlobal[1], it.meshQuad, stc.effect.scope,
										playback.seed | 0, mmSprite(mmScatterSpriteUrl(stc.effect.params && stc.effect.params.sprite)),
										it.backgroundColor || '#000000');
```

(Insert as a peer `else if` immediately after the beerfill `else if` and before the existing `} else if (typeof mmDrawMaskInCanvas === 'function') {`.)

- [ ] **Step 5: Overlay branch.** In `applyTransitionNow`'s mask block, add a `scatter` case beside `beerfill` (before the `mmDrawMaskOverlay` fallback), matching the real variable names found in Step 1:

```javascript
				} else if (st.effect.name === 'scatter' && typeof mmDrawScatter === 'function') {
					mmDrawScatter(cmx, st.effect.params, st.effect.phase, st.effect.front,
						GWm, GHm, quad, st.effect.scope, playback.seed | 0,
						mmSprite(mmScatterSpriteUrl(st.effect.params && st.effect.params.sprite)),
						item.backgroundColor || '#000000');
```

- [ ] **Step 6: ES5 + parse sanity check**

Run: `node -e "require('fs').readFileSync('index.html','utf8'); console.log('read ok')"`
Then grep your added lines to confirm only `var`/`function` (no `let`/`const`/arrow/template-literals).
Expected: `read ok`; edits are ES5.

- [ ] **Step 7: Commit**

```bash
git add index.html
git commit -m "feat(transitions): wire scatter into mesh in-canvas + overlay paths (sprite cache + cover gate)"
```

---

### Task 7: Seed hop sprite + Scatter Demo + on-wall sign-off

**Files:**
- Create: `tools/_make_scatter_demo.py` (throwaway sender)
- Asset: ensure `media/server/images/hop.png` exists (transparent PNG)

**Interfaces:**
- Consumes: `SAVE_PLAYLIST` SockJS handler; item schema `{id, file:'plasma', playmode:'SCRIPT', scriptSpan:'mesh', duration, backgroundColor, startEffect, endEffect}` with effect `{name:'scatter', params:{sprite, scope, count, fillMs, drainMs, audioFade}}`.
- Produces: a "Scatter Demo" playlist (two plasma mesh items handing off via `scatter`, sprite=hop, scope=wall).

- [ ] **Step 1: Seed the hop sprite into the media library.** A transparent `hop.png` already exists at `media/server/sprites/hop.png` (or `media/server/images/hop.png`). Ensure it is in the library dir `media/server/images/`:

```bash
mkdir -p media/server/images
[ -f media/server/images/hop.png ] || cp media/server/sprites/hop.png media/server/images/hop.png
ls -la media/server/images/hop.png
```

Expected: the file exists (a transparent PNG ~27 KB).

- [ ] **Step 2: Create the demo sender** — `tools/_make_scatter_demo.py`:

```python
"""Create a 'Scatter Demo' playlist: two plasma mesh items handing off via the
scatter transition (sprite=hop, wall). Run with the server up."""
import asyncio, json, aiohttp
HOST = "127.0.0.1:3000"

def sc():
    return {"name": "scatter",
            "params": {"sprite": "hop", "scope": "wall", "count": 40,
                       "fillMs": 2500, "drainMs": 2500, "audioFade": True}}

ITEMS = [
    {"id": "sc-a", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 8, "backgroundColor": "#000000", "startEffect": None, "endEffect": sc()},
    {"id": "sc-b", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 8, "backgroundColor": "#000000", "startEffect": sc(), "endEffect": None},
]

async def main():
    url = "http://%s/sockjs/000/claudecmd/websocket" % HOST
    p = {"SRC": "claude-admin", "DEST": "SRV", "REQUEST": "SAVE_PLAYLIST",
         "PAYLOAD": {"name": "Scatter Demo", "items": ITEMS, "loop": True}}
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url) as ws:
            await ws.send_str(json.dumps([json.dumps(p)]))
            print("sent Scatter Demo")
            try:
                for _ in range(6):
                    m = await asyncio.wait_for(ws.receive(), timeout=2.0)
                    if "SAVE_PLAYLIST" in str(m.data): print("recv:", str(m.data)[:140])
            except asyncio.TimeoutError:
                pass

if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: (Deploy-time, operator) create the playlist + verify**

With the server running on this branch: `python tools/_make_scatter_demo.py`; then `curl -s http://localhost:3000/api/playlists` and confirm a `Scatter Demo` entry with 2 items whose effects are named `scatter`.
Expected: `recv: ...SAVE_PLAYLIST...SUCCESS`; playlist present.

- [ ] **Step 4: On-wall iPad-1 sign-off (manual, requires deploy + operator)**

Deploy (server restart on this branch + fleet reload so clients pick up the new `index.html`/`transitions.js`). Assign **Scatter Demo** to the calibrated "OEB Sign 1" group and Play. Verify on the sign:
- Item A→B: giant hop grows at the sign center + 40 hops erupt outward across the wall, screen covers cleanly, then reveals the next plasma — **smooth, no pause** at the full-size hold.
- Try `scope:screen` (edit the sender) → each screen bursts independently.
- Confirm acceptable performance at `count: 40`; if a screen struggles, lower `count`.

Expected: reads as a hop explosion from the sign center, clean cover + reveal.

- [ ] **Step 5: Commit the demo tool**

```bash
git add tools/_make_scatter_demo.py
git commit -m "chore(transitions): scatter demo playlist sender"
```

(The `media/server/images/hop.png` asset is gitignored runtime media — not committed; it lives on the server.)

---

## Notes for the implementer

- `playback.seed` is the shared per-run seed already threaded into SCRIPT items; passing it to `mmScatterParticles` makes every screen compute the identical burst so the wall reads as one coherent explosion. Do not substitute a per-screen/time seed.
- Wall coordination needs no special code: mesh SCRIPT content (and the scatter drawn on top in `runScriptLoop`) renders in global wall coords and is warped per-screen by `mmMeshTransform`, so a burst from the global center automatically spans the sign.
- The sprite `Image` is created once per URL (`mmSprite` cache) and decoded by the browser; `mmDrawScatter` no-ops its stamps until `img.width` is non-zero, so the first transition still covers cleanly (disc) even if the PNG is a frame late.
- `count` is the iPad-1 performance lever (≈`count`+1 `drawImage`/frame). Default 40; verify on-wall and lower if needed.
