# Generative Animations Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four seeded generative SCRIPT animations (`starfield`, `fireworks`, `truchet`, `spirograph`) that look different every run but stay bit-identical across every screen in a group.

**Architecture:** Each is one self-describing `{key, label, description, draw}` entry appended to the `MM_ANIMATIONS` array in `js/animations.js`. `draw(ctx, tMs, w, h, nowMs, seed)` is a pure function — randomness comes ONLY from `MM_RNG(seed)` / `mmDeriveSeed(seed, n)` (both already defined in the same IIFE closure, so `draw` calls them directly). No server change; the admin picker auto-updates from `MM_ANIMATIONS`.

**Tech Stack:** ES5 + Canvas2D (`js/animations.js`, runs on iPad-1 / Safari 5.1), Node 20 `node --test` (`python pytest_runner.py --js`).

**Spec:** `docs/superpowers/specs/2026-06-19-generative-animations-design.md`

---

## Background the implementer needs

**Current state (verified):** `js/animations.js` is an IIFE `(function (root) { ... })(window-or-globalThis)`. Inside it, `MM_RNG(seed)` (line ~17) returns a `function(): float in [0,1)`, and `mmDeriveSeed(runSeed, idx)` (line ~29) returns a uint — both **callable directly from any `draw`** (same closure). The `var animations = [ ... ]` array ends with the `sunMoonTransit` entry (~line 361); append new entries before the array's closing `]`. Exposure lines `root.MM_ANIMATIONS = animations; root.MM_RNG = ...; root.mmDeriveSeed = ...` follow (~line 406). The 12 existing keys: bouncingBalls, lissajous, phyllotaxis, wireframeCube, radialPulse, particleGalaxy, plasma, pendulumWave, dvdLogo, analogClock, wordClock, sunMoonTransit.

**The determinism rule (critical, from the spec):** pull `MM_RNG` values in a FIXED ORDER at the top of `draw` (or once per fixed slot for `fireworks`), BEFORE any `tMs`-dependent branch/`continue`. Never gate an `MM_RNG()` call behind a `tMs`-varying condition or count — otherwise two `draw`s at the same `(tMs, seed)` pull different values and the screens desync. Each draw below already does this; preserve it.

**ES5 rules:** `var`/`function` only — no `let`/`const`/arrow/template-literals/`Math.imul`. Canvas2D primitives only. Match the array's 2-space indentation.

**Test idiom (copy from `tests/unit/js/test_animations_plasma.js`):**
```js
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));
const W = 1024, H = 768;
```
Seeded animations are called `byKey.NAME(ctx, tMs, W, H, 0, seed)` (5th arg `nowMs` = 0, 6th = seed). Each animation gets 4 tests: **deterministic** (same `(tMs, seed)` → `deepStrictEqual` ops), **animates** (different `tMs` → `notDeepStrictEqual`), **seeded** (different `seed` → `notDeepStrictEqual`), **structure** (an op-count/shape sanity check).

**Branch:** `feature/generative-animations` (already created off `main`, spec committed). Do NOT start on `main`.

---

## File Structure

| File | Responsibility | Task |
|------|----------------|------|
| `tests/unit/js/test_animations_starfield.js` | starfield determinism/animates/seeded/structure | 1 |
| `tests/unit/js/test_animations_fireworks.js` | fireworks "" | 2 |
| `tests/unit/js/test_animations_truchet.js` | truchet "" | 3 |
| `tests/unit/js/test_animations_spirograph.js` | spirograph "" | 4 |
| `js/animations.js` | 4 new `MM_ANIMATIONS` entries | 1-4 |
| `tests/unit/js/test_animations_module.js` | extend expected-key list (+4) | 5 |

---

### Task 1: `starfield`

**Files:** Modify `js/animations.js`; Test `tests/unit/js/test_animations_starfield.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_animations_starfield.js`:

```js
/**
 * starfield: seeded warp-stars. Seed fixes the star directions/phases (so the
 * field differs per run, identical across screens); tMs drives the outward warp.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));
const W = 1024, H = 768;

test('starfield — deterministic at same (tMs, seed)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.starfield(a, 5000, W, H, 0, 42);
  byKey.starfield(b, 5000, W, H, 0, 42);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('starfield — animates (different tMs differs)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.starfield(a, 1000, W, H, 0, 42);
  byKey.starfield(b, 4000, W, H, 0, 42);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('starfield — seeded (different seed differs)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.starfield(a, 5000, W, H, 0, 1);
  byKey.starfield(b, 5000, W, H, 0, 2);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('starfield — draws a bounded number of streaks', () => {
  const c = makeRecordingCtx();
  byKey.starfield(c, 5000, W, H, 0, 42);
  const strokes = c.__ops.filter((o) => o.op === 'stroke').length;
  assert.ok(strokes > 0 && strokes <= 200, `unexpected streak count ${strokes}`);
});
```

- [ ] **Step 2: Run to confirm fail**

Run: `node --test tests/unit/js/test_animations_starfield.js`
Expected: FAIL — `byKey.starfield is not a function`.

- [ ] **Step 3: Append the entry** (before the array's closing `]`, after `sunMoonTransit`; add a trailing comma to the prior entry's `}`):

```js
    {
      key: 'starfield',
      label: 'Starfield',
      description: 'Warp-speed stars streaking outward — a different field every run.',
      draw: function (ctx, tMs, w, h, nowMs, seed) {
        var rng = MM_RNG(seed);
        var N = 200, i;
        var cx = w / 2, cy = h / 2;
        var SPEED = 3000, SPREAD = Math.min(w, h) * 0.04;
        var maxR = Math.sqrt(w * w + h * h);
        for (i = 0; i < N; i++) {
          var ang = rng() * Math.PI * 2;     // pull all 3 BEFORE any tMs branch
          var phase = rng();
          var b = 0.4 + rng() * 0.6;
          var f = ((tMs / SPEED + phase) % 1 + 1) % 1;   // frac in [0,1)
          var z = 1 - f;                                 // (0,1]
          if (z < 0.001) { continue; }
          var r = (1 / z - 1) * SPREAD;
          if (r > maxR) { continue; }
          var zPrev = z + 0.04; if (zPrev > 1) { zPrev = 1; }
          var rPrev = (1 / zPrev - 1) * SPREAD;
          var ca = Math.cos(ang), sa = Math.sin(ang);
          var g = Math.round(b * 255);
          ctx.strokeStyle = 'rgb(' + g + ',' + g + ',' + g + ')';
          ctx.lineWidth = 1 + (1 - z) * 2;
          ctx.beginPath();
          ctx.moveTo(cx + ca * rPrev, cy + sa * rPrev);
          ctx.lineTo(cx + ca * r, cy + sa * r);
          ctx.stroke();
        }
      }
    }
```

- [ ] **Step 4: Run to confirm pass**

Run: `node --test tests/unit/js/test_animations_starfield.js`  → PASS (4).
Run: `node --test tests/unit/js/test_animations_module.js`  → still PASS.

- [ ] **Step 5: Commit**

```bash
git add js/animations.js tests/unit/js/test_animations_starfield.js
git commit -m "feat(animations): starfield (seeded warp-stars)"
```

---

### Task 2: `fireworks`

**Files:** Modify `js/animations.js`; Test `tests/unit/js/test_animations_fireworks.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_animations_fireworks.js`:

```js
/**
 * fireworks: time-slotted bursts. Each ~800ms slot's burst params come from
 * mmDeriveSeed(seed, slotIndex) — deterministic, non-repeating, synced.
 * tMs=900 puts slot 0 (launched at t0=0) mid-explosion (dt=900, et=450).
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));
const W = 1024, H = 768;

test('fireworks — deterministic at same (tMs, seed)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.fireworks(a, 900, W, H, 0, 42);
  byKey.fireworks(b, 900, W, H, 0, 42);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('fireworks — animates (different tMs differs)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.fireworks(a, 900, W, H, 0, 42);
  byKey.fireworks(b, 1100, W, H, 0, 42);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('fireworks — seeded (different seed differs)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.fireworks(a, 900, W, H, 0, 1);
  byKey.fireworks(b, 900, W, H, 0, 2);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('fireworks — explosion draws a bounded particle count', () => {
  const c = makeRecordingCtx();
  byKey.fireworks(c, 900, W, H, 0, 42);   // slot 0 exploding
  const rects = c.__ops.filter((o) => o.op === 'fillRect').length;
  assert.ok(rects > 0 && rects <= 150, `unexpected particle count ${rects}`);
});
```

- [ ] **Step 2: Run to confirm fail**

Run: `node --test tests/unit/js/test_animations_fireworks.js`
Expected: FAIL — `byKey.fireworks is not a function`.

- [ ] **Step 3: Append the entry:**

```js
    {
      key: 'fireworks',
      label: 'Fireworks',
      description: 'Rockets rise and burst — a continuous, never-repeating show.',
      draw: function (ctx, tMs, w, h, nowMs, seed) {
        var SLOT_MS = 800, RISE_MS = 450, LIFE_MS = 1400, G = 0.0009;
        var S = Math.floor(tMs / SLOT_MS), n, j;
        for (n = S - 2; n <= S; n++) {
          if (n < 0) { continue; }
          var dt = tMs - n * SLOT_MS;
          if (dt < 0 || dt >= LIFE_MS) { continue; }
          var brng = MM_RNG(mmDeriveSeed(seed, n));
          var lx = brng() * w;                          // fixed-order pulls
          var py = h * (0.15 + brng() * 0.35);
          var hue = brng() * 360;
          var M = 30 + Math.floor(brng() * 20);         // 30..49 (deterministic count)
          var v = 0.12 + brng() * 0.08;                 // spread speed
          if (dt < RISE_MS) {
            var rp = dt / RISE_MS;
            var ry = h - (h - py) * rp;
            ctx.fillStyle = 'hsl(' + hue + ', 90%, 70%)';
            ctx.fillRect(lx - 1, ry - 1, 3, 3);
          } else {
            var et = dt - RISE_MS;
            var alpha = 1 - et / (LIFE_MS - RISE_MS);
            if (alpha < 0) { alpha = 0; }
            ctx.fillStyle = 'hsla(' + hue + ', 90%, 60%, ' + alpha.toFixed(3) + ')';
            for (j = 0; j < M; j++) {
              var a = (j / M) * Math.PI * 2;
              var dx = Math.cos(a) * v * et;
              var dy = Math.sin(a) * v * et + 0.5 * G * et * et;
              ctx.fillRect(lx + dx - 1, py + dy - 1, 2, 2);
            }
          }
        }
      }
    }
```

- [ ] **Step 4: Run to confirm pass**

Run: `node --test tests/unit/js/test_animations_fireworks.js`  → PASS (4).
Run: `node --test tests/unit/js/test_animations_module.js`  → still PASS.

- [ ] **Step 5: Commit**

```bash
git add js/animations.js tests/unit/js/test_animations_fireworks.js
git commit -m "feat(animations): fireworks (time-slotted seeded bursts)"
```

---

### Task 3: `truchet`

**Files:** Modify `js/animations.js`; Test `tests/unit/js/test_animations_truchet.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_animations_truchet.js`:

```js
/**
 * truchet: seeded grid of quarter-arc tiles (a different "maze" each run);
 * tMs animates only the hue/highlight (arcs static -> trivially pure).
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));
const W = 1024, H = 768;

test('truchet — deterministic at same (tMs, seed)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.truchet(a, 5000, W, H, 0, 42);
  byKey.truchet(b, 5000, W, H, 0, 42);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('truchet — animates (different tMs differs via hue/highlight)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.truchet(a, 1000, W, H, 0, 42);
  byKey.truchet(b, 4000, W, H, 0, 42);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('truchet — seeded (different seed differs)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.truchet(a, 5000, W, H, 0, 1);
  byKey.truchet(b, 5000, W, H, 0, 2);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('truchet — draws two arcs per grid cell', () => {
  const c = makeRecordingCtx();
  byKey.truchet(c, 5000, W, H, 0, 42);
  const cell = Math.min(W, H) / 8;
  const GW = Math.round(W / cell), GH = Math.round(H / cell);
  const arcs = c.__ops.filter((o) => o.op === 'arc').length;
  assert.equal(arcs, GW * GH * 2);
});
```

- [ ] **Step 2: Run to confirm fail**

Run: `node --test tests/unit/js/test_animations_truchet.js`
Expected: FAIL — `byKey.truchet is not a function`.

- [ ] **Step 3: Append the entry:**

```js
    {
      key: 'truchet',
      label: 'Truchet tiles',
      description: 'A generative maze of flowing arcs with a traveling color wave.',
      draw: function (ctx, tMs, w, h, nowMs, seed) {
        var rng = MM_RNG(seed);
        var cell = Math.min(w, h) / 8;
        var GW = Math.round(w / cell), GH = Math.round(h / cell);
        var gx, gy;
        ctx.lineWidth = Math.max(2, cell * 0.12);
        for (gy = 0; gy < GH; gy++) {
          for (gx = 0; gx < GW; gx++) {
            var o = rng() < 0.5 ? 0 : 1;     // fixed-order pull, one per cell
            var x = gx * cell, y = gy * cell;
            var hue = (((gx + gy) * 8) + tMs / 40) % 360;
            var wave = (((gx + gy) - (tMs / 500)) % 8 + 8) % 8;
            var light = (wave < 1) ? 80 : 50;
            ctx.strokeStyle = 'hsl(' + hue + ', 70%, ' + light + '%)';
            if (o === 0) {
              ctx.beginPath(); ctx.arc(x, y, cell / 2, 0, Math.PI / 2); ctx.stroke();
              ctx.beginPath(); ctx.arc(x + cell, y + cell, cell / 2, Math.PI, Math.PI * 1.5); ctx.stroke();
            } else {
              ctx.beginPath(); ctx.arc(x + cell, y, cell / 2, Math.PI / 2, Math.PI); ctx.stroke();
              ctx.beginPath(); ctx.arc(x, y + cell, cell / 2, Math.PI * 1.5, Math.PI * 2); ctx.stroke();
            }
          }
        }
      }
    }
```

- [ ] **Step 4: Run to confirm pass**

Run: `node --test tests/unit/js/test_animations_truchet.js`  → PASS (4).
Run: `node --test tests/unit/js/test_animations_module.js`  → still PASS.

- [ ] **Step 5: Commit**

```bash
git add js/animations.js tests/unit/js/test_animations_truchet.js
git commit -m "feat(animations): truchet tiles (seeded arc maze + color wave)"
```

---

### Task 4: `spirograph`

**Files:** Modify `js/animations.js`; Test `tests/unit/js/test_animations_spirograph.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_animations_spirograph.js`:

```js
/**
 * spirograph: seeded hypotrochoid (gear params R,r,d from the seed -> a
 * different figure each run); tMs traces + slowly rotates it.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));
const W = 1024, H = 768;

test('spirograph — deterministic at same (tMs, seed)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.spirograph(a, 5000, W, H, 0, 42);
  byKey.spirograph(b, 5000, W, H, 0, 42);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('spirograph — animates (different tMs differs)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.spirograph(a, 1000, W, H, 0, 42);
  byKey.spirograph(b, 4000, W, H, 0, 42);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('spirograph — seeded (different seed differs)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.spirograph(a, 5000, W, H, 0, 1);
  byKey.spirograph(b, 5000, W, H, 0, 2);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('spirograph — traces 500 segments', () => {
  const c = makeRecordingCtx();
  byKey.spirograph(c, 5000, W, H, 0, 42);
  assert.equal(c.__ops.filter((o) => o.op === 'moveTo').length, 1);
  assert.equal(c.__ops.filter((o) => o.op === 'lineTo').length, 500);
});
```

- [ ] **Step 2: Run to confirm fail**

Run: `node --test tests/unit/js/test_animations_spirograph.js`
Expected: FAIL — `byKey.spirograph is not a function`.

- [ ] **Step 3: Append the entry:**

```js
    {
      key: 'spirograph',
      label: 'Spirograph',
      description: 'A hypotrochoid curve traced over time — a new figure every run.',
      draw: function (ctx, tMs, w, h, nowMs, seed) {
        var rng = MM_RNG(seed);
        var R = 0.4 + rng() * 0.1;            // fixed-order pulls
        var r = 0.05 + rng() * 0.25;
        var d = 0.3 + rng() * 0.6;
        var N = 500, i;
        var scale = Math.min(w, h) * 0.45;
        var cx = w / 2, cy = h / 2;
        var rot = tMs / 9000;
        var ratio = (R - r) / r;
        var thetaMax = Math.PI * 2 * 8;
        var grow = (tMs / 6000) % 1;
        var tmax = thetaMax * (0.2 + 0.8 * grow);
        var cr = Math.cos(rot), sr = Math.sin(rot);
        ctx.strokeStyle = 'hsl(' + ((tMs / 40) % 360) + ', 70%, 60%)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        for (i = 0; i <= N; i++) {
          var th = (i / N) * tmax;
          var bx = (R - r) * Math.cos(th) + d * r * Math.cos(ratio * th);
          var by = (R - r) * Math.sin(th) - d * r * Math.sin(ratio * th);
          var rx = bx * cr - by * sr;
          var ry = bx * sr + by * cr;
          var px = cx + rx * scale, py = cy + ry * scale;
          if (i === 0) { ctx.moveTo(px, py); } else { ctx.lineTo(px, py); }
        }
        ctx.stroke();
      }
    }
```

- [ ] **Step 4: Run to confirm pass**

Run: `node --test tests/unit/js/test_animations_spirograph.js`  → PASS (4).
Run: `node --test tests/unit/js/test_animations_module.js`  → still PASS.

- [ ] **Step 5: Commit**

```bash
git add js/animations.js tests/unit/js/test_animations_spirograph.js
git commit -m "feat(animations): spirograph (seeded hypotrochoid)"
```

---

### Task 5: Module key-list, full suite, sign-off, review, PR

**Files:** Modify `tests/unit/js/test_animations_module.js`

- [ ] **Step 1: Extend the expected-key list**

In `tests/unit/js/test_animations_module.js`, the `for (const k of [ ... ])` list currently ends with `'sunMoonTransit'`. Add the 4 new keys:

```js
  for (const k of ['bouncingBalls', 'lissajous', 'phyllotaxis', 'wireframeCube',
                   'radialPulse', 'particleGalaxy', 'plasma', 'pendulumWave',
                   'dvdLogo', 'analogClock', 'wordClock', 'sunMoonTransit',
                   'starfield', 'fireworks', 'truchet', 'spirograph']) {
    assert.ok(keys.includes(k), `missing animation "${k}"`);
  }
```

- [ ] **Step 2: Full JS suite**

Run: `python pytest_runner.py --js`
Expected: all pass — the 4 new per-animation files (16 tests) + the updated module key-list + every pre-existing JS test.

- [ ] **Step 3: ES5 compliance check**

Run: `grep -nE "\\b(let|const)\\b|=>|Math\\.imul" js/animations.js`
Expected: matches ONLY inside comment prose (e.g. the word "const" in a comment), never in the new `draw` bodies. Eyeball the 4 new entries: all `var`/`function`, no arrows/template-literals.

- [ ] **Step 4: iPad-1 hardware sign-off (manual gate)**

Assign a playlist of the 4 new SCRIPT items (20s each, `playmode:'SCRIPT'`) to a group with a real iPad-1 and Play-Now it. For each, confirm: renders (not blank), smooth 30+ FPS, two iPads in the group are in visual lockstep (same frame at the same instant) AND show the same per-run variation. Likely-heaviest is `fireworks` (~100 fillRect at burst peak); if it stutters, reduce the particle count range (`30 + floor(brng()*20)`) and update the fireworks structure-test bound. Record per-animation pass/fail in the PR.

- [ ] **Step 5: Final code review**

Use superpowers:requesting-code-review over the branch. Focus: ES5 + no `Math.imul`; the **fixed-order `MM_RNG` pulls before any `tMs`-dependent branch** in every draw (the sync invariant); `fireworks` deriving each slot's stream via `mmDeriveSeed(seed, n)` with a deterministic particle count `M`; seeded-differs + deterministic holding for all four.

- [ ] **Step 6: Finish the branch**

Use superpowers:finishing-a-development-branch. PR summary: 4 seeded generative animations (starfield, fireworks, truchet, spirograph) on the `MM_RNG` infra; spans spatial-config + temporal-event seeding; no server change; iPad-1 sign-off result; sims (step 3) + mosaic-spanning (step 4) still ahead.

---

## Self-Review

**1. Spec coverage:**
| Spec item | Task |
|---|---|
| `starfield` (warp-stars, seeded field + tMs warp) | Task 1 |
| `fireworks` (time-slotted via `mmDeriveSeed(seed,n)`, rise+explosion+gravity+fade) | Task 2 |
| `truchet` (seeded arc grid + tMs hue/highlight wave) | Task 3 |
| `spirograph` (seeded hypotrochoid gear params + progressive trace + rotate) | Task 4 |
| Per-animation determinism/animates/seeded/structure tests | Tasks 1-4 |
| Module key-list +4 | Task 5 Step 1 |
| Determinism rule (fixed-order pulls before tMs branch) | Honored in every draw + checked in Task 5 review |
| iPad-1 sign-off | Task 5 Step 4 |
| No server change | Confirmed (only `js/animations.js` + tests) |
| sims / mosaic-spanning = non-goals | Not implemented (correct) |

**2. Placeholder scan:** No TBD/"handle X"/"similar to". Every `draw` + test is complete code. Task 5 Step 3 is a grep + eyeball (honest: inline-array ES5 has no node lint).

**3. Type/name consistency:** Keys (`starfield`/`fireworks`/`truchet`/`spirograph`) spelled identically in each task's entry, its test (`byKey.NAME`), and Task 5's key-list. Signature `(ctx, tMs, w, h, nowMs, seed)` matches the call form `byKey.NAME(ctx, tMs, W, H, 0, seed)` in every test. `MM_RNG`/`mmDeriveSeed` used per the shipped closure helpers. Structure-test op counts match each draw (starfield ≤200 strokes; fireworks ≤150 fillRect; truchet `GW*GH*2` arcs; spirograph 1 moveTo + 500 lineTo).
