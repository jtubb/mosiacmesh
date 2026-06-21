# Per-Loop Reseed + Incremental Game of Life — Design

**Date:** 2026-06-19
**Status:** Approved — ready for implementation plan.
**Builds on:** the coordinated-seed infra (`MM_RNG`, `mmDeriveSeed`, `Display.playSeed`, per-item `mmDeriveSeed(seed, pos.index)` in `runScriptLoop`), `gameOfLife` (precomputed Conway cycle), and the scheduled-seed-mint fix.

## Context

Two coupled, client-only changes:

**A. Per-loop reseed.** Today a continuously-looping SCRIPT playlist reuses the same per-item seed every cycle, so a seeded animation (plasma, the generative batch, gameOfLife) looks identical on every loop. We want each playlist loop to re-randomize — a fresh-but-coordinated look per cycle — by folding a loop index into the seed derivation.

**B. Incremental Game of Life + noise.** Per-loop reseed makes `gameOfLife`'s board recompute at every loop boundary. Its precompute was a single synchronous blocking call (~100–550 ms on iPad-1), which freezes the JS thread — so it can't paint a placeholder *during* the compute, and per-loop it would freeze the wall at every cycle. We convert the precompute to **incremental** (a bounded chunk of generations per frame) and render a **seeded noise placeholder** until the needed generation is ready. This eliminates the synchronous freeze entirely (the original stall concern *and* the per-loop-stall tradeoff both disappear) and satisfies "show noise while computing."

Both are pure client-side changes (`index.html` + `js/animations.js`); no server or protocol change.

## Decisions (settled during brainstorming)

- **Reseed scope:** universal — per-loop reseed applies to every animation. `gameOfLife` re-randomizes per loop too (made painless by Part B's incremental compute).
- **Noise approach:** incremental compute + animated coordinated noise (not the cheap frozen-noise hack), because it also removes the freeze.

## A. Per-loop reseed

### `mmLoopItemSeed` (new helper in `js/animations.js`, exposed on `root`)

```js
function mmLoopItemSeed(runSeed, loopIdx, itemIdx) {
  return mmDeriveSeed(mmDeriveSeed(runSeed, loopIdx), itemIdx);
}
```

A pure composition of the existing (tested) `mmDeriveSeed`. Chaining gives a distinct seed per `(loopIdx, itemIdx)` while staying deterministic. Extracted as a named unit so the sync-critical seed math is node-testable rather than buried in the untestable inline `runScriptLoop`.

### `runScriptLoop` wiring (`index.html`)

`playlistIndex` discards the loop count via `elapsedMs % total` (index.html:240). Recompute it from the same shared inputs already in scope:

```js
var rawElapsed = GoTime.now() - playback.startEpoch;
var total = 0; for (k = 0; k < durations.length; k++) { total += durations[k]; }
var loopIdx = (total > 0 && rawElapsed > 0) ? Math.floor(rawElapsed / total) : 0;
// 6th draw arg:
var itemSeed = (typeof mmLoopItemSeed === 'function')
  ? mmLoopItemSeed(playback.seed || 0, loopIdx, pos.index) : 0;
animations[name](ctx, pos.offsetMs, canvas.width, canvas.height, GoTime.now(), itemSeed);
```

**Coordination:** `loopIdx` is a pure function of `GoTime.now() − startEpoch`, `total` (sum of item durations), all SHARED across the group (shared clock, shared `startEpoch` from the PLAY payload, shared durations). So every screen computes the same `loopIdx` at the same instant → same `itemSeed` → identical frame. **No server change needed** — unlike the per-run seed (which required server minting because a timestamp is low-entropy), the loop *index* is deterministic structure on top of the already-coordinated run seed.

**Non-looping playlists:** `rawElapsed < total` ⇒ `loopIdx = 0`; behavior unchanged from today.

**Loop-boundary transient:** at the wrap instant, screens whose clocks differ within the GoTime budget (<50 ms) may flip `loopIdx` ~50 ms apart — a sub-frame mismatch only at each boundary, the same class as existing item-boundary drift. Accepted.

## B. Incremental Game of Life + seeded noise

### Remove `mmPrecomputeLife`

The synchronous all-at-once precompute is replaced by per-frame incremental evolution using the existing `mmLifeStep` (keep it + its blinker rule test). Remove `mmPrecomputeLife` and its tests (`tests/unit/js/test_animations_life.js`'s precompute cases) — superseded.

### `gameOfLife` draw closure — incremental state machine

```js
draw: (function () {
  var GW = 48, GH = 36, G = 300, STEP_PER_FRAME = 12;
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
      // board ready — render live cells
      var base = gen * cells;
      ctx.fillStyle = '#7CFC00';
      for (y = 0; y < GH; y++) {
        for (x = 0; x < GW; x++) {
          if (cache.boards[base + y * GW + x]) { ctx.fillRect(x * cw, y * ch, cw + 1, ch + 1); }
        }
      }
    } else {
      // not computed yet — seeded shimmering noise (coordinated via shared tMs bucket)
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

**Key properties:**
- **No synchronous freeze:** at most `STEP_PER_FRAME` generations are evolved per frame. At ~12 gens/frame the full `G=300` cycle finishes in well under ~2 s even on the A8, and compute (hundreds of gens/sec) vastly outpaces the 10 gen/s display rate, so noise shows only briefly at each fresh seed.
- **Noise is coordinated:** derived from `MM_RNG(mmDeriveSeed(s, floor(tMs/100)))` with the shared `tMs` bucket → screens simultaneously in the noise state show the same noise, shimmering ~10/s.
- **Board determinism unchanged:** incremental evolution from gen 0 produces the identical generations as the old all-at-once precompute; chunking only changes *when* each gen is ready. Once all screens finish warming up they are in lockstep.
- **gen 0 is ready on the first frame** (seeded during reset, `computed=1`), so `tMs=0` always renders the board, never noise.

### Warmup transient

During the ~1–2 s warmup a faster device may have `computed` past the display gen (renders board) while a slower device is still behind (renders noise). This brief boot-phase divergence is acceptable — it converges as soon as both finish, and the noise reads as an intentional "powering up" effect rather than a glitch.

## Testing

All node `_canvas_stub` op-log + pure-helper tests; iPad-1 manual sign-off for timing/feel.

- **`mmLoopItemSeed`** — deterministic (same `(runSeed, loopIdx, itemIdx)` → same); different `loopIdx` → different; different `itemIdx` → different; `loopIdx=0` matches the prior single-derivation behavior is NOT required (value just needs to be consistent + distinct).
- **`mmLifeStep`** — keep the blinker (horizontal→vertical) rule test.
- **`gameOfLife`** op-log:
  - Deterministic at `tMs=0` (gen 0 ready first frame → identical `fillRect`s across two calls).
  - Seeded at `tMs=0` (different seed → different gen-0 board).
  - Noise state: a fresh-seed call at a far-ahead `tMs` (target gen ≫ `computed` after one frame) renders the noise grid, and the noise is deterministic for the same `(seed, tMs)`.
  - Structure: gen 0 draws `0 < fillRects ≤ GW*GH`.
- **Removed:** `mmPrecomputeLife` tests.
- **iPad-1 sign-off:** a looping animation re-randomizes each cycle and stays in lockstep across two screens; `gameOfLife` shows brief coordinated noise resolving into Life with no freeze, at each loop boundary.

## Non-goals

- No server/protocol change (loop index is client-derived from already-shared state).
- No `reseedPerLoop` opt-out flag (universal was chosen).
- No change to `STEP_PER_FRAME` tuning UI — it's a constant, adjusted in code if the iPad-1 sign-off shows the warmup too slow/fast.
- Other stateful sims (boids, reaction-diffusion) and the generic `mmPrecomputeCycle` extraction remain future work; this spec keeps the incremental pattern inline in `gameOfLife`.
- Mosaic-spanning (step 4) is unaffected and still ahead.
