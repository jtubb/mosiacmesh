# Wheat Part Transition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `wheatpart` mask-family transition — a center-seam wheat curtain that parts open (reveal) / closes (cover), with procedural full-height stalks that lean & slide outward over opaque straw backdrop panels.

**Architecture:** Pure, node-tested helpers in `js/transitions.js` (`mmWheatOpenness`, `mmWheatPartGeom`, `mmWheatField`, `mmWheatColor`) carry all the math; a thin `mmDrawWheat` draw glue consumes them; one new branch in `mmTransitionState` produces the effect descriptor and one new branch in `index.html` `runScriptLoop`'s mask-family block calls the draw glue. A catalog entry in `effects.py` makes it appear in the editor. Mesh-only; render-token neutral (audio-fade only).

**Tech Stack:** ES5 client JS (iPad-1 / iOS 5.1 / Safari 5.1), 2D canvas, Python `aiohttp` server, `node --test` for JS units, `pytest` for Python.

## Global Constraints

- **Display-client JS is ES5 ONLY** (`js/transitions.js`, `index.html` inline `<script>`): NO `let`/`const`, arrow functions, template literals, `class`, `Promise`, `fetch`, default params, destructuring, spread. Must run on 1st-gen iPad / iOS 5.1 / Safari 5.1. (Test files under `tests/unit/js/` are node-only and MAY use modern JS.)
- **No `clip()`, no `matrix3d`, no 3D, no WebGL, no canvas filters** in any wheatpart path. Canvas primitives only: `fillRect`, `createLinearGradient`, `save`/`restore`, `translate`, `rotate`, `beginPath`/`moveTo`/`lineTo`/`quadraticCurveTo`, `arc`, `fill`/`stroke`.
- **Render-token neutrality:** `tint`/`density`/`scope` must NOT change the render token; only the audio-fade signature (role, duration when `audioFade` on) may. Enforced by a guard test.
- **Pure-helper pattern:** decision math lives in node-tested helpers in `js/transitions.js`; `index.html` is thin glue.
- **Spelling:** the project is `mosiacmesh` (transposed "ai"). Don't "correct" it.
- **Commit trailer (this branch's convention):** end every commit message with
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- **Tests run via runners**, never bare `pytest`: `python pytest_runner.py --unit` and `python pytest_runner.py --js` (or `node --test tests/unit/js/<file>.js` for one JS file). A single Python test: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -v`.
- **Mask-family `front` convention:** `mmTransitionState` passes the draw helper `front` = LOCAL phase progress rising `0→1` for BOTH roles (out-role `p` counts `1→0`, so it is inverted there). The helper resolves cover-vs-reveal from `phase`. (This matches scatter/beerfill/kegroll — NOT frostcreep, which pre-resolves coverage in `mmTransitionState`.)

---

### Task 1: `effects.py` catalog entry + Python tests

**Files:**
- Modify: `effects.py` (append a new `@register class WheatPartEffect(Effect)` after `CoasterFlipEffect`, which ends at line 268)
- Test: `tests/unit/test_effects.py` (add `test_wheatpart_*`), `tests/unit/test_mosaic.py` (add token guard)

**Interfaces:**
- Produces: effect `name="wheatpart"`, params `tint` (choice golden/amber/pale, default "golden"), `density` (number, default 70, min 10, max 200), `scope` (choice screen/wall, default "wall"), `duration` (number, default 2200, min 0), `audioFade` (boolean, default True). `video_filters(role, params, ctx)` returns `([], _afade(role, params, ctx))`.

- [ ] **Step 1: Write the failing Python tests**

In `tests/unit/test_effects.py`, add (place beside the existing `test_coasterflip_*` tests):

```python
def test_wheatpart_in_catalog_with_defaults():
    import effects
    cat = {e["name"]: e for e in effects.effect_catalog()}
    assert "wheatpart" in cat
    params = {p["key"]: p for p in cat["wheatpart"]["params"]}
    assert params["tint"]["default"] == "golden"
    assert params["tint"]["choices"] == ["golden", "amber", "pale"]
    assert params["density"]["default"] == 70
    assert params["density"]["min"] == 10 and params["density"]["max"] == 200
    assert params["scope"]["default"] == "wall"
    assert params["duration"]["default"] == 2200
    assert params["audioFade"]["default"] is True


def test_wheatpart_video_filters_audio_only_role_aware():
    import effects
    eff = effects.get_effect("wheatpart")
    p = eff.resolve({"audioFade": True, "duration": 2000})
    ctx = {"duration_ms": 8000}
    vstart, astart = eff.video_filters("start", p, ctx)
    vend, aend = eff.video_filters("end", p, ctx)
    assert vstart == [] and vend == []                      # no baked video
    assert astart == ["afade=t=in:st=0:d=2"]
    assert aend == ["afade=t=out:st=6:d=2"]


def test_wheatpart_audiofade_off_bakes_nothing():
    import effects
    eff = effects.get_effect("wheatpart")
    p = eff.resolve({"audioFade": False, "duration": 2000})
    assert eff.video_filters("start", p, {"duration_ms": 8000}) == ([], [])
    assert eff.video_filters("end", p, {"duration_ms": 8000}) == ([], [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -k wheatpart -v`
Expected: FAIL (`get_effect("wheatpart")` returns None / "wheatpart" not in catalog).

- [ ] **Step 3: Implement the effect**

In `effects.py`, after the `CoasterFlipEffect` class (after line 268), append:

```python
@register
class WheatPartEffect(Effect):
    name = "wheatpart"
    label = "Wheat Part"
    # Single `duration`: a wheatpart instance only covers (endEffect) or reveals
    # (startEffect), never both.
    params = [ParamSpec("tint", "choice", "golden", choices=["golden", "amber", "pale"]),
              ParamSpec("density", "number", 70, minimum=10, maximum=200),
              ParamSpec("scope", "choice", "wall", choices=["screen", "wall"]),
              ParamSpec("duration", "number", 2200, minimum=0),
              ParamSpec("audioFade", "boolean", True)]

    def video_filters(self, role, params, ctx):
        return ([], _afade(role, params, ctx))     # visual is client-side; single duration
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -k wheatpart -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Add the render-token guard**

In `tests/unit/test_mosaic.py`, find `test_token_unchanged_by_coasterflip_visual_param_change` and add a sibling beside it (copy its structure, swapping the effect name + varied params):

```python
def test_token_unchanged_by_wheatpart_visual_param_change():
    # Varying wheatpart's purely-visual params (tint, density) must NOT change the
    # render token: the visual is client-side, only the audio-fade signature enters.
    import mosaicmesh.render as render
    base = {"name": "wheatpart",
            "params": {"tint": "golden", "density": 70, "scope": "wall",
                       "duration": 2200, "audioFade": True}}
    varied = {"name": "wheatpart",
              "params": {"tint": "amber", "density": 200, "scope": "wall",
                         "duration": 2200, "audioFade": True}}
    items_a = [{"id": "x", "file": "plasma", "playmode": "SCRIPT",
                "duration": 8, "startEffect": None, "endEffect": base}]
    items_b = [{"id": "x", "file": "plasma", "playmode": "SCRIPT",
                "duration": 8, "startEffect": None, "endEffect": varied}]
    assert render.render_token(items_a, "G") == render.render_token(items_b, "G")
```

NOTE for the implementer: match the EXACT call signature `render.render_token(...)` and item shape used by the adjacent `..._coasterflip_...` test in the same file — if that test builds items or calls the token differently, mirror it precisely rather than the sketch above.

- [ ] **Step 6: Run the guard + full unit suite**

Run: `python -m pytest tests/unit/test_mosaic.py -c tests/pytest.ini -k wheatpart -v` → PASS
Run: `python pytest_runner.py --unit` → all pass (expect 614 passed, 2 skipped, up from 611).

- [ ] **Step 7: Commit**

```bash
git add effects.py tests/unit/test_effects.py tests/unit/test_mosaic.py
git commit -m "feat(effects): wheatpart catalog entry + token guard

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `mmWheatOpenness` + `mmWheatPartGeom` pure helpers + node tests

**Files:**
- Modify: `js/transitions.js` (add two functions near the other mask helpers, e.g. just before `mmScatterParticles`; add two `root.` exports in the exports block around line 1044)
- Test: `tests/unit/js/test_wheatpart.js` (create)

**Interfaces:**
- Produces:
  - `mmWheatOpenness(phase, front)` → number `0..1`: `phase==='reveal' ? clamp(front) : 1-clamp(front)`.
  - `mmWheatPartGeom(openness, GW, GH)` → `{ cx, g, leftEdge, rightEdge, slide, lean }`. `cx=GW/2`, `g=clamp(openness)*cx`, `leftEdge=cx-g`, `rightEdge=cx+g`, `slide=g`, `lean=clamp(openness)*0.5`.

- [ ] **Step 1: Write the failing node test**

Create `tests/unit/js/test_wheatpart.js`:

```js
'use strict';
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

// Load transitions.js into a fake global (same loader pattern as test_coasterflip.js)
const g = {};
const fs = require('node:fs');
const src = fs.readFileSync(path.join(__dirname, '../../../js/transitions.js'), 'utf8');
new Function('window', 'self', 'globalThis', src)(g, g, g);

const C = (a, b) => Math.abs(a - b) < 1e-9;

test('mmWheatOpenness: reveal passes through, cover inverts, clamps', () => {
  assert.ok(C(g.mmWheatOpenness('reveal', 0), 0));
  assert.ok(C(g.mmWheatOpenness('reveal', 1), 1));
  assert.ok(C(g.mmWheatOpenness('reveal', 0.3), 0.3));
  assert.ok(C(g.mmWheatOpenness('cover', 0), 1));   // cover starts open
  assert.ok(C(g.mmWheatOpenness('cover', 1), 0));   // cover ends closed
  assert.ok(C(g.mmWheatOpenness('cover', 0.3), 0.7));
  assert.ok(C(g.mmWheatOpenness('reveal', -1), 0)); // clamp
  assert.ok(C(g.mmWheatOpenness('reveal', 2), 1));
});

test('mmWheatOpenness: both roles reach closed (0) at the handoff', () => {
  // endEffect (cover) ends at front=1 -> openness 0; startEffect (reveal) starts at
  // front=0 -> openness 0. Both are full-wheat at the seam -> continuous.
  assert.ok(C(g.mmWheatOpenness('cover', 1), 0));
  assert.ok(C(g.mmWheatOpenness('reveal', 0), 0));
});

test('mmWheatPartGeom: endpoints, symmetry, monotonic gap/lean', () => {
  const GW = 800, GH = 200;
  const closed = g.mmWheatPartGeom(0, GW, GH);
  assert.ok(C(closed.g, 0));
  assert.ok(C(closed.leftEdge, 400) && C(closed.rightEdge, 400)); // walls meet at cx
  assert.ok(C(closed.lean, 0));
  const open = g.mmWheatPartGeom(1, GW, GH);
  assert.ok(C(open.g, 400));
  assert.ok(C(open.leftEdge, 0) && C(open.rightEdge, 800));        // cleared to edges
  assert.ok(C(open.lean, 0.5));
  const half = g.mmWheatPartGeom(0.5, GW, GH);
  assert.ok(half.g > closed.g && half.g < open.g);                // monotonic
  assert.ok(half.lean > closed.lean && half.lean < open.lean);
  assert.ok(C(half.cx - half.leftEdge, half.rightEdge - half.cx)); // symmetric about cx
});

test('mmWheatPartGeom: clamps out-of-range openness', () => {
  const o = g.mmWheatPartGeom(2, 800, 200);
  assert.ok(C(o.g, 400) && C(o.lean, 0.5));
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/test_wheatpart.js`
Expected: FAIL (`mmWheatOpenness is not a function`).

- [ ] **Step 3: Implement the two helpers**

In `js/transitions.js`, add (just before `function mmScatterParticles`):

```js
  // --- Wheat part (mask family): a center-seam wheat curtain. Pure geometry +
  // role->openness mapping; the draw glue (mmDrawWheat) consumes these. ---
  var _WHEAT_MAX_LEAN = 0.5;                  // outward stalk lean at full-open (~29 deg)

  // Role -> openness (0 closed/full-wheat .. 1 open/content-visible). Mirrors
  // mmScatterCover/mmBeerLevel: front is LOCAL phase progress rising 0->1 for both
  // roles; reveal passes it through, cover inverts it.
  function mmWheatOpenness(phase, front) {
    var f = front < 0 ? 0 : (front > 1 ? 1 : front);
    return phase === 'reveal' ? f : (1 - f);
  }

  // Parting geometry at a given openness. Single vertical seam at wall center cx.
  // The two wheat walls' inner edges sit at cx-g / cx+g; each has slid outward by g
  // and its stalks lean by `lean` toward their outer edge.
  function mmWheatPartGeom(openness, GW, GH) {
    var o = openness < 0 ? 0 : (openness > 1 ? 1 : openness);
    var cx = GW / 2, g = o * cx;
    return { cx: cx, g: g, leftEdge: cx - g, rightEdge: cx + g,
             slide: g, lean: o * _WHEAT_MAX_LEAN };
  }
```

In the exports block (near line 1044, after `root.mmSpriteFit = mmSpriteFit;`), add:

```js
  root.mmWheatOpenness = mmWheatOpenness;
  root.mmWheatPartGeom = mmWheatPartGeom;
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/test_wheatpart.js`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_wheatpart.js
git commit -m "feat(transitions): wheatpart openness + part-geometry helpers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `mmWheatField` + `mmWheatColor` pure helpers + node tests

**Files:**
- Modify: `js/transitions.js` (add two functions beside the Task 2 helpers; add two exports)
- Test: `tests/unit/js/test_wheatpart.js` (append)

**Interfaces:**
- Consumes: `_mmLcg(seed)` (existing private LCG: `var rnd = _mmLcg(seed >>> 0)` → `rnd()` yields `0..1`).
- Produces:
  - `mmWheatField(seed, density, GW, GH)` → array of `density` objects `{ bx, h, sway, headR, side }`. `bx ∈ [0,GW)`, `h ∈ [0.6,1.0)`, `sway ∈ [0,2π)`, `headR ∈ [0.006,0.012)` (fraction of GH), `side = bx < GW/2 ? 'left' : 'right'`. Deterministic in `(seed,density,GW,GH)`.
  - `mmWheatColor(tint)` → `{ backdrop, base, stalk, head }` hex strings; unknown tint → golden.

- [ ] **Step 1: Write the failing node test (append to `test_wheatpart.js`)**

```js
test('mmWheatField: deterministic, sized, in-bounds, side-split about cx', () => {
  const GW = 800, GH = 200;
  const a = g.mmWheatField(12345, 70, GW, GH);
  const b = g.mmWheatField(12345, 70, GW, GH);
  assert.equal(a.length, 70);
  assert.deepEqual(a, b);                                   // same seed -> identical
  const c = g.mmWheatField(99, 70, GW, GH);
  assert.notDeepEqual(a, c);                                // different seed -> different
  for (const s of a) {
    assert.ok(s.bx >= 0 && s.bx < GW, 'bx in [0,GW)');
    assert.ok(s.h >= 0.6 && s.h < 1.0, 'h in [0.6,1.0)');
    assert.ok(s.sway >= 0 && s.sway < 6.2832, 'sway in [0,2pi)');
    assert.equal(s.side, s.bx < GW / 2 ? 'left' : 'right');
  }
});

test('mmWheatColor: known tints return 4 keys; unknown -> golden', () => {
  for (const t of ['golden', 'amber', 'pale']) {
    const pal = g.mmWheatColor(t);
    for (const k of ['backdrop', 'base', 'stalk', 'head']) {
      assert.equal(typeof pal[k], 'string');
    }
  }
  assert.deepEqual(g.mmWheatColor('nope'), g.mmWheatColor('golden'));
  assert.deepEqual(g.mmWheatColor(undefined), g.mmWheatColor('golden'));
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/test_wheatpart.js`
Expected: FAIL (`mmWheatField is not a function`).

- [ ] **Step 3: Implement the two helpers**

In `js/transitions.js`, add beside the Task 2 helpers:

```js
  var _WHEAT = {
    golden: { backdrop: '#b8901f', base: '#8a6a14', stalk: '#d9b23a', head: '#f0d169' },
    amber:  { backdrop: '#9c6f1a', base: '#6f4d10', stalk: '#c8912e', head: '#e6b85a' },
    pale:   { backdrop: '#d8c478', base: '#b6a256', stalk: '#e8dca0', head: '#f7efc8' }
  };
  function mmWheatColor(tint) { return _WHEAT[tint] || _WHEAT.golden; }

  // Deterministic stalk field across the whole wall (seeded -> identical on every
  // screen, like mmScatterParticles/mmBeerBubbles). h/headR are FRACTIONS so the
  // draw scales them to GH and they never warp to sub-pixel specks.
  function mmWheatField(seed, density, GW, GH) {
    var n = density > 0 ? (density | 0) : 1;
    var rnd = _mmLcg(seed >>> 0), arr = [], i, bx;
    var cx = GW / 2;
    for (i = 0; i < n; i++) {
      bx = rnd() * GW;
      arr.push({ bx: bx, h: 0.6 + rnd() * 0.4, sway: rnd() * 6.283185307,
                 headR: 0.006 + rnd() * 0.006, side: bx < cx ? 'left' : 'right' });
    }
    return arr;
  }
```

In the exports block, add beside the Task 2 exports:

```js
  root.mmWheatField = mmWheatField;
  root.mmWheatColor = mmWheatColor;
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/test_wheatpart.js`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_wheatpart.js
git commit -m "feat(transitions): wheatpart seeded field + palette helpers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `mmTransitionState` wheatpart branch + node test

**Files:**
- Modify: `js/transitions.js` (add a branch inside `mmTransitionState`, right after the `frostcreep` branch which ends just before `if (eff.name === 'coasterflip')` near line 156)
- Test: `tests/unit/js/test_wheatpart.js` (append)

**Interfaces:**
- Consumes: the `mmTransitionState(start, end, offsetMs, durationMs, rect, quad)` machinery — inside each branch `eff`, `role` (`'out'`/`'in'`), and `p` (raw progress) are in scope.
- Produces: for `eff.name === 'wheatpart'`, returns `{ role, opacity:1, wipe:null, effect: { name:'wheatpart', family:'mask', front:<localProgress>, scope, params, phase } }` where `front = (role==='out') ? (1-p) : p` and `phase = (role==='out') ? 'cover' : 'reveal'`.

- [ ] **Step 1: Write the failing node test (append to `test_wheatpart.js`)**

```js
test('mmTransitionState: wheatpart is a mask effect with phase + rising local front', () => {
  // endEffect on item A: an 'out' role. Use an offset late in an 8000ms item so the
  // end window is active. duration 2000ms.
  const endEff = { name: 'wheatpart', params: { scope: 'wall' } };
  // Sample two points inside the end window to confirm front rises 0->1 (local progress).
  const near = g.mmTransitionState(null, endEff, 6200, 8000, null, null);  // just into window
  const late = g.mmTransitionState(null, endEff, 7800, 8000, null, null);  // near end
  assert.equal(near.effect.name, 'wheatpart');
  assert.equal(near.effect.family, 'mask');
  assert.equal(near.effect.phase, 'cover');                // out role
  assert.ok(near.effect.front >= 0 && near.effect.front <= 1);
  assert.ok(late.effect.front > near.effect.front, 'local front rises across the cover window');
  assert.equal(near.wipe, null);

  // startEffect on item B: an 'in' role.
  const startEff = { name: 'wheatpart', params: { scope: 'wall' } };
  const s = g.mmTransitionState(startEff, null, 200, 8000, null, null);
  assert.equal(s.effect.phase, 'reveal');                  // in role
  assert.ok(s.effect.front >= 0 && s.effect.front <= 1);
});
```

NOTE for the implementer: the exact `offsetMs`/`durationMs` that land inside the start/end windows are determined by `mmTransitionState`'s internal windowing. If the chosen samples don't activate the effect (e.g. `effect` is null), read how `test_coasterflip.js` / the scatter tests pick offsets for an active window and mirror those values. The behavioral asserts (family, phase, front rising) are what matter.

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/test_wheatpart.js`
Expected: FAIL (`effect` is null or name !== 'wheatpart').

- [ ] **Step 3: Implement the branch**

In `js/transitions.js`, inside `mmTransitionState`, add immediately after the `frostcreep` branch's closing `}` (just before `if (eff.name === 'coasterflip')`):

```js
    if (eff.name === 'wheatpart') {
      var wpsc = (eff.params && eff.params.scope) || 'wall';
      // front = LOCAL phase progress 0->1 (like scatter): `p` counts DOWN on the
      // 'out' window (1->0), so invert there; 'in' already counts up. mmDrawWheat
      // maps front->openness via phase (mmWheatOpenness).
      var wplp = (role === 'out') ? (1 - p) : p;
      return { role: role, opacity: 1, wipe: null,
               effect: { name: 'wheatpart', family: 'mask', front: wplp,
                         scope: wpsc, params: eff.params || {}, phase: mmWheatPhase(role) } };
    }
```

And add the tiny phase helper beside the Task 2/3 wheat helpers:

```js
  function mmWheatPhase(role) { return role === 'out' ? 'cover' : 'reveal'; }
```

Add its export in the exports block:

```js
  root.mmWheatPhase = mmWheatPhase;
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/test_wheatpart.js`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_wheatpart.js
git commit -m "feat(transitions): mmTransitionState wheatpart mask branch

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `mmDrawWheat` draw glue + node smoke test

**Files:**
- Modify: `js/transitions.js` (add `mmDrawWheat` beside `mmDrawScatter`/`mmDrawFrost`; add export)
- Test: `tests/unit/js/test_wheatpart.js` (append a smoke test with a stub ctx)

**Interfaces:**
- Consumes: `mmWheatOpenness`, `mmWheatPartGeom`, `mmWheatField`, `mmWheatColor` (Tasks 2-3), `_mmMaskRegion(scope, quad, GW, GH)` (existing → `{x,y,w,h}`).
- Produces: `mmDrawWheat(ctx, params, phase, front, GW, GH, quad, scope, seed, now)` → void. Fills two opaque straw backdrop rects then draws leaning, swaying stalks on top; culls fully-off-wall stalks.

- [ ] **Step 1: Write the failing smoke test (append to `test_wheatpart.js`)**

A stub 2D context records calls; the test asserts the helper runs, fills backdrops when closed, and draws nothing when fully open.

```js
function stubCtx() {
  const calls = { fillRect: 0, save: 0, restore: 0, beginPath: 0, fill: 0, arc: 0, gradients: 0 };
  return {
    calls,
    fillStyle: '#000', globalAlpha: 1,
    save() { calls.save++; }, restore() { calls.restore++; },
    translate() {}, rotate() {}, scale() {},
    beginPath() { calls.beginPath++; }, moveTo() {}, lineTo() {}, quadraticCurveTo() {},
    arc() { calls.arc++; }, closePath() {},
    fill() { calls.fill++; }, stroke() {}, fillRect() { calls.fillRect++; },
    createLinearGradient() { calls.gradients++; return { addColorStop() {} }; }
  };
}

test('mmDrawWheat: closed (openness 0) fills backdrops; open (openness 1) draws ~nothing', () => {
  // cover phase, front=1 -> openness 0 (fully closed): two backdrop rects expected.
  const closed = stubCtx();
  g.mmDrawWheat(closed, { tint: 'golden', density: 40 }, 'cover', 1, 800, 200, null, 'wall', 7, 0);
  assert.ok(closed.calls.fillRect >= 2, 'closed wheat fills the two backdrop walls');
  assert.ok(closed.calls.save >= 1 && closed.calls.restore === closed.calls.save, 'balanced save/restore');

  // reveal phase, front=1 -> openness 1 (fully open): walls cleared, ~no backdrop.
  const open = stubCtx();
  g.mmDrawWheat(open, { tint: 'golden', density: 40 }, 'reveal', 1, 800, 200, null, 'wall', 7, 0);
  assert.ok(open.calls.fillRect <= closed.calls.fillRect, 'open wheat fills less/none vs closed');
});

test('mmDrawWheat: never throws on degenerate inputs', () => {
  const c = stubCtx();
  assert.doesNotThrow(() => g.mmDrawWheat(c, {}, 'cover', 0.5, 800, 200, null, 'wall', 0, 123));
  assert.doesNotThrow(() => g.mmDrawWheat(c, { density: 0 }, 'reveal', 0.5, 800, 200, null, 'wall', 0, 0));
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/test_wheatpart.js`
Expected: FAIL (`mmDrawWheat is not a function`).

- [ ] **Step 3: Implement `mmDrawWheat`**

In `js/transitions.js`, add beside `mmDrawScatter`/`mmDrawFrost`:

```js
  // Draw the wheat curtain covering the two outer walls; the center gap (content)
  // grows as openness rises. Opaque straw backdrop rects guarantee the cover;
  // leaning, swaying procedural stalks sit on top. Global coords (warped by the
  // mesh affine like the other mask draws). ctx primitives only -- no clip.
  function mmDrawWheat(ctx, params, phase, front, GW, GH, quad, scope, seed, now) {
    var o = mmWheatOpenness(phase, front);
    var geom = mmWheatPartGeom(o, GW, GH);
    var pal = mmWheatColor(params && params.tint);
    var reg = _mmMaskRegion(scope, quad, GW, GH);
    // In 'screen' scope the seam is the region center; in 'wall' it's GW/2. Recompute
    // the local edges within the region span.
    var cx = reg.x + reg.w / 2, g = o * (reg.w / 2);
    var leftEdge = cx - g, rightEdge = cx + g, top = reg.y, bot = reg.y + reg.h;

    // backdrop walls (opaque) -- left [reg.x, leftEdge], right [rightEdge, reg.x+reg.w]
    var grL = ctx.createLinearGradient(0, top, 0, bot);
    grL.addColorStop(0, pal.base); grL.addColorStop(1, pal.backdrop);
    ctx.fillStyle = grL;
    if (leftEdge > reg.x) { ctx.fillRect(reg.x, top, leftEdge - reg.x, reg.h); }
    if (rightEdge < reg.x + reg.w) { ctx.fillRect(rightEdge, top, (reg.x + reg.w) - rightEdge, reg.h); }

    // stalks: rooted at the bottom of each wall, leaning toward the outer edge,
    // sliding outward with the wall, swaying with `now`.
    var field = mmWheatField(seed, (params && params.density) || 70, reg.w, reg.h);
    var stalkW = reg.h * 0.012, ts = (now || 0) * 0.001, i, s, baseX, leanDir, ang;
    var headRpx, tipX, tipY, hY;
    for (i = 0; i < field.length; i++) {
      s = field[i];
      // s.bx is in [0,reg.w); map to region x, then slide outward with its wall
      if (s.side === 'left') { baseX = reg.x + s.bx - geom.slide; leanDir = -1; }
      else { baseX = reg.x + s.bx + geom.slide; leanDir = 1; }
      // cull stalks whose base has slid off its visible wall
      if (s.side === 'left' && (baseX < reg.x || baseX > leftEdge)) { continue; }
      if (s.side === 'right' && (baseX > reg.x + reg.w || baseX < rightEdge)) { continue; }
      ang = leanDir * geom.lean + Math.sin(ts * 1.6 + s.sway) * 0.05;   // lean + gentle sway
      hY = s.h * reg.h;                                                 // stalk height (px)
      ctx.save();
      ctx.translate(baseX, bot);
      ctx.rotate(ang);
      // tapered stalk: a thin triangle base->tip
      ctx.fillStyle = pal.stalk;
      ctx.beginPath();
      ctx.moveTo(-stalkW / 2, 0);
      ctx.lineTo(stalkW / 2, 0);
      ctx.lineTo(0, -hY);
      ctx.closePath();
      ctx.fill();
      // grain head: an ellipse at the tip (arc + scale, no ctx.ellipse dependency)
      headRpx = s.headR * reg.h;
      ctx.fillStyle = pal.head;
      ctx.save();
      ctx.translate(0, -hY);
      ctx.scale(0.6, 1.6);                       // squash into a wheat-head oval
      ctx.beginPath(); ctx.arc(0, 0, headRpx, 0, 6.283185307); ctx.fill();
      ctx.restore();
      ctx.restore();
    }
  }
```

In the exports block, add:

```js
  root.mmDrawWheat = mmDrawWheat;
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/test_wheatpart.js`
Expected: PASS (9 tests).

- [ ] **Step 5: Run the full JS suite (no regressions)**

Run: `python pytest_runner.py --js`
Expected: all pass (expect ~390 pass, up from 381).

- [ ] **Step 6: Commit**

```bash
git add js/transitions.js tests/unit/js/test_wheatpart.js
git commit -m "feat(transitions): mmDrawWheat curtain draw glue

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `index.html` mask-family apply branch + demo tool

**Files:**
- Modify: `index.html` (add one `else if` branch in the mask-family block of `runScriptLoop`, after the `frostcreep` branch around line 778-781 and before the final `else if (typeof mmDrawMaskInCanvas` fallback around line 782)
- Create: `tools/_make_wheat_demo.py`

**Interfaces:**
- Consumes: `mmDrawWheat` (Task 5); the in-scope vars in that block: `ctx`, `stc.effect.params`, `stc.effect.phase`, `stc.effect.front`, `it.meshGlobal[0]`, `it.meshGlobal[1]`, `it.meshQuad`, `stc.effect.scope`, `playback.seed`, `GoTime.now()`, `it.backgroundColor`.

- [ ] **Step 1: Add the apply branch**

In `index.html`, in `runScriptLoop`'s mask block, insert AFTER the `frostcreep` `else if` (the block ending around line 781) and BEFORE the `} else if (typeof mmDrawMaskInCanvas === 'function') {` fallback:

```js
								} else if (stc.effect.name === 'wheatpart' && typeof mmDrawWheat === 'function') {
									mmDrawWheat(ctx, stc.effect.params, stc.effect.phase, stc.effect.front,
										it.meshGlobal[0], it.meshGlobal[1], it.meshQuad, stc.effect.scope,
										playback.seed | 0, GoTime.now());
```

(Match the surrounding tab indentation exactly — that block is tab-indented. If an `Edit` match fails on whitespace, use a small Python script with `assert s.count(old) == 1` as was done for prior effects.)

- [ ] **Step 2: Verify the edit landed once**

Run: `grep -c "wheatpart" index.html`
Expected: `1`.

- [ ] **Step 3: Create the demo tool**

Create `tools/_make_wheat_demo.py`:

```python
"""Create a 'Wheat Part Demo' playlist: two plasma mesh items handing off via the
wheatpart transition (golden, density 70). Run with the server up."""
import asyncio, json, aiohttp
HOST = "127.0.0.1:3000"

def wp():
    return {"name": "wheatpart",
            "params": {"tint": "golden", "density": 70, "scope": "wall",
                       "duration": 2200, "audioFade": True}}

ITEMS = [
    {"id": "wp-a", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 10, "backgroundColor": "#0a0a0a", "startEffect": None, "endEffect": wp()},
    {"id": "wp-b", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 10, "backgroundColor": "#0a0a0a", "startEffect": wp(), "endEffect": None},
]

async def main():
    url = "http://%s/sockjs/000/claudecmd/websocket" % HOST
    p = {"SRC": "claude-admin", "DEST": "SRV", "REQUEST": "SAVE_PLAYLIST",
         "PAYLOAD": {"name": "Wheat Part Demo", "items": ITEMS, "loop": True}}
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url) as ws:
            await ws.send_str(json.dumps([json.dumps(p)]))
            print("sent Wheat Part Demo")
            try:
                for _ in range(6):
                    m = await asyncio.wait_for(ws.receive(), timeout=2.0)
                    if "SAVE_PLAYLIST" in str(m.data): print("recv:", str(m.data)[:140])
            except asyncio.TimeoutError:
                pass

if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Sanity-check `index.html` parses (no JS syntax break)**

Run: `node -e "const fs=require('fs');const h=fs.readFileSync('index.html','utf8');const m=h.match(/runScriptLoop[\s\S]*?wheatpart[\s\S]{0,400}/);if(!m){process.exit(1)}console.log('wheatpart branch present')"`
Expected: prints `wheatpart branch present`. (This is a presence check; full validation is the on-wall sign-off.)

- [ ] **Step 5: Run both suites (no regressions)**

Run: `python pytest_runner.py --js` → all pass
Run: `python pytest_runner.py --unit` → all pass

- [ ] **Step 6: Commit**

```bash
git add index.html tools/_make_wheat_demo.py
git commit -m "feat(client): wire wheatpart mask apply + demo playlist

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: iPad-1 on-wall sign-off

**Files:** none (manual acceptance + deploy)

**Interfaces:** Consumes the whole feature.

This task is a human/on-wall verification — the controller deploys and the user judges. Server restart is required ONLY to expose the new catalog params in the editor (the wall itself updates via a client RELOAD); **a restart requires explicit user authorization** per the project's standing rule. The demo playlist carries explicit param values, so the wall renders wheatpart without a restart.

- [ ] **Step 1: Deploy to the wall**

- Reload the static client on the target group (SockJS `RELOAD`) so the new `js/transitions.js` + `index.html` load.
- Run `python tools/_make_wheat_demo.py` (server up) to create the demo, then `ASSIGN_PLAYLIST` it to the test group and `PLAY`.

- [ ] **Step 2: Acceptance checklist (user confirms on the physical wall)**

- [ ] item A's wheat **closes** over A (cover); item B's wheat **parts open** to reveal B (reveal).
- [ ] **Full opaque wheat at the handoff** — no outgoing content peeks through at the midpoint.
- [ ] Stalks **lean & slide outward** from the center seam with a gentle sway.
- [ ] The seam is at the **true wall center** and all screens share **one** field (no per-screen reseeding / mismatched stalks across the seam).
- [ ] Smooth at wall scale across the calibrated group (no jank on iPad-1).

- [ ] **Step 3: (If the user authorizes) restart the server to expose editor params**

Confirm `/api/effects` lists `wheatpart` with `tint`/`density`/`scope`/`duration`/`audioFade`.

- [ ] **Step 4: Mark the feature complete**

Proceed to the whole-branch review (superpowers:requesting-code-review) then superpowers:finishing-a-development-branch.

---

## Self-Review

**1. Spec coverage:**
- Mask-family + center-seam geometry → Tasks 2, 4, 5 ✅
- `mmWheatOpenness`/`mmWheatPartGeom`/`mmWheatField`/`mmWheatColor`/`mmWheatPhase`/`mmDrawWheat` → Tasks 2, 3, 4, 5 ✅
- Opaque backdrop + leaning/sway stalks → Task 5 ✅
- `effects.py` params + audio-only `video_filters` → Task 1 ✅
- One mask-apply site, mesh-only → Task 6 ✅
- Render-token neutrality guard → Task 1 (Step 5) ✅
- Node + Python + on-wall tests → Tasks 1-7 ✅
- Demo playlist → Task 6 ✅

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to". Two explicit NOTE-to-implementer callouts (Task 1 Step 5, Task 4 Step 1) point at adjacent tests to mirror exact call shapes — these are guidance, not gaps; the behavioral asserts are fully specified.

**3. Type consistency:** `front` = local progress (rising 0→1) everywhere; `mmWheatOpenness(phase, front)` resolves openness; `mmWheatPartGeom(openness, …)` consumes openness; `mmDrawWheat(ctx, params, phase, front, GW, GH, quad, scope, seed, now)` is the single signature used in Task 5 and called identically in Task 6. Field object keys `{bx,h,sway,headR,side}` match between Task 3 (produce) and Task 5 (consume). Palette keys `{backdrop,base,stalk,head}` match between Task 3 and Task 5.
