# Game of Life + Precompute-from-Seed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `gameOfLife` SCRIPT animation whose board is randomized per run but bit-identical across every screen, via a seed-derived precomputed generation cycle.

**Architecture:** Two new module-level helpers in `js/animations.js` — `mmLifeStep(prev, GW, GH)` (one pure Conway step, toroidal) and `mmPrecomputeLife(seed, GW, GH, G)` (gen-0 from `MM_RNG(seed)`, then `G-1` steps, returning a `Uint8Array(G*GW*GH)`). The `gameOfLife` `MM_ANIMATIONS` entry is an IIFE-returned `draw` closure holding a per-seed board cache; each frame it renders a `fillRect` per live cell of `boards[floor(tMs/100)%G]`.

**Tech Stack:** ES5 + Canvas2D + `Uint8Array` (all supported on iPad-1 / Safari 5.1), Node 20 `node --test` (`python pytest_runner.py --js`).

**Spec:** `docs/superpowers/specs/2026-06-19-gameoflife-precompute-design.md`

---

## Background the implementer needs

**Current state (verified):** `js/animations.js` is an IIFE `(function (root) { ... })(window-or-globalThis)`. It defines `MM_RNG(seed)` (line ~17) and `mmDeriveSeed` (line ~29); the `var animations = [ ... ]` array ends with `sunMoonTransit` (~line 361) then `]`; exposure is `root.MM_ANIMATIONS = animations; root.MM_RNG = ...; root.mmDeriveSeed = ...` (~lines 406-408). `MM_RNG` is callable from any `draw`/helper via closure.

**Why precompute:** GoL is stateful — gen N depends on gen N-1. Computing gen N on demand would re-evolve N steps every frame. So precompute all `G` boards once from the seed, cache them, render `boards[gen(tMs)]` in O(1)/frame. The coordinated seed makes that table identical on every screen — the whole reason this can be a coordinated animation.

**Determinism rule:** the board is seeded ONLY by `MM_RNG(seed)`; no `Math.random`, no wall-clock. Same `seed` → identical board array → identical render at a given `tMs`.

**ES5 + portability:** `var`/`function` only, no `let`/`const`/arrow/template-literals/`Math.imul`. `Uint8Array`, `.subarray`, `.set` are available on Safari 5.1. 2-space indent inside the array.

**Test idiom (canvas-stub op-log), from `tests/unit/js/test_animations_plasma.js`:**
```js
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));
const W = 1024, H = 768;
```
Seeded call form: `byKey.gameOfLife(ctx, tMs, W, H, 0, seed)`. The helpers are exposed on `globalThis` for direct testing.

**Branch:** `feature/gameoflife-precompute` (off `main`, spec committed). Do NOT start on `main`.

**Note on a parallel branch:** `feature/generative-animations` (PR #42, unmerged) also appends to `MM_ANIMATIONS`. When both land there may be a trivial array-tail merge conflict (both add an entry before `]`) — resolve by keeping both entries. Not a blocker.

---

## File Structure

| File | Responsibility | Task |
|------|----------------|------|
| `js/animations.js` | `mmLifeStep` + `mmPrecomputeLife` helpers (exposed on `root`); `gameOfLife` entry | 1, 2 |
| `tests/unit/js/test_animations_life.js` | Direct helper tests: blinker rule, precompute determinism/seeded/dims/density | 1 |
| `tests/unit/js/test_animations_gameoflife.js` | Op-log tests: determinism/animates/seeded/structure | 2 |
| `tests/unit/js/test_animations_module.js` | key-list +`gameOfLife` | 3 |

---

### Task 1: `mmLifeStep` + `mmPrecomputeLife` helpers

**Files:** Modify `js/animations.js`; Test `tests/unit/js/test_animations_life.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_animations_life.js`:

```js
/**
 * Conway helpers behind gameOfLife. mmLifeStep is one pure toroidal generation
 * (tested with a blinker — the real rule check). mmPrecomputeLife builds the
 * G-board cycle from MM_RNG(seed): deterministic + seeded, the sync guarantee.
 */
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/animations.js');
const { mmLifeStep, mmPrecomputeLife } = globalThis;

test('mmLifeStep — blinker oscillates horizontal -> vertical', () => {
  var GW = 5, GH = 5;
  var b = new Uint8Array(GW * GH);
  b[2 * 5 + 1] = 1; b[2 * 5 + 2] = 1; b[2 * 5 + 3] = 1;   // horizontal, row 2 cols 1-3
  var n = mmLifeStep(b, GW, GH);
  var expected = new Uint8Array(GW * GH);
  expected[1 * 5 + 2] = 1; expected[2 * 5 + 2] = 1; expected[3 * 5 + 2] = 1; // vertical, col 2 rows 1-3
  assert.deepStrictEqual(n, expected);
});

test('mmPrecomputeLife — dimensions G*GW*GH', () => {
  assert.equal(mmPrecomputeLife(42, 8, 6, 5).length, 5 * 8 * 6);
});

test('mmPrecomputeLife — deterministic for a seed', () => {
  assert.deepStrictEqual(mmPrecomputeLife(42, 16, 12, 10), mmPrecomputeLife(42, 16, 12, 10));
});

test('mmPrecomputeLife — different seeds differ', () => {
  assert.notDeepStrictEqual(mmPrecomputeLife(1, 16, 12, 10), mmPrecomputeLife(2, 16, 12, 10));
});

test('mmPrecomputeLife — gen 0 ~35% density and it evolves', () => {
  var GW = 32, GH = 32, cells = GW * GH, i;
  var b = mmPrecomputeLife(42, GW, GH, 3);
  var alive = 0;
  for (i = 0; i < cells; i++) { alive += b[i]; }
  var frac = alive / cells;
  assert.ok(frac > 0.25 && frac < 0.45, 'gen0 density ' + frac);
  assert.notDeepStrictEqual(b.subarray(cells, 2 * cells), b.subarray(0, cells)); // gen1 != gen0
});
```

- [ ] **Step 2: Run to confirm fail**

Run: `node --test tests/unit/js/test_animations_life.js`
Expected: FAIL — `mmLifeStep is not a function` (destructured `undefined`).

- [ ] **Step 3: Add the helpers**

In `js/animations.js`, inside the IIFE, after `mmDeriveSeed` (~line 38, before `var animations = [`), add:

```js
  // One pure Conway step (toroidal edges). prev/next are Uint8Array(GW*GH) of
  // 0/1. Live survives on 2-3 neighbours; dead is born on exactly 3.
  function mmLifeStep(prev, GW, GH) {
    var cells = GW * GH;
    var next = new Uint8Array(cells);
    var x, y, dx, dy;
    for (y = 0; y < GH; y++) {
      for (x = 0; x < GW; x++) {
        var n = 0;
        for (dy = -1; dy <= 1; dy++) {
          for (dx = -1; dx <= 1; dx++) {
            if (dx === 0 && dy === 0) { continue; }
            var nx = (x + dx + GW) % GW, ny = (y + dy + GH) % GH;
            n += prev[ny * GW + nx];
          }
        }
        var alive = prev[y * GW + x];
        next[y * GW + x] = (alive ? (n === 2 || n === 3) : (n === 3)) ? 1 : 0;
      }
    }
    return next;
  }

  // Precompute a G-generation Game-of-Life cycle from a coordinated seed.
  // gen 0 is a ~35%-dense random board from MM_RNG(seed); gens 1..G-1 evolve via
  // mmLifeStep. Returns one Uint8Array(G*GW*GH) (board g at offset g*GW*GH). Pure:
  // same seed -> identical array -> identical on every screen.
  function mmPrecomputeLife(seed, GW, GH, G) {
    var cells = GW * GH;
    var boards = new Uint8Array(G * cells);
    var rng = MM_RNG(seed);
    var i;
    for (i = 0; i < cells; i++) { boards[i] = (rng() < 0.35) ? 1 : 0; }
    var g;
    for (g = 1; g < G; g++) {
      var prev = boards.subarray((g - 1) * cells, g * cells);
      boards.set(mmLifeStep(prev, GW, GH), g * cells);
    }
    return boards;
  }
```

At the exposure block (after `root.mmDeriveSeed = mmDeriveSeed;`), add:

```js
  root.mmLifeStep = mmLifeStep;
  root.mmPrecomputeLife = mmPrecomputeLife;
```

- [ ] **Step 4: Run to confirm pass**

Run: `node --test tests/unit/js/test_animations_life.js`  → PASS (5).
Run: `node --test tests/unit/js/test_animations_module.js`  → still PASS.

- [ ] **Step 5: Commit**

```bash
git add js/animations.js tests/unit/js/test_animations_life.js
git commit -m "feat(animations): mmLifeStep + mmPrecomputeLife (seeded Conway cycle)"
```

---

### Task 2: `gameOfLife` animation entry

**Files:** Modify `js/animations.js`; Test `tests/unit/js/test_animations_gameoflife.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_animations_gameoflife.js`:

```js
/**
 * gameOfLife: renders a fillRect per live cell of the precomputed board at
 * gen=floor(tMs/100)%G. The op-log IS the board, so deep-equal proves the
 * board is identical across runs/screens (the sync guarantee).
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));
const W = 1024, H = 768;
const GW = 48, GH = 36;

test('gameOfLife — deterministic at same (tMs, seed)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.gameOfLife(a, 0, W, H, 0, 42);
  byKey.gameOfLife(b, 0, W, H, 0, 42);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('gameOfLife — animates (gen 0 vs gen 20 differ)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.gameOfLife(a, 0, W, H, 0, 42);      // gen 0
  byKey.gameOfLife(b, 2000, W, H, 0, 42);   // gen 20
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('gameOfLife — seeded (different seed differs)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.gameOfLife(a, 0, W, H, 0, 1);
  byKey.gameOfLife(b, 0, W, H, 0, 2);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('gameOfLife — gen 0 draws a bounded number of live cells', () => {
  const c = makeRecordingCtx();
  byKey.gameOfLife(c, 0, W, H, 0, 42);
  const rects = c.__ops.filter((o) => o.op === 'fillRect').length;
  assert.ok(rects > 0 && rects <= GW * GH, `unexpected live count ${rects}`);
});
```

- [ ] **Step 2: Run to confirm fail**

Run: `node --test tests/unit/js/test_animations_gameoflife.js`
Expected: FAIL — `byKey.gameOfLife is not a function`.

- [ ] **Step 3: Append the entry**

In `js/animations.js`, change `sunMoonTransit`'s closing `}` to `},` and insert before the array's closing `]`:

```js
    {
      key: 'gameOfLife',
      label: "Conway's Game of Life",
      description: 'A cellular-automaton cycle from a seeded random board — different every run.',
      draw: (function () {
        var GW = 48, GH = 36, G = 300;
        var cache = { seed: null, boards: null };
        return function (ctx, tMs, w, h, nowMs, seed) {
          var s = (seed >>> 0);
          if (cache.seed !== s || !cache.boards) {
            cache.boards = mmPrecomputeLife(s, GW, GH, G);   // one-time per run
            cache.seed = s;
          }
          var cells = GW * GH;
          var gen = Math.floor(tMs / 100) % G;
          if (gen < 0) { gen = 0; }
          var base = gen * cells;
          var cw = w / GW, ch = h / GH, x, y;
          ctx.fillStyle = '#7CFC00';
          for (y = 0; y < GH; y++) {
            for (x = 0; x < GW; x++) {
              if (cache.boards[base + y * GW + x]) {
                ctx.fillRect(x * cw, y * ch, cw + 1, ch + 1);
              }
            }
          }
        };
      })()
    }
```

- [ ] **Step 4: Run to confirm pass**

Run: `node --test tests/unit/js/test_animations_gameoflife.js`  → PASS (4).
Run: `node --test tests/unit/js/test_animations_module.js`  → still PASS.

- [ ] **Step 5: Commit**

```bash
git add js/animations.js tests/unit/js/test_animations_gameoflife.js
git commit -m "feat(animations): gameOfLife (seeded precomputed Conway cycle)"
```

---

### Task 3: Module key-list, full suite, sign-off, review, PR

**Files:** Modify `tests/unit/js/test_animations_module.js`

- [ ] **Step 1: Extend the expected-key list**

In `tests/unit/js/test_animations_module.js`, the `for (const k of [ ... ])` list ends with `'sunMoonTransit'`. Add `'gameOfLife'`:

```js
  for (const k of ['bouncingBalls', 'lissajous', 'phyllotaxis', 'wireframeCube',
                   'radialPulse', 'particleGalaxy', 'plasma', 'pendulumWave',
                   'dvdLogo', 'analogClock', 'wordClock', 'sunMoonTransit',
                   'gameOfLife']) {
    assert.ok(keys.includes(k), `missing animation "${k}"`);
  }
```

(If this branch is later rebased onto a `main` that already has the generative-batch keys from PR #42, keep all keys — additive.)

- [ ] **Step 2: Full JS suite**

Run: `python pytest_runner.py --js`
Expected: all pass — `test_animations_life.js` (5) + `test_animations_gameoflife.js` (4) + updated module key-list + every pre-existing JS test.

- [ ] **Step 3: ES5 compliance check**

Run: `grep -nE "\\b(let|const)\\b|=>|Math\\.imul" js/animations.js`
Expected: matches ONLY in comment prose, never in the new `mmLifeStep`/`mmPrecomputeLife`/`gameOfLife` code. Eyeball the new code: `var`/`function`, `Uint8Array`, no arrows/template-literals.

- [ ] **Step 4: iPad-1 hardware sign-off (manual gate)**

Assign a SCRIPT playlist with a `gameOfLife` item (e.g. 30s, `playmode:'SCRIPT'`) to a group with a real iPad-1 and Play-Now it. Confirm:
- The **one-time precompute stall** at item start is acceptable (a brief freeze / ≤ ~1 dropped frame), not a multi-second hang. If it hangs noticeably, reduce `G` (e.g. 200) and/or grid (e.g. 40×30) in the `gameOfLife` entry — and update the structure test's `GW*GH` bound accordingly — OR move the precompute into the SETPLAYLIST/PRELOAD path (off the rAF loop) as the spec notes.
- Steady-state holds 30+ FPS.
- Two iPads in the group show the **same board at the same instant** (the coordination payoff) AND a **different board than a previous run** (seeded).
Record the result in the PR.

- [ ] **Step 5: Final code review**

Use superpowers:requesting-code-review over the branch. Focus: the Conway rule in `mmLifeStep` (blinker test proves it; confirm toroidal wrap `(x+dx+GW)%GW`); `mmPrecomputeLife` determinism (gen-0 from `MM_RNG(seed)` only); the closure cache recomputes on seed change and is keyed by `seed>>>0`; `fillRect`-per-live-cell render (no background fill — relies on `runScriptLoop`'s `clearRect`); ES5 + `Uint8Array` portability; op-log determinism/animates/seeded holding.

- [ ] **Step 6: Finish the branch**

Use superpowers:finishing-a-development-branch. PR summary: `gameOfLife` (first stateful sim) via seed-derived precomputed cycle (`mmPrecomputeLife` + `mmLifeStep`); coordinated (same board across screens) + randomized per run; op-log + blinker-rule tests; iPad-1 precompute-stall sign-off result; generic `mmPrecomputeCycle` + other sims (boids, reaction-diffusion) deferred to follow-ups (step 3b/4).

---

## Self-Review

**1. Spec coverage:**
| Spec item | Task |
|---|---|
| `mmPrecomputeLife(seed, GW, GH, G)` — Uint8Array, gen-0 from MM_RNG ~35%, toroidal evolve | Task 1 |
| (refinement) `mmLifeStep` pure step for rule-testability | Task 1 |
| `gameOfLife` IIFE-closure entry + per-seed cache + `fillRect` per live cell | Task 2 |
| `gen = floor(tMs/100)%G`, grid 48×36, G=300 | Task 2 |
| Op-log tests (determinism/animates/seeded/structure) | Task 2 |
| Direct precompute determinism + blinker rule test | Task 1 |
| Module key-list +gameOfLife | Task 3 Step 1 |
| iPad-1 precompute-stall sign-off | Task 3 Step 4 |
| No server change | Confirmed (only `js/animations.js` + tests) |
| Generic `mmPrecomputeCycle` / other sims = non-goals | Not implemented (correct) |

**2. Placeholder scan:** No TBD/"handle X"/"similar to". Every helper, entry, and test is complete code. Task 3 Step 3 is grep+eyeball (honest: inline-array ES5 has no node lint).

**3. Type/name consistency:** `mmLifeStep(prev, GW, GH)` and `mmPrecomputeLife(seed, GW, GH, G)` signatures match between definition (Task 1), the helper tests (Task 1), and the `gameOfLife` closure call `mmPrecomputeLife(s, GW, GH, G)` (Task 2). Board indexing `g*cells + y*GW + x` consistent (Task 1 precompute write, Task 2 read `base + y*GW + x`). Key `'gameOfLife'` identical across entry, both test files, and Task 3 key-list. Grid `48×36`/`G=300` consistent between the Task 2 entry and the Task 2 structure test's `GW=48, GH=36` bound. Render draws only live cells → `fillRect` count == live count (no bg fill), matching the structure assertion `> 0 && <= GW*GH`.
