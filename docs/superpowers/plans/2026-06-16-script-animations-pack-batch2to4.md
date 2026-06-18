# SCRIPT Animations Pack — Batches 2-4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add eight deterministic, clock-synced SCRIPT animations (`radialPulse`, `particleGalaxy`, `plasma`, `pendulumWave`, `dvdLogo`, `analogClock`, `wordClock`, `sunMoonTransit`) to the existing playback engine, plus the one-line `nowMs` wiring the three wall-clock animations need.

**Architecture:** Each animation is one self-describing entry `{key, label, description, draw}` appended to the `MM_ANIMATIONS` array in `js/animations.js` — the single ES5 source of truth loaded by `index.html` (display client), `admin.html` (picker), and the Node `--test` determinism suite. `draw(ctx, tMs, w, h, nowMs)` is a pure function of elapsed time (and, for wall-clock animations, the shared `GoTime.now()` value passed as the 5th arg), so every screen draws the identical frame. No server changes; no new files except per-animation tests.

**Tech Stack:** ES5 + Canvas2D (the module must run on a 1st-gen iPad / Safari 5.1), Node 20 `node --test` (`python pytest_runner.py --js`), Playwright via `tests/e2e/run.js` (browser smoke). No build step.

**Spec:** `docs/superpowers/specs/2026-06-09-script-animations-pack-design.md` (animations #3, #4, #5, #6, #9, #10, #11 + #8). **Deferred to a later batch:** `gameOfLife` (needs a precomputed-cycle pattern) and `fleetStatus` (heartbeat-fed, breaks pure-function-of-time — the spec says it "deserves its own focused review"). This plan is the 8 pure-leaf / wall-clock additions only.

---

## Background the implementer needs

**The current architecture (post Section-2 refactor — this is NOT the batch-1 mirror world):**

- `js/animations.js` (ES5, no module syntax) defines `var animations = [ {key,label,description,draw}, ... ]` and assigns `root.MM_ANIMATIONS = animations` (`root` = `window` in the browser, `globalThis` in Node). It currently holds `bouncingBalls`, `lissajous`, `phyllotaxis`, `wireframeCube`. Read it before starting — you append entries to that array.
- `index.html` loads it via `<script src="/js/animations.js"></script>` (line ~23), then builds a `name -> draw` map: `for (...) { animations[list[i].key] = list[i].draw; }` (line ~466). `runScriptLoop` calls `animations[name](ctx, pos.offsetMs, canvas.width, canvas.height)` (line ~485), guarded by `if (animations[name])`.
- `admin.html` loads the same file; the playlist editor + content list read `window.MM_ANIMATIONS` directly (no separate catalog file — the array IS the catalog, because each entry carries `label`/`description`). So **adding an entry makes it appear in the operator picker automatically** — no admin-side change needed.
- Node tests import the module for side-effect and read `globalThis.MM_ANIMATIONS`:
  ```js
  import { makeRecordingCtx } from './_canvas_stub.js';
  await import('../../../js/animations.js');
  const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));
  ```
- `tests/unit/js/_canvas_stub.js` exports `makeRecordingCtx()` — a Proxy that records every method call as `{op, args}` and every property set as `{set, value}` into `ctx.__ops`. `createLinearGradient`/`createRadialGradient` return a recording gradient whose `addColorStop` calls are also logged. Two runs of a pure animation at the same inputs produce deep-equal `__ops` — that IS the cross-screen sync guarantee in testable form.
- The determinism unit-test idiom (copy from `tests/unit/js/test_animations_lissajous.js`): three tests per animation — **deterministic** (same inputs → `deepStrictEqual` ops), **animates** (different inputs → `notDeepStrictEqual`), **draws something** (non-empty / exact op-count).

**The data path (no server work):** the playlist editor saves SCRIPT items as `{id, file:<animationKey>, duration, playmode:'SCRIPT'}` verbatim through `PUT /api/playlists`; `render._build_media_elements` maps `'SCRIPT'` → `PlayMode.SCRIPT`; `_media_item_payload` emits `playmode:'SCRIPT'`; `showItem` keys off it and calls `runScriptLoop(canvas, item.file)`. `item.file` is the registry key. Unchanged by this plan.

**The `nowMs` 5th argument (Task 1):** `analogClock`, `wordClock`, `sunMoonTransit` need wall-clock time. The contract is `draw(ctx, tMs, w, h, nowMs)` where `nowMs = GoTime.now()` — the same shared-offset clock every screen reads, so every screen sees the same wall-clock to within the GoTime drift budget. Animations that don't need it ignore the 5th arg. Task 1 adds `GoTime.now()` to the `runScriptLoop` call site once; the Node tests pass `nowMs` explicitly so they don't depend on `index.html`.

**ES5 rules for `js/animations.js` (hard requirement — runs on iPad-1 / Safari 5.1):**
- `var` only — no `let`/`const`. `function` only — no arrows. No template literals (string build with `+`). No `Array.prototype.includes`/`Array.from`/`Object.assign`. No `Path2D`.
- Canvas2D primitives only: `fillStyle`/`strokeStyle`/`lineWidth`/`globalAlpha`/`font`/`textBaseline`, `beginPath`/`moveTo`/`lineTo`/`arc`/`fill`/`stroke`/`fillRect`/`clearRect`/`fillText`, `createLinearGradient`+`addColorStop`. All confirmed available on iOS 5 Safari.
- Each `draw` is **stateless across calls** — no module-level mutable state; per-frame values recomputed from `tMs`/`nowMs`. Internal constants (counts, palettes, periods) are fine.
- Match the file's existing 2-space indentation inside the array.

**Branch:** Create `feature/script-animations-batch2to4` off `main` (`origin/main` after PRs #37/#38 land, or current `main` if not). Do NOT start on `main`. Do NOT stack on the GC or py314 branches.

**Plasma rendering-strategy note (deliberate deviation from the spec):** the spec renders `plasma` via a low-res `ImageData` + `putImageData` + scaled `drawImage` (offscreen canvas). That path needs an offscreen-canvas stub in Node to be determinism-testable, and `createImageData` returns `undefined` from the current recording stub. We instead render plasma as a **coarse `fillRect` grid** (40×30 cells, each `fillRect` colored by the plasma field). This is testable with the existing op-log stub (no stub change), iPad-1-friendly, and reads as authentic blocky demoscene plasma. If the iPad-1 hardware sign-off (Task 10) shows stutter, drop the grid to 32×24 in `js/animations.js` and update the plasma test's `fillRect` count assertion. This is the same "drop resolution" knob the spec anticipated.

---

## File Structure

| File | Responsibility | Create/Modify |
|------|----------------|---------------|
| `index.html` | Pass `GoTime.now()` as the 5th arg in the `runScriptLoop` draw call (line ~485). | Modify (Task 1) |
| `js/animations.js` | Append 8 animation entries to the `MM_ANIMATIONS` array; update the header comment to document the `(ctx, tMs, w, h, nowMs)` signature. | Modify (Tasks 1-9) |
| `tests/unit/js/test_animations_radialpulse.js` | Determinism/animates/draws for `radialPulse`. | Create (Task 2) |
| `tests/unit/js/test_animations_particlegalaxy.js` | Same for `particleGalaxy`. | Create (Task 3) |
| `tests/unit/js/test_animations_plasma.js` | Same for `plasma`. | Create (Task 4) |
| `tests/unit/js/test_animations_pendulumwave.js` | Same for `pendulumWave`. | Create (Task 5) |
| `tests/unit/js/test_animations_dvdlogo.js` | Same for `dvdLogo`. | Create (Task 6) |
| `tests/unit/js/test_animations_analogclock.js` | Same for `analogClock` (wall-clock: varies `nowMs`). | Create (Task 7) |
| `tests/unit/js/test_animations_wordclock.js` | Same for `wordClock` (wall-clock). | Create (Task 8) |
| `tests/unit/js/test_animations_suntransit.js` | Same for `sunMoonTransit` (wall-clock). | Create (Task 9) |
| `tests/unit/js/test_animations_module.js` | Extend the expected-keys list to include all 8 new animations. | Modify (Task 10) |
| `tests/e2e/test-script-animations.spec.js` | Extend the existing SCRIPT smoke to render a couple of the new animations. | Modify (Task 10) |

---

### Task 1: Wire `nowMs` (5th arg) for wall-clock animations

**Files:**
- Modify: `index.html` (the `runScriptLoop` draw call, ~line 485)
- Modify: `js/animations.js` (header comment only)

This must land before Tasks 7-9 (the wall-clock animations) so they receive a real clock in the browser. There is no Node unit test for an inline-`index.html` edit (it's not importable); it's verified by grep here and by the wall-clock animations' browser/e2e smoke in Task 10. The Node determinism tests for Tasks 7-9 pass `nowMs` explicitly and do not depend on this edit.

- [ ] **Step 1: Read the call site**

Run: `grep -n "animations\[name\](ctx" index.html`
Expected: one line, `~485`: `if (animations[name]) { animations[name](ctx, pos.offsetMs, canvas.width, canvas.height); }`

- [ ] **Step 2: Add `GoTime.now()` as the 5th argument**

In `index.html`, change that line to pass the shared clock as `nowMs`:

```js
			if (animations[name]) { animations[name](ctx, pos.offsetMs, canvas.width, canvas.height, GoTime.now()); }
```

(Use a TAB-leading indentation to match the surrounding inline script. `GoTime` is already in scope at this call site — the same `GoTime.now()` used on lines 481/516.)

- [ ] **Step 3: Document the signature in the module header**

In `js/animations.js`, the header comment says `draw(ctx, tMs, w, h) is a PURE function of elapsed time + canvas size`. Replace that sentence with:

```js
 * Each entry is self-describing; draw(ctx, tMs, w, h, nowMs) is a PURE function
 * of elapsed time (tMs), canvas size, and — for wall-clock animations only —
 * the shared GoTime.now() value (nowMs), so every display draws the same frame.
 * Animations that don't need wall-clock time ignore the 5th argument.
```

- [ ] **Step 4: Verify the edit + module still loads**

Run: `grep -n "GoTime.now())" index.html` → confirms the 5th arg is present on the draw line.
Run: `node --test tests/unit/js/test_animations_module.js`
Expected: PASS (the module still imports cleanly; no entries added yet).

- [ ] **Step 5: Commit**

```bash
git add index.html js/animations.js
git commit -m "feat(animations): pass GoTime.now() as nowMs 5th arg for wall-clock animations"
```

---

### Task 2: `radialPulse` — concentric pulse rings

**Files:**
- Modify: `js/animations.js` (append to `MM_ANIMATIONS`)
- Test: `tests/unit/js/test_animations_radialpulse.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_animations_radialpulse.js`:

```js
/**
 * radialPulse: K=5 concentric rings expanding from center, fading out.
 * Determinism (sync), animates, and draws exactly K stroked rings.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));

const W = 1024, H = 768;

test('radialPulse — deterministic at same tMs', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.radialPulse(a, 12345, W, H);
  byKey.radialPulse(b, 12345, W, H);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('radialPulse — animates (different tMs ⇒ different output)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.radialPulse(a, 1000, W, H);
  byKey.radialPulse(b, 3000, W, H);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('radialPulse — strokes 5 rings', () => {
  const c = makeRecordingCtx();
  byKey.radialPulse(c, 5000, W, H);
  const arcs = c.__ops.filter((o) => o.op === 'arc').length;
  assert.equal(arcs, 5);
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --test tests/unit/js/test_animations_radialpulse.js`
Expected: FAIL — `byKey.radialPulse is not a function`.

- [ ] **Step 3: Append the entry to `MM_ANIMATIONS`**

In `js/animations.js`, the array currently ends with the `wireframeCube` entry then `]` (line ~102). Change `wireframeCube`'s closing `}` to `},` and insert before the array's closing `]`:

```js
    {
      key: 'radialPulse',
      label: 'Radial pulse',
      description: 'Concentric color rings expanding from the center and fading out.',
      draw: function (ctx, tMs, w, h) {
        var K = 5, PERIOD = 4000, k;
        var cx = w / 2, cy = h / 2;
        var maxR = Math.sqrt(w * w + h * h) / 2;
        ctx.lineWidth = Math.max(0.1, 4 + 6 * Math.sin(tMs / 1000));
        for (k = 0; k < K; k++) {
          var frac = ((tMs / PERIOD) + (k / K)) % 1;
          var R = frac * maxR;
          var alpha = 1 - (R / maxR);
          ctx.globalAlpha = alpha < 0 ? 0 : (alpha > 1 ? 1 : alpha);
          ctx.strokeStyle = 'hsl(' + (((tMs / 40) + k * 30) % 360) + ', 80%, 60%)';
          ctx.beginPath();
          ctx.arc(cx, cy, R > 0.1 ? R : 0.1, 0, Math.PI * 2);
          ctx.stroke();
        }
        ctx.globalAlpha = 1;
      }
    }
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `node --test tests/unit/js/test_animations_radialpulse.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add js/animations.js tests/unit/js/test_animations_radialpulse.js
git commit -m "feat(animations): radialPulse rings (batch 2)"
```

---

### Task 3: `particleGalaxy` — orbital particles

**Files:**
- Modify: `js/animations.js`
- Test: `tests/unit/js/test_animations_particlegalaxy.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_animations_particlegalaxy.js`:

```js
/**
 * particleGalaxy: N=400 particles on golden-ratio-spread Keplerian orbits,
 * drawn as 2px fillRect dots. Determinism, animates, exact dot count.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));

const W = 1024, H = 768;

test('particleGalaxy — deterministic at same tMs', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.particleGalaxy(a, 22222, W, H);
  byKey.particleGalaxy(b, 22222, W, H);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('particleGalaxy — animates (different tMs ⇒ different output)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.particleGalaxy(a, 1000, W, H);
  byKey.particleGalaxy(b, 9000, W, H);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('particleGalaxy — draws 400 particles', () => {
  const c = makeRecordingCtx();
  byKey.particleGalaxy(c, 5000, W, H);
  const rects = c.__ops.filter((o) => o.op === 'fillRect').length;
  assert.equal(rects, 400);
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --test tests/unit/js/test_animations_particlegalaxy.js`
Expected: FAIL — `byKey.particleGalaxy is not a function`.

- [ ] **Step 3: Append the entry**

In `js/animations.js`, change the `radialPulse` entry's closing `}` to `},` and insert before the array's closing `]`:

```js
    {
      key: 'particleGalaxy',
      label: 'Particle galaxy',
      description: 'A slow galactic swirl of particles on Keplerian orbits.',
      draw: function (ctx, tMs, w, h) {
        var N = 400, i;
        var cx = w / 2, cy = h / 2;
        var mn = Math.min(w, h);
        var RMIN = mn * 0.08, RMAX = mn * 0.45, W0 = 0.0008;
        var GOLD = 137.5 * Math.PI / 180;
        for (i = 0; i < N; i++) {
          var r = RMIN + (RMAX - RMIN) * ((i * 0.6180339887) % 1);
          var omega = W0 * Math.sqrt(RMIN / r);
          var phi = i * GOLD;
          var x = cx + r * Math.cos(omega * tMs + phi);
          var y = cy + r * Math.sin(omega * tMs + phi);
          ctx.fillStyle = 'hsl(' + (((tMs / 80) + i * 2) % 360) + ', 80%, 60%)';
          ctx.fillRect(x - 1, y - 1, 2, 2);
        }
      }
    }
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `node --test tests/unit/js/test_animations_particlegalaxy.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add js/animations.js tests/unit/js/test_animations_particlegalaxy.js
git commit -m "feat(animations): particleGalaxy orbits (batch 2)"
```

---

### Task 4: `plasma` — demoscene plasma (fillRect grid)

**Files:**
- Modify: `js/animations.js`
- Test: `tests/unit/js/test_animations_plasma.js`

See the "Plasma rendering-strategy note" above: this uses a 40×30 `fillRect` grid (1200 cells), not the spec's `ImageData` path, for testability + iPad-1 friendliness.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_animations_plasma.js`:

```js
/**
 * plasma: a 40x30 fillRect grid colored by a sum-of-sines field. The
 * synchronization-critical property is determinism of the field; we assert
 * deterministic op log, animation over time, and the exact 40*30 cell count.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));

const W = 1024, H = 768;

test('plasma — deterministic at same tMs', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.plasma(a, 31415, W, H);
  byKey.plasma(b, 31415, W, H);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('plasma — animates (different tMs ⇒ different output)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.plasma(a, 1000, W, H);
  byKey.plasma(b, 6000, W, H);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('plasma — fills a 40x30 grid', () => {
  const c = makeRecordingCtx();
  byKey.plasma(c, 5000, W, H);
  const rects = c.__ops.filter((o) => o.op === 'fillRect').length;
  assert.equal(rects, 1200);
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --test tests/unit/js/test_animations_plasma.js`
Expected: FAIL — `byKey.plasma is not a function`.

- [ ] **Step 3: Append the entry**

In `js/animations.js`, change the `particleGalaxy` entry's closing `}` to `},` and insert before the array's closing `]`:

```js
    {
      key: 'plasma',
      label: 'Plasma',
      description: 'Classic demoscene plasma — smoothly shifting color clouds.',
      draw: function (ctx, tMs, w, h) {
        var GW = 40, GH = 30, gx, gy;
        var k1 = 8, k2 = 12, k3 = 10, k4 = 14;
        var T1 = 2500, T2 = 3300, T3 = 4100, T4 = 1900;
        var cw = w / GW, ch = h / GH;
        for (gy = 0; gy < GH; gy++) {
          for (gx = 0; gx < GW; gx++) {
            var u = gx / GW, v = gy / GH;
            var du = u - 0.5, dv = v - 0.5;
            var c = Math.sin(u * k1 + tMs / T1)
                  + Math.sin(v * k2 + tMs / T2)
                  + Math.sin((u + v) * k3 + tMs / T3)
                  + Math.sin(Math.sqrt(du * du + dv * dv) * k4 + tMs / T4);
            ctx.fillStyle = 'hsl(' + (((c + 4) / 8) * 360) + ', 100%, 50%)';
            ctx.fillRect(gx * cw, gy * ch, cw + 1, ch + 1);
          }
        }
      }
    }
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `node --test tests/unit/js/test_animations_plasma.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add js/animations.js tests/unit/js/test_animations_plasma.js
git commit -m "feat(animations): plasma field as fillRect grid (batch 2)"
```

---

### Task 5: `pendulumWave` — multi-pendulum interference

**Files:**
- Modify: `js/animations.js`
- Test: `tests/unit/js/test_animations_pendulumwave.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_animations_pendulumwave.js`:

```js
/**
 * pendulumWave: N=16 pendulums with staggered periods that scramble and
 * re-sync over minutes. Determinism, animates, one bob (arc) per pendulum.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));

const W = 1024, H = 768;

test('pendulumWave — deterministic at same tMs', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.pendulumWave(a, 44444, W, H);
  byKey.pendulumWave(b, 44444, W, H);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('pendulumWave — animates (different tMs ⇒ different output)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.pendulumWave(a, 1000, W, H);
  byKey.pendulumWave(b, 5000, W, H);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('pendulumWave — draws 16 bobs', () => {
  const c = makeRecordingCtx();
  byKey.pendulumWave(c, 5000, W, H);
  const arcs = c.__ops.filter((o) => o.op === 'arc').length;
  assert.equal(arcs, 16);
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --test tests/unit/js/test_animations_pendulumwave.js`
Expected: FAIL — `byKey.pendulumWave is not a function`.

- [ ] **Step 3: Append the entry**

In `js/animations.js`, change the `plasma` entry's closing `}` to `},` and insert before the array's closing `]`:

```js
    {
      key: 'pendulumWave',
      label: 'Pendulum wave',
      description: 'Sixteen pendulums with staggered periods scrambling and re-syncing.',
      draw: function (ctx, tMs, w, h) {
        var N = 16, i;
        var TB = 4000, TS = 80, AMAX = Math.PI / 6;
        var L = h * 0.7, y0 = h * 0.15;
        for (i = 0; i < N; i++) {
          var xi = (i + 0.5) * w / N;
          var Ti = TB - i * TS;
          var theta = AMAX * Math.sin(2 * Math.PI * tMs / Ti);
          var bx = xi + L * Math.sin(theta);
          var by = y0 + L * Math.cos(theta);
          ctx.strokeStyle = '#ffffff';
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(xi, y0);
          ctx.lineTo(bx, by);
          ctx.stroke();
          ctx.fillStyle = 'hsl(' + ((i / N) * 360) + ', 80%, 60%)';
          ctx.beginPath();
          ctx.arc(bx, by, 8, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    }
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `node --test tests/unit/js/test_animations_pendulumwave.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add js/animations.js tests/unit/js/test_animations_pendulumwave.js
git commit -m "feat(animations): pendulumWave (batch 4)"
```

---

### Task 6: `dvdLogo` — bouncing logo (closed-form on tMs)

**Files:**
- Modify: `js/animations.js`
- Test: `tests/unit/js/test_animations_dvdlogo.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_animations_dvdlogo.js`:

```js
/**
 * dvdLogo: a logo bouncing off edges via a closed-form triangle wave on tMs
 * (not an integrator — so every screen agrees on position and bounce color).
 * Determinism, animates, draws the label text.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));

const W = 1024, H = 768;

test('dvdLogo — deterministic at same tMs', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.dvdLogo(a, 17000, W, H);
  byKey.dvdLogo(b, 17000, W, H);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('dvdLogo — animates (different tMs ⇒ different output)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.dvdLogo(a, 1000, W, H);
  byKey.dvdLogo(b, 12000, W, H);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('dvdLogo — draws the label', () => {
  const c = makeRecordingCtx();
  byKey.dvdLogo(c, 5000, W, H);
  const texts = c.__ops.filter((o) => o.op === 'fillText');
  assert.equal(texts.length, 1);
  assert.equal(texts[0].args[0], 'MOSAICMESH');
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --test tests/unit/js/test_animations_dvdlogo.js`
Expected: FAIL — `byKey.dvdLogo is not a function`.

- [ ] **Step 3: Append the entry**

In `js/animations.js`, change the `pendulumWave` entry's closing `}` to `},` and insert before the array's closing `]`:

```js
    {
      key: 'dvdLogo',
      label: 'Bouncing logo',
      description: 'A MOSAICMESH logo bouncing off the edges, recoloring on each hit.',
      draw: function (ctx, tMs, w, h) {
        var lw = w * 0.18, lh = h * 0.06;
        var vx = 80, vy = 50;
        var rangeX = w - lw, rangeY = h - lh;
        var xRaw = vx * tMs / 1000, yRaw = vy * tMs / 1000;
        var periodX = 2 * rangeX, periodY = 2 * rangeY;
        var x = Math.abs((xRaw % periodX) - rangeX);
        var y = Math.abs((yRaw % periodY) - rangeY);
        var bounces = Math.floor(xRaw / rangeX) + Math.floor(yRaw / rangeY);
        var palette = ['#e74c3c', '#e67e22', '#f1c40f', '#2ecc71', '#1abc9c',
                       '#3498db', '#9b59b6', '#e84393', '#fd79a8', '#00cec9',
                       '#6c5ce7', '#fab1a0', '#55efc4', '#ffeaa7', '#74b9ff'];
        ctx.fillStyle = palette[((bounces % palette.length) + palette.length) % palette.length];
        ctx.font = 'bold ' + Math.round(lh * 0.9) + 'px sans-serif';
        ctx.textBaseline = 'top';
        ctx.fillText('MOSAICMESH', x, y);
      }
    }
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `node --test tests/unit/js/test_animations_dvdlogo.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add js/animations.js tests/unit/js/test_animations_dvdlogo.js
git commit -m "feat(animations): dvdLogo bouncing label (batch 4)"
```

---

### Task 7: `analogClock` — analog clock face (wall-clock)

**Files:**
- Modify: `js/animations.js`
- Test: `tests/unit/js/test_animations_analogclock.js`

Wall-clock animation: `draw(ctx, tMs, w, h, nowMs)`. Its output depends on `nowMs` (the shared `GoTime.now()`), NOT `tMs` — so the determinism test fixes `nowMs`, and the **animates** test varies `nowMs` (not `tMs`).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_animations_analogclock.js`:

```js
/**
 * analogClock: hour/minute/second hands driven by the shared wall clock
 * (nowMs = GoTime.now()). Determinism is in (tMs, nowMs); the clock advances
 * with nowMs, so the "animates" check varies nowMs.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));

const W = 1024, H = 768;
const NOON = Date.UTC(2026, 0, 1, 12, 0, 0);

test('analogClock — deterministic at same (tMs, nowMs)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.analogClock(a, 0, W, H, NOON);
  byKey.analogClock(b, 0, W, H, NOON);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('analogClock — advances with nowMs (different time ⇒ different output)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.analogClock(a, 0, W, H, NOON);
  byKey.analogClock(b, 0, W, H, NOON + 7 * 60 * 1000); // +7 minutes
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('analogClock — draws face, 12 ticks, 3 hands', () => {
  const c = makeRecordingCtx();
  byKey.analogClock(c, 0, W, H, NOON);
  const arcs = c.__ops.filter((o) => o.op === 'arc').length;
  const strokes = c.__ops.filter((o) => o.op === 'stroke').length;
  assert.equal(arcs, 1);          // the face circle
  assert.equal(strokes, 1 + 12 + 3); // face + 12 ticks + 3 hands
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --test tests/unit/js/test_animations_analogclock.js`
Expected: FAIL — `byKey.analogClock is not a function`.

- [ ] **Step 3: Append the entry**

In `js/animations.js`, change the `dvdLogo` entry's closing `}` to `},` and insert before the array's closing `]`:

```js
    {
      key: 'analogClock',
      label: 'Analog clock',
      description: 'A synchronized analog clock face (hours, minutes, seconds).',
      draw: function (ctx, tMs, w, h, nowMs) {
        var cx = w / 2, cy = h / 2;
        var R = Math.min(w, h) * 0.45;
        var d = new Date(nowMs || 0);
        var H12 = d.getHours() % 12, M = d.getMinutes(), S = d.getSeconds();
        var k;
        ctx.fillStyle = '#000000';
        ctx.fillRect(0, 0, w, h);
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(cx, cy, R, 0, Math.PI * 2);
        ctx.stroke();
        for (k = 0; k < 12; k++) {
          var a = k * Math.PI / 6 - Math.PI / 2;
          var inner = (k % 3 === 0) ? 0.86 : 0.92;
          ctx.lineWidth = (k % 3 === 0) ? 4 : 2;
          ctx.beginPath();
          ctx.moveTo(cx + Math.cos(a) * R * inner, cy + Math.sin(a) * R * inner);
          ctx.lineTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R);
          ctx.stroke();
        }
        var ah = (H12 + M / 60) * Math.PI / 6 - Math.PI / 2;
        var am = (M + S / 60) * Math.PI / 30 - Math.PI / 2;
        var as = S * Math.PI / 30 - Math.PI / 2;
        ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 5;
        ctx.beginPath(); ctx.moveTo(cx, cy);
        ctx.lineTo(cx + Math.cos(ah) * R * 0.5, cy + Math.sin(ah) * R * 0.5); ctx.stroke();
        ctx.lineWidth = 3;
        ctx.beginPath(); ctx.moveTo(cx, cy);
        ctx.lineTo(cx + Math.cos(am) * R * 0.7, cy + Math.sin(am) * R * 0.7); ctx.stroke();
        ctx.strokeStyle = '#e74c3c'; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(cx, cy);
        ctx.lineTo(cx + Math.cos(as) * R * 0.75, cy + Math.sin(as) * R * 0.75); ctx.stroke();
      }
    }
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `node --test tests/unit/js/test_animations_analogclock.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add js/animations.js tests/unit/js/test_animations_analogclock.js
git commit -m "feat(animations): analogClock wall-clock face (batch 3)"
```

---

### Task 8: `wordClock` — letter-grid word clock (wall-clock)

**Files:**
- Modify: `js/animations.js`
- Test: `tests/unit/js/test_animations_wordclock.js`

A 13-column × 8-row letter grid. Every letter is drawn each frame (dim or lit); the lit set spells the current time ("IT IS … PAST/TO …"). The grid + word-position table below are self-consistent — verify by reading each word off the grid at its `(row, col, len)`.

Grid (each row exactly 13 chars):
```
row0: I T L I S H K M F I V E X   -> IT(0,2) IS(3,2) FIVE-min(8,4)
row1: T W E N T Y Q U A R T E R   -> TWENTY(0,6) QUARTER(6,7)
row2: H A L F B T E N P A S T O   -> HALF(0,4) TEN-min(5,3) PAST(8,4) TO(11,2)
row3: O N E T W O T H R E E X X   -> ONE(0,3) TWO(3,3) THREE(6,5)
row4: F O U R F I V E S I X X X   -> FOUR(0,4) FIVE-hr(4,4) SIX(8,3)
row5: S E V E N E I G H T X X X   -> SEVEN(0,5) EIGHT(5,5)
row6: N I N E T E N E L E V E N   -> NINE(0,4) TEN-hr(4,3) ELEVEN(7,6)
row7: T W E L V E O C L O C K X   -> TWELVE(0,6) OCLOCK(6,6)
```

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_animations_wordclock.js`:

```js
/**
 * wordClock: a 13x8 letter grid; lit letters spell the rounded time.
 * Determinism in (tMs, nowMs); advances with nowMs. Every cell is drawn
 * (dim or lit) so fillText count == 13*8 = 104.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));

const W = 1024, H = 768;
const T_1010 = Date.UTC(2026, 0, 1, 10, 10, 0); // "ten past ten"
const T_0345 = Date.UTC(2026, 0, 1, 3, 45, 0);  // "quarter to four"

test('wordClock — deterministic at same (tMs, nowMs)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.wordClock(a, 0, W, H, T_1010);
  byKey.wordClock(b, 0, W, H, T_1010);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('wordClock — different times light different words', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.wordClock(a, 0, W, H, T_1010);
  byKey.wordClock(b, 0, W, H, T_0345);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('wordClock — draws every cell of the 13x8 grid', () => {
  const c = makeRecordingCtx();
  byKey.wordClock(c, 0, W, H, T_1010);
  const texts = c.__ops.filter((o) => o.op === 'fillText').length;
  assert.equal(texts, 104);
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --test tests/unit/js/test_animations_wordclock.js`
Expected: FAIL — `byKey.wordClock is not a function`.

- [ ] **Step 3: Append the entry**

In `js/animations.js`, change the `analogClock` entry's closing `}` to `},` and insert before the array's closing `]`:

```js
    {
      key: 'wordClock',
      label: 'Word clock',
      description: 'A letter grid that lights up to spell the current time in words.',
      draw: function (ctx, tMs, w, h, nowMs) {
        var ROWS = [
          'ITLISHKMFIVEX',
          'TWENTYQUARTER',
          'HALFBTENPASTO',
          'ONETWOTHREEXX',
          'FOURFIVESIXXX',
          'SEVENEIGHTXXX',
          'NINETENELEVEN',
          'TWELVEOCLOCKX'
        ];
        var COLS = 13, NROW = 8;
        // Word positions: [row, col, len].
        var P = {
          IT: [0, 0, 2], IS: [0, 3, 2], M_FIVE: [0, 8, 4],
          M_TWENTY: [1, 0, 6], M_QUARTER: [1, 6, 7],
          M_HALF: [2, 0, 4], M_TEN: [2, 5, 3], PAST: [2, 8, 4], TO: [2, 11, 2],
          H1: [3, 0, 3], H2: [3, 3, 3], H3: [3, 6, 5],
          H4: [4, 0, 4], H5: [4, 4, 4], H6: [4, 8, 3],
          H7: [5, 0, 5], H8: [5, 5, 5],
          H9: [6, 0, 4], H10: [6, 4, 3], H11: [6, 7, 6],
          H12: [7, 0, 6], OCLOCK: [7, 6, 6]
        };
        var HOURWORD = [null, 'H1', 'H2', 'H3', 'H4', 'H5', 'H6',
                        'H7', 'H8', 'H9', 'H10', 'H11', 'H12'];
        var d = new Date(nowMs || 0);
        var hr = d.getHours(), mn = d.getMinutes();
        var slot = Math.floor(mn / 5);              // 0..11
        var lit = ['IT', 'IS'], dispHour = hr % 12;
        if (slot === 0) { /* o'clock */ }
        else if (slot === 1) { lit.push('M_FIVE', 'PAST'); }
        else if (slot === 2) { lit.push('M_TEN', 'PAST'); }
        else if (slot === 3) { lit.push('M_QUARTER', 'PAST'); }
        else if (slot === 4) { lit.push('M_TWENTY', 'PAST'); }
        else if (slot === 5) { lit.push('M_TWENTY', 'M_FIVE', 'PAST'); }
        else if (slot === 6) { lit.push('M_HALF', 'PAST'); }
        else if (slot === 7) { lit.push('M_TWENTY', 'M_FIVE', 'TO'); }
        else if (slot === 8) { lit.push('M_TWENTY', 'TO'); }
        else if (slot === 9) { lit.push('M_QUARTER', 'TO'); }
        else if (slot === 10) { lit.push('M_TEN', 'TO'); }
        else { lit.push('M_FIVE', 'TO'); }
        if (slot >= 7) { dispHour = (hr + 1) % 12; }
        var hourIdx = (dispHour === 0) ? 12 : dispHour;
        lit.push(HOURWORD[hourIdx]);
        if (slot === 0) { lit.push('OCLOCK'); }
        // Build a lit-cell lookup (row*COLS+col -> true).
        var on = {}, i, j;
        for (i = 0; i < lit.length; i++) {
          var p = P[lit[i]];
          for (j = 0; j < p[2]; j++) { on[p[0] * COLS + (p[1] + j)] = true; }
        }
        // Render the grid.
        var cell = Math.min(w / COLS, h / NROW);
        var fs = Math.floor(cell * 0.7);
        var ox = (w - cell * COLS) / 2, oy = (h - cell * NROW) / 2;
        ctx.fillStyle = '#000000';
        ctx.fillRect(0, 0, w, h);
        ctx.font = fs + 'px monospace';
        ctx.textBaseline = 'top';
        var r, c2;
        for (r = 0; r < NROW; r++) {
          for (c2 = 0; c2 < COLS; c2++) {
            ctx.fillStyle = on[r * COLS + c2] ? '#ffffff' : '#333333';
            ctx.fillText(ROWS[r].charAt(c2), ox + c2 * cell, oy + r * cell);
          }
        }
      }
    }
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `node --test tests/unit/js/test_animations_wordclock.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add js/animations.js tests/unit/js/test_animations_wordclock.js
git commit -m "feat(animations): wordClock letter grid (batch 3)"
```

---

### Task 9: `sunMoonTransit` — astronomical transit (wall-clock)

**Files:**
- Modify: `js/animations.js`
- Test: `tests/unit/js/test_animations_suntransit.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_animations_suntransit.js`:

```js
/**
 * sunMoonTransit: a body traversing an arc, day/night palette by nowMs.
 * Determinism in (tMs, nowMs); advances with nowMs; draws a gradient
 * background + the body (one arc).
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));

const W = 1024, H = 768;
const NOON = Date.UTC(2026, 0, 1, 12, 0, 0);
const MIDNIGHT = Date.UTC(2026, 0, 1, 0, 0, 0);

test('sunMoonTransit — deterministic at same (tMs, nowMs)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.sunMoonTransit(a, 0, W, H, NOON);
  byKey.sunMoonTransit(b, 0, W, H, NOON);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('sunMoonTransit — day vs night differ', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.sunMoonTransit(a, 0, W, H, NOON);
  byKey.sunMoonTransit(b, 0, W, H, MIDNIGHT);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('sunMoonTransit — draws a gradient background and the body', () => {
  const c = makeRecordingCtx();
  byKey.sunMoonTransit(c, 0, W, H, NOON);
  const grads = c.__ops.filter((o) => o.op === 'createLinearGradient').length;
  const arcs = c.__ops.filter((o) => o.op === 'arc').length;
  assert.equal(grads, 1);
  assert.ok(arcs >= 1, 'expected at least the body arc');
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `node --test tests/unit/js/test_animations_suntransit.js`
Expected: FAIL — `byKey.sunMoonTransit is not a function`.

- [ ] **Step 3: Append the entry**

In `js/animations.js`, change the `wordClock` entry's closing `}` to `},` and insert before the array's closing `]`:

```js
    {
      key: 'sunMoonTransit',
      label: 'Sun / moon transit',
      description: 'A sun (day) or moon (night) arcing across the sky by the wall clock.',
      draw: function (ctx, tMs, w, h, nowMs) {
        var d = new Date(nowMs || 0);
        var hh = d.getHours() + d.getMinutes() / 60;
        var isDay = (hh >= 6 && hh < 18);
        var t;
        if (isDay) { t = (hh - 6) / 12; }
        else { t = (hh < 6) ? (hh + 6) / 12 : (hh - 18) / 12; }
        if (t < 0) { t = 0; }
        if (t > 1) { t = 1; }
        var grad = ctx.createLinearGradient(0, 0, 0, h);
        if (isDay) {
          grad.addColorStop(0, '#4a90d9');
          grad.addColorStop(1, '#bfe3ff');
        } else {
          grad.addColorStop(0, '#06070f');
          grad.addColorStop(1, '#10233f');
        }
        ctx.fillStyle = grad;
        ctx.fillRect(0, 0, w, h);
        if (!isDay) {
          var s = 12345, i;
          for (i = 0; i < 30; i++) {
            s = (s * 1103515245 + 12345) & 0x7fffffff;
            var sx = (s % 1000) / 1000 * w;
            s = (s * 1103515245 + 12345) & 0x7fffffff;
            var sy = (s % 1000) / 1000 * h * 0.6;
            ctx.fillStyle = '#ffffff';
            ctx.fillRect(sx, sy, 2, 2);
          }
        }
        var cx = w * t;
        var cy = h * 0.4 - h * 0.3 * Math.sin(Math.PI * t);
        var rad = Math.min(w, h) * 0.05;
        ctx.fillStyle = isDay ? '#ffec70' : '#e8eef7';
        ctx.beginPath();
        ctx.arc(cx, cy, rad, 0, Math.PI * 2);
        ctx.fill();
      }
    }
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `node --test tests/unit/js/test_animations_suntransit.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add js/animations.js tests/unit/js/test_animations_suntransit.js
git commit -m "feat(animations): sunMoonTransit (batch 3)"
```

---

### Task 10: Module key-list guard, e2e smoke, full suite, review, PR

**Files:**
- Modify: `tests/unit/js/test_animations_module.js`
- Modify: `tests/e2e/test-script-animations.spec.js`

- [ ] **Step 1: Extend the module key-list assertion**

`tests/unit/js/test_animations_module.js` asserts the batch-1 keys are present. Find the loop:

```js
  for (const k of ['bouncingBalls', 'lissajous', 'phyllotaxis', 'wireframeCube']) {
    assert.ok(keys.includes(k), `missing animation "${k}"`);
  }
```

Replace the array with the full set so a forgotten entry is caught:

```js
  for (const k of ['bouncingBalls', 'lissajous', 'phyllotaxis', 'wireframeCube',
                   'radialPulse', 'particleGalaxy', 'plasma', 'pendulumWave',
                   'dvdLogo', 'analogClock', 'wordClock', 'sunMoonTransit']) {
    assert.ok(keys.includes(k), `missing animation "${k}"`);
  }
```

- [ ] **Step 2: Run the full JS suite**

Run: `python pytest_runner.py --js`
Expected: all pass — the 8 new per-animation files (24 tests) + the updated module key-list test + every pre-existing JS test.

- [ ] **Step 3: Extend the e2e SCRIPT smoke**

Open `tests/e2e/test-script-animations.spec.js`. It currently drives one or more batch-1 animations through the real SCRIPT path and asserts a non-blank canvas + teardown. Add two of the new animations to whatever list/loop of animation keys it iterates — include one wall-clock animation (`analogClock`) so the `nowMs` wiring from Task 1 is exercised end-to-end, and one pure animation (`radialPulse`). Match the spec's existing structure exactly (read the file first; do not change its harness shape — same `__e2e_` fixture + cleanup conventions). The assertion is unchanged: the SCRIPT canvas renders at least one non-background pixel, and STOP tears it down.

- [ ] **Step 4: Run the e2e smoke (needs dev server + npm install)**

Run: `node tests/e2e/run.js script-animations`
Expected: passes — SCRIPT canvas renders non-blank for the sampled animations, tears down on stop.

(If the Playwright environment isn't set up — no `node_modules`/chromium — note it to the controller; the Node determinism tests + the manual/hardware check in Step 6 cover the gap. Do not block the PR on an unavailable e2e environment, but say so explicitly.)

- [ ] **Step 5: Commit the test updates**

```bash
git add tests/unit/js/test_animations_module.js tests/e2e/test-script-animations.spec.js
git commit -m "test(animations): cover 8 new animations in module-key + e2e smoke (batches 2-4)"
```

- [ ] **Step 6: iPad-1 hardware sign-off (manual gate)**

The determinism tests prove synchronization math; only hardware proves the per-frame budget (<8 ms target, the spec's iPad-1 cost budget). Assign a playlist of all 8 SCRIPT items (20 s each, `playmode:'SCRIPT'`) to a group with a real iPad-1 and Play-Now it. For each, confirm: it renders (not blank), motion is smooth (30+ FPS, no multi-second stalls), and two iPads in the group are in visual lockstep. The likely-heaviest is `plasma` (1200 `fillRect`/frame); if it stutters, drop its grid to 32×24 in `js/animations.js` and change the plasma test's `fillRect` assertion to `768`. Record pass/fail per animation in the PR description.

- [ ] **Step 7: Final code review**

Use superpowers:requesting-code-review over the branch's commits. Focus: ES5 compliance in `js/animations.js` (no `let`/`const`/arrows/template-literals leaked in), determinism (no `Date.now()`/`Math.random()`/module-level mutable state — wall-clock animations read only the passed `nowMs`), the `wordClock` grid/word-position fidelity (each lit word spells correctly), and the plasma `fillRect`-grid deviation being intentional + documented.

- [ ] **Step 8: Finish the branch**

Use superpowers:finishing-a-development-branch. The PR summary should note: 8 new SCRIPT animations across spec batches 2-4; the `nowMs` 5th-arg wiring for the 3 wall-clock ones; the plasma `fillRect`-grid deviation from the spec's `ImageData` path (testability + iPad-1); `gameOfLife` + `fleetStatus` still deferred; iPad-1 hardware sign-off result.

---

## Self-Review

**1. Spec coverage** (against `docs/superpowers/specs/2026-06-09-script-animations-pack-design.md`):

| Spec animation | Task | Notes |
|---|---|---|
| #3 `radialPulse` | Task 2 | K=5 rings, PERIOD=4000, corner-reaching maxR |
| #6 `particleGalaxy` | Task 3 | N=400, golden-ratio radius, Keplerian ω, fillRect dots |
| #4 `plasma` | Task 4 | **Deviation:** 40×30 fillRect grid instead of ImageData (documented) |
| #5 `pendulumWave` | Task 5 | N=16, T_BASE=4000, T_STEP=80 |
| #11 `dvdLogo` | Task 6 | closed-form triangle wave, bounce-count color |
| #9 `analogClock` | Task 7 | wall-clock, nowMs |
| #8 `wordClock` | Task 8 | wall-clock, 13×8 grid (spec said 11×10 — relaxed for clean word placement) |
| #10 `sunMoonTransit` | Task 9 | wall-clock, gradient day/night, LFSR stars |
| Fifth `nowMs` arg unconditionally | Task 1 | index.html call site + signature doc |
| Operator picker | automatic | admin reads `MM_ANIMATIONS` directly (no catalog file in current arch) |
| Node determinism tests (same inputs → identical op log) | Tasks 2-9 | 3 per animation |
| Light Playwright smoke | Task 10 | extends existing spec with 2 new animations |
| iPad-1 frame budget | Task 10 Step 6 | manual |
| #12 `gameOfLife`, #13 `fleetStatus` | **deferred** | precompute / heartbeat — out of scope, stated up front |

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N" — every animation has complete `draw` code and a complete test file. Task 10 Step 3 references the existing e2e spec's structure rather than reprinting it (correct — the implementer must read the live file to match its harness shape; the assertion + which animations to add are fully specified).

**3. Type/name consistency:**
- Registry entry shape `{key, label, description, draw}` — consistent with the existing `js/animations.js` entries.
- Signature `draw(ctx, tMs, w, h)` for the 5 pure animations; `draw(ctx, tMs, w, h, nowMs)` for the 3 wall-clock ones — consistent with Task 1's wiring and each wall-clock test passing `nowMs`.
- Test idiom (`await import('../../../js/animations.js')` + `byKey = Object.fromEntries(...)`) — identical across Tasks 2-9 and matches the existing `test_animations_lissajous.js`.
- Keys (`radialPulse`, `particleGalaxy`, `plasma`, `pendulumWave`, `dvdLogo`, `analogClock`, `wordClock`, `sunMoonTransit`) — spelled identically in each task's entry, its test, and Task 10's module key-list.
- Op-count assertions match the code: radialPulse 5 arcs; particleGalaxy 400 fillRect; plasma 1200 fillRect; pendulumWave 16 arcs; dvdLogo 1 fillText; analogClock 1 arc + 16 strokes; wordClock 104 fillText; sunMoonTransit 1 gradient + ≥1 arc.

No gaps found.
