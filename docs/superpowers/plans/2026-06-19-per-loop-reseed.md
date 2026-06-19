# Per-Loop Reseed + Incremental Game of Life Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a continuously-looping SCRIPT animation re-randomize each playlist loop (coordinated across all screens) and convert `gameOfLife`'s synchronous board precompute into a per-frame incremental state machine that paints seeded coordinated noise while warming up — eliminating the iPad-1 freeze.

**Architecture:** Two pure client-side changes, no server/protocol change. (A) A new pure helper `mmLoopItemSeed(runSeed, loopIdx, itemIdx)` composes the existing tested `mmDeriveSeed` twice; `runScriptLoop` in `index.html` derives `loopIdx` from the already-shared clock inputs (`GoTime.now() − playback.startEpoch` ÷ total duration) and passes the loop-aware seed as the 6th draw arg. (B) `gameOfLife`'s draw closure becomes an incremental state machine `{seed, boards, computed, done}` that evolves at most `STEP_PER_FRAME=12` generations per frame via the existing `mmLifeStep`, rendering the live board when the needed generation is ready and a seeded coordinated noise grid otherwise. The all-at-once `mmPrecomputeLife` is removed.

**Tech Stack:** ES5-only browser JS (`js/animations.js`, `index.html` — must run on Safari 5.1 / iPad-1: no `let`/`const`/arrow/template-literals/`Math.imul`; `Uint8Array` is OK). Node 20+ `node --test` op-log suites under `tests/unit/js/` (run via `python pytest_runner.py --js`). The animations module is an IIFE `(function(root){...})(window||globalThis)` exposing helpers on `root`; node tests `await import` it for side-effect and read `globalThis.*`.

**Reference spec:** `docs/superpowers/specs/2026-06-19-per-loop-reseed-design.md`

---

## File Structure

- `js/animations.js` — add `mmLoopItemSeed` (pure helper) + expose on `root`; rewrite the `gameOfLife` entry's `draw` closure to be incremental; remove `mmPrecomputeLife` + its `root` exposure. (ES5 only.)
- `index.html` — wire `runScriptLoop` (the inline display-client playback loop) to compute `loopIdx` and call `mmLoopItemSeed`. (ES5 only.)
- `tests/unit/js/test_animations_rng.js` — add `mmLoopItemSeed` unit tests.
- `tests/unit/js/test_animations_life.js` — remove the four `mmPrecomputeLife` tests; keep the `mmLifeStep` blinker test; drop the `mmPrecomputeLife` import.
- `tests/unit/js/test_animations_gameoflife.js` — update op-log tests for the incremental model (gen-0 determinism/seeded stay; replace the cycle-wrap test with a noise-state test).

No server, Python, or protocol files change.

---

## Task 1: `mmLoopItemSeed` helper + node tests

**Files:**
- Modify: `js/animations.js` (add function near `mmDeriveSeed` at `js/animations.js:29`; expose on `root` near `js/animations.js:609`)
- Test: `tests/unit/js/test_animations_rng.js`

- [ ] **Step 1: Write the failing tests**

Add to the bottom of `tests/unit/js/test_animations_rng.js`, and extend the destructure on line 12 from `const { MM_RNG, mmDeriveSeed } = globalThis;` to `const { MM_RNG, mmDeriveSeed, mmLoopItemSeed } = globalThis;`:

```js
test('mmLoopItemSeed — deterministic for same (runSeed, loopIdx, itemIdx)', () => {
  assert.equal(mmLoopItemSeed(777, 0, 3), mmLoopItemSeed(777, 0, 3));
  assert.equal(mmLoopItemSeed(12345, 9, 1), mmLoopItemSeed(12345, 9, 1));
});

test('mmLoopItemSeed — distinct per loop index (same item)', () => {
  const seen = new Set();
  for (let loop = 0; loop < 64; loop++) seen.add(mmLoopItemSeed(777, loop, 0));
  assert.equal(seen.size, 64, 'loop index collision in mmLoopItemSeed');
});

test('mmLoopItemSeed — distinct per item index (same loop)', () => {
  const seen = new Set();
  for (let item = 0; item < 64; item++) seen.add(mmLoopItemSeed(777, 5, item));
  assert.equal(seen.size, 64, 'item index collision in mmLoopItemSeed');
});

test('mmLoopItemSeed — equals nested mmDeriveSeed composition', () => {
  // It is exactly mmDeriveSeed(mmDeriveSeed(runSeed, loopIdx), itemIdx).
  assert.equal(mmLoopItemSeed(42, 7, 2), mmDeriveSeed(mmDeriveSeed(42, 7), 2));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/unit/js/test_animations_rng.js`
Expected: FAIL — `mmLoopItemSeed is not a function` (it's `undefined` on `globalThis`).

- [ ] **Step 3: Add the helper + exposure**

In `js/animations.js`, immediately after the `mmDeriveSeed` function (which ends at `js/animations.js:35` with its closing `}`), add:

```js
  // Per-(loop, item) seed for continuously-looping playlists: fold the loop
  // index into the run seed, then the item index. Pure composition of the
  // tested mmDeriveSeed, so it stays bit-identical across Safari 5.1 / Node /
  // V8. loopIdx is client-derived from the shared clock (see runScriptLoop),
  // so every screen computes the same value at the same instant -> coordinated.
  function mmLoopItemSeed(runSeed, loopIdx, itemIdx) {
    return mmDeriveSeed(mmDeriveSeed(runSeed, loopIdx), itemIdx);
  }
```

Then add the exposure. The current block at `js/animations.js:607-611` is:

```js
  root.MM_ANIMATIONS = animations;
  root.MM_RNG = MM_RNG;
  root.mmDeriveSeed = mmDeriveSeed;
  root.mmLifeStep = mmLifeStep;
  root.mmPrecomputeLife = mmPrecomputeLife;
```

Add `mmLoopItemSeed` after the `mmDeriveSeed` line:

```js
  root.mmDeriveSeed = mmDeriveSeed;
  root.mmLoopItemSeed = mmLoopItemSeed;
```

(Leave `mmPrecomputeLife` exposure for now — Task 3 removes it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/unit/js/test_animations_rng.js`
Expected: PASS (all tests, including the existing `MM_RNG`/`mmDeriveSeed`/`Math.imul`-guard ones).

- [ ] **Step 5: Commit**

```bash
git add js/animations.js tests/unit/js/test_animations_rng.js
git commit -m "feat(animations): add mmLoopItemSeed for per-loop coordinated reseed

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Wire `runScriptLoop` to per-loop seed (`index.html`)

**Files:**
- Modify: `index.html:477-494` (the `runScriptLoop` function; specifically the draw block at `index.html:486-490`)

> No node test — `runScriptLoop` is the inline, untestable display loop (it depends on `GoTime`, `playback`, RAF). The sync-critical math (`mmLoopItemSeed`) is already unit-tested in Task 1. Correctness here is verified by reading the diff against the spec invariants + the iPad-1 sign-off (Task 6).

- [ ] **Step 1: Replace the draw block**

The current block at `index.html:486-490` is:

```js
			ctx.clearRect(0, 0, canvas.width, canvas.height);
			if (animations[name]) {
				var itemSeed = (typeof mmDeriveSeed === 'function') ? mmDeriveSeed(playback.seed, pos.index) : 0;
				animations[name](ctx, pos.offsetMs, canvas.width, canvas.height, GoTime.now(), itemSeed);
			}
```

Replace it with:

```js
			ctx.clearRect(0, 0, canvas.width, canvas.height);
			if (animations[name]) {
				// Per-loop reseed: recover the loop count playlistIndex discards
				// (it does elapsedMs % total). loopIdx is a pure function of the
				// SHARED clock (GoTime.now() - startEpoch) and SHARED durations,
				// so every screen derives the same itemSeed at the same instant.
				var total = 0, t;
				for (t = 0; t < durations.length; t++) { total += durations[t]; }
				var rawElapsed = GoTime.now() - playback.startEpoch;
				var loopIdx = (total > 0 && rawElapsed > 0) ? Math.floor(rawElapsed / total) : 0;
				var itemSeed = (typeof mmLoopItemSeed === 'function')
					? mmLoopItemSeed(playback.seed || 0, loopIdx, pos.index) : 0;
				animations[name](ctx, pos.offsetMs, canvas.width, canvas.height, GoTime.now(), itemSeed);
			}
```

Notes for the implementer:
- `durations` is already built earlier in `frame()` at `index.html:481-482` — reuse it; do not rebuild the array.
- For a non-looping playlist `playlistIndex` returns `null` once `elapsedMs >= total` (`index.html:241`), so the draw block is never reached past the first loop ⇒ `loopIdx` stays `0` ⇒ behavior is identical to today.
- `playback.seed || 0` mirrors the spec and tolerates an absent seed (an animation playlist that never went through a seed-minting PLAY path).

- [ ] **Step 2: ES5 + portability self-check**

Visually confirm the new lines use only `var` (no `let`/`const`/arrow/template-literals), no `Math.imul`, no `Array.prototype` ES6 methods. Confirm there is exactly one `mmLoopItemSeed` call and the old `mmDeriveSeed(playback.seed, pos.index)` line is gone.

Run: `node --test tests/unit/js/test_animations_rng.js` (the `Math.imul` portability guard there also scans `js/animations.js`, unchanged here — sanity that nothing regressed).
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "feat(display): per-loop reseed in runScriptLoop via mmLoopItemSeed

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Incremental `gameOfLife` + remove `mmPrecomputeLife` (`js/animations.js`)

**Files:**
- Modify: `js/animations.js` — rewrite the `gameOfLife` `draw` closure at `js/animations.js:450-473`; remove `mmPrecomputeLife` (`js/animations.js:60-76`) and its `root` exposure (`js/animations.js:611`)

> Tests for this task live in Tasks 4 + 5 (life + gameoflife suites). This task is the production change; do Task 3 → 4 → 5 together but commit per the steps. Because removing `mmPrecomputeLife` breaks `test_animations_life.js`'s import, run the JS suite only after Task 4 updates that file.

- [ ] **Step 1: Replace the `gameOfLife` draw closure**

The current closure at `js/animations.js:450-473` is:

```js
      draw: (function () {
        var GW = 48, GH = 36, G = 300;
        var cache = { seed: null, boards: null };
        return function (ctx, tMs, w, h, nowMs, seed) {
          var s = (seed >>> 0);
          if (cache.seed !== s || !cache.boards) {
            cache.boards = mmPrecomputeLife(s, GW, GH, G);
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
```

Replace it with the incremental state machine:

```js
      draw: (function () {
        var GW = 48, GH = 36, G = 300, STEP_PER_FRAME = 12;
        // Incremental cache: gen 0 is seeded immediately (computed=1); each frame
        // evolves at most STEP_PER_FRAME generations via mmLifeStep until `done`.
        // No synchronous all-at-once precompute -> no JS-thread freeze on iPad-1.
        var cache = { seed: null, boards: null, computed: 0, done: false };
        return function (ctx, tMs, w, h, nowMs, seed) {
          var s = (seed >>> 0);
          var cells = GW * GH;
          if (cache.seed !== s || !cache.boards) {
            cache.boards = new Uint8Array(G * cells);
            var rng = MM_RNG(s);
            var i;
            for (i = 0; i < cells; i++) { cache.boards[i] = (rng() < 0.35) ? 1 : 0; }
            cache.seed = s; cache.computed = 1; cache.done = false;
          }
          if (!cache.done) {
            var budget = STEP_PER_FRAME;
            while (cache.computed < G && budget-- > 0) {
              var prev = cache.boards.subarray((cache.computed - 1) * cells, cache.computed * cells);
              cache.boards.set(mmLifeStep(prev, GW, GH), cache.computed * cells);
              cache.computed++;
            }
            if (cache.computed >= G) { cache.done = true; }
          }
          var gen = Math.floor(tMs / 100) % G;
          if (gen < 0) { gen = 0; }
          var cw = w / GW, ch = h / GH, x, y;
          if (gen < cache.computed) {
            // Board for this generation is ready — render live cells.
            var base = gen * cells;
            ctx.fillStyle = '#7CFC00';
            for (y = 0; y < GH; y++) {
              for (x = 0; x < GW; x++) {
                if (cache.boards[base + y * GW + x]) { ctx.fillRect(x * cw, y * ch, cw + 1, ch + 1); }
              }
            }
          } else {
            // Not computed yet — seeded coordinated noise (shared tMs bucket ->
            // screens in the noise state at the same 100ms tick draw the same grid).
            var nrng = MM_RNG(mmDeriveSeed(s, Math.floor(tMs / 100)));
            ctx.fillStyle = '#3a5a3a';   // dim "warming up" tint
            for (y = 0; y < GH; y++) {
              for (x = 0; x < GW; x++) {
                if (nrng() < 0.5) { ctx.fillRect(x * cw, y * ch, cw + 1, ch + 1); }
              }
            }
          }
        };
      })()
```

- [ ] **Step 2: Remove `mmPrecomputeLife` (function + exposure)**

Delete the entire `mmPrecomputeLife` function block at `js/animations.js:60-76` (the comment beginning `// Precompute a G-generation Game-of-Life cycle...` through its closing `}`).

Delete its exposure line at `js/animations.js:611`:

```js
  root.mmPrecomputeLife = mmPrecomputeLife;
```

Leave `mmLifeStep` and its exposure (`root.mmLifeStep = mmLifeStep;`) — the incremental closure still uses it.

- [ ] **Step 3: ES5 + portability self-check**

Confirm the new closure uses only `var`, `Uint8Array`, `subarray`/`set` (all OK on Safari 5.1), no `Math.imul`, no arrow/template-literals. Confirm `mmPrecomputeLife` no longer appears anywhere in `js/animations.js`:

Run: `grep -n mmPrecomputeLife js/animations.js`
Expected: no output.

(Do not run the JS test suite yet — `test_animations_life.js` still imports `mmPrecomputeLife` and would crash; Task 4 fixes it. Commit happens after Task 5.)

---

## Task 4: Update the life helper tests (`test_animations_life.js`)

**Files:**
- Modify: `tests/unit/js/test_animations_life.js`

- [ ] **Step 1: Strip the `mmPrecomputeLife` tests, keep the blinker**

Replace the entire contents of `tests/unit/js/test_animations_life.js` with:

```js
/**
 * Conway helper behind gameOfLife. mmLifeStep is one pure toroidal generation
 * (tested with a blinker — the real rule check). The board cycle is now built
 * incrementally inside the gameOfLife draw closure (see test_animations_gameoflife.js);
 * the old all-at-once mmPrecomputeLife was removed.
 */
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/animations.js');
const { mmLifeStep } = globalThis;

test('mmLifeStep — blinker oscillates horizontal -> vertical', () => {
  var GW = 5, GH = 5;
  var b = new Uint8Array(GW * GH);
  b[2 * 5 + 1] = 1; b[2 * 5 + 2] = 1; b[2 * 5 + 3] = 1;
  var n = mmLifeStep(b, GW, GH);
  var expected = new Uint8Array(GW * GH);
  expected[1 * 5 + 2] = 1; expected[2 * 5 + 2] = 1; expected[3 * 5 + 2] = 1;
  assert.deepStrictEqual(n, expected);
});
```

- [ ] **Step 2: Run to verify it passes**

Run: `node --test tests/unit/js/test_animations_life.js`
Expected: PASS — one test (`mmLifeStep — blinker ...`); no reference to `mmPrecomputeLife`.

---

## Task 5: Update the gameOfLife op-log tests (`test_animations_gameoflife.js`)

**Files:**
- Modify: `tests/unit/js/test_animations_gameoflife.js`

The incremental model changes two test assumptions:
- **gen 0 is ready on the first frame** (`computed=1` after reset) — so `tMs=0` still renders the board deterministically and seeded. Those tests stay.
- **A far-ahead `tMs` on a fresh seed renders noise**, because only `STEP_PER_FRAME` gens are computed in the single call — the target gen is past `computed`. The old "gen wraps at G*100ms back to gen 0" deep-equal test no longer holds in one isolated call (the board at gen 0 vs the noise/late-gen at 30000ms aren't comparable across fresh closures), so replace it with a noise-state test.

> Note on cache statefulness: the `gameOfLife` draw closure holds a module-level `cache` shared across all calls in the same node process. Each test below uses a **distinct seed** so the first call with that seed triggers the reset branch (`cache.seed !== s`) deterministically, making each test independent of call order. Keep the seeds distinct as written.

- [ ] **Step 1: Replace the test file contents**

Replace the entire contents of `tests/unit/js/test_animations_gameoflife.js` with:

```js
/**
 * gameOfLife (incremental): gen 0 is seeded on the first frame, so tMs=0 renders
 * the live board deterministically + seeded (the cross-screen sync guarantee).
 * A far-ahead gen on a fresh seed isn't computed yet in a single call, so it
 * renders a seeded coordinated noise grid. Each test uses a DISTINCT seed so the
 * shared draw-closure cache resets deterministically regardless of call order.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));
const W = 1024, H = 768;
const GW = 48, GH = 36;

test('gameOfLife — gen 0 deterministic at same (tMs=0, seed)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.gameOfLife(a, 0, W, H, 0, 42);
  byKey.gameOfLife(b, 0, W, H, 0, 42);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('gameOfLife — gen 0 seeded (different seed differs)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.gameOfLife(a, 0, W, H, 0, 101);
  byKey.gameOfLife(b, 0, W, H, 0, 202);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('gameOfLife — gen 0 draws a bounded number of live cells', () => {
  const c = makeRecordingCtx();
  byKey.gameOfLife(c, 0, W, H, 0, 303);
  const rects = c.__ops.filter((o) => o.op === 'fillRect').length;
  assert.ok(rects > 0 && rects <= GW * GH, `unexpected live count ${rects}`);
});

test('gameOfLife — far-ahead gen on a fresh seed renders seeded noise', () => {
  // G=300, 100ms/gen. tMs for gen 250 is way past STEP_PER_FRAME(=12) computed
  // in a single call with a fresh seed, so it hits the noise branch (#3a5a3a).
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  const tFar = 250 * 100;
  byKey.gameOfLife(a, tFar, W, H, 0, 404);
  byKey.gameOfLife(b, tFar, W, H, 0, 404);
  assert.deepStrictEqual(a.__ops, b.__ops, 'noise must be deterministic for same (seed, tMs)');
  const fills = a.__ops.filter((o) => o.set === 'fillStyle').map((o) => o.value);
  assert.ok(fills.includes('#3a5a3a'), 'expected the warming-up noise tint');
  const rects = a.__ops.filter((o) => o.op === 'fillRect').length;
  assert.ok(rects > 0 && rects <= GW * GH, `unexpected noise cell count ${rects}`);
});

test('gameOfLife — noise differs from the live board for the same seed', () => {
  // Fresh seed: tMs=0 renders the green board; a far-ahead gen renders dim noise.
  const board = makeRecordingCtx(), noise = makeRecordingCtx();
  byKey.gameOfLife(board, 0, W, H, 0, 505);
  byKey.gameOfLife(noise, 250 * 100, W, H, 0, 606);
  const boardFills = board.__ops.filter((o) => o.set === 'fillStyle').map((o) => o.value);
  const noiseFills = noise.__ops.filter((o) => o.set === 'fillStyle').map((o) => o.value);
  assert.ok(boardFills.includes('#7CFC00'), 'gen 0 should use the live-cell green');
  assert.ok(noiseFills.includes('#3a5a3a'), 'far-ahead gen should use the noise tint');
});
```

- [ ] **Step 2: Run the gameOfLife suite**

Run: `node --test tests/unit/js/test_animations_gameoflife.js`
Expected: PASS — five tests.

- [ ] **Step 3: Run the full JS suite**

Run: `python pytest_runner.py --js`
Expected: PASS — all `tests/unit/js/*.js` suites green (rng incl. `mmLoopItemSeed` + `Math.imul` guard, life blinker only, gameoflife incremental, module key-list still lists `gameOfLife`, and every other animation suite untouched).

- [ ] **Step 4: Commit (Tasks 3–5 together)**

```bash
git add js/animations.js tests/unit/js/test_animations_life.js tests/unit/js/test_animations_gameoflife.js
git commit -m "feat(animations): incremental gameOfLife with seeded noise; drop mmPrecomputeLife

Per-frame STEP_PER_FRAME=12 evolution via mmLifeStep + a seeded coordinated
noise placeholder while warming up — removes the synchronous precompute freeze
so per-loop reseed is painless. mmPrecomputeLife and its tests are superseded.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: iPad-1 manual sign-off

**Files:** none (manual hardware verification).

> No code change. This is the spec's required hardware sign-off — automated op-log tests can't judge timing/feel, only the device can. Defer until the user is at the fleet; record the outcome in the PR.

- [ ] **Step 1: Per-loop reseed lockstep**

On two iPad-1 screens in the same display group, play a short SCRIPT playlist (e.g. plasma + one generative animation) on loop. Verify: (a) each loop cycle looks different from the previous (re-randomized), and (b) both screens show the **same** frame at the same instant on every loop, including across a loop boundary (sub-frame drift at the wrap instant is acceptable).

- [ ] **Step 2: gameOfLife warmup feel**

Play a `gameOfLife` item on loop. Verify: (a) no visible freeze/stall at item start or at each loop boundary, (b) a brief dim-green coordinated noise shimmer appears and resolves into the green Life board within ~1–2 s, (c) the two screens are in lockstep once warmed up. If the warmup reads as too slow or too fast, adjust `STEP_PER_FRAME` (currently 12) in `js/animations.js` and re-test — it's a single constant.

- [ ] **Step 2 outcome:** Record pass/fail (and any `STEP_PER_FRAME` change) in the PR description.

---

## Self-Review

**Spec coverage:**
- Part A `mmLoopItemSeed` helper + exposure + node test → Task 1. ✅
- Part A `runScriptLoop` wiring (`loopIdx` from shared clock, 6th draw arg) → Task 2. ✅
- Part B incremental `gameOfLife` state machine (`{seed,boards,computed,done}`, `STEP_PER_FRAME=12`, `mmLifeStep` evolution, board-or-noise render, `MM_RNG(mmDeriveSeed(s, floor(tMs/100)))` noise, `#3a5a3a` tint, gen-0-ready-first-frame) → Task 3. ✅
- Part B remove `mmPrecomputeLife` + its tests, keep `mmLifeStep` blinker test → Tasks 3 + 4. ✅
- Part B updated gameOfLife op-log tests (gen-0 determinism/seeded + noise-state test) → Task 5. ✅
- iPad-1 sign-off → Task 6. ✅
- No server/protocol change → confirmed; only `js/animations.js`, `index.html`, and three JS test files. ✅

**Placeholder scan:** No TBD/TODO/"add error handling" — every code step shows the full code. ✅

**Type/name consistency:** `mmLoopItemSeed(runSeed, loopIdx, itemIdx)` signature is identical in Task 1 (definition + tests) and Task 2 (call site). Cache shape `{seed, boards, computed, done}` consistent in Task 3. `STEP_PER_FRAME=12`, `GW=48`, `GH=36`, `G=300`, noise tint `#3a5a3a`, live tint `#7CFC00` consistent between Task 3 (impl) and Task 5 (tests). ✅
