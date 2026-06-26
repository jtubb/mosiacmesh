# Wheat Part Transition ("wheatpart") — Design

**Date:** 2026-06-25
**Status:** Approved (design); pending implementation plan
**Area:** `effects.py` (catalog), `js/transitions.js` (pure geom/field/palette helpers + `mmDrawWheat` draw glue + `mmTransitionState` branch), `index.html` (one mask-family in-canvas branch). Editor is data-driven and needs no change.

## Overview

A **mask-family** transition, `wheatpart` — a full-height curtain of golden wheat that **parts from a center seam**. As a `startEffect` the wheat starts closed (a full wall of wheat) and **parts open**, the two halves leaning and sliding outward to reveal the incoming content; as an `endEffect` the wheat **closes** from the edges over the outgoing content. Placed as item A's `endEffect` and item B's `startEffect`, both reach full-wheat at the midpoint → one continuous part across the handoff.

It is the brewery cousin of the iris (a center-seam reveal instead of a circle), and joins the other procedural draw-helper effects (beerfill/scatter/frostcreep). Runs on the 1st-gen iPad-1 (iOS 5.1 / Safari 5.1): client code is **ES5 only**; the field is drawn purely in 2D canvas (tapered strokes + ellipse heads) — no image asset, no CSS 3D, no WebGL, no `clip()`.

## Architecture — mask family, local-progress openness

`wheatpart` rides the **same path** the other mask effects use:

- `mmTransitionState` returns, for `name === 'wheatpart'` (added to the mask-family branch alongside beerfill/scatter/frostcreep): `effect: { name:'wheatpart', family:'mask', front: <inverted local progress>, scope, params, phase }`, `wipe:null`. `phase = 'cover'` (out/endEffect) or `'reveal'` (in/startEffect).
- **Local-progress `front` (the mask-family convention):** `mmTransitionState` passes the draw helper `front` = **local phase progress rising `0 → 1` for *both* roles** (the out-role `p` counts down `1 → 0`, so it is inverted there — exactly like beerfill/scatter/frostcreep). The helper then maps `front` to **openness** using `phase`, mirroring `mmScatterCover`/`mmBeerLevel`:
  - `mmWheatOpenness(phase, front) = (phase === 'reveal') ? front : (1 − front)` — openness 0 = fully closed (full wall of wheat, content hidden), 1 = fully open (content visible).
  - **`startEffect` (reveal, `phase='reveal'`):** `front` 0 → 1 ⇒ openness 0 → 1 — wheat parts open to reveal incoming content.
  - **`endEffect` (cover, `phase='cover'`):** `front` 0 → 1 ⇒ openness 1 → 0 — wheat closes over the outgoing content.
- Both roles reach openness 0 (full opaque wheat) at the A→B handoff (cover ends at `front=1`, reveal starts at `front=0`), so the seam is continuous.

## Geometry

Single vertical seam at wall-center `cx = GW/2`. Gap half-width `g = openness · cx`:
- Left wheat wall occupies `[0, cx − g]`, right wall `[cx + g, GW]` — each an **opaque straw backdrop rect** (the sliding wheat wall) that slides outward by `g`, with leaning procedural stalks drawn on top.
- openness 0 → `g = 0`: the two walls meet at `cx`, covering the whole wall.
- openness 1 → `g = cx`: both walls have cleared to the edges, content fully visible.

The opaque backdrop guarantees the outgoing content is hidden at the handoff (stalks alone would let content peek through); the stalks supply the organic lean + sway.

## Pure helpers (testable core), in `js/transitions.js`

- **`mmWheatOpenness(phase, front)`** → openness `0..1`: `(phase === 'reveal') ? clamp(front) : (1 − clamp(front))`. The role→openness mapping, isolated and node-tested (mirrors `mmScatterCover`/`mmBeerLevel`).

- **`mmWheatPartGeom(openness, GW, GH)`** → `{ cx, g, leftEdge, rightEdge, slide, lean }`:
  - `cx = GW/2`; `o = clamp(openness, 0, 1)`; `g = o · cx`; `leftEdge = cx − g`; `rightEdge = cx + g`; `slide = g`; `lean = o · MAX_LEAN` (`MAX_LEAN ≈ 0.5` rad).
  - Endpoints: `openness=0` → `{ g:0, leftEdge:cx, rightEdge:cx, slide:0, lean:0 }`; `openness=1` → `{ g:cx, leftEdge:0, rightEdge:GW, slide:cx, lean:MAX_LEAN }`. `g` and `lean` rise monotonically with openness.

- **`mmWheatField(seed, density, GW, GH)`** → deterministic array of `density` stalks `[{ bx, h, sway, headR, side }]`:
  - Seeded LCG (same pattern as the scatter particle field) so every screen generates the **identical** wall. `bx` spread across `[0, GW]`; `h` = height fraction in ~`[0.6, 1.0]`; `sway` = a per-stalk phase; `headR` = grain-head radius; `side = (bx < GW/2) ? 'left' : 'right'`.
  - Deterministic: same `(seed, density, GW, GH)` → identical array.

- **`mmWheatColor(tint)`** → `{ backdrop, base, stalk, head }` (hex). `_WHEAT = { golden:{…}, amber:{…}, pale:{…} }`; unknown tint falls back to `golden`.

Every choice that could be wrong (seam position, gap/lean ramp, stalk distribution, palette) lives in these node-tested helpers; the draw glue only consumes the numbers.

## Draw glue — `mmDrawWheat`, in `js/transitions.js`

`mmDrawWheat(ctx, params, phase, front, GW, GH, quad, scope, seed, now)` (thin; mirrors the `mmDrawBeer`/`mmDrawScatter` signature):

1. `openness = mmWheatOpenness(phase, front)`; `geom = mmWheatPartGeom(openness, GW, GH)`; `pal = mmWheatColor(params.tint)`; `field = mmWheatField(seed, params.density, GW, GH)`.
2. **Backdrop:** fill the two opaque straw rects — left `[0, geom.leftEdge] × [0, GH]`, right `[geom.rightEdge, GW] × [0, GH]` — with a subtle vertical gradient (`pal.base → pal.backdrop`). This is the opaque cover.
3. **Stalks:** for each stalk, translate its base by `±geom.slide` (left side −, right side +) so it rides with its wall; cull if its base is outside the visible wall span. Draw rooted at the base `(bx ± slide, GH)`, rotated by `±geom.lean` (leaning toward its outer edge) plus a gentle `sin(now·ω + sway)` wobble: a tapered stalk up to `h·GH`, capped with a `pal.head` ellipse (radius `headR`). Stalk color `pal.stalk`.
4. `scope === 'screen'` confines the seam/geometry to the current screen's quad (per the existing scope handling shared with the other mask helpers); default `wall` uses global mesh coords so the seam is at the true wall center.

ES5 / Safari-5.1 safe: `fillRect`, `createLinearGradient`, `save`/`restore`, `translate`, `rotate`, `beginPath`/`moveTo`/`lineTo`/`quadraticCurveTo`, `arc`/`ellipse` (or `arc` + `scale` fallback), `fill`/`stroke`. No `clip()`, no 3D, no filters.

## `effects.py` — new `Effect` subclass

`WheatPartEffect(Effect)`, single-`duration` (a wheatpart instance only covers (endEffect) or reveals (startEffect), never both):

- `name = "wheatpart"`, label `"Wheat Part"`.
- `params`:
  - `tint` — choice `[golden, amber, pale]`, default `"golden"`.
  - `density` — number, default `70`, min `10`, max `200` (stalks across the wall).
  - `scope` — choice `[screen, wall]`, default `"wall"`.
  - `duration` — number, default `2200`, min `0`.
  - `audioFade` — boolean, default `True`.
- `video_filters(role, params, ctx)` returns audio-only (`_afade`) when `audioFade` on, else `([], [])`. No baked video filter.

## Apply — one mask-family site (`index.html`)

Add a `wheatpart` case to the mask-family block in `runScriptLoop` (beside `scatter`/`frostcreep`), drawn under the mesh affine in global wall coords:

```js
} else if (stc.effect.name === 'wheatpart' && typeof mmDrawWheat === 'function') {
    mmDrawWheat(ctx, stc.effect.params, stc.effect.phase, stc.effect.front,
        it.meshGlobal[0], it.meshGlobal[1], it.meshQuad, stc.effect.scope,
        playback.seed | 0, GoTime.now());
}
```

**Mesh-only** — no element/media-path apply (consistent with the other procedural draw-helper effects). On a media/mirror item it no-ops to a plain cut; documented limitation. The seeded field uses `playback.seed` exactly like scatter/beerfill/frostcreep, so all clients share one wall.

## Render-token / server impact

**None beyond the catalog.** Visual is client-side; only the audio-fade signature (`role`, `duration`, when `audioFade` on) enters the render token — `_audio_fade_sig` reads only `audioFade`. Editing `tint`/`density`/`scope` (or `duration` while `audioFade` off) is instant, no re-render. A regression-guard test confirms the new visual params never change the token.

## Testing

- **Node (`tests/unit/js/test_wheatpart.js`):**
  - `mmWheatOpenness`: `(reveal, f) = f`, `(cover, f) = 1 − f`; clamps out-of-range `front`. Confirms both roles reach openness 0 at the handoff (`cover` at `front=1`, `reveal` at `front=0`).
  - `mmWheatPartGeom`: endpoints (`openness=0` seam-closed at `cx`, `openness=1` cleared to edges); `g` and `lean` monotonic in openness; `leftEdge`/`rightEdge` symmetric about `cx`; clamps out-of-range openness.
  - `mmWheatField`: determinism (same `(seed,density,GW,GH)` → identical array); length = `density`; every `bx ∈ [0,GW]`; `side` splits about `cx`.
  - `mmWheatColor`: each known tint returns the four keys; unknown → golden fallback.
  - `mmTransitionState` `wheatpart` branch: `effect.family === 'mask'`, `phase` `'cover'`/`'reveal'` per role, `front` = local progress rising `0→1` for both an out-role and an in-role offset; existing mask/transform tests unchanged and green.
- **Python (`tests/unit/test_effects.py`):** `wheatpart` in `effect_catalog()` with params/defaults; `video_filters` audio-only + single-duration role-aware (`afade` in/out, duration honored); `audioFade:false` bakes nothing. **`tests/unit/test_mosaic.py`:** render-token regression guard varying `tint`/`density` (token-neutral).
- **On-wall iPad-1 sign-off (acceptance):** `wheatpart` (golden) as `endEffect`→`startEffect` — item A's wheat closes over A, item B's wheat parts to reveal B; full opaque wheat at the handoff (no content peek); stalks lean and slide outward from the center seam with a gentle sway; the seam is at the true wall center and all screens share one field; smooth at wall scale.

## Demo / delivery

A **Wheat Part Demo** playlist (two plasma mesh items handing off via `wheatpart`, tint=golden, density=70, duration=2200), alongside the existing Beer / Scatter / Keg Roll / Frost Creep / Coaster Flip / Transition demos (`tools/_make_wheat_demo.py`).

## Out of scope (YAGNI)

- Configurable seam position / parting axis (center-vertical only).
- The element/media path (mesh-only, like the other procedural draw helpers).
- Grain-burst / falling-stalk variants (this is a clean center part).
- The remaining brewery effect (splash crown — separate spec).
