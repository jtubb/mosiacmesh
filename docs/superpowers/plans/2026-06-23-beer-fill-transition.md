# Beer-Fill Transition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `beerfill` transition effect — beer fills bottom-up to cover the outgoing item (pour stream, foam, bubbles), then drains to reveal the incoming item — spanning the calibrated mesh wall as one glass.

**Architecture:** A server `Effect` subclass (`effects.py`) that only bakes the audio fade; all visuals are pure ES5 helpers + a canvas draw in `js/transitions.js`, wired into the existing mesh in-canvas and overlay cover paths in `index.html`. It is a **mask-family** effect: opaque beer fills the bottom fraction of the (per-screen or wall) region; the wall-coordinated sweep is free because mesh SCRIPT content is drawn in global coords and warped per-screen.

**Tech Stack:** Python (aiohttp server, pytest), ES5 JavaScript (no module syntax — runs on iPad-1 / Safari 5.1), Node `--test` for the pure JS helpers.

## Global Constraints

- Client code in `js/transitions.js` and `index.html` is **ES5 only** — no `let`/`const`, arrow functions, template literals, `class`, `Promise`. (1st-gen iPad / iOS 5.1 / Safari 5.1.)
- Canvas drawing uses only `fillRect`, `arc`+`fill`, `drawImage`, and simple polyline paths. **No `clip()`, no `destination-*` compositing** (unreliable/slow on iPad-1).
- Effects register via `@register` and auto-appear in `effect_catalog()` / `/api/effects`; visual transitions bake **audio only** (`_afade`), never video filters.
- Pure helpers go in `js/transitions.js`, exported on `root` at the bottom, and are unit-tested under `tests/unit/js/`.
- Tests are run with `python pytest_runner.py --unit` (Python) and `node --test tests/unit/js/<file>` (JS). A bare `pytest` won't pick up config.
- Beer presets (validated): `pale` `{beerTop:#F6C744, beerBot:#E0A21A, foam:#FFF8E7, headH:0.11, bubbleDensity:34, foamBubbles:30}`, `amber` `{#C9791C,#8A4A0E,#F3E0C0,0.14,22,26}`, `stout` `{#3A241A,#160C07,#E8C9A0,0.20,12,34}`.

---

### Task 1: Server — `BeerFillEffect` in `effects.py`

**Files:**
- Modify: `effects.py` (add subclass after `DissolveEffect`, ~line 183)
- Test: `tests/unit/test_effects.py`, `tests/unit/test_playlists.py`

**Interfaces:**
- Consumes: `Effect`, `ParamSpec`, `register`, `_afade` (existing in `effects.py`).
- Produces: effect `name="beerfill"` with params `beerType` (choice pale/amber/stout, default pale), `scope` (choice screen/wall, default wall), `fillMs` (number, default 2500, min 0), `drainMs` (number, default 2500, min 0), `audioFade` (boolean, default True). `video_filters(role, params, ctx)` returns `([], <afade using fillMs for role 'end', drainMs for role 'start'>)`.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_effects.py`:

```python
def test_beerfill_params():
    e = next(e for e in effects.effect_catalog() if e["name"] == "beerfill")
    by = {p["key"]: p for p in e["params"]}
    assert by["beerType"]["choices"] == ["pale", "amber", "stout"] and by["beerType"]["default"] == "pale"
    assert by["scope"]["choices"] == ["screen", "wall"] and by["scope"]["default"] == "wall"
    assert by["fillMs"]["type"] == "number" and by["fillMs"]["default"] == 2500
    assert by["drainMs"]["type"] == "number" and by["drainMs"]["default"] == 2500
    assert by["audioFade"]["type"] == "boolean" and by["audioFade"]["default"] is True


def test_beerfill_audio_uses_fillMs_on_end_drainMs_on_start():
    bf = effects.get_effect("beerfill")
    ctx = {"duration_ms": 6000}
    # start role (drain) -> fade in over drainMs
    v, a = bf.video_filters("start", bf.resolve({"drainMs": 2000, "audioFade": True}), ctx)
    assert v == [] and a == ["afade=t=in:st=0:d=2"]
    # end role (fill) -> fade out over fillMs, ending at clip end
    v2, a2 = bf.video_filters("end", bf.resolve({"fillMs": 1500, "audioFade": True}), ctx)
    assert v2 == [] and a2 == ["afade=t=out:st=4.5:d=1.5"]


def test_beerfill_no_audio_when_off():
    bf = effects.get_effect("beerfill")
    v, a = bf.video_filters("end", bf.resolve({"audioFade": False}), {"duration_ms": 6000})
    assert v == [] and a == []
```

Also update the catalog set assertion in the same file:

```python
def test_catalog_has_all_effects():
    names = {e["name"] for e in effects.effect_catalog()}
    assert names == {"fade", "wipe", "slide", "zoom", "iris", "dissolve", "beerfill"}
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -v`
Expected: FAIL — `beerfill` not in catalog / `get_effect("beerfill")` returns None.

- [ ] **Step 3: Implement** — add to `effects.py` after `DissolveEffect`:

```python
@register
class BeerFillEffect(Effect):
    name = "beerfill"
    label = "Beer Fill"
    params = [ParamSpec("beerType", "choice", "pale", choices=["pale", "amber", "stout"]),
              ParamSpec("scope", "choice", "wall", choices=["screen", "wall"]),
              ParamSpec("fillMs", "number", 2500, minimum=0),
              ParamSpec("drainMs", "number", 2500, minimum=0),
              ParamSpec("audioFade", "boolean", True)]

    def video_filters(self, role, params, ctx):
        # Visual is client-side. Audio fade length follows the active phase:
        # fill (end role) uses fillMs, drain (start role) uses drainMs.
        dur = params.get("fillMs") if role == "end" else params.get("drainMs")
        p = dict(params)
        p["duration"] = dur
        return ([], _afade(role, p, ctx))
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -v`
Expected: PASS (all, including the updated catalog test).

- [ ] **Step 5: Fix the full-catalog assertion in `test_playlists.py`**

Find the test asserting the effects list (search `test_api_effects` or the set `{"fade","wipe","slide","zoom","iris","dissolve"}`) and add `"beerfill"`:

```python
        assert names == {"fade", "wipe", "slide", "zoom", "iris", "dissolve", "beerfill"}
```

- [ ] **Step 6: Run the full unit suite**

Run: `python pytest_runner.py --unit`
Expected: all pass (no other test asserts the old catalog set).

- [ ] **Step 7: Commit**

```bash
git add effects.py tests/unit/test_effects.py tests/unit/test_playlists.py
git commit -m "feat(transitions): beerfill server effect (audio-only, fillMs/drainMs by role)"
```

---

### Task 2: Pure palette / phase / level / duration helpers

**Files:**
- Modify: `js/transitions.js` (add functions before the `root.*` export block ~line 288; add exports)
- Test: `tests/unit/js/test_beerfill.js` (create)

**Interfaces:**
- Produces (all pure, exported on `root`):
  - `mmBeerPalette(beerType)` → `{beerTop,beerBot,foam,headH,bubbleDensity,foamBubbles}` (unknown/empty → `pale`).
  - `mmBeerPhase(role)` → `'fill'` for role `'out'`, `'drain'` otherwise (`'in'`).
  - `mmBeerDuration(params, role)` → `fillMs` for role `'out'` else `drainMs`; missing → 2500.
  - `mmBeerLevel(phase, p)` → coverage fraction in `[0,1]`: `phase==='fill'` → clamp(p); else clamp(1−p).

- [ ] **Step 1: Write the failing test** — create `tests/unit/js/test_beerfill.js`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/transitions.js');

const Pal = globalThis.mmBeerPalette, Phase = globalThis.mmBeerPhase;
const Dur = globalThis.mmBeerDuration, Level = globalThis.mmBeerLevel;

test('mmBeerPalette: known types + default', () => {
  assert.equal(Pal('pale').beerTop, '#F6C744');
  assert.equal(Pal('stout').headH, 0.20);
  assert.equal(Pal('amber').foam, '#F3E0C0');
  assert.equal(Pal('nope').beerTop, '#F6C744');   // unknown -> pale
  assert.equal(Pal(undefined).beerTop, '#F6C744');
});

test('mmBeerPhase: out=fill, in=drain', () => {
  assert.equal(Phase('out'), 'fill');
  assert.equal(Phase('in'), 'drain');
});

test('mmBeerDuration: fillMs on out, drainMs on in, default 2500', () => {
  assert.equal(Dur({ fillMs: 1500, drainMs: 3000 }, 'out'), 1500);
  assert.equal(Dur({ fillMs: 1500, drainMs: 3000 }, 'in'), 3000);
  assert.equal(Dur({}, 'out'), 2500);
  assert.equal(Dur(null, 'in'), 2500);
});

test('mmBeerLevel: fill rises 0->1, drain falls 1->0, clamped', () => {
  assert.equal(Level('fill', 0), 0);
  assert.equal(Level('fill', 1), 1);
  assert.equal(Level('drain', 0), 1);
  assert.equal(Level('drain', 1), 0);
  assert.equal(Level('fill', -0.5), 0);
  assert.equal(Level('drain', 1.5), 0);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test tests/unit/js/test_beerfill.js`
Expected: FAIL — `mmBeerPalette` etc. undefined.

- [ ] **Step 3: Implement** — add to `js/transitions.js` (before the export block):

```javascript
  var _BEER = {
    pale:  { beerTop: '#F6C744', beerBot: '#E0A21A', foam: '#FFF8E7', headH: 0.11, bubbleDensity: 34, foamBubbles: 30 },
    amber: { beerTop: '#C9791C', beerBot: '#8A4A0E', foam: '#F3E0C0', headH: 0.14, bubbleDensity: 22, foamBubbles: 26 },
    stout: { beerTop: '#3A241A', beerBot: '#160C07', foam: '#E8C9A0', headH: 0.20, bubbleDensity: 12, foamBubbles: 34 }
  };
  function mmBeerPalette(beerType) { return _BEER[beerType] || _BEER.pale; }
  function mmBeerPhase(role) { return role === 'out' ? 'fill' : 'drain'; }
  function mmBeerDuration(params, role) {
    var ms = role === 'out' ? (params && params.fillMs) : (params && params.drainMs);
    ms = +ms;
    return (ms > 0) ? ms : 2500;
  }
  function mmBeerLevel(phase, p) {
    var lv = phase === 'fill' ? p : (1 - p);
    return lv < 0 ? 0 : (lv > 1 ? 1 : lv);
  }
```

Add exports in the `root.*` block:

```javascript
  root.mmBeerPalette = mmBeerPalette;
  root.mmBeerPhase = mmBeerPhase;
  root.mmBeerDuration = mmBeerDuration;
  root.mmBeerLevel = mmBeerLevel;
```

- [ ] **Step 4: Run to verify pass**

Run: `node --test tests/unit/js/test_beerfill.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_beerfill.js
git commit -m "feat(transitions): beerfill palette/phase/level/duration pure helpers"
```

---

### Task 3: Pure foam-wave + seeded bubble helpers

**Files:**
- Modify: `js/transitions.js` (add functions + exports)
- Test: `tests/unit/js/test_beerfill.js` (append)

**Interfaces:**
- Consumes: `_mmLcg(seed)` (existing — deterministic [0,1) generator).
- Produces (pure, exported):
  - `mmFoamWaveY(xFrac, t, amp, baseY)` → `baseY + sin(xFrac*15 + t*9.4)*amp*0.5 + sin(xFrac*41 − t*6.3)*amp*0.3`.
  - `mmBeerBubbles(seed, count)` → array of `{x,phase,r,spd}` (x,phase in [0,1]; r in [1,3.4]; spd in [0.45,1.25]) — deterministic from `seed`.
  - `mmFoamBubbles(seed, count)` → array of `{x,yf,r,a}` (x,yf in [0,1]; r in [1,4.2]; a in [0.22,0.62]) — deterministic, different stream from `mmBeerBubbles`.

- [ ] **Step 1: Write the failing test** — append to `tests/unit/js/test_beerfill.js`:

```javascript
const Wave = globalThis.mmFoamWaveY, Bub = globalThis.mmBeerBubbles, Foam = globalThis.mmFoamBubbles;

test('mmFoamWaveY: deterministic + amp scaling around baseY', () => {
  const a = Wave(0.5, 1.0, 10, 100), b = Wave(0.5, 1.0, 10, 100);
  assert.equal(a, b);                                  // pure
  assert.ok(Math.abs(a - 100) <= 10 + 1e-9);           // within +/- amp*(0.5+0.3)
  assert.notEqual(Wave(0.5, 1.0, 10, 100), Wave(0.5, 2.0, 10, 100)); // t matters
});

test('mmBeerBubbles: deterministic per seed, ranges, count', () => {
  const x = mmBeerBubbles(7, 20), y = mmBeerBubbles(7, 20), z = mmBeerBubbles(8, 20);
  assert.equal(x.length, 20);
  assert.deepEqual(x, y);                              // same seed -> identical (wall-coherent)
  assert.notDeepEqual(x, z);                           // different seed -> different
  x.forEach(b => {
    assert.ok(b.x >= 0 && b.x < 1 && b.phase >= 0 && b.phase < 1);
    assert.ok(b.r >= 1 && b.r <= 3.4 && b.spd >= 0.45 && b.spd <= 1.25);
  });
});

test('mmFoamBubbles: deterministic, distinct stream from beer bubbles', () => {
  const f = mmFoamBubbles(7, 15), g = mmFoamBubbles(7, 15);
  assert.deepEqual(f, g);
  assert.notDeepEqual(f.map(b => b.x), mmBeerBubbles(7, 15).map(b => b.x)); // different stream
  f.forEach(b => { assert.ok(b.a >= 0.22 && b.a <= 0.62 && b.r >= 1 && b.r <= 4.2); });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test tests/unit/js/test_beerfill.js`
Expected: FAIL — `mmFoamWaveY` etc. undefined.

- [ ] **Step 3: Implement** — add to `js/transitions.js`:

```javascript
  function mmFoamWaveY(xFrac, t, amp, baseY) {
    return baseY
      + Math.sin(xFrac * 15.0 + t * 9.4) * amp * 0.5
      + Math.sin(xFrac * 41.0 - t * 6.3) * amp * 0.3;
  }
  function mmBeerBubbles(seed, count) {
    var rnd = _mmLcg(seed >>> 0), arr = [], i;
    for (i = 0; i < count; i++) {
      arr.push({ x: rnd(), phase: rnd(), r: 1 + rnd() * 2.4, spd: 0.45 + rnd() * 0.8 });
    }
    return arr;
  }
  function mmFoamBubbles(seed, count) {
    var rnd = _mmLcg(((seed >>> 0) ^ 0x9e3779b9) >>> 0), arr = [], i;
    for (i = 0; i < count; i++) {
      arr.push({ x: rnd(), yf: rnd(), r: 1 + rnd() * 3.2, a: 0.22 + rnd() * 0.4 });
    }
    return arr;
  }
```

Add exports:

```javascript
  root.mmFoamWaveY = mmFoamWaveY;
  root.mmBeerBubbles = mmBeerBubbles;
  root.mmFoamBubbles = mmFoamBubbles;
```

- [ ] **Step 4: Run to verify pass**

Run: `node --test tests/unit/js/test_beerfill.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_beerfill.js
git commit -m "feat(transitions): beerfill foam-wave + seeded bubble helpers"
```

---

### Task 4: `_dur` role-awareness + `mmTransitionState` beerfill branch

**Files:**
- Modify: `js/transitions.js` (`_dur` at line 6; `mmTransitionState` at lines 88–119)
- Test: `tests/unit/js/test_beerfill.js` (append)

**Interfaces:**
- Consumes: `mmBeerPhase`, `mmBeerLevel`, `mmBeerDuration` (Task 2).
- Produces: for a `beerfill` effect, `mmTransitionState(...)` returns `{role, opacity:1, wipe:null, effect:{name:'beerfill', family:'mask', front:<beer level>, scope, params, phase:'fill'|'drain'}}`. `front` is the beer **coverage level** (already direction-resolved), not raw progress. Transition activates using `fillMs` (end) / `drainMs` (start).

- [ ] **Step 1: Write the failing test** — append to `tests/unit/js/test_beerfill.js`:

```javascript
const State = globalThis.mmTransitionState;

test('mmTransitionState: beerfill end-role = fill phase, level rises', () => {
  const end = { name: 'beerfill', params: { fillMs: 2000, drainMs: 2000, scope: 'wall' } };
  // duration 6000, offset 5000 -> 1000ms into the 2000ms fill (out), progress p=0.5 -> level 0.5
  const st = State(null, end, 5000, 6000, null, null);
  assert.equal(st.role, 'out');
  assert.equal(st.effect.name, 'beerfill');
  assert.equal(st.effect.family, 'mask');
  assert.equal(st.effect.phase, 'fill');
  assert.ok(Math.abs(st.effect.front - 0.5) < 1e-9);   // fill: level == progress
  assert.equal(st.effect.scope, 'wall');
});

test('mmTransitionState: beerfill start-role = drain phase, level falls', () => {
  const start = { name: 'beerfill', params: { fillMs: 2000, drainMs: 2000 } };
  // offset 500 -> p=0.25 into drain -> level 1-0.25 = 0.75
  const st = State(start, null, 500, 6000, null, null);
  assert.equal(st.role, 'in');
  assert.equal(st.effect.phase, 'drain');
  assert.ok(Math.abs(st.effect.front - 0.75) < 1e-9);
  assert.equal(st.effect.scope, 'wall');               // default when unset
});

test('mmTransitionState: beerfill inactive mid-item', () => {
  const end = { name: 'beerfill', params: { fillMs: 2000, drainMs: 2000 } };
  assert.equal(State(null, end, 1000, 6000, null, null).role, 'none');  // 1000 < 6000-2000
});
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test tests/unit/js/test_beerfill.js`
Expected: FAIL — beerfill returns the generic fade branch (`opacity:p`, no `effect`), or `_dur` returns 0 so role is `none`.

- [ ] **Step 3: Implement** — change `_dur` (line 6) to be role-aware:

```javascript
  function _dur(eff, role) {
    if (!eff || !eff.params) { return 0; }
    if (eff.name === 'beerfill') { return mmBeerDuration(eff.params, role); }
    return (+eff.params.duration) || 0;
  }
```

Update the two call sites in `mmTransitionState` (line 88):

```javascript
    var sd = _dur(startEff, 'in'), ed = _dur(endEff, 'out'), role = 'none', eff = null, p = 1;
```

Add a `beerfill` branch BEFORE the `slide/zoom/iris/dissolve` branch (after the `wipe` branch, ~line 113):

```javascript
    if (eff.name === 'beerfill') {
      var bsc = (eff.params && eff.params.scope) || 'wall';
      var phase = mmBeerPhase(role === 'out' ? 'out' : 'in');
      return { role: role, opacity: 1, wipe: null,
               effect: { name: 'beerfill', family: 'mask', front: mmBeerLevel(phase, p),
                         scope: bsc, params: eff.params || {}, phase: phase } };
    }
```

(Note: `mmBeerPhase` takes `'out'`/`'in'`; `role` here is already `'out'`/`'in'`, so `mmBeerPhase(role)` is equivalent — keep it explicit for clarity.)

- [ ] **Step 4: Run to verify pass**

Run: `node --test tests/unit/js/test_beerfill.js`
Expected: PASS.

- [ ] **Step 5: Run the existing transition tests (no regressions)**

Run: `node --test tests/unit/js/test_transitions.js tests/unit/js/transition-effects.test.js`
Expected: PASS (the `_dur` signature change is backward-compatible — the 2nd arg is ignored for non-beerfill).

- [ ] **Step 6: Commit**

```bash
git add js/transitions.js tests/unit/js/test_beerfill.js
git commit -m "feat(transitions): mmTransitionState beerfill branch (mask, role->phase/level)"
```

---

### Task 5: `mmDrawBeer` canvas draw function

**Files:**
- Modify: `js/transitions.js` (add `mmDrawBeer` + export)
- Test: `tests/unit/js/test_beerfill.js` (append; uses an inline recording 2D-context stub)

**Interfaces:**
- Consumes: `mmBeerPalette`, `mmBeerBubbles`, `mmFoamBubbles`, `mmFoamWaveY` (Tasks 2–3), `_mmMaskRegion` (existing).
- Produces: `mmDrawBeer(ctx, params, phase, level, t, GW, GH, quad, scope, seed)` — draws opaque beer covering the bottom `level` fraction of the region (`_mmMaskRegion(scope, quad, GW, GH)`): gradient body, rising bubbles, wavy foam head, scattered foam bubbles, and (when `phase==='fill'`) a pour stream from the region top. No-op when `level <= 0`. Uses only `fillRect`, `arc`+`fill`, polyline path, `createLinearGradient`.

- [ ] **Step 1: Write the failing test** — append to `tests/unit/js/test_beerfill.js`:

```javascript
const Beer = globalThis.mmDrawBeer;

function recCtx() {
  return {
    rects: [], fills: 0, arcs: 0, _grad: { addColorStop() {} },
    fillStyle: '#000', _started: false,
    createLinearGradient() { return this._grad; },
    fillRect(x, y, w, h) { this.rects.push({ x, y, w, h }); },
    beginPath() {}, moveTo() {}, lineTo() {}, closePath() {},
    arc() { this.arcs++; }, fill() { this.fills++; }
  };
}

test('mmDrawBeer: level 0 draws nothing', () => {
  const c = recCtx();
  Beer(c, { beerType: 'pale' }, 'fill', 0, 0, 300, 200, null, 'wall', 1);
  assert.equal(c.rects.length, 0);
});

test('mmDrawBeer: fill draws beer body covering bottom level fraction + pour', () => {
  const c = recCtx();
  Beer(c, { beerType: 'pale' }, 'fill', 0.5, 0, 300, 200, null, 'wall', 1);
  // beer body: a rect whose top is at y=100 (half of 200), height ~100
  const body = c.rects.find(r => Math.abs(r.y - 100) < 1 && Math.abs(r.h - 100) < 1 && r.w === 300);
  assert.ok(body, 'beer body rect present');
  // pour stream present in fill phase: a narrow rect starting at region top (y=0)
  assert.ok(c.rects.some(r => r.y === 0 && r.w < 300 * 0.5), 'pour stream present');
  assert.ok(c.arcs > 0, 'bubbles drawn');
});

test('mmDrawBeer: drain phase draws no pour stream', () => {
  const c = recCtx();
  Beer(c, { beerType: 'pale' }, 'drain', 0.5, 0, 300, 200, null, 'wall', 1);
  assert.ok(!c.rects.some(r => r.y === 0), 'no pour stream rect at region top');
});
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test tests/unit/js/test_beerfill.js`
Expected: FAIL — `mmDrawBeer` undefined.

- [ ] **Step 3: Implement** — add to `js/transitions.js`:

```javascript
  // Draw opaque beer covering the bottom `level` fraction of the region.
  // phase 'fill' adds a pour stream from the region top. Used both in-canvas
  // (mesh SCRIPT, drawn in global coords then warped) and on the overlay (media).
  // ctx primitives only: fillRect / arc+fill / polyline / linear gradient. No clip.
  function mmDrawBeer(ctx, params, phase, level, t, GW, GH, quad, scope, seed) {
    var lv = level < 0 ? 0 : (level > 1 ? 1 : level);
    if (lv <= 0) { return; }
    var pal = mmBeerPalette(params && params.beerType);
    var reg = _mmMaskRegion(scope, quad, GW, GH);
    var beerH = lv * reg.h, bottom = reg.y + reg.h, surfaceY = bottom - beerH;
    var ts = t * 0.001;   // ms -> s-ish for wave/bubble motion

    // beer body (vertical gradient)
    var g = ctx.createLinearGradient(0, surfaceY, 0, bottom);
    g.addColorStop(0, pal.beerTop); g.addColorStop(1, pal.beerBot);
    ctx.fillStyle = g; ctx.fillRect(reg.x, surfaceY, reg.w, beerH);

    // rising bubbles inside the beer (seeded; identical across screens for a wall)
    var bubs = mmBeerBubbles(seed >>> 0, pal.bubbleDensity), i, by;
    ctx.fillStyle = 'rgba(255,255,255,0.55)';
    for (i = 0; i < bubs.length; i++) {
      by = bottom - (((bubs[i].phase + ts * bubs[i].spd * 0.35) % 1) * beerH);
      if (by < surfaceY + 4) { continue; }
      ctx.beginPath(); ctx.arc(reg.x + bubs[i].x * reg.w, by, bubs[i].r, 0, 6.2832); ctx.fill();
    }

    // foam head with a wavy top edge (one polyline path)
    var fh = reg.h * pal.headH, topBase = surfaceY - fh;
    var amp = fh * 0.4 < 2.5 ? 2.5 : fh * 0.4, steps = 60, s, sx;
    ctx.fillStyle = pal.foam;
    ctx.beginPath(); ctx.moveTo(reg.x, surfaceY + 2); ctx.lineTo(reg.x, topBase);
    for (s = 0; s <= steps; s++) {
      sx = s / steps;
      ctx.lineTo(reg.x + sx * reg.w, mmFoamWaveY(sx, ts, amp, topBase));
    }
    ctx.lineTo(reg.x + reg.w, surfaceY + 2); ctx.closePath(); ctx.fill();

    // scattered foam bubbles
    var fbs = mmFoamBubbles(seed >>> 0, pal.foamBubbles), k, f;
    for (k = 0; k < fbs.length; k++) {
      f = fbs[k];
      ctx.fillStyle = 'rgba(255,255,255,' + f.a + ')';
      ctx.beginPath(); ctx.arc(reg.x + f.x * reg.w, topBase + f.yf * fh, f.r, 0, 6.2832); ctx.fill();
    }

    // pour stream from the region top (fill phase only)
    if (phase === 'fill') {
      var pw = reg.w * 0.10, px = reg.x + reg.w / 2, ph = topBase - reg.y;
      if (ph < 0) { ph = 0; }
      ctx.fillStyle = pal.beerTop; ctx.fillRect(px - pw / 2, reg.y, pw, ph);
      ctx.fillStyle = 'rgba(255,255,255,0.18)'; ctx.fillRect(px - pw * 0.18, reg.y, pw * 0.36, ph);
    }
  }
```

Add export:

```javascript
  root.mmDrawBeer = mmDrawBeer;
```

- [ ] **Step 4: Run to verify pass**

Run: `node --test tests/unit/js/test_beerfill.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_beerfill.js
git commit -m "feat(transitions): mmDrawBeer cover draw (gradient body, foam, bubbles, pour)"
```

---

### Task 6: Wire `beerfill` into `index.html` (mesh in-canvas + overlay + cover gate)

**Files:**
- Modify: `index.html` (`_cvEff` gate ~line 938; mesh mask branch ~lines 651–662; `applyTransitionNow` mask branch ~lines 1039–1055)

**Interfaces:**
- Consumes: `mmDrawBeer` (Task 5), `mmTransitionState` beerfill descriptor (`st.effect.{name,phase,front,scope,params}`) (Task 4).
- Produces: beerfill renders on mesh SCRIPT items (in-canvas, global coords → warped) and on media/element items (overlay cover). No new exports.

This task is wiring of already-tested helpers; verification is the iPad-1 sign-off in Task 7 (no unit test — these are DOM/canvas glue paths the node suite can't exercise).

- [ ] **Step 1: Extend the cover gate** — `index.html` ~line 938, add `beerfill` so media items get an overlay cover:

```javascript
		var _cvEff = function (x) { return x && (x.name === 'wipe' || x.name === 'iris' || x.name === 'dissolve' || x.name === 'beerfill'); };
```

- [ ] **Step 2: Mesh in-canvas branch** — in `runScriptLoop`, inside `if (stc.effect && stc.effect.family === 'mask') {` (~line 651), add a `beerfill` case before the generic `mmDrawMaskInCanvas` fallback. The existing structure is `if (name==='iris') {...} else if (mmDrawMaskInCanvas) {...}`; insert:

```javascript
								} else if (stc.effect.name === 'beerfill' && typeof mmDrawBeer === 'function') {
									mmDrawBeer(ctx, stc.effect.params, stc.effect.phase, stc.effect.front,
										GoTime.now(), it.meshGlobal[0], it.meshGlobal[1], it.meshQuad,
										stc.effect.scope, playback.seed | 0);
								} else if (typeof mmDrawMaskInCanvas === 'function') {
```

(Use the same `it.meshGlobal[0]`/`[1]` and `it.meshQuad` the iris branch on line 656 uses; `GoTime.now()` is the shared clock already used elsewhere in this file.)

- [ ] **Step 3: Overlay branch** — in `applyTransitionNow`, inside the `st.effect.family === 'mask'` block (~line 1039) that currently calls `mmDrawMaskOverlay`, branch on `beerfill` first:

```javascript
				if (st.effect.name === 'beerfill' && typeof mmDrawBeer === 'function') {
					mmDrawBeer(cmx, st.effect.params, st.effect.phase, st.effect.front,
						GoTime.now(), GWm, GHm, quad, st.effect.scope, playback.seed | 0);
				} else {
					mmDrawMaskOverlay(cmx, st.effect.name, st.effect.params, st.effect.front,
						GWm, GHm, quad, st.effect.scope, playback.seed | 0, item.backgroundColor || '#000000');
				}
```

(Match the exact context-variable names already in that block — `cmx`, `GWm`, `GHm`, `quad`. Read lines 1039–1055 and reuse them verbatim.)

- [ ] **Step 4: Syntax sanity check (no ES6 crept in)**

Run: `node -e "require('fs').readFileSync('index.html','utf8'); console.log('read ok')"` then visually confirm the three edits use `var`/`function` only (no `let`/`const`/arrows).
Expected: prints `read ok`; edits are ES5.

- [ ] **Step 5: Commit**

```bash
git add index.html
git commit -m "feat(transitions): wire beerfill into mesh in-canvas + overlay cover paths"
```

---

### Task 7: Demo playlist + iPad-1 on-wall sign-off

**Files:**
- Create: `tools/_make_beer_demo.py` (throwaway sender, mirrors `tools/_make_transition_demo.py`)

**Interfaces:**
- Consumes: the running server's `SAVE_PLAYLIST` SockJS handler (PAYLOAD `{name, items, loop}`); item schema `{id, file, playmode:'SCRIPT', scriptSpan:'mesh', duration, backgroundColor, startEffect, endEffect}` with effects `{name:'beerfill', params:{beerType, scope, fillMs, drainMs, audioFade}}`.
- Produces: a "Beer Demo" playlist with two `plasma` mesh items, each handing off via `beerfill` (item A `endEffect`=fill, item B `startEffect`=drain), `scope:'wall'`, `beerType:'pale'`.

- [ ] **Step 1: Create the demo sender** — `tools/_make_beer_demo.py`:

```python
"""Create a 'Beer Demo' playlist: two plasma mesh items handing off via the
beerfill transition (fill out of A, drain into B). Run with the server up."""
import asyncio, json, aiohttp
HOST = "127.0.0.1:3000"

def bf():
    return {"name": "beerfill",
            "params": {"beerType": "pale", "scope": "wall",
                       "fillMs": 2500, "drainMs": 2500, "audioFade": True}}

ITEMS = [
    {"id": "beer-a", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 8, "backgroundColor": "#000000", "startEffect": None, "endEffect": bf()},
    {"id": "beer-b", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 8, "backgroundColor": "#000000", "startEffect": bf(), "endEffect": None},
]

async def main():
    url = "http://%s/sockjs/000/claudecmd/websocket" % HOST
    p = {"SRC": "claude-admin", "DEST": "SRV", "REQUEST": "SAVE_PLAYLIST",
         "PAYLOAD": {"name": "Beer Demo", "items": ITEMS, "loop": True}}
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url) as ws:
            await ws.send_str(json.dumps([json.dumps(p)]))
            print("sent Beer Demo")
            try:
                for _ in range(6):
                    m = await asyncio.wait_for(ws.receive(), timeout=2.0)
                    if "SAVE_PLAYLIST" in str(m.data): print("recv:", str(m.data)[:140])
            except asyncio.TimeoutError:
                pass

if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run it (server must be up) and confirm the playlist saved**

Run: `python tools/_make_beer_demo.py`
Then: `curl -s http://localhost:3000/api/playlists` and confirm a `Beer Demo` entry with 2 items, `endEffect`/`startEffect` named `beerfill`.
Expected: `recv: ...SAVE_PLAYLIST...SUCCESS` and the playlist present.

- [ ] **Step 3: On-wall sign-off (manual, requires operator)**

Assign **Beer Demo** to the calibrated "OEB Sign 1" group and Play. Verify on the physical sign:
- Item A → B: beer fills bottom-up **across the whole sign** (lower screens first), wide pour from the top, wavy foam line sweeping up, bubbles rising; screen ends full of beer.
- Then drains down revealing the next plasma (top-first as the level drops), no pour during drain.
- Smooth, no flicker; fill ≈ 2.5s, drain ≈ 2.5s.
- Try `scope:'screen'` (edit the sender) → each screen pours/fills independently.

Expected: reads unmistakably as a pour-and-empty beer wipe across the mesh.

- [ ] **Step 4: Commit the demo tool (optional)**

```bash
git add tools/_make_beer_demo.py
git commit -m "chore(transitions): beer-fill demo playlist sender"
```

---

## Notes for the implementer

- `playback.seed` is the shared per-run seed already threaded into SCRIPT items; passing it to `mmBeerBubbles`/`mmFoamBubbles` makes every screen draw identical bubble layouts so the wall reads as one coherent glass. Do not substitute a per-screen or time-based seed.
- The wall-coordinated sweep needs **no special code**: mesh SCRIPT content (and the beer drawn on top of it in `runScriptLoop`) is rendered in global wall coordinates and warped to each screen by `mmMeshTransform`, so filling the bottom `level` fraction of the *global* region automatically staggers across screens.
- If `meshGlobal`/`meshQuad` are absent (uncalibrated), `_mmMaskRegion` falls back to the full canvas and beerfill still fills that screen bottom-up — degraded but not broken.
