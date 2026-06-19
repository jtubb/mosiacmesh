# Game of Life + Precompute-from-Seed — Design

**Date:** 2026-06-19
**Status:** Approved — ready for implementation plan.
**Builds on:** the coordinated-seed infra (`MM_RNG(seed)`, `Display.playSeed`, the 6th `seed` draw arg — merged to `main`) and the SCRIPT animation model (`js/animations.js`, `MM_ANIMATIONS`, node `_canvas_stub` determinism tests).

## Context

Conway's Game of Life is the first **stateful** animation: each generation depends on the previous, so it can't be a pure `f(tMs)` like the geometric/generative animations. We make it coordinated anyway by **precomputing a fixed cycle** of `G` generations from a seed-derived initial board, caching it, and rendering `precomputed[gen(tMs)]` each frame. The coordinated seed seeds the initial board, so the board sequence is **randomized per run but bit-identical across every screen in a group** — the first member of the "stateful sims" tier (boids, reaction-diffusion, falling-sand follow later).

This spec also establishes the **precompute-cycle pattern** that those later sims reuse — but keeps it GoL-specific for now (YAGNI); the generic extraction is deferred to the second consumer.

## Decisions (settled during brainstorming)

- **Render + test:** draw a `fillRect` per **live** cell (not all cells) on a modest grid; test via the canvas-stub **op-log** exactly like the other animations (same `(tMs, seed)` → identical `fillRect`s proves the board is identical). No `ImageData`, no offscreen canvas.
- **Generalization:** a focused `mmPrecomputeLife(seed, GW, GH, G)` helper + a per-entry closure cache. The generic `mmPrecomputeCycle(seed, initFn, stepFn, G)` is extracted later, from two real consumers.

## Architecture / data flow

`gameOfLife`'s `MM_ANIMATIONS` entry is built by an **IIFE that returns the draw closure**, so it carries a per-entry cache without polluting the module scope:

```js
{
  key: 'gameOfLife',
  label: "Conway's Game of Life",
  description: '...',
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
      var gen = Math.floor(tMs / 100) % G;               // 10 gen/s, loops every 30s
      if (gen < 0) { gen = 0; }
      var base = gen * cells;
      var cw = w / GW, ch = h / GH, x, y;
      ctx.fillStyle = '#7CFC00';                          // alive color
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

**Why precompute-all-once (not on-demand):** rendering gen N on demand would require evolving N steps every frame (gen 500 → 500 evolutions/frame). Precomputing all `G` once and caching gives O(1)-per-frame render. The coordinated seed is what makes that table identical on every screen — a precomputed cycle is only safe to share when its initial state is identical everywhere, which is exactly what the seed guarantees.

## Components

### `mmPrecomputeLife(seed, GW, GH, G)` (new module-level helper in `js/animations.js`)

- Allocate `var boards = new Uint8Array(G * GW * GH)` (1 byte/cell; `Uint8Array` is supported on Safari 5.1 / iOS 5). At 48×36×300 ≈ **520 KB** — fine on the 256 MB iPad-1.
- **Init gen 0:** `var rng = MM_RNG(seed); for each cell: boards[i] = rng() < 0.35 ? 1 : 0;` — the per-run coordinated board.
- **Evolve gen 1..G-1:** standard Conway rule (a live cell survives with 2–3 live neighbours; a dead cell is born with exactly 3), **toroidal edges** (neighbours wrap via `(x+dx+GW)%GW`, `(y+dy+GH)%GH` — avoids edge die-off and keeps the field active). Read from `boards[(g-1)*cells + ...]`, write to `boards[g*cells + ...]`.
- Returns `boards`. Pure: same `seed` → identical array. No canvas, no `Math.random`.

### `gameOfLife` draw (the closure above)

- Recomputes only on seed change (cache keyed by `seed >>> 0`).
- `runScriptLoop` already `clearRect`s each frame, so **dead cells are transparent** — the `#canvas` background (set from `item.backgroundColor`, default `#000000`) shows through. No background `fillRect` needed; the op-log's `fillRect` count equals the live-cell count exactly.

## Parameters & cost

- Grid **48×36**, **G=300**, `gen = floor(tMs/100) % 300` (10 generations/sec, 30 s loop). Live-cell `fillRect` count ≈ 600 at the dense gen 0, settling far lower — comparable to `plasma` (1200).
- **One-time precompute stall:** ~`G*GW*GH*8` ≈ 4M neighbour reads on the first draw per run — a brief freeze (tens-to-~100 ms on the A4). Done lazily on first draw.
- These are starting points; the iPad-1 sign-off validates the stall + frame rate. **Tuning levers if needed:** shrink `G`/grid, or move the precompute into `showItem`/PRELOAD (off the rAF path) so it doesn't drop a playback frame.

## Edge cases

- **Seed change** (new run) → cache miss → one recompute. **Same seed** → cache hit, O(1)/frame.
- **Mid-playback join / reconnect:** the late-joiner receives the same seed in its PLAY payload (the seed infra's reconnect path), recomputes the identical table, and renders the same `gen` at the same `tMs` — in lockstep after its ~100 ms build (brief black, then synced; acceptable).
- **Extinction / still-life / oscillator:** a board may settle to a static or periodic state; that is valid GoL behaviour (no crash, just a calm screen). The structure test samples gen 0, which is guaranteed populated (~35 % density).
- **`seed == 0`:** `MM_RNG(0)` uses its non-degenerate default → a valid fixed board. No blank/crash.
- **Tab backgrounding / first frame:** if the rAF loop drops the precompute frame, the next frame renders normally; the visible animation only depends on `precomputed[gen]`, identical everywhere.

## Testing

Node `_canvas_stub` op-log, matching the batch (`byKey.gameOfLife(ctx, tMs, W, H, 0, seed)`):
1. **Deterministic** — same `(tMs, seed)` → identical `__ops` (`deepStrictEqual`). Proves the precomputed board is identical across runs/screens.
2. **Animates** — gen 0 (`tMs=0`) vs gen 20 (`tMs=2000`) → `notDeepStrictEqual`.
3. **Seeded** — different `seed`, same `tMs` → `notDeepStrictEqual` (different initial board → different evolution).
4. **Structure** — at `tMs=0` (gen 0): `0 < fillRect count ≤ GW*GH`.

The precompute runs in-node (pure board math, no canvas) and is fast on a dev machine. Extend `test_animations_module.js`'s key-list with `gameOfLife`. iPad-1 hardware sign-off (manual, final task): the precompute stall is acceptable (≤ ~1 dropped frame at item start), steady-state holds 30+ FPS, and two iPads in a group show the same board at the same instant (the coordination payoff) AND a different board per run.

## Non-goals (follow-ups)

- **Generic `mmPrecomputeCycle(seed, initFn, stepFn, G)`** — extracted later, from two real consumers (this + the next sim).
- **Other stateful sims** (boids, reaction-diffusion, falling-sand) — they reuse the pattern this establishes; separate specs.
- **`ImageData` rendering / denser grid** — not needed; `fillRect`-of-live-cells is testable and fast enough at 48×36.
- **Incremental/background precompute, seeded alive-color, configurable density/speed** — possible later refinements; v1 ships one canonical look.
- No server change, no protocol change — a pure client-side `MM_ANIMATIONS` leaf addition (plus one module-level helper).
