# Additional Transition Effects Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four transition effects — Slide, Zoom, Iris, Dissolve — usable as Start/End effects with a duration, wall-coherent where it matters, alongside the existing Fade and Wipe.

**Architecture:** ADDITIVE — the proven Fade/Wipe paths are untouched. New effects ride a parallel `st.effect` descriptor from `mmTransitionState`, dispatched at apply time. Iris/Dissolve are reveal-mask effects (cover canvas / in-canvas, calibration-aware via the mesh affine); Slide/Zoom are transform effects (in-canvas global transform for mesh animations, element `-webkit-transform` for mirror/media). Pure geometry helpers in `js/transitions.js` are node-tested; canvas drawing is glue verified by smoke + on-wall.

**Tech Stack:** Python (`effects.py` catalog), ES5 JS (`js/transitions.js`, `index.html` — iPad-1/Safari 5.1: no let/const/arrow/template-literal/class; canvas 2d incl. `globalCompositeOperation` `destination-in`/`destination-out`, `globalAlpha`, `arc`, `fillRect`; `-webkit-transform`). Node `--test` for JS, pytest for Python.

**Reference spec:** `docs/superpowers/specs/2026-06-22-additional-transition-effects-design.md`

---

## File Structure

- `effects.py` (modify) — 4 new `@register`ed `Effect` subclasses (catalog + params; audio-only `video_filters`).
- `tests/unit/test_effects.py` (modify) — update the catalog-set assertion; add per-effect param tests.
- `js/transitions.js` (modify) — pure helpers (`mmSlideOffset`, `mmZoomFactor`, `mmIrisCircle`, `mmDissolveOrder`, `mmDissolveCovered`, `_mmLcg`), the additive `effect` descriptor in `mmTransitionState`, and the `mmDrawCoverMask` glue.
- `tests/unit/js/transition-effects.test.js` (create) — node tests for the pure helpers + the new-effect descriptors.
- `index.html` (modify) — dispatch `st.effect` in `applyTransitionNow` (overlay mask + element transform) and in the `runScriptLoop` in-canvas block (in-canvas mask + global transform).

### JS test harness (used by the new test file)

`js/transitions.js` is a classic script that attaches helpers to the global object; node tests import it for side effects and read `globalThis.*` (see `tests/unit/js/test_transitions.js`). The new file begins:

```js
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/transitions.js');
const S = globalThis.mmTransitionState;
```

---

### Task 1: effects.py — Slide/Zoom/Iris/Dissolve catalog entries

**Files:**
- Modify: `effects.py`
- Modify: `tests/unit/test_effects.py`

- [ ] **Step 1: Update + add failing tests**

In `tests/unit/test_effects.py`, REPLACE `test_catalog_has_fade_and_wipe_only` with:

```python
def test_catalog_has_all_effects():
    names = {e["name"] for e in effects.effect_catalog()}
    assert names == {"fade", "wipe", "slide", "zoom", "iris", "dissolve"}
```

Then append:

```python
def test_slide_params():
    e = next(e for e in effects.effect_catalog() if e["name"] == "slide")
    by = {p["key"]: p for p in e["params"]}
    assert by["direction"]["choices"] == ["left", "right", "up", "down"]
    assert by["scope"]["choices"] == ["screen", "wall"] and by["scope"]["default"] == "wall"
    assert by["duration"]["type"] == "number" and by["audioFade"]["type"] == "boolean"

def test_zoom_params():
    e = next(e for e in effects.effect_catalog() if e["name"] == "zoom")
    by = {p["key"]: p for p in e["params"]}
    assert by["scale"]["type"] == "number" and by["scale"]["default"] == 0.6
    assert by["scope"]["choices"] == ["screen", "wall"]

def test_iris_params():
    e = next(e for e in effects.effect_catalog() if e["name"] == "iris")
    by = {p["key"]: p for p in e["params"]}
    assert by["scope"]["choices"] == ["screen", "wall"] and by["duration"]["type"] == "number"

def test_dissolve_params():
    e = next(e for e in effects.effect_catalog() if e["name"] == "dissolve")
    by = {p["key"]: p for p in e["params"]}
    assert by["blocks"]["type"] == "number" and by["blocks"]["default"] == 16

def test_new_effects_bake_audio_only():
    for name in ("slide", "zoom", "iris", "dissolve"):
        eff = effects.get_effect(name)
        v, a = eff.video_filters("start", eff.resolve({"duration": 600, "audioFade": True}), {"duration_ms": 5000})
        assert v == [] and a == ["afade=t=in:st=0:d=0.6"]
        v2, a2 = eff.video_filters("start", eff.resolve({"duration": 600, "audioFade": False}), {"duration_ms": 5000})
        assert v2 == [] and a2 == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -q`
Expected: FAIL (catalog set mismatch; `get_effect("slide")` is None).

- [ ] **Step 3: Implement the four effects**

In `effects.py`, after `class WipeEffect`, add (each follows the existing pattern — `video_filters` returns audio-only via `_afade`):

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_effects.py -c tests/pytest.ini -q`
Expected: PASS (all, incl. the existing fade/wipe tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add effects.py tests/unit/test_effects.py
git commit -m "feat(effects): slide/zoom/iris/dissolve catalog entries (audio-only bake)"
```

---

### Task 2: `mmSlideOffset` pure helper

**Files:**
- Modify: `js/transitions.js`
- Test: `tests/unit/js/transition-effects.test.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/transition-effects.test.js` with the harness above, then append:

```js
const Slide = globalThis.mmSlideOffset;

test('mmSlideOffset: front 0 fully off-edge, front 1 in place', () => {
  assert.deepEqual(Slide(0, 'left', 200, 100), { dx: 200, dy: 0 });
  assert.deepEqual(Slide(1, 'left', 200, 100), { dx: 0, dy: 0 });
});
test('mmSlideOffset: direction signs', () => {
  assert.deepEqual(Slide(0.5, 'left', 200, 100), { dx: 100, dy: 0 });
  assert.deepEqual(Slide(0.5, 'right', 200, 100), { dx: -100, dy: 0 });
  assert.deepEqual(Slide(0.5, 'up', 200, 100), { dx: 0, dy: 50 });
  assert.deepEqual(Slide(0.5, 'down', 200, 100), { dx: 0, dy: -50 });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/transition-effects.test.js`
Expected: FAIL — `mmSlideOffset is not a function`.

- [ ] **Step 3: Implement**

In `js/transitions.js`, add a private function (near `mmWipeSlide`):

```js
  // Global-px offset for a Slide. 'direction' is the motion direction; content enters
  // from the opposite edge. front 0 -> one wall off; front 1 -> {0,0}. Pure.
  function mmSlideOffset(front, direction, GW, GH) {
    var f = front < 0 ? 0 : (front > 1 ? 1 : front);
    var k = 1 - f, dx = 0, dy = 0;
    if (direction === 'left') { dx = k * GW; }
    else if (direction === 'right') { dx = -k * GW; }
    else if (direction === 'up') { dy = k * GH; }
    else { dy = -k * GH; }   // down
    return { dx: dx, dy: dy };
  }
```

Expose it on `root` (where `mmWipeSlide` etc. are exported): `root.mmSlideOffset = mmSlideOffset;`

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/transition-effects.test.js`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/transition-effects.test.js
git commit -m "feat(transitions): pure mmSlideOffset"
```

---

### Task 3: `mmZoomFactor` pure helper

**Files:**
- Modify: `js/transitions.js`
- Test: `tests/unit/js/transition-effects.test.js`

- [ ] **Step 1: Append failing test**

```js
const Zoom = globalThis.mmZoomFactor;

test('mmZoomFactor: scale ramps to 1, alpha ramps to 1', () => {
  assert.deepEqual(Zoom(0, 0.6), { s: 0.6, alpha: 0 });
  assert.deepEqual(Zoom(1, 0.6), { s: 1, alpha: 1 });
  const mid = Zoom(0.5, 0.6);
  assert.ok(Math.abs(mid.s - 0.8) < 1e-9 && Math.abs(mid.alpha - 0.5) < 1e-9);
});
test('mmZoomFactor: default scale 0.6 when omitted', () => {
  assert.deepEqual(Zoom(0, null), { s: 0.6, alpha: 0 });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/transition-effects.test.js`
Expected: FAIL — `mmZoomFactor is not a function`.

- [ ] **Step 3: Implement**

```js
  // Scale + opacity for a Zoom. s ramps scale->1, alpha ramps 0->1 with front. Pure.
  function mmZoomFactor(front, scale) {
    var f = front < 0 ? 0 : (front > 1 ? 1 : front);
    if (scale == null) { scale = 0.6; }
    return { s: scale + (1 - scale) * f, alpha: f };
  }
```

Expose: `root.mmZoomFactor = mmZoomFactor;`

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/transition-effects.test.js`
Expected: PASS (4 tests total).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/transition-effects.test.js
git commit -m "feat(transitions): pure mmZoomFactor"
```

---

### Task 4: `mmIrisCircle` pure helper

**Files:**
- Modify: `js/transitions.js`
- Test: `tests/unit/js/transition-effects.test.js`

- [ ] **Step 1: Append failing test**

```js
const Iris = globalThis.mmIrisCircle;
const QUAD = [[0.5, 0], [1, 0], [1, 1], [0.5, 1]]; // right half, full height

test('mmIrisCircle: wall scope centered, radius 0->halfDiagonal', () => {
  assert.deepEqual(Iris(0, 200, 100, 'wall', null), { cx: 100, cy: 50, r: 0 });
  const full = Iris(1, 200, 100, 'wall', null);
  assert.ok(full.cx === 100 && full.cy === 50);
  assert.ok(Math.abs(full.r - Math.sqrt(100 * 100 + 50 * 50)) < 1e-9);
});
test('mmIrisCircle: screen scope centers on the panel bbox', () => {
  // bbox global x[0.5,1]->px[100,200], y[0,1]->[0,100]; center (150,50)
  const c = Iris(0.5, 200, 100, 'screen', QUAD);
  assert.ok(Math.abs(c.cx - 150) < 1e-9 && Math.abs(c.cy - 50) < 1e-9);
  // half-diagonal of a 100x100 bbox = ~70.71; at front 0.5 -> ~35.36
  assert.ok(Math.abs(c.r - 0.5 * Math.sqrt(50 * 50 + 50 * 50)) < 1e-9);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/transition-effects.test.js`
Expected: FAIL — `mmIrisCircle is not a function`.

- [ ] **Step 3: Implement**

```js
  // Circle (global px) for an Iris reveal. Center = wall center (wall) or panel bbox
  // center (screen); radius ramps 0 -> half the region diagonal (so front 1 fully
  // covers the region's farthest corner). Pure.
  function mmIrisCircle(front, GW, GH, scope, quad) {
    var f = front < 0 ? 0 : (front > 1 ? 1 : front);
    var loX = 0, loY = 0, hiX = GW, hiY = GH, i;
    if (scope !== 'wall' && quad && quad.length >= 4) {
      var xs = [], ys = [];
      for (i = 0; i < quad.length; i++) { xs.push(quad[i][0]); ys.push(quad[i][1]); }
      loX = Math.min.apply(null, xs) * GW; hiX = Math.max.apply(null, xs) * GW;
      loY = Math.min.apply(null, ys) * GH; hiY = Math.max.apply(null, ys) * GH;
    }
    var hx = (hiX - loX) / 2, hy = (hiY - loY) / 2;
    return { cx: loX + hx, cy: loY + hy, r: f * Math.sqrt(hx * hx + hy * hy) };
  }
```

Expose: `root.mmIrisCircle = mmIrisCircle;`

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/transition-effects.test.js`
Expected: PASS (6 tests total).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/transition-effects.test.js
git commit -m "feat(transitions): pure mmIrisCircle"
```

---

### Task 5: `mmDissolveOrder` / `mmDissolveCovered` pure helpers

**Files:**
- Modify: `js/transitions.js`
- Test: `tests/unit/js/transition-effects.test.js`

- [ ] **Step 1: Append failing test**

```js
const Order = globalThis.mmDissolveOrder;
const Covered = globalThis.mmDissolveCovered;

test('mmDissolveOrder: deterministic permutation per seed', () => {
  const a = Order(16, 42), b = Order(16, 42), c = Order(16, 99);
  assert.deepEqual(a, b);                              // same seed -> same order
  assert.notDeepEqual(a, c);                           // different seed -> different
  assert.deepEqual(a.slice().sort((x, y) => x - y), Array.from({ length: 16 }, (_, i) => i)); // valid perm
});
test('mmDissolveCovered: monotonic, n at front 0, 0 at front 1', () => {
  assert.equal(Covered(0, 256), 256);
  assert.equal(Covered(1, 256), 0);
  assert.ok(Covered(0.25, 256) > Covered(0.75, 256));
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/transition-effects.test.js`
Expected: FAIL — `mmDissolveOrder is not a function`.

- [ ] **Step 3: Implement**

```js
  // Tiny deterministic LCG (per-seed) -> [0,1) generator. Pure; ES5/bit-portable.
  function _mmLcg(seed) {
    var s = (seed >>> 0) || 1;
    return function () { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
  }
  // Seeded reveal order: a permutation of 0..n-1 (Fisher-Yates over _mmLcg). The SAME
  // seed (playback.seed) on every screen -> identical order -> wall-coherent dissolve.
  function mmDissolveOrder(n, seed) {
    var arr = [], i;
    for (i = 0; i < n; i++) { arr.push(i); }
    var rnd = _mmLcg(seed);
    for (i = n - 1; i > 0; i--) {
      var j = Math.floor(rnd() * (i + 1));
      var t = arr[i]; arr[i] = arr[j]; arr[j] = t;
    }
    return arr;
  }
  // Count of cells still covered at this front (cells revealed = floor(front*n)). Pure.
  function mmDissolveCovered(front, n) {
    var f = front < 0 ? 0 : (front > 1 ? 1 : front);
    return n - Math.floor(f * n);
  }
```

Expose: `root.mmDissolveOrder = mmDissolveOrder; root.mmDissolveCovered = mmDissolveCovered;`

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/transition-effects.test.js`
Expected: PASS (8 tests total).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/transition-effects.test.js
git commit -m "feat(transitions): pure mmDissolveOrder/Covered (seeded)"
```

---

### Task 6: `mmTransitionState` — additive `effect` descriptor for the 4 new effects

**Files:**
- Modify: `js/transitions.js`
- Test: `tests/unit/js/transition-effects.test.js`

- [ ] **Step 1: Append failing tests**

```js
const SLIDE = { name: 'slide', params: { direction: 'left', scope: 'wall', duration: 1000 } };
const ZOOM  = { name: 'zoom',  params: { scale: 0.6, scope: 'wall', duration: 1000 } };
const IRIS  = { name: 'iris',  params: { scope: 'wall', duration: 1000 } };
const DISS  = { name: 'dissolve', params: { blocks: 16, duration: 1000 } };

test('mmTransitionState: new effects yield an effect descriptor with family + front', () => {
  const s1 = S(SLIDE, null, 250, 1000, null, null);
  assert.equal(s1.effect.name, 'slide');
  assert.equal(s1.effect.family, 'transform');
  assert.ok(Math.abs(s1.effect.front - 0.25) < 1e-9);
  assert.equal(s1.effect.scope, 'wall');
  assert.equal(s1.wipe, null);
  assert.equal(S(ZOOM, null, 250, 1000, null, null).effect.family, 'transform');
  assert.equal(S(IRIS, null, 250, 1000, null, null).effect.family, 'mask');
  assert.equal(S(DISS, null, 250, 1000, null, null).effect.family, 'mask');
});

test('mmTransitionState: fade and wipe descriptors are unchanged (no effect field)', () => {
  const fade = { name: 'fade', params: { duration: 1000 } };
  const wipe = { name: 'wipe', params: { direction: 'down', scope: 'wall', duration: 1000 } };
  const f = S(fade, null, 500, 10000, null, null);
  assert.ok(Math.abs(f.opacity - 0.5) < 1e-9 && !f.effect && f.wipe === null);
  const w = S(wipe, null, 250, 1000, { x: 0, y: 0, w: 1, h: 1 }, null);
  assert.ok(w.wipe && !w.effect);   // still the wipe descriptor, no effect field
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/transition-effects.test.js`
Expected: FAIL — `s1.effect` is undefined.

- [ ] **Step 3: Implement the additive branch**

In `js/transitions.js`, inside `mmTransitionState`, the current tail is:

```js
    if (eff.name === 'wipe') { ... return { ... wipe: {...} }; }
    return { role: role, opacity: p, wipe: null };   // fade
```

Insert a new branch BETWEEN the `wipe` block and the fade `return` (so wipe/fade are untouched):

```js
    if (eff.name === 'slide' || eff.name === 'zoom' || eff.name === 'iris' || eff.name === 'dissolve') {
      var fam = (eff.name === 'iris' || eff.name === 'dissolve') ? 'mask' : 'transform';
      var esc = (eff.params && eff.params.scope) || 'wall';
      return { role: role, opacity: 1, wipe: null,
               effect: { name: eff.name, family: fam, front: p, scope: esc, params: eff.params || {} } };
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/transition-effects.test.js` then `node --test tests/unit/js/test_transitions.js`
Expected: both PASS — new file 10 tests; the existing `test_transitions.js` UNCHANGED and still green (wipe/fade untouched).

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js tests/unit/js/transition-effects.test.js
git commit -m "feat(transitions): additive effect descriptor for slide/zoom/iris/dissolve"
```

---

### Task 7: Mask-family apply (Iris, Dissolve) — overlay + in-canvas

**Files:**
- Modify: `js/transitions.js` (add `mmDrawCoverMask` glue + region helper)
- Modify: `index.html` (dispatch `st.effect` family `mask` in `applyTransitionNow` overlay path and the `runScriptLoop` in-canvas block)

There is no new node test (canvas drawing is glue); verification is the full JS suite (unchanged green) + ES5 lint + a node viz-harness geometry check + on-wall. The PURE geometry (`mmIrisCircle`, `mmDissolveOrder/Covered`) is already tested.

- [ ] **Step 1: Add the mask drawers to `js/transitions.js`**

Add a region helper + two drawer entry points. `mmDrawMaskOverlay` draws onto the SEPARATE overlay canvas (content is below, so we punch/clear to reveal). `mmDrawMaskInCanvas` draws onto the SAME canvas as the content (so we keep the revealed content and clear/cover the rest). Both assume the ctx is already under the mesh affine (global coords).

```js
  function _mmMaskRegion(scope, quad, GW, GH) {
    if (scope !== 'wall' && quad && quad.length >= 4) {
      var xs = [], ys = [], i;
      for (i = 0; i < quad.length; i++) { xs.push(quad[i][0]); ys.push(quad[i][1]); }
      var lx = Math.min.apply(null, xs) * GW, ly = Math.min.apply(null, ys) * GH;
      return { x: lx, y: ly, w: Math.max.apply(null, xs) * GW - lx, h: Math.max.apply(null, ys) * GH - ly };
    }
    return { x: 0, y: 0, w: GW, h: GH };
  }

  // OVERLAY canvas (content is on a layer BELOW): cover the region with bg, then
  // REVEAL by clearing the covered pixels where content should show.
  // iris  -> fill region, destination-out a growing circle (hole shows content).
  // dissolve -> fill ONLY the not-yet-revealed cells (revealed cells stay clear).
  function mmDrawMaskOverlay(ctx, name, params, front, GW, GH, quad, scope, seed, bg) {
    var reg = _mmMaskRegion(scope, quad, GW, GH);
    if (name === 'iris') {
      ctx.fillStyle = bg; ctx.fillRect(reg.x, reg.y, reg.w, reg.h);
      var c = mmIrisCircle(front, GW, GH, scope, quad);
      if (c.r > 0) {
        ctx.save();
        ctx.globalCompositeOperation = 'destination-out';
        ctx.beginPath(); ctx.arc(c.cx, c.cy, c.r, 0, 6.2831853, false); ctx.fill();
        ctx.restore();
      }
    } else if (name === 'dissolve') {
      var blocks = (params && params.blocks) || 16, n = blocks * blocks;
      var revealed = blocks * blocks - mmDissolveCovered(front, n);
      var order = mmDissolveOrder(n, seed | 0);
      var cw = reg.w / blocks, ch = reg.h / blocks, k, cell, col, rw;
      ctx.fillStyle = bg;
      for (k = revealed; k < n; k++) {
        cell = order[k]; col = cell % blocks; rw = Math.floor(cell / blocks);
        ctx.fillRect(reg.x + col * cw, reg.y + rw * ch, cw + 1, ch + 1); // +1 hides seams
      }
    }
  }

  // IN-CANVAS (drawn onto the SAME canvas as the content, AFTER the content draw):
  // iris  -> destination-in a growing circle (keeps content inside the circle, clears
  //          the rest so the item background shows through).
  // dissolve -> fill ONLY the not-yet-revealed cells with bg (covers content there).
  function mmDrawMaskInCanvas(ctx, name, params, front, GW, GH, quad, scope, seed, bg) {
    if (name === 'iris') {
      var c = mmIrisCircle(front, GW, GH, scope, quad);
      ctx.save();
      ctx.globalCompositeOperation = 'destination-in';
      ctx.beginPath();
      if (c.r > 0) { ctx.arc(c.cx, c.cy, c.r, 0, 6.2831853, false); }
      ctx.fill();                       // r==0 -> empty path -> clears all (front 0)
      ctx.restore();
    } else if (name === 'dissolve') {
      var reg = _mmMaskRegion(scope, quad, GW, GH);
      var blocks = (params && params.blocks) || 16, n = blocks * blocks;
      var revealed = blocks * blocks - mmDissolveCovered(front, n);
      var order = mmDissolveOrder(n, seed | 0);
      var cw = reg.w / blocks, ch = reg.h / blocks, k, cell, col, rw;
      ctx.fillStyle = bg;
      for (k = revealed; k < n; k++) {
        cell = order[k]; col = cell % blocks; rw = Math.floor(cell / blocks);
        ctx.fillRect(reg.x + col * cw, reg.y + rw * ch, cw + 1, ch + 1);
      }
    }
  }
```

Expose both: `root.mmDrawMaskOverlay = mmDrawMaskOverlay; root.mmDrawMaskInCanvas = mmDrawMaskInCanvas;`

- [ ] **Step 2: Wire the OVERLAY path (media/mirror) in `index.html`**

In `applyTransitionNow`, the existing affine-cover branch handles `st.wipe` by computing the matrix and calling `drawAffineCover`. ADD, before the existing `st.wipe` affine branch, a parallel branch for `st.effect && st.effect.family === 'mask'` that draws via `mmDrawMaskOverlay` onto the same overlay canvas (`mmTransCoverCanvas`), using the same matrix setup `drawAffineCover` uses. Concretely, factor the matrix+canvas setup so both wipe and mask use it; the mask branch does:

```js
		// Mask effects (iris/dissolve) on the overlay canvas — same affine as content.
		if (st.effect && st.effect.family === 'mask' && quad && item.meshGlobal
				&& typeof mmMeshTransform === 'function' && typeof mmDrawMaskOverlay === 'function') {
			var GWm = item.meshGlobal[0], GHm = item.meshGlobal[1], mm = null;
			try { mm = mmMeshTransform(quad, GWm, GHm, window.innerWidth, window.innerHeight); }
			catch (em2) { mm = null; }
			if (mm) {
				if (playback.transCover) { playback.transCover.style.display = 'none'; }
				var cvm = document.getElementById('mmTransCoverCanvas');
				if (cvm) {
					var cmx = cvm.getContext('2d');
					cmx.setTransform(1, 0, 0, 1, 0, 0);
					cmx.clearRect(0, 0, cvm.width, cvm.height);
					cvm.style.display = 'block';
					cmx.setTransform(mm.a, mm.b, mm.c, mm.d, mm.e, mm.f);
					mmDrawMaskOverlay(cmx, st.effect.name, st.effect.params, st.effect.front,
						GWm, GHm, quad, st.effect.scope, playback.seed | 0, item.backgroundColor || '#000000');
				}
				if (playback.currentEl) { playback.currentEl.style.opacity = '1'; }
				return;
			}
		}
```

(Place this in `applyTransitionNow` right where the SCRIPT-animation early-return / wipe branch decisions are made — i.e., this overlay path is for NON-mesh-animation items; mesh animations use the in-canvas path in Step 3. The existing guard `item.playmode === 'SCRIPT' && scriptSpan==='mesh'` early-returns mesh animations before this, so this branch naturally serves media/mirror.)

- [ ] **Step 3: Wire the IN-CANVAS path (mesh animations) in `runScriptLoop`**

In the in-canvas block added for the wipe (inside `if (m) { ... ctx.restore(); }` after the animation draw, where the wipe cover currently draws), ADD a parallel mask branch using `st`-equivalent state. Since `runScriptLoop` computes the transition via `mmTransitionState(it.startEffect, it.endEffect, pos.offsetMs, it.duration, null, it.meshQuad)` (the `stc` variable already there for the wipe), extend it:

```js
								// Mask effects (iris/dissolve) drawn in-canvas under the same transform.
								if (stc.effect && stc.effect.family === 'mask' && typeof mmDrawMaskInCanvas === 'function') {
									mmDrawMaskInCanvas(ctx, stc.effect.name, stc.effect.params, stc.effect.front,
										it.meshGlobal[0], it.meshGlobal[1], it.meshQuad, stc.effect.scope,
										(playback.seed | 0), it.backgroundColor || '#000000');
								}
```

(Add it adjacent to the existing `if (stc.wipe) { ... }` block, both inside the `ctx.save()/setTransform(m) ... ctx.restore()` region so they share the content transform.)

- [ ] **Step 4: Verify geometry with a node viz-harness (evidence before deploy)**

Write a throwaway node script (delete after) that imports `js/transitions.js`, builds an upright + a 180° quad, and for `iris` prints `mmIrisCircle` at front 0/0.5/1 and for `dissolve` prints `mmDissolveCovered`; confirm radius 0→halfDiag and covered n→0. (Pure-helper sanity; the canvas compositing itself is confirmed on-wall.)

Run the full JS suite: `node --test tests/unit/js/*.js` — expected all pass (existing + new), 0 fail.
ES5-lint your index.html additions: `sed -n '<edited ranges>' index.html | grep -nE "\b(let|const|class)\b|=>"` → none.

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js index.html
git commit -m "feat(transitions): iris + dissolve apply (overlay + in-canvas, wall-coherent)"
```

---

### Task 8: Transform-family apply (Slide, Zoom) — in-canvas + element

**Files:**
- Modify: `index.html` (in-canvas global transform for mesh animations; element transform for mirror/media in `applyTransitionNow`)

No new node test (the math helpers `mmSlideOffset`/`mmZoomFactor` are already tested); verify via full JS suite + ES5 lint + on-wall.

- [ ] **Step 1: In-canvas global transform for mesh animations (`runScriptLoop`)**

In the mesh branch of `runScriptLoop`, BEFORE the `animations[name](...)` draw (inside the `ctx.save(); ctx.setTransform(m, ...)` region), apply the transform-family effect to the global coordinate system so the whole wall image slides/zooms coherently. Using the `stc` transition state already computed:

```js
							// Transform effects (slide/zoom) — global-space, in-canvas (wall-coherent).
							if (stc.effect && stc.effect.family === 'transform') {
								if (stc.effect.name === 'slide' && typeof mmSlideOffset === 'function') {
									var so = mmSlideOffset(stc.effect.front, (stc.effect.params && stc.effect.params.direction) || 'left',
										it.meshGlobal[0], it.meshGlobal[1]);
									ctx.translate(so.dx, so.dy);
								} else if (stc.effect.name === 'zoom' && typeof mmZoomFactor === 'function') {
									var zf = mmZoomFactor(stc.effect.front, (stc.effect.params && stc.effect.params.scale));
									var zcx = it.meshGlobal[0] / 2, zcy = it.meshGlobal[1] / 2; // wall-center (scope 'wall')
									ctx.translate(zcx, zcy); ctx.scale(zf.s, zf.s); ctx.translate(-zcx, -zcy);
									ctx.globalAlpha = zf.alpha;
								}
							}
```

(This sits AFTER `ctx.setTransform(m,...)` and BEFORE `animations[name](...)`. The surrounding `ctx.save()/restore()` already in place restores `globalAlpha` and the transform each frame — verify the save happens before this and restore after the draw; if `globalAlpha` is set, ensure it's within the saved state so it resets next frame.)

- [ ] **Step 2: Element transform for mirror animations + media (`applyTransitionNow`)**

For NON-mesh items (mirror animations, FULL/media) — i.e., the path that does NOT hit the mesh in-canvas early-return — add an `st.effect.family === 'transform'` branch that applies `-webkit-transform` + opacity to `playback.currentEl`:

```js
		// Transform effects (slide/zoom) on the content element (mirror animations + media).
		if (st.effect && st.effect.family === 'transform' && playback.currentEl) {
			var el = playback.currentEl;
			if (st.effect.name === 'slide') {
				var so2 = mmSlideOffset(st.effect.front, (st.effect.params && st.effect.params.direction) || 'left', 100, 100);
				var t2 = 'translate(' + so2.dx + '%,' + so2.dy + '%)';   // GW=GH=100 -> percent of element
				el.style.webkitTransform = t2; el.style.transform = t2; el.style.opacity = '1';
			} else {
				var zf2 = mmZoomFactor(st.effect.front, (st.effect.params && st.effect.params.scale));
				var t3 = 'scale(' + zf2.s + ')';
				el.style.webkitTransform = t3; el.style.transform = t3; el.style.opacity = '' + zf2.alpha;
			}
			var cvh = document.getElementById('mmTransCoverCanvas'); if (cvh) { cvh.style.display = 'none'; }
			return;
		}
```

(Place this alongside the other `st.effect`/`st.wipe` branches in `applyTransitionNow`, after the mesh-animation early-return and the mask-overlay branch. `transform-origin` defaults to center, which is correct for scale; for slide the element translates by a percent of itself.)

- [ ] **Step 3: Verify**

Run: `node --test tests/unit/js/*.js` → all pass (no JS unit change; this is index.html glue).
ES5-lint the edited index.html ranges: `grep -nE "\b(let|const|class)\b|=>"` → none in your additions.
Read the `runScriptLoop` save/restore around your Step-1 insert and confirm `globalAlpha` is reset each frame (it must be inside the per-frame `ctx.save()/ctx.restore()`; if the existing code doesn't `save()` before the mesh draw, add `ctx.globalAlpha = 1;` at the top of the frame to avoid carry-over).

- [ ] **Step 4: Commit**

```bash
git add index.html
git commit -m "feat(transitions): slide + zoom apply (in-canvas mesh + element mirror/media)"
```

---

### Task 9: iPad-1 on-wall sign-off (manual)

**Files:** none (verification only)

- [ ] **Step 1:** Reload the wall (client-only changes: `effects.py` is server-side catalog, served on next `/api/effects` fetch by the admin; `js/transitions.js` + `index.html` reload on the display clients). No server restart needed for the JS/HTML; restart the server only to pick up `effects.py` (the catalog) for the editor.
- [ ] **Step 2:** In the playlist editor, confirm Slide / Zoom / Iris / Dissolve appear in the Start/End effect dropdowns with their params.
- [ ] **Step 3:** Build a test playlist (or extend "Transition Test") with one plasma/mesh item per new effect (2 s in/out, 1 s dwell, loop). Verify on the wall: Iris grows as one circle across the wall; Dissolve reveals coherently (same block order everywhere); Slide moves the whole wall image as one; Zoom scales about the wall center — all with no per-tile tearing.
- [ ] **Step 4:** Verify on a FULL/mirror item: Slide/Zoom apply identically per screen; Iris/Dissolve reveal coherently.
- [ ] **Step 5:** Confirm Fade and Wipe still behave exactly as before (regression check on the untouched paths).

---

## Self-Review

**Spec coverage:** four effects in catalog (T1) ✓; pure helpers Slide/Zoom/Iris/Dissolve (T2–T5) ✓; additive `effect` descriptor, fade/wipe untouched (T6) ✓; mask family overlay + in-canvas, wall-coherent (T7) ✓; transform family in-canvas mesh + element mirror/media (T8) ✓; seeded wall-coherent dissolve via `playback.seed` (T5+T7) ✓; data-driven editor (no change; verified T9) ✓; no render-token impact (T1 audio-only; new params never enter `_audio_fade_sig`) ✓; ES5 + Safari-5.1 canvas ops (T7/T8 lint) ✓; documented per-screen-video limitation (spec; not a code task) ✓; on-wall acceptance (T9) ✓.

**Placeholder scan:** none — every code step has complete code and exact run/expected lines. T7/T8 canvas glue is fully written; the "verify" steps are evidence gathering, not deferred work.

**Type/name consistency:** helper names (`mmSlideOffset`, `mmZoomFactor`, `mmIrisCircle`, `mmDissolveOrder`, `mmDissolveCovered`, `_mmLcg`, `_mmMaskRegion`, `mmDrawMaskOverlay`, `mmDrawMaskInCanvas`), the `effect` descriptor shape `{name,family,front,scope,params}`, family values `mask`/`transform`, and `playback.seed` usage are consistent across T2–T8. Effect names match `effects.py` (`slide/zoom/iris/dissolve`).

**Known integration caveat for the implementer:** T7/T8 edit `index.html` functions whose exact line numbers shifted during the drift work — re-read `applyTransitionNow`, `drawAffineCover`, and the `runScriptLoop` in-canvas block before placing edits. The mesh-animation early-return in `applyTransitionNow` means the overlay/element branches there serve media/mirror, while `runScriptLoop` serves mesh animations — keep that split intact.
