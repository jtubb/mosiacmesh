# Splash Crown Transition ("splashcrown") — Design

**Date:** 2026-06-27
**Status:** Approved (design); pending implementation plan
**Area:** `effects.py` (catalog), `js/transitions.js` (pure helpers + `mmDrawSplash` draw glue + `mmTransitionState` branch), `index.html` (one mask-family in-canvas branch). Editor is data-driven and needs no change.

## Overview

A **mask-family** transition, `splashcrown` — a droplet of beer falls and impacts the wall center, a crown of beer spikes leaps up, and an **opaque beer disc blooms outward** from the impact to cover the content. The brewery cousin of the iris: a radial cover/reveal whose advancing edge is the classic Worthington **crown** (pointed jets tipped with beads, some flung ahead). As a `startEffect` it plays in reverse — the beer disc contracts back to the center and the droplet lifts off, revealing the incoming content.

It joins the other procedural draw-helper effects (beerfill/scatter/frostcreep/wheatpart) and reuses beerfill's beer palette + scatter's seeded-bead pattern. Runs on the 1st-gen iPad-1 (iOS 5.1 / Safari 5.1): client code is **ES5 only**, 2D canvas, **no `clip()` / `matrix3d` / WebGL / `ctx.ellipse`** (the opaque disc is a plain filled `arc`, not a mask hole).

## Architecture — mask family, local-progress + lead-in sequence

`splashcrown` rides the same path the other mask effects use:

- `mmTransitionState` returns, for `name === 'splashcrown'` (branch beside wheatpart/scatter/frostcreep): `effect: { name:'splashcrown', family:'mask', front:<localProgress>, scope, params, phase }`, `wipe:null`. `phase = mmSplashPhase(role)` → `'cover'` (out) / `'reveal'` (in). `front` = LOCAL phase progress rising `0→1` for BOTH roles (out-role `1-p`, in-role `p`) — the scatter convention.
- The draw helper resolves direction + the lead-in/bloom sequence from `phase` via `mmSplashSeq`.

## Geometry & sequence

An opaque beer disc centered at the wall center (`scope:'screen'` → the screen-region center), radius `R = bloom · halfDiagonal`, so `bloom=1` covers the wall corners (full beer). A `leadFrac ≈ 0.18` lead-in (like beerfill's pour) precedes the bloom:

- **`endEffect` (cover):** `[0, leadFrac]` — a procedural beer **droplet falls** top→center (no beer yet, content A visible); `[leadFrac, 1]` — **impact**, the crown leaps and the **beer disc blooms** `R: 0→full`, covering A with crown spikes + flung beads on the advancing edge. Ends full beer.
- **`startEffect` (reveal):** the time-reverse — starts **full beer**, the disc **contracts** `R: full→0` revealing B (crown rim collapsing inward), and at the very end the droplet lifts back off the top.

Both roles sit at full beer at the A→B handoff (cover ends `bloom=1`; reveal starts `bloom=1`), so the seam is continuous.

## Pure helpers (testable core), in `js/transitions.js`

- **`mmSplashPhase(role)`** → `'cover'` (out) | `'reveal'` (in).

- **`mmSplashSeq(phase, front, leadFrac)`** → `{ dropY, bloom, impacted }`:
  - `lp = (phase === 'cover') ? clamp(front) : (1 − clamp(front))` (cover forward; reveal time-reverses).
  - `lp < leadFrac` → lead-in: `{ dropY: lp/leadFrac, bloom: 0, impacted: false }` (`dropY` 0=top → 1=center).
  - else → bloom: `{ dropY: 1, bloom: (lp−leadFrac)/(1−leadFrac), impacted: true }`.
  - Endpoints: cover `front=1` → `bloom=1`; reveal `front=0` → `bloom=1` (handoff full-beer); reveal `front=1` → `bloom=0, dropY=0` (drop lifted, fully revealed).

- **`mmSplashRadius(bloom, GW, GH)`** → `clamp(bloom) · 0.5 · √(GW²+GH²)` (half-diagonal; covers the corners at `bloom=1`).

- **`mmCrownSpikes(seed, count)`** → deterministic array of `count` spikes `{ ang, lenF, beadF, flyF, phase }` via the shared seeded `_mmLcg` (identical on every screen → wall-coherent). `ang` = rim angle, `lenF` = spike length fraction, `beadF` = tip-bead size, `flyF` = how far the detached bead flies ahead, `phase` = a per-spike jitter.

- **Reuses `mmBeerPalette(beerType)`** (from beerfill) for the beer/foam colors — no new palette.

Every choice that could be wrong (lead-in vs bloom split, disc radius, spike distribution, handoff continuity) lives in these node-tested helpers; the draw glue only consumes the numbers.

## Draw glue — `mmDrawSplash`, in `js/transitions.js`

`mmDrawSplash(ctx, params, phase, front, GW, GH, quad, scope, seed, now)` (thin; mirrors `mmDrawBeer`/`mmDrawScatter`):

1. `seq = mmSplashSeq(phase, front, leadFrac)`; `pal = mmBeerPalette(params.beerType)`; `reg = _mmMaskRegion(scope, quad, GW, GH)`; center `cx,cy` = region center.
2. **Lead-in (`!seq.impacted`):** draw the procedural beer **droplet** — a beer-colored teardrop (stretched vertically by fall speed) with a thin motion streak — at `cx`, `y = seq.dropY · cy` (top→center). No beer disc yet.
3. **Bloom (`seq.impacted`):** `R = mmSplashRadius(seq.bloom, reg.w, reg.h)`; fill the opaque beer **disc** (`arc` at `cx,cy` radius `R` + beer vertical gradient `pal.beerTop→pal.beerBot`). Then the **crown** at the rim: for each spike (from `mmCrownSpikes(seed, crownCount)`) a beer triangle jutting outward from `R` along `ang` with a foam-highlighted **bead** at the tip, plus a detached **flung bead** ahead by `flyF` (alpha/offset scaled by `bloom` so beads lead the advancing edge).
4. **Per-screen cull:** spikes whose rim point (`cx+R·cosθ, cy+R·sinθ`, plus reach) can't land on this client's `quad` are skipped (the wheatpart lesson). `quad` null → no cull.

ES5 / Safari-5.1 safe: `arc`, `beginPath`/`moveTo`/`lineTo`/`closePath`, `createLinearGradient`, `save`/`restore`, `translate`/`rotate`, `fill`/`stroke`, `fillRect`. No `clip()`, `ctx.ellipse`, `matrix3d`, 3D, or filters.

## `effects.py` — `SplashCrownEffect`

Single-`duration` (covers OR reveals, never both). Params:
- `beerType` — choice `[pale, amber, stout]`, default `"pale"`.
- `crownCount` — number, default `28`, min `8`, max `60` (spikes around the rim).
- `scope` — choice `[screen, wall]`, default `"wall"`.
- `duration` — number, default `2000`, min `0`.
- `audioFade` — boolean, default `True`.
- `video_filters(role, params, ctx)` → audio-only (`_afade`); no baked video filter.

## Apply — one mask-family site (`index.html`)

A `splashcrown` branch in `runScriptLoop`'s mask block (beside `wheatpart`/`scatter`/`frostcreep`, before the `mmDrawMaskInCanvas` fallback), drawn under the mesh affine in global coords:
```js
} else if (stc.effect.name === 'splashcrown' && typeof mmDrawSplash === 'function') {
    mmDrawSplash(ctx, stc.effect.params, stc.effect.phase, stc.effect.front,
        it.meshGlobal[0], it.meshGlobal[1], it.meshQuad, stc.effect.scope,
        playback.seed | 0, GoTime.now());
}
```
**Mesh-only** — no element/media-path apply (consistent with the other procedural draw helpers); no-ops to a plain cut on media items. The seeded crown uses `playback.seed` so all clients share one disc + crown.

## Render-token / server impact

**None beyond the catalog.** Visual is client-side; only the audio-fade signature enters the render token. Editing `beerType`/`crownCount`/`scope` (or `duration` while `audioFade` off) is instant, no re-render. A regression-guard test confirms the new visual params never change the token.

## Testing

- **Node (`tests/unit/js/test_splashcrown.js`):**
  - `mmSplashPhase`: role mapping.
  - `mmSplashSeq`: lead-in vs bloom split at `leadFrac`; endpoints (cover `front=1`→bloom 1; reveal `front=0`→bloom 1 [handoff]; reveal `front=1`→bloom 0 + dropY 0); `dropY` rises 0→1 across the lead-in; clamps.
  - `mmSplashRadius`: `bloom=0`→0, `bloom=1`→half-diagonal (covers corners); clamps.
  - `mmCrownSpikes`: determinism (same seed/count → identical array); length = count; `ang ∈ [0,2π)`, fractions in range.
  - `mmTransitionState` `splashcrown` branch: `family==='mask'`, `phase` per role, `front` rising 0→1 for both an out- and in-role offset.
  - `mmDrawSplash` stub-ctx smoke: lead-in draws the droplet (no disc fill); bloom fills the disc + crown; balanced save/restore; degenerate inputs don't throw.
- **Python (`tests/unit/test_effects.py`):** `splashcrown` in `effect_catalog()` with params/defaults; `video_filters` audio-only + single-duration role-aware; `audioFade:false` bakes nothing. **`tests/unit/test_mosaic.py`:** render-token regression guard varying `beerType`/`crownCount` (token-neutral).
- **On-wall iPad-1 sign-off (acceptance):** drop falls top→center, crown leaps, opaque beer disc blooms to cover A; full beer at the handoff; reveal contracts to show B with the drop lifting off; crown spikes + flung beads read clearly; one shared disc across the wall; smooth at wall scale.

## Demo / delivery

A **Splash Crown Demo** playlist (two plasma mesh items handing off via `splashcrown`, pale, crownCount 28, duration 2000) — `tools/_make_splash_demo.py`.

## Out of scope (YAGNI)

- Multiple simultaneous splash points / configurable off-center impact (single center splash).
- The element/media path (mesh-only, like the other procedural draw helpers).
- Secondary ripple rings ahead of the beer (chose spikes + flung beads).
- The droplet as a selectable PNG sprite (procedural only).
