# Splash Crown Transition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `splashcrown` mask-family transition — a beer droplet falls and impacts the wall center, a Worthington crown leaps, and an opaque beer disc blooms outward to cover the content (reverse drains to reveal).

**Architecture:** Pure, node-tested helpers in `js/transitions.js` (`mmSplashPhase`, `mmSplashSeq`, `mmSplashRadius`, `mmCrownSpikes`) carry all the math; a thin `mmDrawSplash` draw glue consumes them (procedural droplet lead-in, then opaque beer `arc`+`fill` disc + crown spikes/beads, per-screen culled); one branch in `mmTransitionState` and one in `index.html` `runScriptLoop`'s mask block. A catalog entry in `effects.py`. Reuses beerfill's `mmBeerPalette`. Mesh-only; render-token neutral.

**Tech Stack:** ES5 client JS (iPad-1 / iOS 5.1 / Safari 5.1), 2D canvas, Python `aiohttp` server, `node --test` for JS units, `pytest` for Python.

## Global Constraints

- **Display-client JS is ES5 ONLY** (`js/transitions.js`, `index.html` inline `<script>`): NO `let`/`const`, arrow functions, template literals, `class`, `Promise`, `fetch`, default params, destructuring, spread. Must run on 1st-gen iPad / iOS 5.1 / Safari 5.1. (Node test files under `tests/unit/js/` MAY use modern JS.)
- **No `clip()`, no `ctx.ellipse`, no `matrix3d`, no 3D, no WebGL, no canvas filters** in any splashcrown path. Canvas primitives only: `fillRect`, `createLinearGradient`, `save`/`restore`, `translate`/`rotate`, `beginPath`/`moveTo`/`lineTo`/`closePath`/`quadraticCurveTo`, `arc`, `fill`/`stroke`.
- **Render-token neutrality:** `beerType`/`crownCount`/`scope` must NOT change the render token; only the audio-fade signature (role, duration when `audioFade` on) may. Enforced by a guard test.
- **Mask-family `front` convention:** `mmTransitionState` passes the draw helper `front` = LOCAL phase progress rising `0→1` for BOTH roles (out-role `1-p`, in-role `p`). The helper resolves cover-vs-reveal + the lead-in/bloom sequence from `phase` (matches scatter/beerfill/wheatpart).
- **Spelling:** the project is `mosiacmesh` (transposed "ai"). Don't "correct" it.
- **Commit trailer (this branch's convention):** end every commit message with
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- **Tests run via runners**, never bare `pytest`: `python pytest_runner.py --unit`, `python pytest_runner.py --js` (or `node --test tests/unit/js/<file>.js` for one JS file). A single Python test: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -k splashcrown -v`.
- **NEVER run the full `pytest_runner.py --unit` if `settings.dat` matters** is no longer a hazard (the 2026-06-26 fix isolates test writes), but still run from the repo root as normal.

---

### Task 1: `effects.py` catalog entry + Python tests

**Files:**
- Modify: `effects.py` (append a `@register class SplashCrownEffect(Effect)` after `WheatPartEffect`, the current last class)
- Test: `tests/unit/test_effects.py` (add `test_splashcrown_*`), `tests/unit/test_mosaic.py` (add token guard)

**Interfaces:**
- Produces: effect `name="splashcrown"`, params `beerType` (choice pale/amber/stout, default "pale"), `crownCount` (number, default 28, min 8, max 60), `scope` (choice screen/wall, default "wall"), `duration` (number, default 2000, min 0), `audioFade` (boolean, default True). `video_filters(role, params, ctx)` returns `([], _afade(role, params, ctx))`.

- [ ] **Step 1: Write the failing Python tests**

In `tests/unit/test_effects.py`, add (beside the existing `test_wheatpart_*` tests):

```python
def test_splashcrown_in_catalog_with_defaults():
    import effects
    cat = {e["name"]: e for e in effects.effect_catalog()}
    assert "splashcrown" in cat
    params = {p["key"]: p for p in cat["splashcrown"]["params"]}
    assert params["beerType"]["default"] == "pale"
    assert params["beerType"]["choices"] == ["pale", "amber", "stout"]
    assert params["crownCount"]["default"] == 28
    assert params["crownCount"]["min"] == 8 and params["crownCount"]["max"] == 60
    assert params["scope"]["default"] == "wall"
    assert params["duration"]["default"] == 2000
    assert params["audioFade"]["default"] is True


def test_splashcrown_video_filters_audio_only_role_aware():
    import effects
    eff = effects.get_effect("splashcrown")
    p = eff.resolve({"audioFade": True, "duration": 2000})
    ctx = {"duration_ms": 8000}
    vstart, astart = eff.video_filters("start", p, ctx)
    vend, aend = eff.video_filters("end", p, ctx)
    assert vstart == [] and vend == []
    assert astart == ["afade=t=in:st=0:d=2"]
    assert aend == ["afade=t=out:st=6:d=2"]


def test_splashcrown_audiofade_off_bakes_nothing():
    import effects
    eff = effects.get_effect("splashcrown")
    p = eff.resolve({"audioFade": False, "duration": 2000})
    assert eff.video_filters("start", p, {"duration_ms": 8000}) == ([], [])
    assert eff.video_filters("end", p, {"duration_ms": 8000}) == ([], [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -k splashcrown -v`
Expected: FAIL (`get_effect("splashcrown")` returns None).

- [ ] **Step 3: Implement the effect**

In `effects.py`, after the `WheatPartEffect` class, append:

```python
@register
class SplashCrownEffect(Effect):
    name = "splashcrown"
    label = "Splash Crown"
    # Single `duration`: a splashcrown instance only covers (endEffect) or reveals
    # (startEffect), never both.
    params = [ParamSpec("beerType", "choice", "pale", choices=["pale", "amber", "stout"]),
              ParamSpec("crownCount", "number", 28, minimum=8, maximum=60),
              ParamSpec("scope", "choice", "wall", choices=["screen", "wall"]),
              ParamSpec("duration", "number", 2000, minimum=0),
              ParamSpec("audioFade", "boolean", True)]

    def video_filters(self, role, params, ctx):
        return ([], _afade(role, params, ctx))     # visual is client-side; single duration
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -k splashcrown -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Add the render-token guard**

In `tests/unit/test_mosaic.py`, find `test_token_unchanged_by_wheatpart_visual_param_change` and add a sibling beside it (copy its EXACT structure — `_token_setup` helper + `server.compute_render_token("Default")` call — swapping the effect name + varied params):

```python
def test_token_unchanged_by_splashcrown_visual_param_change():
    # Varying splashcrown's purely-visual params (beerType, crownCount) must NOT change
    # the render token: the visual is client-side, only the audio-fade signature enters.
    base = {"name": "splashcrown",
            "params": {"beerType": "pale", "crownCount": 28, "scope": "wall",
                       "duration": 2000, "audioFade": True}}
    varied = {"name": "splashcrown",
              "params": {"beerType": "stout", "crownCount": 60, "scope": "wall",
                         "duration": 2000, "audioFade": True}}
    # ... mirror the wheatpart guard's exact _token_setup + compute_render_token assertion,
    # putting `base` then `varied` on a MediaElement.startEffect and asserting equal tokens.
```

NOTE for the implementer: open `test_token_unchanged_by_wheatpart_visual_param_change` and replicate its precise mechanism (how it builds the MediaElement, sets the effect, and calls the token function). Do not invent a call shape.

- [ ] **Step 6: Run the guard + full unit suite**

Run: `python -m pytest tests/unit/test_mosaic.py -c tests/pytest.ini -k splashcrown -v` → PASS
Run: `python pytest_runner.py --unit` → all pass (expect 624 passed, up from 620: +3 effect +1 token guard).

- [ ] **Step 7: Commit**

```bash
git add effects.py tests/unit/test_effects.py tests/unit/test_mosaic.py
git commit -m "feat(effects): splashcrown catalog entry + token guard

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `mmSplashPhase` + `mmSplashSeq` pure helpers + node tests

**Files:**
- Modify: `js/transitions.js` (add two functions near the other mask helpers, e.g. just before `mmScatterParticles`; add two `root.` exports in the exports block)
- Test: `tests/unit/js/test_splashcrown.js` (create)

**Interfaces:**
- Produces:
  - `mmSplashPhase(role)` → `'cover'` (role `'out'`) | `'reveal'` (role `'in'`).
  - `mmSplashSeq(phase, front, leadFrac)` → `{ dropY, bloom, impacted }`. `lp = phase==='cover' ? clamp(front) : 1-clamp(front)`; `lp<leadFrac` → `{dropY: lp/leadFrac, bloom:0, impacted:false}` else `{dropY:1, bloom:(lp-leadFrac)/(1-leadFrac), impacted:true}`. `leadFrac` defaults to 0.18 if not in `(0,1)`.

- [ ] **Step 1: Write the failing node test**

Create `tests/unit/js/test_splashcrown.js` (use the SAME loader pattern as `tests/unit/js/test_wheatpart.js` — open it and copy its module-load lines):

```js
'use strict';
const test = require('node:test');
const assert = require('node:assert');
// load transitions.js exactly as test_wheatpart.js does (await import / globalThis):
const g = globalThis;
require('node:module').createRequire(__filename);
await import('../../../js/transitions.js');

const C = (a, b) => Math.abs(a - b) < 1e-9;

test('mmSplashPhase: out->cover, in->reveal', () => {
  assert.equal(g.mmSplashPhase('out'), 'cover');
  assert.equal(g.mmSplashPhase('in'), 'reveal');
});

test('mmSplashSeq: lead-in then bloom; cover forward, reveal reversed; handoff full-beer', () => {
  // cover: front 0 -> drop at top, no beer; front 1 -> full bloom
  let s = g.mmSplashSeq('cover', 0, 0.18);
  assert.ok(!s.impacted && C(s.dropY, 0) && C(s.bloom, 0));
  s = g.mmSplashSeq('cover', 0.09, 0.18);          // mid lead-in
  assert.ok(!s.impacted && C(s.dropY, 0.5) && C(s.bloom, 0));
  s = g.mmSplashSeq('cover', 0.18, 0.18);          // impact edge
  assert.ok(s.impacted && C(s.bloom, 0));
  s = g.mmSplashSeq('cover', 1, 0.18);
  assert.ok(s.impacted && C(s.bloom, 1));
  // reveal is the time-reverse: front 0 -> full beer (handoff), front 1 -> drop up/no beer
  s = g.mmSplashSeq('reveal', 0, 0.18);
  assert.ok(s.impacted && C(s.bloom, 1), 'reveal starts full-beer at the handoff');
  s = g.mmSplashSeq('reveal', 1, 0.18);
  assert.ok(!s.impacted && C(s.bloom, 0) && C(s.dropY, 0), 'reveal ends drop-up, no beer');
});

test('mmSplashSeq: clamps and defaults leadFrac', () => {
  const a = g.mmSplashSeq('cover', 2, 0);          // leadFrac 0 -> default 0.18
  assert.ok(a.impacted && C(a.bloom, 1));
  const b = g.mmSplashSeq('cover', -1, 0.18);
  assert.ok(!b.impacted && C(b.dropY, 0));
});
```

NOTE: if the loader sketch above doesn't expose the helpers, copy the exact first ~6 lines of `tests/unit/js/test_wheatpart.js` (its proven loader) verbatim and drop the `createRequire` line.

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/test_splashcrown.js`
Expected: FAIL (`mmSplashPhase is not a function`).

- [ ] **Step 3: Implement the two helpers**

In `js/transitions.js`, add (just before `function mmScatterParticles`):

```js
  // --- Splash crown (mask family): a beer droplet impacts the wall center, a crown
  // leaps, and an opaque beer disc blooms outward. Pure sequencing + geometry; the
  // draw glue (mmDrawSplash) consumes these. ---
  function mmSplashPhase(role) { return role === 'out' ? 'cover' : 'reveal'; }

  // Lead-in (drop falls) -> bloom (disc grows). cover plays forward, reveal time-reverses
  // so both roles sit at full beer (bloom 1) at the A->B handoff. dropY 0=top..1=center.
  function mmSplashSeq(phase, front, leadFrac) {
    var f = front < 0 ? 0 : (front > 1 ? 1 : front);
    var lf = (leadFrac > 0 && leadFrac < 1) ? leadFrac : 0.18;
    var lp = (phase === 'cover') ? f : (1 - f);
    if (lp < lf) { return { dropY: lp / lf, bloom: 0, impacted: false }; }
    return { dropY: 1, bloom: (lp - lf) / (1 - lf), impacted: true };
  }
```

In the exports block (near the other `root.mm*` exports), add:

```js
  root.mmSplashPhase = mmSplashPhase;
  root.mmSplashSeq = mmSplashSeq;
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/test_splashcrown.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_splashcrown.js
git commit -m "feat(transitions): splashcrown phase + lead-in/bloom sequence helpers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `mmSplashRadius` + `mmCrownSpikes` pure helpers + node tests

**Files:**
- Modify: `js/transitions.js` (add two functions beside the Task 2 helpers; add two exports)
- Test: `tests/unit/js/test_splashcrown.js` (append)

**Interfaces:**
- Consumes: `_mmLcg(seed)` (existing private LCG: `var rnd = _mmLcg(seed >>> 0)` → `rnd()` ∈ [0,1)).
- Produces:
  - `mmSplashRadius(bloom, GW, GH)` → `clamp(bloom) * 0.5 * sqrt(GW*GW + GH*GH)`.
  - `mmCrownSpikes(seed, count)` → array of `count` objects `{ ang, lenF, beadF, flyF, phase }`. `ang ∈ [0,2π)` (evenly spaced + jitter), `lenF ∈ [0.5,1.0)`, `beadF ∈ [0.5,1.1)`, `flyF ∈ [0.6,1.5)`, `phase ∈ [0,2π)`. Deterministic in `(seed,count)`.

- [ ] **Step 1: Write the failing node test (append to `test_splashcrown.js`)**

```js
test('mmSplashRadius: 0 at bloom 0, half-diagonal at bloom 1, clamps', () => {
  assert.ok(C(g.mmSplashRadius(0, 800, 600), 0));
  assert.ok(C(g.mmSplashRadius(1, 800, 600), 0.5 * Math.sqrt(800 * 800 + 600 * 600)));
  assert.ok(C(g.mmSplashRadius(2, 800, 600), 0.5 * Math.sqrt(800 * 800 + 600 * 600)));  // clamp
});

test('mmCrownSpikes: deterministic, sized, in-bounds', () => {
  const a = g.mmCrownSpikes(123, 28);
  const b = g.mmCrownSpikes(123, 28);
  assert.equal(a.length, 28);
  assert.deepEqual(a, b);
  assert.notDeepEqual(a, g.mmCrownSpikes(999, 28));
  for (const s of a) {
    assert.ok(s.ang >= 0 && s.ang < 6.2832 + 1, 'ang ~ [0,2pi)');
    assert.ok(s.lenF >= 0.5 && s.lenF < 1.0);
    assert.ok(s.beadF >= 0.5 && s.beadF < 1.11);
    assert.ok(s.flyF >= 0.6 && s.flyF < 1.51);
  }
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/test_splashcrown.js`
Expected: FAIL (`mmSplashRadius is not a function`).

- [ ] **Step 3: Implement the two helpers**

In `js/transitions.js`, beside the Task 2 helpers:

```js
  function mmSplashRadius(bloom, GW, GH) {
    var b = bloom < 0 ? 0 : (bloom > 1 ? 1 : bloom);
    return b * 0.5 * Math.sqrt(GW * GW + GH * GH);
  }

  // Deterministic crown: `count` spikes around the rim (seeded -> identical on every
  // screen, like mmScatterParticles). Evenly spaced + per-spike jitter so the rim isn't
  // a perfect ring. lenF/beadF/flyF/phase shape each spike + its flung bead.
  function mmCrownSpikes(seed, count) {
    var n = count > 0 ? (count | 0) : 1;
    var rnd = _mmLcg(seed >>> 0), arr = [], i, base, step = 6.283185307 / n;
    for (i = 0; i < n; i++) {
      base = i * step;
      arr.push({ ang: base + (rnd() - 0.5) * step * 0.8,
                 lenF: 0.5 + rnd() * 0.5,
                 beadF: 0.5 + rnd() * 0.6,
                 flyF: 0.6 + rnd() * 0.9,
                 phase: rnd() * 6.283185307 });
    }
    return arr;
  }
```

In the exports block, beside the Task 2 exports:

```js
  root.mmSplashRadius = mmSplashRadius;
  root.mmCrownSpikes = mmCrownSpikes;
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/test_splashcrown.js`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_splashcrown.js
git commit -m "feat(transitions): splashcrown radius + seeded crown-spike helpers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `mmTransitionState` splashcrown branch + node test

**Files:**
- Modify: `js/transitions.js` (add a branch inside `mmTransitionState`, right after the `wheatpart` branch and before the `coasterflip` branch — search for `eff.name === 'wheatpart'`)
- Test: `tests/unit/js/test_splashcrown.js` (append)

**Interfaces:**
- Consumes: the `mmTransitionState(start, end, offsetMs, durationMs, rect, quad)` machinery — inside each branch `eff`, `role` (`'out'`/`'in'`), and `p` (raw progress) are in scope.
- Produces: for `eff.name === 'splashcrown'`, returns `{ role, opacity:1, wipe:null, effect: { name:'splashcrown', family:'mask', front:<localProgress>, scope, params, phase } }` where `front = (role==='out') ? (1-p) : p` and `phase = mmSplashPhase(role)`.

- [ ] **Step 1: Write the failing node test (append to `test_splashcrown.js`)**

```js
test('mmTransitionState: splashcrown is a mask effect with phase + rising local front', () => {
  const endEff = { name: 'splashcrown', params: { scope: 'wall', duration: 2000 } };
  const near = g.mmTransitionState(null, endEff, 6200, 8000, null, null);
  const late = g.mmTransitionState(null, endEff, 7800, 8000, null, null);
  assert.equal(near.effect.name, 'splashcrown');
  assert.equal(near.effect.family, 'mask');
  assert.equal(near.effect.phase, 'cover');
  assert.ok(near.effect.front >= 0 && near.effect.front <= 1);
  assert.ok(late.effect.front > near.effect.front, 'local front rises across the cover window');
  assert.equal(near.wipe, null);

  const startEff = { name: 'splashcrown', params: { scope: 'wall', duration: 2000 } };
  const s = g.mmTransitionState(startEff, null, 200, 8000, null, null);
  assert.equal(s.effect.phase, 'reveal');
});
```

NOTE: if the chosen offsets don't activate a window (effect is null), mirror the offset/duration values that `test_wheatpart.js`'s `mmTransitionState` test uses. The behavioral asserts (family/phase/front rising) are what matter.

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/test_splashcrown.js`
Expected: FAIL (`effect` null or name mismatch).

- [ ] **Step 3: Implement the branch**

In `js/transitions.js`, inside `mmTransitionState`, immediately after the `wheatpart` branch's closing `}` and before `if (eff.name === 'coasterflip')`:

```js
    if (eff.name === 'splashcrown') {
      var spsc = (eff.params && eff.params.scope) || 'wall';
      // front = LOCAL phase progress 0->1 (scatter convention): invert on the 'out'
      // window; mmDrawSplash maps front->sequence via phase (mmSplashSeq).
      var splp = (role === 'out') ? (1 - p) : p;
      return { role: role, opacity: 1, wipe: null,
               effect: { name: 'splashcrown', family: 'mask', front: splp,
                         scope: spsc, params: eff.params || {}, phase: mmSplashPhase(role) } };
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/test_splashcrown.js`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_splashcrown.js
git commit -m "feat(transitions): mmTransitionState splashcrown mask branch

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `mmDrawSplash` draw glue + node smoke test

**Files:**
- Modify: `js/transitions.js` (add `mmDrawSplash` beside `mmDrawScatter`/`mmDrawFrost`; add export)
- Test: `tests/unit/js/test_splashcrown.js` (append a stub-ctx smoke test)

**Interfaces:**
- Consumes: `mmSplashSeq`, `mmSplashRadius`, `mmCrownSpikes` (Tasks 2-3), `mmBeerPalette(beerType)` (existing → `{beerTop, beerBot, foamTop, foam, ...}`), `_mmMaskRegion(scope, quad, GW, GH)` (existing → `{x,y,w,h}`).
- Produces: `mmDrawSplash(ctx, params, phase, front, GW, GH, quad, scope, seed, now)` → void. Lead-in draws the falling droplet; bloom draws the opaque beer disc + crown; per-screen culls spikes.

- [ ] **Step 1: Write the failing smoke test (append to `test_splashcrown.js`)**

```js
function stubCtx() {
  const calls = { fillRect: 0, beginPath: 0, fill: 0, arc: 0, quad: 0, gradients: 0, moveTo: 0 };
  return {
    calls, fillStyle: '#000', strokeStyle: '#000', globalAlpha: 1, lineWidth: 1,
    save() {}, restore() {}, translate() {}, rotate() {},
    beginPath() { calls.beginPath++; }, moveTo() { calls.moveTo++; }, lineTo() {}, closePath() {},
    quadraticCurveTo() { calls.quad++; }, arc() { calls.arc++; },
    fill() { calls.fill++; }, stroke() {}, fillRect() { calls.fillRect++; },
    createLinearGradient() { calls.gradients++; return { addColorStop() {} }; }
  };
}

test('mmDrawSplash: lead-in draws droplet (no disc), bloom fills disc + crown', () => {
  // cover, front mid lead-in (front 0.09, lead 0.18) -> droplet only, NO arc disc fill
  const lead = stubCtx();
  g.mmDrawSplash(lead, { beerType: 'pale', crownCount: 12 }, 'cover', 0.09, 800, 600, null, 'wall', 5, 0);
  assert.ok(lead.calls.quad >= 4, 'lead-in draws the teardrop via quadraticCurveTo');

  // cover, well into bloom -> opaque disc (arc+fill) + crown spikes (arcs)
  const bloom = stubCtx();
  g.mmDrawSplash(bloom, { beerType: 'pale', crownCount: 12 }, 'cover', 0.7, 800, 600, null, 'wall', 5, 0);
  assert.ok(bloom.calls.arc >= 1, 'bloom fills the beer disc (arc)');
  assert.ok(bloom.calls.fill > lead.calls.fill, 'bloom draws more than the lead-in droplet');
});

test('mmDrawSplash: never throws on degenerate inputs / screen scope', () => {
  const quad = [[0.25, 0.5], [0.75, 0.5], [0.75, 1.0], [0.25, 1.0]];
  assert.doesNotThrow(() => g.mmDrawSplash(stubCtx(), {}, 'reveal', 0.5, 800, 600, quad, 'screen', 0, 100));
  assert.doesNotThrow(() => g.mmDrawSplash(stubCtx(), { crownCount: 0 }, 'cover', 1, 800, 600, null, 'wall', 0, 0));
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/test_splashcrown.js`
Expected: FAIL (`mmDrawSplash is not a function`).

- [ ] **Step 3: Implement `mmDrawSplash`**

In `js/transitions.js`, beside `mmDrawScatter`/`mmDrawFrost`:

```js
  // Draw the splash crown: a beer droplet lead-in, then an OPAQUE beer disc blooming
  // from the center with a crown of spikes + flung beads on the advancing edge. Global
  // coords (warped by the mesh affine). ctx primitives only -- no clip.
  function mmDrawSplash(ctx, params, phase, front, GW, GH, quad, scope, seed, now) {
    var seq = mmSplashSeq(phase, front, 0.18);
    var pal = mmBeerPalette(params && params.beerType);
    var reg = _mmMaskRegion(scope, quad, GW, GH);
    var cx = reg.x + reg.w / 2, cy = reg.y + reg.h / 2, TWO = 6.283185307;

    if (!seq.impacted) {
      if (seq.dropY <= 0) { return; }
      var dy = reg.y + seq.dropY * (cy - reg.y);          // top -> center
      var dw = reg.h * 0.018, dh = reg.h * 0.045;
      ctx.fillStyle = pal.beerTop;
      ctx.globalAlpha = 0.35;                              // motion streak above the drop
      ctx.fillRect(cx - dw * 0.25, reg.y, dw * 0.5, dy - reg.y);
      ctx.globalAlpha = 1;
      ctx.beginPath();                                     // beer teardrop (pointed top)
      ctx.moveTo(cx, dy - dh);
      ctx.quadraticCurveTo(cx + dw, dy - dh * 0.1, cx + dw, dy + dh * 0.2);
      ctx.quadraticCurveTo(cx + dw, dy + dh, cx, dy + dh);
      ctx.quadraticCurveTo(cx - dw, dy + dh, cx - dw, dy + dh * 0.2);
      ctx.quadraticCurveTo(cx - dw, dy - dh * 0.1, cx, dy - dh);
      ctx.fill();
      return;
    }

    var R = mmSplashRadius(seq.bloom, reg.w, reg.h);
    if (R <= 0) { return; }
    var g = ctx.createLinearGradient(0, cy - R, 0, cy + R);  // beer disc body
    g.addColorStop(0, pal.beerTop); g.addColorStop(1, pal.beerBot);
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, TWO); ctx.fill();

    var spikes = mmCrownSpikes(seed, (params && params.crownCount) || 28);
    var spikeMax = reg.h * 0.06, ts = (now || 0) * 0.001, i, s, sa, slen, tipx, tipy, sw, px, py, fb;
    var qLo = -1, qHi = 0;
    if (quad && quad.length >= 4) {
      qLo = Math.min(quad[0][0], quad[1][0], quad[2][0], quad[3][0]) * GW - spikeMax * 3;
      qHi = Math.max(quad[0][0], quad[1][0], quad[2][0], quad[3][0]) * GW + spikeMax * 3;
    }
    for (i = 0; i < spikes.length; i++) {
      s = spikes[i]; sa = s.ang;
      px = cx + R * Math.cos(sa); py = cy + R * Math.sin(sa);     // rim base
      if (qLo >= 0 && (px < qLo || px > qHi)) { continue; }       // off this screen
      slen = spikeMax * s.lenF * (0.6 + 0.4 * Math.sin(ts * 3 + s.phase));
      tipx = cx + (R + slen) * Math.cos(sa); tipy = cy + (R + slen) * Math.sin(sa);
      sw = reg.h * 0.012 * s.beadF;
      ctx.fillStyle = pal.beerTop;                               // spike triangle
      ctx.beginPath();
      ctx.moveTo(px - (-Math.sin(sa)) * sw, py - (Math.cos(sa)) * sw);
      ctx.lineTo(px + (-Math.sin(sa)) * sw, py + (Math.cos(sa)) * sw);
      ctx.lineTo(tipx, tipy); ctx.closePath(); ctx.fill();
      ctx.fillStyle = pal.foamTop || pal.foam || pal.beerTop;    // foam-highlighted tip bead
      ctx.beginPath(); ctx.arc(tipx, tipy, sw, 0, TWO); ctx.fill();
      fb = R + slen + spikeMax * s.flyF * (0.5 + seq.bloom);     // flung bead ahead
      ctx.globalAlpha = 0.6 * (1 - seq.bloom * 0.3);
      ctx.beginPath(); ctx.arc(cx + fb * Math.cos(sa), cy + fb * Math.sin(sa), sw * 0.7, 0, TWO); ctx.fill();
      ctx.globalAlpha = 1;
    }
  }
```

In the exports block, add:

```js
  root.mmDrawSplash = mmDrawSplash;
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/test_splashcrown.js`
Expected: PASS (8 tests).

- [ ] **Step 5: Run the full JS suite (no regressions)**

Run: `python pytest_runner.py --js`
Expected: all pass (expect ~401 pass, up from 393).

- [ ] **Step 6: Commit**

```bash
git add js/transitions.js tests/unit/js/test_splashcrown.js
git commit -m "feat(transitions): mmDrawSplash droplet + beer-disc bloom + crown

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `index.html` mask-family apply branch + demo tool

**Files:**
- Modify: `index.html` (add one `else if` branch in the mask-family block of `runScriptLoop`, after the `wheatpart` branch (line ~782) and before the `} else if (typeof mmDrawMaskInCanvas === 'function') {` fallback (line ~787))
- Create: `tools/_make_splash_demo.py`

**Interfaces:**
- Consumes: `mmDrawSplash` (Task 5); in-scope vars in that block: `ctx`, `stc.effect.params`, `stc.effect.phase`, `stc.effect.front`, `it.meshGlobal[0]`, `it.meshGlobal[1]`, `it.meshQuad`, `stc.effect.scope`, `playback.seed`, `GoTime.now()`.

- [ ] **Step 1: Add the apply branch**

`index.html` is **TAB-indented** and the `Edit` tool often fails to match it. Use a Python script that reads the file, builds the exact old/new strings with `"\t"*N`, asserts `s.count(old) == 1`, and writes — the reliable approach used for every prior effect. First read the `wheatpart` branch (line ~782) to confirm the tab depth (8 tabs for `} else if`, 9 for the call, 10 for continuations). Insert AFTER the `wheatpart` block and BEFORE the `} else if (typeof mmDrawMaskInCanvas` fallback:

```js
								} else if (stc.effect.name === 'splashcrown' && typeof mmDrawSplash === 'function') {
									mmDrawSplash(ctx, stc.effect.params, stc.effect.phase, stc.effect.front,
										it.meshGlobal[0], it.meshGlobal[1], it.meshQuad, stc.effect.scope,
										playback.seed | 0, GoTime.now());
```

- [ ] **Step 2: Verify the edit landed once**

Run: `grep -c "splashcrown" index.html`
Expected: `1`.

- [ ] **Step 3: Create the demo tool**

Create `tools/_make_splash_demo.py`:

```python
"""Create a 'Splash Crown Demo' playlist: two plasma mesh items handing off via the
splashcrown transition (pale, crownCount 28). Run with the server up."""
import asyncio, json, aiohttp
HOST = "127.0.0.1:3000"

def sc():
    return {"name": "splashcrown",
            "params": {"beerType": "pale", "crownCount": 28, "scope": "wall",
                       "duration": 2000, "audioFade": True}}

ITEMS = [
    {"id": "sc-a", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 10, "backgroundColor": "#0a0a0a", "startEffect": None, "endEffect": sc()},
    {"id": "sc-b", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 10, "backgroundColor": "#0a0a0a", "startEffect": sc(), "endEffect": None},
]

async def main():
    url = "http://%s/sockjs/000/claudecmd/websocket" % HOST
    p = {"SRC": "claude-admin", "DEST": "SRV", "REQUEST": "SAVE_PLAYLIST",
         "PAYLOAD": {"name": "Splash Crown Demo", "items": ITEMS, "loop": True}}
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url) as ws:
            await ws.send_str(json.dumps([json.dumps(p)]))
            print("sent Splash Crown Demo")
            try:
                for _ in range(6):
                    m = await asyncio.wait_for(ws.receive(), timeout=2.0)
                    if "SAVE_PLAYLIST" in str(m.data): print("recv:", str(m.data)[:140])
            except asyncio.TimeoutError:
                pass

if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Sanity-check `index.html` has the branch**

Run: `node -e "const fs=require('fs');const h=fs.readFileSync('index.html','utf8');process.exit(h.indexOf('mmDrawSplash')>0?0:1)"`
Expected: exit 0 (branch present).

- [ ] **Step 5: Run both suites (no regressions)**

Run: `python pytest_runner.py --js` → all pass
Run: `python pytest_runner.py --unit` → all pass

- [ ] **Step 6: Commit**

```bash
git add index.html tools/_make_splash_demo.py
git commit -m "feat(client): wire splashcrown mask apply + demo playlist

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: iPad-1 on-wall sign-off

**Files:** none (manual acceptance + deploy)

**Interfaces:** Consumes the whole feature.

This is a human/on-wall verification — the controller deploys and the user judges. A server restart is required ONLY to expose the new catalog params in the editor (the wall itself updates via a client RELOAD); **a restart requires explicit user authorization** per the standing rule. The demo playlist carries explicit param values, so the wall renders splashcrown without a restart.

- [ ] **Step 1: Deploy to the wall**

- Reload the static client on the target group (SockJS `RELOAD`) so the new `js/transitions.js` + `index.html` load.
- Run `python tools/_make_splash_demo.py` (server up), then `ASSIGN_PLAYLIST` it to the test group and `PLAY`.

- [ ] **Step 2: Acceptance checklist (user confirms on the physical wall)**

- [ ] Lead-in: a beer droplet falls top→center and impacts.
- [ ] Crown leaps and an **opaque beer disc blooms** outward to cover item A; **full beer at the handoff**.
- [ ] Reveal: the disc **contracts** back to center revealing item B, droplet lifting off.
- [ ] Crown rim reads as **spikes + flung beads** on the advancing edge.
- [ ] One shared disc/crown centered on the true wall center; smooth at wall scale (no jank on iPad-1).

- [ ] **Step 3: (If the user authorizes) restart the server to expose editor params**

Confirm `/api/effects` lists `splashcrown` with `beerType`/`crownCount`/`scope`/`duration`/`audioFade`.

- [ ] **Step 4: Mark the feature complete**

Proceed to the whole-branch review (superpowers:requesting-code-review) then superpowers:finishing-a-development-branch.

---

## Self-Review

**1. Spec coverage:**
- Mask-family + radial bloom geometry → Tasks 2, 3, 4, 5 ✅
- `mmSplashPhase`/`mmSplashSeq`/`mmSplashRadius`/`mmCrownSpikes`/`mmDrawSplash` → Tasks 2, 3, 4, 5 ✅
- Droplet lead-in + opaque beer disc + crown spikes/beads → Task 5 ✅
- `effects.py` params + audio-only `video_filters` → Task 1 ✅
- One mask-apply site, mesh-only → Task 6 ✅
- Render-token neutrality guard → Task 1 (Step 5) ✅
- Per-screen spike cull → Task 5 ✅
- Reuses `mmBeerPalette` → Task 5 (consumes) ✅
- Node + Python + on-wall tests → Tasks 1-7 ✅
- Demo playlist → Task 6 ✅

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to". Two NOTE-to-implementer callouts (Task 1 Step 5, Task 4 Step 1) point at adjacent tests/values to mirror exact shapes — guidance, not gaps; the behavioral asserts are fully specified.

**3. Type consistency:** `front` = local progress (rising 0→1) everywhere; `mmSplashSeq(phase, front, leadFrac)` → `{dropY, bloom, impacted}` consumed identically in Task 5; `mmSplashRadius(bloom, GW, GH)` called with `(seq.bloom, reg.w, reg.h)` in Task 5; `mmCrownSpikes(seed, count)` keys `{ang,lenF,beadF,flyF,phase}` produced in Task 3 and consumed in Task 5; `mmDrawSplash(ctx, params, phase, front, GW, GH, quad, scope, seed, now)` is the single signature used in Task 5 and called identically in Task 6. Palette keys `beerTop`/`beerBot`/`foamTop`/`foam` match `mmBeerPalette`'s output.
