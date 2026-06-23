# Additional Transition Effects — Design

**Date:** 2026-06-22
**Status:** Approved (design); pending implementation plan
**Area:** `effects.py` (catalog), `js/transitions.js` (pure effect math + apply dispatch), `index.html` (in-canvas mesh path + element-transform path). Editor is data-driven and needs no change.

## Goal

Add four new transition effects — **Slide**, **Zoom**, **Iris**, **Dissolve** — alongside the existing Fade and Wipe, usable as Start and/or End effects with a configurable duration, and (where it matters) coherent across the calibrated physical wall.

## Background (current engine)

- `effects.py` declares effects as `Effect` subclasses with a `params` list (`ParamSpec`) and `video_filters` (only audio fades are baked server-side; visual transitions are client-side). The editor builds its controls from `effect_catalog()` via `/api/effects`, so **new effects appear automatically** with their params.
- `js/transitions.js` (ES5, pure + DOM apply) drives visuals: `mmTransitionState(start,end,offsetMs,durationMs,rect,quad)` returns the active role + a `wipe` descriptor (incl. `front = p`, the 0→1 (in) / 1→0 (out) progress); `mmApplyTransition` applies opacity (Fade) or the affine cover (Wipe).
- The affine cover is a viewport `<canvas>` drawn with the **same `mmMeshTransform` matrix** the content uses, so masks drawn in global coords are wall-coherent and calibration-aware. For mesh SCRIPT animations the cover is drawn **in-canvas** inside `runScriptLoop` (frame-locked); for media/mirror it's the overlay canvas.
- Hard constraint: the display client runs on iPad-1 / Safari 5.1 — **ES5 only**, `-webkit-transform`, opacity, canvas 2d (incl. `globalCompositeOperation='destination-out'`, `globalAlpha`). No CSS filters, clip-path, or Web Animations.

## Decisions (locked during brainstorming)

1. Effects to add: **Slide, Zoom, Iris, Dissolve**. **Blur dropped** (no `-webkit-filter` on Safari 5.1; no cheap path). **Push dropped** (needs two-item coexistence; engine is single-item — Slide is plain slide-in/out over the background).
2. **Iris & Dissolve** extend the affine cover → wall-coherent & calibration-aware automatically.
3. **Slide & Zoom** are wall-coherent on **mesh animations** (applied in global space in-canvas), and per-screen on mirror/media (coherent for FULL/mirror). On per-screen warped video (SEGMENT/INDIVIDUAL) they may tear — documented limitation; recommend Fade/Iris/Dissolve there.

## Architecture — two families

### Family A — Reveal-mask effects (Wipe existing, + Iris, Dissolve)

A mask effect covers the **un-revealed** region with the item background color, drawn in global px under the mesh affine, driven by `front = p`. **The existing Wipe cover path is left UNCHANGED** (it is freshly debugged and on-wall-proven — no refactor). Iris and Dissolve are added through a NEW dispatcher used by BOTH the in-canvas path (mesh animations) and the overlay-canvas path (media/mirror):

`mmDrawCoverMask(ctx, effectName, params, front, GW, GH, quad, scope, seed)` — fills/clears the cover for `iris`/`dissolve`. (`ctx` is already under the mesh transform in the in-canvas case, or the overlay canvas's 2d context with the matrix applied in the overlay case.)

Mask drawers (the geometry is pure + node-tested; the canvas calls are glue):

- **Wipe** (existing, unchanged): rectangle via `mmWipeCoverRect` on the existing `st.wipe` path.
- **Iris**: fill the region with bg, then punch a growing circle with `globalCompositeOperation='destination-out'`.
  - Pure helper `mmIrisCircle(front, GW, GH, scope, quad)` → `{cx, cy, r}` in global px.
  - Wall scope: `cx,cy = GW/2, GH/2`; `maxR = ` distance from center to the farthest region corner (so `front=1` fully clears). Screen scope: `cx,cy` = quad-bbox center; `maxR` = half the bbox diagonal. `r = front * maxR`.
- **Dissolve**: a `blocks×blocks` grid over the region; cover only the cells not yet revealed.
  - Pure helper `mmDissolveOrder(n, seed)` → a deterministic permutation of `0..n-1` (Fisher–Yates over a small seeded LCG, self-contained in transitions.js so node tests need no other module). `mmDissolveCovered(front, n)` → number still covered = `n - floor(front*n)`; a cell at order-rank `>= floor(front*n)` is covered. The `seed` is `playback.seed` (the shared per-playback seed already delivered in the PLAY payload and available on BOTH apply paths — `runScriptLoop` and `applyTransitionNow`), so every screen computes the same order → wall-coherent regardless of content type.

### Family B — Transform effects (Fade existing, + Zoom, Slide)

Transform effects move/scale the content. Pure helpers compute the transform from `front`:

- **Slide**: `mmSlideOffset(front, direction, GW, GH)` → `{dx, dy}` global-px offset. `direction` is the motion direction; content enters from the opposite edge: `left`→`dx=(1-front)*GW`, `right`→`dx=-(1-front)*GW`, `up`→`dy=(1-front)*GH`, `down`→`dy=-(1-front)*GH`. At `front=0` content is one wall off-edge (background shows); at `front=1` it's in place.
- **Zoom**: `mmZoomFactor(front, scale)` → `{s, alpha}` where `s = scale + (1-scale)*front` and `alpha = front`. (`scale` is the start factor, e.g. 0.6.)

Apply paths:
- **Mesh SCRIPT animation** (wall-coherent, in-canvas in `runScriptLoop`, before the animation draw, under `ctx.setTransform(m)`):
  - Slide: `ctx.translate(dx, dy)`.
  - Zoom: `ctx.translate(cx,cy); ctx.scale(s,s); ctx.translate(-cx,-cy)` about wall-center `(cx,cy)=(GW/2,GH/2)` (or panel-center for screen scope), and set `ctx.globalAlpha = alpha` for the draw.
- **Mirror animation + FULL media + (best-effort) other media** (`applyTransitionNow`, element transform on `currentEl`): `-webkit-transform` `translate(dx%,dy%)` / `scale(s)` + `style.opacity = alpha`. Per-screen; identical across screens for mirror/FULL.

### `mmTransitionState` — additive extension (no change to wipe/fade)

The existing returns are **untouched**: Fade still returns `{role, opacity:p, wipe:null}`; Wipe still returns its `{role, opacity:1, wipe:{reveal,direction,slide,front,scope}}`. For the FOUR new effects ONLY, `mmTransitionState` additionally returns `effect: { name, family, front, scope, params }` (and `wipe:null`), where `family` is `mask` (iris/dissolve) or `transform` (slide/zoom). The two apply call sites (`applyTransitionNow`, the in-canvas block in `runScriptLoop`) keep their existing `st.wipe`/fade handling verbatim and ADD a branch: if `st.effect` is present, dispatch on `st.effect.family` — `mask` → `mmDrawCoverMask`, `transform` → element/in-canvas transform. `front = p` exactly as the wipe uses it. Result: existing wipe + fade behavior and their tests are unchanged; new effects ride a parallel path.

## effects.py — new `Effect` subclasses

Each mirrors the Wipe/Fade pattern: declare `params` (with `duration` + `audioFade` like the others so audio still fades for video), and `video_filters` returns audio-only (`_afade`). Catalog entries (params beyond the shared `duration`/`audioFade`):

- `slide`: `direction` choice `[left,right,up,down]` (default `left`); `scope` choice `[screen,wall]` (default `wall`).
- `zoom`: `scale` number (default `0.6`, min `0.05`, max `1`); `scope` choice `[screen,wall]` (default `wall`).
- `iris`: `scope` choice `[screen,wall]` (default `wall`).
- `dissolve`: `blocks` number (default `16`, min `2`, max `64`).

`effect_audio_fade_default` already generalizes; no change needed.

## Render-token / server impact

None beyond the catalog. Visual effects are client-side; the render token's audio-fade signature (`_audio_fade_sig`) already covers `audioFade`. New visual params do not change rendered assets, so they must NOT enter the render token (consistent with today — only `audioFade` does). Confirm `_audio_fade_sig` ignores the new params (it reads only `audioFade`), so editing direction/scope/blocks never forces a re-render.

## Testing

Pure helpers get node `--test` coverage (mirroring `gotime-steer`/`transitions` suites):
- `mmIrisCircle`: r=0 at front 0, r=maxR at front 1, center per scope, maxR reaches farthest corner.
- `mmDissolveOrder`: same seed → identical permutation; different seed → different; result is a valid permutation of `0..n-1`. `mmDissolveCovered`: monotonic non-increasing in front; `n` at 0, `0` at 1.
- `mmSlideOffset`: each direction's sign + magnitude; `front=0` → full off-edge, `front=1` → `{0,0}`.
- `mmZoomFactor`: `s` ramps `scale→1`, `alpha` ramps `0→1`; `front=1` → `{1,1}`.
- `mmTransitionState`: a representative effect of each NEW family yields the right `effect.family`/`front`/`params`; the existing fade + wipe tests are **unchanged and stay green** (those paths are untouched).

Canvas drawing + in-canvas/element apply wiring is glue → module-load smoke + the on-wall sign-off. Optionally a small viz harness (like the wipe's) to confirm mask geometry under rotation before deploy.

## Legacy / ES5 constraints

All client code ES5 only (no `let`/`const`/arrow/template-literal/`class`). Canvas ops used: `globalCompositeOperation='destination-out'` (Iris), `globalAlpha` (Zoom), `fillRect`/`arc` — all supported on Safari 5.1. Element effects use `-webkit-transform` + `opacity`.

## Out of scope (YAGNI)

- **Push** (two-item coexistence) — engine is single-item.
- **Blur** — infeasible on iPad-1 (dropped).
- Per-effect easing curves (linear `front` only, matching Fade/Wipe).
- Editor changes — it is data-driven from the catalog; new effects + params surface automatically.
- Wall-coherent Slide/Zoom on warped per-screen video (SEGMENT/INDIVIDUAL) — best-effort/per-screen; documented limitation.
