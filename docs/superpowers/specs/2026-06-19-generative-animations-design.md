# Generative Animations Batch — Design

**Date:** 2026-06-19
**Status:** Approved — ready for implementation plan.
**Builds on:** the coordinated-seed infra (`MM_RNG` / `mmDeriveSeed`, `Display.playSeed`, the 6th `seed` draw arg — PR #41) and the SCRIPT animation model (`js/animations.js`, `MM_ANIMATIONS`, the node `_canvas_stub` determinism tests).

## Context

The seed infra makes "randomized but coordinated" animations possible: a server-minted per-run seed reaches every screen, so a seeded PRNG (`MM_RNG`) produces identical "random" visuals everywhere, fresh each playback. `plasma` was the first consumer. This batch adds **four generative animations** that each draw their randomness from the seed: `starfield`, `fireworks`, `truchet`, `spirograph`.

Each is a pure ES5 `draw(ctx, tMs, w, h, nowMs, seed)` added as one `MM_ANIMATIONS` entry. They ignore `nowMs` (not wall-clock). Same `(tMs, seed)` → identical frame on every screen; fresh seed per run → different look. The admin picker auto-updates (it reads `MM_ANIMATIONS`); no server change.

## Two seeding patterns (the reusable shape)

- **Spatial-config seeding** (`starfield`, `truchet`, `spirograph`): the seed fixes a layout/shape *once* (`var rng = MM_RNG(seed)` at the top of `draw`, consumed to build the configuration); `tMs` only animates that fixed configuration.
- **Temporal-event seeding** (`fireworks`): the seed drives a *schedule* of events via `mmDeriveSeed(seed, slotIndex)` — each time slot gets its own derived sub-seed, so the event stream is deterministic and non-repeating.

## The animations

All are ES5 (`var`/`function`, no `let`/`const`/arrow/template-literals), Canvas2D primitives only, no `Math.imul`, randomness ONLY via `MM_RNG`/`mmDeriveSeed` (never `Math.random()`). Counts below are starting points; the iPad-1 sign-off may tune them.

### 1. `starfield` — warp-stars (spatial-config)

**Visual:** a field of stars streaking outward past the viewer (classic "warp speed"), a different arrangement each run.

**Math:** `var rng = MM_RNG(seed)`. For `i` in `[0, N)` (N≈200): a fixed direction `ang_i = rng() * 2π`, a phase `phase_i = rng()`, a brightness `b_i = 0.4 + rng()*0.6`. Per frame, the star's depth cycles near→far: `z = 1 - frac(tMs/SPEED + phase_i)` (frac = `x - floor(x)`); `z` in `(0,1]`. Screen distance from center grows as `z→0`: `r = (1/z - 1) * SPREAD`, capped to the canvas. Position `= (cx + cos(ang_i)*r, cy + sin(ang_i)*r)`. Draw a short streak from the star's previous-z position to its current one (length grows near the edge) in a grey scaled by `b_i`. `SPEED`≈3000ms, `SPREAD`≈min(w,h)*0.04. Stars beyond the canvas are skipped.

**Cost:** ~200 short `moveTo/lineTo` strokes. <2ms.

### 2. `fireworks` — time-slotted bursts (temporal-event)

**Visual:** rockets rise and explode into fading particle bursts; a continuous, non-repeating show, identical on every screen.

**Math:** `SLOT_MS`≈800. The active slot index now is `S = floor(tMs / SLOT_MS)`. For each candidate slot `n` in a small window `[S-2, S]` (so recently-launched bursts still visible), derive `var brng = MM_RNG(mmDeriveSeed(seed, n))` → launch x `lx = brng()*w`, peak height `py = h*(0.15 + brng()*0.35)`, hue `hue = brng()*360`, particle count `M` (≈30–50 from `brng()`), spread speed. Burst start `t0 = n*SLOT_MS`; local time `dt = tMs - t0`. Phase A (rocket rise, `dt < RISE_MS≈450`): a dot ascending `lx → (lx, py)` on an ease. Phase B (explosion, `RISE_MS ≤ dt < LIFE_MS≈1400`): for `j` in `[0,M)`, a particle at angle `2π*j/M` (+ small per-j jitter from `brng()` cached), radius `= v*(dt-RISE_MS)`, plus gravity `+ 0.5*G*(dt-RISE_MS)^2`, alpha fading `1 - (dt-RISE_MS)/(LIFE_MS-RISE_MS)`. Skip slots whose `dt ≥ LIFE_MS` or `dt < 0`. Render with `fillRect` dots colored by `hue`.

**Determinism note:** derive ALL of a burst's randomness from one `MM_RNG(mmDeriveSeed(seed, n))` stream pulled in a fixed order; never call `MM_RNG` inside the per-particle loop with a per-frame-varying count, so two frames at the same `tMs` pull identical values.

**Cost:** ≤2 active bursts × ≤50 particles ≈ 100 `fillRect`/frame. ~2ms.

### 3. `truchet` — generative tiles (spatial-config)

**Visual:** a grid of quarter-arc tiles forming flowing connected curves — a different "maze" each run — with a slow hue/brightness wave traveling across it.

**Math:** grid `GW × GH` (≈ derived so cells are ~square: `GW = round(w/cell)`, `cell = min(w,h)/8`). `var rng = MM_RNG(seed)`. For each cell `(gx, gy)`: orientation `o = rng() < 0.5 ? 0 : 1` (two truchet rotations of the two-quarter-arc tile). The arcs are **static** per run (fixed by the seed). `tMs` animates only color: each cell's hue = `(baseHue + (gx+gy)*8 + tMs/40) % 360` and a brightness bump where a diagonal wave front (`pos = (gx+gy) - tMs/PERIOD*…`) passes — a traveling highlight. Draw each cell's two quarter-arcs (`ctx.arc` centered on the appropriate corners) with the cell's color. Keeping arcs static (only color animates) preserves purity trivially.

**Cost:** `GW*GH` ≈ 48 cells × 2 arcs = ~96 arc ops. ~3ms.

### 4. `spirograph` — hypotrochoid (spatial-config)

**Visual:** a spirograph curve traced over time; a different figure each run; slowly rotating.

**Math:** `var rng = MM_RNG(seed)`. Gear params: `R = 0.4 + rng()*0.1` (outer, fraction of `minDim/2`), `r = 0.05 + rng()*0.25` (inner), `d = 0.3 + rng()*0.6` (pen offset). Scale `= min(w,h)*0.45`. Hypotrochoid for parameter `θ`:
- `x(θ) = ((R−r)·cos θ + d·r·cos(((R−r)/r)·θ)) · scale + cx`
- `y(θ) = ((R−r)·sin θ − d·r·sin(((R−r)/r)·θ)) · scale + cy`

Trace `N`≈500 points over `θ ∈ [0, θ_max]` where `θ_max` grows with `tMs` then holds/loops (progressive draw), and add a slow global rotation `+ tMs/9000` to the whole figure. Stroke with a slowly-cycling hue (`hsl((tMs/40)%360, ...)`). Cap `(R−r)/r` to a sane range so the figure closes in a reasonable number of turns.

**Cost:** ~500 `lineTo` segments. <2ms (like `lissajous`).

## Edge cases

- **`seed == 0` / no seed** — `MM_RNG(0)` uses its non-degenerate default (one fixed-but-valid look); every pre-seed-aware path still renders. No crash, no blank.
- **Per-frame determinism** — every animation pulls `MM_RNG` values in a fixed order at the top of `draw` (or once per fixed slot for `fireworks`); no `MM_RNG` call is gated by a `tMs`-varying branch/count, so two `draw`s at the same `(tMs, seed)` produce byte-identical op logs.
- **Off-canvas / degenerate** — `starfield` skips stars whose `r` exceeds the canvas diagonal; `spirograph` clamps gear ratios; `fireworks` skips expired/not-yet-started slots.
- **Aspect ratio** — radii use `min(w,h)`; grid spacing uses `w`/`GW` (not fixed pixels), so all four fit any screen shape.

## Testing

Per the established node `_canvas_stub` pattern, one test file per animation (`tests/unit/js/test_animations_<name>.js`), each asserting:
1. **Deterministic** — same `(tMs, seed)` → identical `__ops` (`deepStrictEqual`). The sync guarantee.
2. **Animates** — different `tMs` (same seed) → different `__ops`.
3. **Seeded** — different `seed` (same `tMs`) → different `__ops`. The per-run-variety guarantee.
4. **Structure/op-count sanity** — e.g. `truchet` draws `GW*GH*2` arcs; `spirograph` draws a non-empty stroke; `starfield` draws ≤N streaks; `fireworks` draws a bounded particle count.

Plus: extend `test_animations_module.js`'s expected-key list to include the 4 new keys; the existing module-load smoke covers admin consumption. iPad-1 hardware sign-off (manual, final task): each sustains 30+ FPS, and two iPads in a group are visually in lockstep (same frame at the same instant) AND show the same per-run variation.

## Non-goals (follow-ups)

- **Stateful sims** (`gameOfLife`, boids, reaction-diffusion) — step 3; needs the precompute-from-seed pattern.
- **Mosaic-spanning** — step 4; needs the per-client coordinate-transform payload.
- **Per-animation parameter controls** (density, palette, speed knobs) — a later parameterization slice; each ships with one canonical look.
- No new server code, no protocol change — these are pure client-side `MM_ANIMATIONS` leaf additions.
