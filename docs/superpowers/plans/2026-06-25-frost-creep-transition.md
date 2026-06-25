# Frost Creep Transition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a `frostcreep` transition — frost spreads across the region via a spatially-correlated seeded noise field under a rising coverage threshold (soft growing blotches), then recedes on reveal.

**Architecture:** Mask-family effect riding the existing additive `effect` descriptor + in-canvas/overlay dispatch (same as beerfill/scatter). Pure, node-tested core (`mmFrostField` noise + `mmFrostBlotch` growth + `mmFrostPhase`) plus a thin `mmDrawFrost` drawer. Fully procedural — no sprite. Single `duration` param. The proven Wipe path is untouched.

**Tech Stack:** Python (`effects.py`, pytest), ES5 JavaScript (`js/transitions.js`, node `--test`), `index.html` (ES5 client glue), aiohttp (demo tool).

## Global Constraints

- **Display-client JS is ES5 ONLY** (`js/transitions.js`, `index.html` inline scripts): no `let`/`const`/arrow/template-literal/`class`/`Promise`/`fetch`. `var`/`function` only. (Node `--test` files may use modern JS.)
- **Canvas ops allowed on Safari 5.1:** `arc`+`fill`, `fillRect`, `globalAlpha`/rgba fill. **No** `clip()`, **no** `destination-*` compositing, **no** CSS filters.
- **Do NOT modify the Wipe path** (`mmWipeCoverRect` / `st.wipe`) or any existing effect branch — frostcreep is purely additive.
- Run tests via the runner, never bare `pytest`: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -v` (pytest.ini lives in `tests/`); full unit `python pytest_runner.py --unit`; JS `node --test tests/unit/js/<file>` or `python pytest_runner.py --js`.
- **Single `duration` schema** (matches kegroll + consolidated beerfill): a frostcreep instance only ever covers (endEffect) or reveals (startEffect).
- **Commit trailer on EVERY commit (exact, incl. the parenthetical):**
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- Coverage convention: `mmTransitionState` computes `cover ∈ [0,1]` (local progress for `cover` phase, `1 − local progress` for `reveal`) and passes it as `effect.front`. A cell frosts when `field[c] ≤ cover`.

---

### Task 1: `effects.py` — `FrostCreepEffect` catalog entry

**Files:**
- Modify: `effects.py` (append a `@register` class after `KegRollEffect`)
- Test: `tests/unit/test_effects.py`

**Interfaces:**
- Consumes: `Effect`, `ParamSpec`, `register`, `_afade` (existing).
- Produces: `FrostCreepEffect` with `name = "frostcreep"`; appears in `effect_catalog()`; `video_filters` returns `([], _afade(role, params, ctx))` (single `duration`, audio-only). Catalog name set gains `kegroll`'s sibling `frostcreep`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_effects.py`:

```python
def test_catalog_includes_frostcreep():
    names = {e["name"] for e in effects.effect_catalog()}
    assert "frostcreep" in names


def test_frostcreep_params():
    e = next(e for e in effects.effect_catalog() if e["name"] == "frostcreep")
    by = {p["key"]: p for p in e["params"]}
    assert by["tint"]["choices"] == ["frost", "blue", "clear"] and by["tint"]["default"] == "frost"
    assert by["scope"]["choices"] == ["screen", "wall"] and by["scope"]["default"] == "wall"
    assert by["duration"]["type"] == "number" and by["duration"]["default"] == 2200
    assert by["audioFade"]["type"] == "boolean" and by["audioFade"]["default"] is True


def test_frostcreep_audio_single_duration():
    fc = effects.get_effect("frostcreep")
    ctx = {"duration_ms": 6000}
    v, a = fc.video_filters("start", fc.resolve({"duration": 2000, "audioFade": True}), ctx)
    assert v == [] and a == ["afade=t=in:st=0:d=2"]
    v2, a2 = fc.video_filters("end", fc.resolve({"duration": 2000, "audioFade": True}), ctx)
    assert v2 == [] and a2 == ["afade=t=out:st=4:d=2"]
    v3, a3 = fc.video_filters("end", fc.resolve({"audioFade": False}), ctx)
    assert v3 == [] and a3 == []
```

Update the exhaustive catalog assertion `test_catalog_has_all_effects`:

```python
def test_catalog_has_all_effects():
    names = {e["name"] for e in effects.effect_catalog()}
    assert names == {"fade", "wipe", "slide", "zoom", "iris", "dissolve",
                     "beerfill", "scatter", "kegroll", "frostcreep"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -v -k "frostcreep or all_effects"`
Expected: FAIL — `frostcreep` not in catalog; `get_effect("frostcreep")` is `None`.

- [ ] **Step 3: Add the effect class**

Append to `effects.py` after `KegRollEffect`:

```python
@register
class FrostCreepEffect(Effect):
    name = "frostcreep"
    label = "Frost Creep"
    # Single `duration`: a frostcreep instance only covers (endEffect) or reveals
    # (startEffect), never both.
    params = [ParamSpec("tint", "choice", "frost", choices=["frost", "blue", "clear"]),
              ParamSpec("scope", "choice", "wall", choices=["screen", "wall"]),
              ParamSpec("duration", "number", 2200, minimum=0),
              ParamSpec("audioFade", "boolean", True)]

    def video_filters(self, role, params, ctx):
        return ([], _afade(role, params, ctx))     # visual is client-side; single duration
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -v`
Expected: PASS (all effects tests, incl. the 3 new + updated exhaustive set).

- [ ] **Step 5: Commit**

```bash
git add effects.py tests/unit/test_effects.py
git commit -m "feat(effects): frostcreep catalog entry (tint/scope/duration)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Pure helpers in `js/transitions.js`

**Files:**
- Modify: `js/transitions.js` (add 3 functions near the keg helpers; add 3 exports in the `root.*` block)
- Test: `tests/unit/js/test_frostcreep.js` (new)

**Interfaces:**
- Consumes: `_mmLcg(seed)` → a function returning `[0,1)` (existing private in `transitions.js`).
- Produces (exported on `root`):
  - `mmFrostPhase(role)` → `'cover'` when `role === 'out'`, else `'reveal'`.
  - `mmFrostField(blocks, seed)` → Array of `blocks*blocks` numbers in `[0,1)`; spatially correlated (2 box-blur passes), renormalized to `[0, 0.98)`; flat field → all zeros. Deterministic per seed.
  - `mmFrostBlotch(fieldVal, cover, grow)` → `{ on: bool, t: number }`. `on = cover >= fieldVal`; `t = clamp((cover-fieldVal)/grow, 0, 1)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/js/test_frostcreep.js`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/transitions.js');
const g = globalThis;

test('mmFrostPhase: out=cover, in=reveal', () => {
  assert.equal(g.mmFrostPhase('out'), 'cover');
  assert.equal(g.mmFrostPhase('in'), 'reveal');
});

test('mmFrostField: deterministic per seed, values in [0,1), right length', () => {
  const a = g.mmFrostField(12, 99), b = g.mmFrostField(12, 99), c = g.mmFrostField(12, 100);
  assert.deepEqual(a, b);                 // same seed -> identical (wall-coherent)
  assert.notDeepEqual(a, c);              // different seed -> different
  assert.equal(a.length, 144);
  a.forEach(v => assert.ok(v >= 0 && v < 1, 'value in [0,1): ' + v));
});

test('mmFrostField: spatially correlated (smoother than random pairs)', () => {
  const blocks = 16, field = g.mmFrostField(blocks, 12345);
  let adjSum = 0, adjN = 0, r, c;
  for (r = 0; r < blocks; r++) for (c = 0; c < blocks - 1; c++) {
    adjSum += Math.abs(field[r * blocks + c] - field[r * blocks + c + 1]); adjN++;
  }
  const adjMean = adjSum / adjN;
  let rndSum = 0, rndN = 0, i;
  for (i = 0; i < 500; i++) {
    rndSum += Math.abs(field[(i * 7) % field.length] - field[(i * 13 + 3) % field.length]); rndN++;
  }
  const rndMean = rndSum / rndN;
  // smoothing -> adjacent cells much closer than arbitrary pairs
  assert.ok(adjMean < rndMean * 0.8, 'adjacent ' + adjMean + ' should be < 0.8 * random ' + rndMean);
});

test('mmFrostBlotch: off below threshold, grows 0->1 above (clamped)', () => {
  assert.deepEqual(g.mmFrostBlotch(0.5, 0.4, 0.25), { on: false, t: 0 });
  let b = g.mmFrostBlotch(0.5, 0.5, 0.25); assert.ok(b.on && Math.abs(b.t - 0) < 1e-9);
  b = g.mmFrostBlotch(0.5, 0.625, 0.25); assert.ok(Math.abs(b.t - 0.5) < 1e-9);   // halfway through grow
  b = g.mmFrostBlotch(0.5, 0.95, 0.25);  assert.ok(b.on && Math.abs(b.t - 1) < 1e-9);  // clamped
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/unit/js/test_frostcreep.js`
Expected: FAIL — `mmFrostPhase`/`mmFrostField`/`mmFrostBlotch` not functions.

- [ ] **Step 3: Add the helpers**

In `js/transitions.js`, add after the keg-roll helpers (after `mmKegAngle`/`_kegAxisOffset`, before the `root.*` export block):

```javascript
  // --- Frost creep (mask family): a spatially-correlated seeded noise field thresholded
  // by a rising coverage front; soft growing blotches. Pure. ---
  function mmFrostPhase(role) { return role === 'out' ? 'cover' : 'reveal'; }

  // blocks*blocks thresholds in [0, 0.98), precomputed: seeded per-cell randoms ->
  // 2 box-blur passes (4-neighbour avg, edge-clamped) for spatial correlation (frost
  // PATCHES, not speckle) -> renormalize to [0, 0.98) (strictly < 1 so every cell frosts
  // before cover hits 1; the consolidation fill then guarantees full opacity). Pure.
  function mmFrostField(blocks, seed) {
    var n = blocks * blocks, rnd = _mmLcg(seed >>> 0), raw = [], i, pass, out, r, c, idx, sum, cnt;
    for (i = 0; i < n; i++) { raw.push(rnd()); }
    for (pass = 0; pass < 2; pass++) {
      out = [];
      for (r = 0; r < blocks; r++) {
        for (c = 0; c < blocks; c++) {
          idx = r * blocks + c; sum = raw[idx]; cnt = 1;
          if (c > 0)          { sum += raw[idx - 1]; cnt++; }
          if (c < blocks - 1) { sum += raw[idx + 1]; cnt++; }
          if (r > 0)          { sum += raw[idx - blocks]; cnt++; }
          if (r < blocks - 1) { sum += raw[idx + blocks]; cnt++; }
          out.push(sum / cnt);
        }
      }
      raw = out;
    }
    var mn = raw[0], mx = raw[0];
    for (i = 1; i < n; i++) { if (raw[i] < mn) { mn = raw[i]; } if (raw[i] > mx) { mx = raw[i]; } }
    var span = mx - mn;
    if (span < 1e-9) { for (i = 0; i < n; i++) { raw[i] = 0; } return raw; }
    for (i = 0; i < n; i++) { raw[i] = ((raw[i] - mn) / span) * 0.98; }
    return raw;
  }

  // Per-cell frost growth from the rising coverage front. on once cover reaches the
  // cell's threshold; t ramps 0->1 over the `grow` window after crossing. Pure.
  function mmFrostBlotch(fieldVal, cover, grow) {
    if (cover < fieldVal) { return { on: false, t: 0 }; }
    var g = grow > 0 ? grow : 0.25;
    var t = (cover - fieldVal) / g;
    if (t < 0) { t = 0; } else if (t > 1) { t = 1; }
    return { on: true, t: t };
  }
```

Add exports in the `root.*` block (next to the other transition exports):

```javascript
  root.mmFrostPhase = mmFrostPhase;
  root.mmFrostField = mmFrostField;
  root.mmFrostBlotch = mmFrostBlotch;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/unit/js/test_frostcreep.js`
Expected: PASS (all 4 tests).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_frostcreep.js
git commit -m "feat(transitions): frost-creep pure helpers (field/blotch/phase)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `mmTransitionState` — `frostcreep` branch

**Files:**
- Modify: `js/transitions.js` (`mmTransitionState`, add a branch after the `kegroll` branch, before the `slide/zoom/iris/dissolve` branch)
- Test: `tests/unit/js/test_frostcreep.js` (append)

**Interfaces:**
- Consumes: `mmFrostPhase` (Task 2). `_dur` already returns `(+eff.params.duration) || 0` for any effect not named `beerfill`/`scatter`, so frostcreep's single `duration` is honored with **no `_dur` change**.
- Produces: for `eff.name === 'frostcreep'`, returns `{ role, opacity:1, wipe:null, effect: { name:'frostcreep', family:'mask', front: cover, scope, params, phase } }` where `cover` is local progress on the cover phase and `1 − local progress` on the reveal phase.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/js/test_frostcreep.js`:

```javascript
test('mmTransitionState: frostcreep end=cover (rises), start=reveal (mask family)', () => {
  const S = g.mmTransitionState;
  const end = { name: 'frostcreep', params: { duration: 2000, scope: 'wall' } };
  // end window [4000,6000]; offset 4500 -> p=(6000-4500)/2000=0.75 -> flp=1-p=0.25 -> cover=0.25
  let st = S(null, end, 4500, 6000, null, null);
  assert.equal(st.role, 'out');
  assert.equal(st.effect.name, 'frostcreep');
  assert.equal(st.effect.family, 'mask');
  assert.equal(st.effect.phase, 'cover');
  assert.ok(Math.abs(st.effect.front - 0.25) < 1e-9);
  assert.equal(st.effect.scope, 'wall');
  // later in the window -> MORE frost (rises): offset 5500 -> p=0.25 -> flp=0.75 -> cover=0.75
  st = S(null, end, 5500, 6000, null, null);
  assert.ok(Math.abs(st.effect.front - 0.75) < 1e-9);
  // start role (reveal): offset 500 -> p=0.25 -> flp=0.25 -> cover=1-0.25=0.75
  const start = { name: 'frostcreep', params: { duration: 2000 } };
  st = S(start, null, 500, 6000, null, null);
  assert.equal(st.effect.phase, 'reveal');
  assert.ok(Math.abs(st.effect.front - 0.75) < 1e-9);
  assert.equal(st.effect.scope, 'wall');     // default
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/unit/js/test_frostcreep.js`
Expected: FAIL — `st.effect` undefined for `frostcreep` (falls through to the fade return).

- [ ] **Step 3: Add the branch**

In `js/transitions.js`, insert after the `kegroll` branch (before `if (eff.name === 'slide' || ...)`):

```javascript
    if (eff.name === 'frostcreep') {
      var frsc = (eff.params && eff.params.scope) || 'wall';
      var frlp = (role === 'out') ? (1 - p) : p;       // LOCAL phase progress 0->1
      var frph = mmFrostPhase(role);
      var frcov = (frph === 'cover') ? frlp : (1 - frlp);   // coverage front: cover rises, reveal falls
      return { role: role, opacity: 1, wipe: null,
               effect: { name: 'frostcreep', family: 'mask', front: frcov,
                         scope: frsc, params: eff.params || {}, phase: frph } };
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/unit/js/test_frostcreep.js && node --test tests/unit/js/test_transitions.js`
Expected: PASS — new frostcreep branch test passes; existing transition tests stay green.

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_frostcreep.js
git commit -m "feat(transitions): mmTransitionState frostcreep mask-family descriptor

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `mmDrawFrost` drawer + tint palette

**Files:**
- Modify: `js/transitions.js` (add `_FROST` palette + `mmFrostPalette` + `mmDrawFrost` after `mmDrawKegRoll`; export `mmFrostPalette` + `mmDrawFrost`)
- Test: `tests/unit/js/test_frostcreep.js` (append)

**Interfaces:**
- Consumes: `_mmMaskRegion(scope, quad, GW, GH)` (existing private), `mmFrostField`/`mmFrostBlotch` (Task 2). Reads optional `root._mmFrostDbg.blocks` (the `?frostblocks` knob, wired in Task 5) and uses/maintains `root._mmFrostCache` for field memoization.
- Produces: `mmDrawFrost(ctx, params, phase, cover, GW, GH, quad, scope, seed)` — draws frost blotches for frozen cells + a consolidation fill near full cover. `mmFrostPalette(tint)` → `{core, spark}` rgb strings. No `t` arg (frost is time-independent). Exported on `root`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/js/test_frostcreep.js`:

```javascript
function recCtxFrost() {
  return { rects: [], arcs: 0, fillStyle: '#000',
    beginPath() {}, arc() { this.arcs++; }, fill() {},
    fillRect(x, y, w, h) { this.rects.push({ x: x, y: y, w: w, h: h }); } };
}

test('mmFrostPalette: known tints + default', () => {
  assert.ok(g.mmFrostPalette('frost').core);
  assert.ok(g.mmFrostPalette('blue').core);
  assert.equal(g.mmFrostPalette('nope').core, g.mmFrostPalette('frost').core);   // unknown -> frost
});

test('mmDrawFrost: nothing at cover 0', () => {
  const c = recCtxFrost();
  g.mmDrawFrost(c, { tint: 'frost' }, 'cover', 0, 300, 200, null, 'wall', 5);
  assert.equal(c.arcs, 0);
  assert.equal(c.rects.length, 0);
});

test('mmDrawFrost: blotches mid-cover, consolidation fill near full', () => {
  let c = recCtxFrost();
  g.mmDrawFrost(c, { tint: 'frost' }, 'cover', 0.5, 300, 200, null, 'wall', 5);
  assert.ok(c.arcs > 0, 'blotches drawn mid-cover');
  c = recCtxFrost();
  g.mmDrawFrost(c, { tint: 'frost' }, 'cover', 1, 300, 200, null, 'wall', 5);
  assert.ok(c.rects.some(r => r.x === 0 && r.y === 0 && r.w === 300 && r.h === 200),
            'full-region consolidation fill present at cover 1');
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/unit/js/test_frostcreep.js`
Expected: FAIL — `mmFrostPalette`/`mmDrawFrost` not functions.

- [ ] **Step 3: Add the palette + drawer**

In `js/transitions.js`, add after `mmDrawKegRoll` (before the `root.*` export block):

```javascript
  var _FROST = {
    frost: { core: '244,250,255', spark: '255,255,255' },
    blue:  { core: '208,230,247', spark: '236,247,255' },
    clear: { core: '224,239,250', spark: '255,255,255' }
  };
  function mmFrostPalette(tint) { return _FROST[tint] || _FROST.frost; }

  // Draw frost: soft growing blotches for cells whose seeded threshold the coverage
  // front has crossed, + a consolidation fill near full cover so the outgoing item is
  // fully hidden at the handoff. arc/fillRect only; no clip/composite. Drawn in global
  // coords under the mesh affine (in-canvas) or overlay matrix -> wall-coherent. The
  // field is memoized on root._mmFrostCache (seed,blocks). ?frostblocks=N tunes density.
  function mmDrawFrost(ctx, params, phase, cover, GW, GH, quad, scope, seed) {
    var cov = cover < 0 ? 0 : (cover > 1 ? 1 : cover);
    if (cov <= 0) { return; }
    var reg = _mmMaskRegion(scope, quad, GW, GH);
    var fdbg = root._mmFrostDbg || {};
    var blocks = (fdbg.blocks > 0) ? fdbg.blocks : 18;
    var sk = seed >>> 0, cache = root._mmFrostCache;
    if (!cache || cache.seed !== sk || cache.blocks !== blocks) {
      cache = { seed: sk, blocks: blocks, field: mmFrostField(blocks, sk) };
      root._mmFrostCache = cache;
    }
    var field = cache.field, pal = mmFrostPalette(params && params.tint);
    var cw = reg.w / blocks, ch = reg.h / blocks, cell = (cw < ch ? cw : ch);
    var r, c, idx, fb, cx, cy, rad;
    for (r = 0; r < blocks; r++) {
      for (c = 0; c < blocks; c++) {
        idx = r * blocks + c;
        fb = mmFrostBlotch(field[idx], cov, 0.25);
        if (!fb.on) { continue; }
        cx = reg.x + (c + 0.5) * cw; cy = reg.y + (r + 0.5) * ch;
        rad = cell * (0.6 + 0.7 * fb.t);
        ctx.fillStyle = 'rgba(' + pal.core + ',' + (0.85 * fb.t) + ')';
        ctx.beginPath(); ctx.arc(cx, cy, rad, 0, 6.2832); ctx.fill();
        if (fb.t > 0.7) {                          // sparkle glint on settled frost
          ctx.fillStyle = 'rgba(' + pal.spark + ',0.9)';
          ctx.beginPath(); ctx.arc(cx, cy, cell * 0.12, 0, 6.2832); ctx.fill();
        }
      }
    }
    if (cov >= 0.88) {                              // consolidation -> opaque by cov=1
      var a = (cov - 0.88) / 0.12; if (a > 1) { a = 1; }
      ctx.fillStyle = 'rgba(' + pal.core + ',' + a + ')';
      ctx.fillRect(reg.x, reg.y, reg.w, reg.h);
    }
  }
```

Add exports in the `root.*` block:

```javascript
  root.mmFrostPalette = mmFrostPalette;
  root.mmDrawFrost = mmDrawFrost;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/unit/js/test_frostcreep.js`
Expected: PASS (all frostcreep tests).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/test_frostcreep.js
git commit -m "feat(transitions): mmDrawFrost drawer (blotches + consolidation fill)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `index.html` wiring (in-canvas + overlay) + `?frostblocks` knob

**Files:**
- Modify: `index.html` (in-canvas mask dispatch — add a `frostcreep` branch after the `kegroll` branch; overlay mask dispatch — same; the scatter-knob parse block — add `?frostblocks`)
- Test: existing `python pytest_runner.py --js` (module-load smoke) + a wiring-count sanity check (on-wall is Task 7).

**Interfaces:**
- Consumes: `mmDrawFrost` (Task 4). The `st.effect.family === 'mask'` dispatch already exists at both sites (used by beerfill/scatter/kegroll/iris/dissolve); this adds a `frostcreep` branch to each. The `?frostblocks` knob sets `window._mmFrostDbg = {blocks: N}`, read by `mmDrawFrost`.
- Produces: no new exports. Wires the drawer into the live client.

- [ ] **Step 1: Add the in-canvas branch**

In `index.html`, in the in-canvas mask block, insert immediately after the `kegroll` branch (and before the `} else if (typeof mmDrawMaskInCanvas === 'function') {` fallback):

```javascript
								} else if (stc.effect.name === 'frostcreep' && typeof mmDrawFrost === 'function') {
									mmDrawFrost(ctx, stc.effect.params, stc.effect.phase, stc.effect.front,
										it.meshGlobal[0], it.meshGlobal[1], it.meshQuad, stc.effect.scope, playback.seed | 0);
```

- [ ] **Step 2: Add the overlay branch**

In `index.html`, in the overlay mask block, insert immediately after the `kegroll` branch (and before the `} else {` that calls `mmDrawMaskOverlay`):

```javascript
						} else if (st.effect.name === 'frostcreep' && typeof mmDrawFrost === 'function') {
							mmDrawFrost(cmx, st.effect.params, st.effect.phase, st.effect.front,
								GWm, GHm, quad, st.effect.scope, playback.seed | 0);
```

- [ ] **Step 3: Add the `?frostblocks` knob**

In `index.html`, in the IIFE that parses the scatter knobs (the block that ends `window._mmSdbg = sd;` and sets `window._mmKegFill`), add before the IIFE closes:

```javascript
		// frost-creep live knob: ?frostblocks=N sets the noise-grid density (cost lever).
		if ((m = /[?&]frostblocks=(\d+)/.exec(h))) { window._mmFrostDbg = { blocks: parseInt(m[1], 10) }; }
```

- [ ] **Step 4: Verify JS suite + wiring count**

Run: `python pytest_runner.py --js`
Expected: PASS — modules still load, all JS unit tests green.

Run: `node -e "var s=require('fs').readFileSync('index.html','utf8'); var n=(s.match(/frostcreep/g)||[]).length; if(n!==2){throw new Error('expected 2 frostcreep wiring sites, got '+n)} console.log('OK 2 sites')"`
Expected: `OK 2 sites` (one in-canvas + one overlay).

- [ ] **Step 5: Commit**

```bash
git add index.html
git commit -m "feat(client): wire frostcreep into mask dispatch + ?frostblocks knob

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: "Frost Creep Demo" playlist tool

**Files:**
- Create: `tools/_make_frost_demo.py`

**Interfaces:**
- Consumes: the running server on `127.0.0.1:3000` (SockJS). Mirrors `tools/_make_beer_demo.py` / `tools/_make_kegroll_demo.py`.
- Produces: a `Frost Creep Demo` playlist (two plasma mesh items handing off via `frostcreep`).

- [ ] **Step 1: Write the demo tool**

Create `tools/_make_frost_demo.py`:

```python
"""Create a 'Frost Creep Demo' playlist: two plasma mesh items handing off via the
frostcreep transition (tint=frost, wall). Run with the server up."""
import asyncio, json, aiohttp
HOST = "127.0.0.1:3000"

def fc():
    return {"name": "frostcreep",
            "params": {"tint": "frost", "scope": "wall", "duration": 2200, "audioFade": True}}

ITEMS = [
    {"id": "fc-a", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 8, "backgroundColor": "#06121a", "startEffect": None, "endEffect": fc()},
    {"id": "fc-b", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh",
     "duration": 8, "backgroundColor": "#06121a", "startEffect": fc(), "endEffect": None},
]

async def main():
    url = "http://%s/sockjs/000/claudecmd/websocket" % HOST
    p = {"SRC": "claude-admin", "DEST": "SRV", "REQUEST": "SAVE_PLAYLIST",
         "PAYLOAD": {"name": "Frost Creep Demo", "items": ITEMS, "loop": True}}
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url) as ws:
            await ws.send_str(json.dumps([json.dumps(p)]))
            print("sent Frost Creep Demo")
            try:
                for _ in range(6):
                    m = await asyncio.wait_for(ws.receive(), timeout=2.0)
                    if "SAVE_PLAYLIST" in str(m.data): print("recv:", str(m.data)[:140])
            except asyncio.TimeoutError:
                pass

if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Create the demo + verify**

Run: `python tools/_make_frost_demo.py`
Expected: `sent Frost Creep Demo`

Run: `curl -s http://localhost:3000/api/playlists`
Expected: a `Frost Creep Demo` entry with 2 items whose `endEffect`/`startEffect` are named `frostcreep`.

- [ ] **Step 3: Commit**

```bash
git add tools/_make_frost_demo.py
git commit -m "feat(tools): Frost Creep Demo playlist

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: iPad-1 on-wall sign-off (manual acceptance — gated on deploy)

**Files:** none (manual verification).

**Interfaces:** Tasks 1-6 deployed; a server restart (to pick up `effects.py` so the editor shows the catalog — **requires explicit user authorization**) + fleet reload (mtime-cached static JS picks up on reload); a calibrated mesh group; the `Frost Creep Demo`.

- [ ] **Step 1:** Request a server restart (for the `effects.py` catalog) — do NOT restart unprompted. Reload the OEB Sign 1 fleet so the iPads fetch the new `transitions.js` + `index.html`.
- [ ] **Step 2:** Assign `Frost Creep Demo` to the calibrated mesh group and play.
- [ ] **Step 3:** Observe acceptance: frost spreads in coherent patches that grow and merge to a clean, gap-free full cover, then recedes on reveal; smooth at wall scale; no seam tearing; the handoff is fully covered. If density looks off, sweep `?frostblocks=14`/`22` on a screen and bake the chosen value.
- [ ] **Step 4:** Record the result; file any tint/density follow-ups.

---

## Self-Review

**1. Spec coverage** (each spec section → task):
- Catalog entry + params (tint/scope/duration/audioFade) → Task 1. ✓
- `video_filters` audio-only, single duration → Task 1. ✓
- Pure helpers `mmFrostPhase`/`mmFrostField` (box-blur + [0,0.98) renorm + flat guard)/`mmFrostBlotch` → Task 2. ✓
- `mmTransitionState` additive descriptor (cover rises / reveal falls; local-progress) → Task 3. ✓
- `mmDrawFrost` drawer (memoized field, soft growing blotches, sparkle, consolidation fill) + tint palette → Task 4. ✓
- Both apply sites + `?frostblocks` knob → Task 5. ✓
- Wipe path untouched → confirmed (frostcreep rides `st.effect`, never `st.wipe`). ✓
- No render-token impact → Task 1's `_afade`-only `video_filters`; `_audio_fade_sig` reads only `audioFade`. (A dedicated token regression test is optional; the spec calls for one — covered by the existing visual-only token guard pattern in `test_mosaic.py`; add a frostcreep case there only if a reviewer wants it explicit. Noted, not a separate task.) ✓
- Demo + procedural (no sprite) → Task 6. ✓
- On-wall sign-off → Task 7. ✓
- ES5 / canvas-op constraints → Global Constraints + helper/drawer code (no `let`/`const`/clip/composite). ✓

**2. Placeholder scan:** No TBD/TODO/"similar to Task N"/"add error handling" — every code step shows complete code; the demo tool is given in full. ✓

**3. Type consistency:** `mmDrawFrost(ctx, params, phase, cover, GW, GH, quad, scope, seed)` is identical between Task 4 (definition) and Task 5 (both call sites). `mmFrostPhase`/`mmFrostField`/`mmFrostBlotch` names + arg orders match between Task 2 (defs), Task 3 (`mmFrostPhase` use), and Task 4 (drawer uses `mmFrostField`/`mmFrostBlotch`). `effect.front`/`phase`/`scope`/`params` keys match between Task 3 (producer) and Tasks 4-5 (consumers). `root._mmFrostDbg.blocks` written in Task 5, read in Task 4. ✓
