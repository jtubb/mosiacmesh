# Coaster Flip Transition ("coasterflip") — Design

**Date:** 2026-06-25
**Status:** Approved (design); pending implementation plan
**Area:** `effects.py` (catalog), `js/transitions.js` (pure flip math + `mmTransitionState` branch), `index.html` (transform-family apply: in-canvas mesh + element). Editor is data-driven and needs no change.

## Overview

A new **transform-family** transition, `coasterflip` — the screen content is treated as printed on a beer coaster that **flips over**. On the end-effect the content folds **edge-on** (scale on the chosen axis `1→0`, dimming, with a cardboard edge sliver at the fold); on the start-effect the incoming content **opens back out** (`0→1`). Placed as item A's `endEffect` and item B's `startEffect`, it reads as one continuous coaster turn across the handoff.

It is the first transform-family brewery effect (beerfill/scatter/kegroll/frostcreep are all mask covers), and reuses the existing slide/zoom transform plumbing. Runs on the 1st-gen iPad-1 (iOS 5.1 / Safari 5.1): client code is **ES5 only**, and a "flip" is faked in **2D** (`ctx.scale` / `-webkit-transform: scale`) — no CSS 3D, no `matrix3d`, no WebGL.

## Architecture — transform family, raw-progress front

`coasterflip` rides the **same path slide/zoom use** (the additive `effect` descriptor with `family:'transform'` + the in-canvas/element transform apply):

- `mmTransitionState` returns, for `name === 'coasterflip'` (added to the existing slide/zoom transform branch), `effect: { name:'coasterflip', family:'transform', front: p, scope, params }` (and `wipe:null`). `axis` rides in `params`.
- **Raw progress `front: p`** (NOT local-progress inverted — exactly like slide/zoom): for the out role `p` counts `1→0` (open→edge), for the in role `0→1` (edge→open). So `scale = front` directly; no phase needed.
- Both apply sites already branch on `st.effect.family === 'transform'`:
  - **In-canvas** (`runScriptLoop`, mesh SCRIPT): the content scale + the edge sliver are drawn under the mesh affine, about the global wall center -> wall-coherent.
  - **Element** (`applyTransitionNow`, media/mirror): `-webkit-transform: scale` + opacity on the mounted element.

## Role behavior

`front = p`. The flip openness is `front` itself (1 = full open, 0 = edge-on):
- **`endEffect` (out):** `p` 1→0 -> content folds open→edge over the phase. Phase duration = `duration`.
- **`startEffect` (in):** `p` 0→1 -> content opens edge→open. Phase duration = `duration`.

## Pure helper (testable core), in `js/transitions.js`

- **`mmFlipFactor(front, axis)`** → `{ sx, sy, alpha, edge }`:
  - `f = clamp(front, 0, 1)` (flip openness).
  - `sx = (axis === 'vertical') ? 1 : f`, `sy = (axis === 'vertical') ? f : 1` — scale drives the chosen axis only; the other stays 1.
  - `alpha = 0.35 + 0.65 * f` — content opacity, dims toward edge-on, full when open.
  - `edge = 1 - f` — cardboard edge-sliver opacity, strongest at edge-on, gone when open.
  - At `front = 1` → `{sx:1, sy:1, alpha:1, edge:0}`; at `front = 0` → `{sx:0, sy:1, alpha:0.35, edge:1}` (horizontal).

Every choice that could be wrong (which axis, how much dim, when the edge shows) lives in this node-tested helper; the apply glue only consumes the numbers.

## `effects.py` — new `Effect` subclass

`CoasterFlipEffect(Effect)`, single-`duration` (a coasterflip instance only folds (endEffect) or opens (startEffect), never both):

- `name = "coasterflip"`, label `"Coaster Flip"`.
- `params`:
  - `axis` — choice `[horizontal, vertical]`, default `"horizontal"`.
  - `coaster` — choice `[kraft, cork, slate]`, default `"kraft"` (the edge-sliver cardboard tone).
  - `scope` — choice `[screen, wall]`, default `"wall"`.
  - `duration` — number, default `700`, min `0`.
  - `audioFade` — boolean, default `True`.
- `video_filters(role, params, ctx)` returns audio-only (`_afade`) when `audioFade` on, else `([], [])`. No baked video filter.

## Apply — two transform sites (`index.html`)

A small coaster-tone palette (client-side, in `js/transitions.js`): `_COASTER = { kraft:'#b9935f', cork:'#c8a06a', slate:'#5a5e63' }`, `mmCoasterColor(name)` → hex (default kraft).

1. **In-canvas (mesh SCRIPT, `runScriptLoop`)** — add a `coasterflip` case to the transform-family block (alongside slide/zoom), under the mesh affine `m`:
   - `var ff = mmFlipFactor(stc.effect.front, axis)`; scale about wall center `cx,cy = it.meshGlobal[0]/2, it.meshGlobal[1]/2`: `ctx.translate(cx,cy); ctx.scale(ff.sx, ff.sy); ctx.translate(-cx,-cy)`; `ctx.globalAlpha = ff.alpha`. The animation then draws flipped + dimmed.
   - **Edge sliver (post-content):** in the post-animation block, re-apply the pure mesh affine `ctx.setTransform(m.a,m.b,m.c,m.d,m.e,m.f)` (dropping the flip scale), set `ctx.globalAlpha = ff.edge`, fill a thin cardboard bar (`mmCoasterColor(coaster)`) along the fold: a narrow vertical rect at global x `cx` (horizontal flip) spanning the wall height, or a narrow horizontal rect at `cy` (vertical flip) spanning the width. Bar thickness ≈ a small fraction of the wall span (e.g. `0.012 * meshGlobal`). Restore `globalAlpha = 1`.

2. **Element (media / mirror, `applyTransitionNow`)** — add a `coasterflip` case to the transform-family element block (alongside slide/zoom):
   - `var ff = mmFlipFactor(st.effect.front, axis)`; `el.style.webkitTransform = el.style.transform = 'scaleX(' + ff.sx + ') scaleY(' + ff.sy + ')'` (transform-origin center = default); `el.style.opacity = '' + ff.alpha`.
   - No edge sliver on the element path (it needs in-canvas global drawing); the scale + dim alone read as the flip. Documented limitation, consistent with slide/zoom on media.

ES5 / Safari-5.1 safe: `ctx.scale`, `globalAlpha`, `fillRect`, `ctx.setTransform`, `-webkit-transform: scale`. No `clip()`, no `matrix3d`, no 3D, no filters.

## Render-token / server impact

**None beyond the catalog.** Visual is client-side; only the audio-fade signature (`role`, `duration`, when `audioFade` on) enters the render token — `_audio_fade_sig` reads only `audioFade`. Editing `axis`/`coaster`/`scope` (or `duration` while `audioFade` off) is instant, no re-render. A regression-guard test confirms the new visual params never change the token.

## Testing

- **Node (`tests/unit/js/test_coasterflip.js`):**
  - `mmFlipFactor`: horizontal drives `sx` (sy stays 1), vertical drives `sy` (sx stays 1); endpoints `front=1` → `{sx:1,sy:1,alpha:1,edge:0}`, `front=0` → `{sx:0,sy:1,alpha:0.35,edge:1}` (horizontal); `alpha` ramps `0.35→1`; `edge` ramps `1→0`; clamps out-of-range front.
  - `mmTransitionState` `coasterflip` branch: `effect.family === 'transform'`, `front === p` for both an end-role and a start-role offset, `scope` default `wall`; existing slide/zoom/iris/dissolve tests unchanged and green.
- **Python (`tests/unit/test_effects.py`):** `coasterflip` in `effect_catalog()` with params/defaults; `video_filters` audio-only + single-duration role-aware (`afade` in/out, duration honored); `audioFade:false` bakes nothing; render-token regression guard.
- **On-wall iPad-1 sign-off (acceptance):** `coasterflip` (axis=horizontal) as `endEffect`→`startEffect` — item A folds edge-on (dim + cardboard edge sliver at the center fold), item B opens back out; the whole wall folds about its center coherently; smooth at wall scale.

## Demo / delivery

A **Coaster Flip Demo** playlist (two plasma mesh items handing off via `coasterflip`, axis=horizontal, coaster=kraft), alongside the existing Beer / Scatter / Keg Roll / Frost Creep / Transition demos. Fully procedural (no sprite).

## Out of scope (YAGNI)

- Real 3D / perspective flip (impossible on iPad-1 — 2D scale only).
- A coaster-back graphic flashing mid-flip (chose the edge-sliver + dim).
- The edge sliver on the element/media path (mesh-only).
- The remaining brewery effects (splash crown / wheat part — separate specs).
