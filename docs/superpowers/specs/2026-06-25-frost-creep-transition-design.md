# Frost Creep Transition ("frostcreep") — Design

**Date:** 2026-06-25
**Status:** Approved (design); pending implementation plan
**Area:** `effects.py` (catalog), `js/transitions.js` (pure noise/coverage helpers + drawer), `index.html` (in-canvas + overlay mask dispatch). Editor is data-driven and needs no change.

## Overview

A new **mask-family** transition, `frostcreep`, joining the brewery family (`beerfill`, `scatter`, `kegroll`). Frost spreads organically across the region — patches appear and merge where a static seeded noise field falls under a rising coverage threshold — until the region is fully iced over (cover), then recedes/melts back (reveal). It reads as creeping frost rather than a hard `dissolve` because the noise field is **spatially correlated** (smoothed), so adjacent cells freeze together into coherent growing patches.

Fully procedural (no sprite asset). Must run on the 1st-gen iPad-1 display clients (iOS 5.1 / Safari 5.1): client code is **ES5 only**, per-frame work is `arc`+`fill` + a `fillRect` — no `clip()`, no `destination-*` compositing, no CSS filters.

## Architecture — mask family, additive `effect` descriptor

`frostcreep` rides the **same path Beerfill/Scatter use** (the additive `effect` descriptor + the in-canvas/overlay mask dispatch); the proven Wipe path is untouched.

- `mmTransitionState` returns, for `name === 'frostcreep'` ONLY, `effect: { name:'frostcreep', family:'mask', front: cover, scope, params, phase }` (and `wipe:null`). Other effects' returns are unchanged.
- Both apply sites already branch on `st.effect.family === 'mask'`:
  - **In-canvas** (`runScriptLoop`, mesh SCRIPT animations): `ctx` is under the mesh affine → drawn in global coords, wall-coherent.
  - **Overlay** (`applyTransitionNow`, media/mirror): the overlay 2d context with the mesh matrix applied.

## Role-aware behavior

Reads the transition role; `cover ∈ [0,1]` is the coverage front (fraction of the threshold range currently frosted). Local progress `flp` rises 0→1 over either phase (same inversion as beerfill: `role 'out'` counts `p` down, so invert).

- **`endEffect` (COVER):** `phase = 'cover'`, `cover = flp` (0→1). Frost builds until the region is fully iced, hiding the outgoing item. Phase duration = `duration`.
- **`startEffect` (REVEAL):** `phase = 'reveal'`, `cover = 1 − flp` (1→0). Frost recedes, revealing the incoming item. Phase duration = `duration`.

Placing `frostcreep` as item A's `endEffect` and item B's `startEffect` (same params) produces the full freeze-then-thaw handoff; the region is fully covered at the boundary.

## Pure helpers (testable core), in `js/transitions.js`

- **`mmFrostPhase(role)`** → `'cover'` when `role === 'out'`, else `'reveal'`. (Mirrors `mmBeerPhase`/`mmKegPhase`.)
- **`mmFrostField(blocks, seed)`** → array of `blocks*blocks` thresholds in `[0,1)`, precomputed once. Seed per-cell randoms via the existing `_mmLcg`, then run **2 box-blur passes** (each cell averaged with its 4-neighbours, edge-clamped) to introduce spatial correlation, then renormalize via `(v − min) / (max − min)` scaled to span **`[0, 0.98)`** (kept strictly `< 1` so every cell frosts before `cover` reaches 1; the consolidation fill then guarantees full opacity). Guards a flat field (`max − min < 1e-9` → all-zeros). The smoothing is what turns speckle into coherent frost patches. Deterministic + wall-coherent (same `seed` → identical field on every screen).
- **`mmFrostBlotch(fieldVal, cover, grow)`** → `{ on, t }`. `on = cover >= fieldVal` (cell has started to frost). `t = clamp((cover − fieldVal) / grow, 0, 1)` — growth 0→1 over the `grow` window after the threshold is crossed (`grow` ≈ 0.25 locked). Drives the blotch radius + opacity so frost creeps in soft rather than popping.

Coverage convention: `mmTransitionState` computes `cover` (local progress for `cover` phase, `1 − local progress` for `reveal`) and passes it as `effect.front`. A cell frosts when `field[c] ≤ cover`.

## `effects.py` — new `Effect` subclass

`FrostCreepEffect(Effect)`, mirroring the single-`duration` pattern (a frostcreep instance only ever covers or reveals, never both):

- `name = "frostcreep"`, label `"Frost Creep"`.
- `params`:
  - `tint` — choice `[frost, blue, clear]`, default `"frost"` (icy palette: near-white / pale-blue / faint blue-glass).
  - `scope` — choice `[screen, wall]`, default `"wall"`.
  - `duration` — number, default `2200`, min `0`.
  - `audioFade` — boolean, default `True`.
- `video_filters(role, params, ctx)` returns audio-only (`_afade`) when `audioFade` on, else `([], [])`. No baked video filter (visual is client-side).

## Drawer — `mmDrawFrost(ctx, params, phase, cover, GW, GH, quad, scope, seed)`

Canvas glue (thin). No `t` arg — frost is time-independent (only the threshold rises).

1. `reg = _mmMaskRegion(scope, quad, GW, GH)`; `blocks = FROST_BLOCKS` (locked ≈18; a `?frostblocks=N` live URL knob for on-wall tuning, like `?scount`/`?kgfill`).
2. `field = mmFrostField(blocks, seed)` — **memoized** on a module cache keyed by `(seed, blocks)`, rebuilt only when those change (not per frame).
3. Resolve the `tint` palette (frost-white / pale-blue / faint blue). For each cell: `fb = mmFrostBlotch(field[c], cover, GROW)`; skip if `!fb.on`. Draw a soft frost disc at the cell center, radius ≈ `cellSize · (0.6 + 0.7·fb.t)` (grows past the cell so neighbours overlap into patches), fill-alpha ≈ `fb.t`. A faint sparkle dot at high `t` for ice glint.
4. **Consolidation fill** (gap-free guarantee): when `cover ≥ 0.88`, fill the whole region with frost-white at alpha `clamp((cover − 0.88) / 0.12)` → fully opaque exactly at `cover = 1`, so the outgoing item is completely hidden at the handoff regardless of blotch gaps.

`arc` / `fillRect` only — no clip/composite/filter. `FROST_BLOCKS` is the cost lever (~324 arcs at full coverage, in the proven range of `dissolve`'s 256 fillRects).

## Two apply sites (`index.html`)

Each mask-dispatch site gains a `frostcreep` branch calling `mmDrawFrost(..., playback.seed | 0)` — same shape as the beerfill/scatter branches:
- in-canvas: `mmDrawFrost(ctx, stc.effect.params, stc.effect.phase, stc.effect.front, it.meshGlobal[0], it.meshGlobal[1], it.meshQuad, stc.effect.scope, playback.seed | 0)`
- overlay: `mmDrawFrost(cmx, st.effect.params, st.effect.phase, st.effect.front, GWm, GHm, quad, st.effect.scope, playback.seed | 0)`

## Render-token / server impact

**None beyond the catalog.** Visual is client-side; only the audio-fade signature (`role`, `duration`, when `audioFade` on) enters the render token — `_audio_fade_sig` reads only `audioFade`. Editing `tint`/`scope` (or `duration` while `audioFade` off) is instant, no re-render. A regression-guard test confirms the new visual params never change the token.

## Testing

- **Node (`tests/unit/js/test_frostcreep.js`):**
  - `mmFrostPhase`: `out`→`cover`, `in`→`reveal`.
  - `mmFrostField`: same seed → identical; different seed → different; every value in `[0,1)`; **spatial correlation** — mean |delta| between horizontally-adjacent cells < mean |delta| between random pairs (proves the box-blur smoothing took effect).
  - `mmFrostBlotch`: `cover < fieldVal` → `on:false`; `cover === fieldVal` → `on:true, t:0`; `cover ≥ fieldVal + grow` → `t:1` (clamped); monotonic in `cover`.
  - `mmTransitionState` `frostcreep` branch: `family:'mask'`, correct `phase`, `front`/coverage rises on cover (end-role) and falls on reveal (start-role), `scope` default `wall`; existing transition tests unchanged.
  - `mmDrawFrost` recording-context smoke: `cover = 0` → no blotch arcs; a mid `cover` → some arcs; near `cover = 1` → consolidation `fillRect` present.
- **Python (`tests/unit/test_effects.py`):** `frostcreep` in `effect_catalog()` with params/defaults; `video_filters` audio-only + single-duration role-aware (`afade` in/out, duration honored); `audioFade:false` bakes nothing; render-token regression guard (visual-param edits don't change the audio signature).
- **On-wall iPad-1 sign-off (acceptance):** on the calibrated mesh, `frostcreep` (tint=frost, scope=wall) as `endEffect`→`startEffect` — frost spreads in coherent patches that merge to a clean full cover, then recedes; smooth at wall scale; no gaps at the handoff.

## Demo / delivery

A **Frost Creep Demo** playlist (two plasma mesh items handing off via `frostcreep`, tint=frost, scope=wall), alongside the existing Beer / Scatter / Keg Roll / Transition demos. No sprite asset (fully procedural).

## Legacy / ES5 constraints

All client code ES5 only (no `let`/`const`/arrow/template-literal/`class`). Canvas ops: `arc`+`fill`, `fillRect`, `globalAlpha` — all Safari-5.1-safe. No `clip()`, no `destination-*`, no filters.

## Out of scope (YAGNI)

- Exposing `blocks` / `grow` / blotch-size / sparkle as params (locked constants; `?frostblocks` for tuning only).
- Animated/twinkling frost (the field is static; only the threshold moves).
- Per-cell crystal dendrites (chose soft blotches).
- The remaining brewery effects (coaster flip / splash crown / wheat part — separate specs).
