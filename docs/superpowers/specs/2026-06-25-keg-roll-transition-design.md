# Keg Roll Transition ("kegroll") — Design

**Date:** 2026-06-25
**Status:** Approved (design); pending implementation plan
**Area:** `effects.py` (catalog), `js/transitions.js` (pure roll/cover geometry + drawer), `index.html` (sprite load wiring — same hooks scatter uses). Editor is data-driven and needs no change.

## Overview

A new **keg-as-wipe-edge transition**, `kegroll`, joining the brewery transition family (`beerfill`, `scatter`) alongside the general transitions (`fade`, `wipe`, `slide`, `zoom`, `iris`, `dissolve`). A **giant keg sprite rolls across the wall** as the moving boundary of a directional wipe: the region the keg has passed is covered with the item's `backgroundColor` (on `endEffect`) or revealed (on `startEffect`). The keg's rotation is tied to the distance it travels, so it reads as genuinely *rolling*, not sliding.

The sprite is any **transparent PNG in the uploaded media library** (default `keg`), so this is the seed of a "roll" family paralleling scatter — the same effect rolls a bottlecap, ball, or wheel across by swapping the chosen image, with zero new code or art per variant.

It must run on the 1st-gen iPad-1 display clients (iOS 5.1 / Safari 5.1): client code is **ES5 only**, and the per-frame work is one `fillRect` (the cover) + one `drawImage` of a once-decoded PNG (the keg). No `clip()`, no `destination-*` compositing.

## Architecture — mask family, additive `effect` descriptor

Keg roll rides the **same path Iris and Dissolve use** (the additive `effect` descriptor + the `mmDrawCoverMask` dispatch), so the freshly-debugged, on-wall-proven **Wipe cover path is left UNCHANGED** — keg roll re-derives the wipe's reveal *math* in its own pure helper rather than touching the wipe's *code*.

- `mmTransitionState` returns, for `name === 'kegroll'` ONLY, an additive `effect: { name:'kegroll', family:'mask', front, scope, params }` (and `wipe:null`). Fade/wipe/slide/zoom/iris/dissolve returns are untouched; their tests stay green. `front = p` exactly as the other effects use it.
- Both apply sites already branch on `st.effect` (from the Iris/Dissolve work) — no new call sites:
  - **In-canvas** (`runScriptLoop`, mesh SCRIPT animations): `ctx` is already under the mesh affine → the drawer works in global coords, wall-coherent and calibration-aware.
  - **Overlay canvas** (`applyTransitionNow`, media/mirror): the overlay 2d context with the mesh matrix applied.

## Role-aware behavior

Like beerfill/scatter, `kegroll` reads its transition role; `p∈[0,1]` is the clamped phase progress.

- **`endEffect` (COVER):** keg rolls in from the leading edge across the region; the area the keg has passed fills with the item's `backgroundColor`. At `front`→1 the region is fully covered. Phase duration = `duration`.
- **`startEffect` (REVEAL):** the reverse — keg rolls across and the cover retreats behind it, revealing the incoming item. Phase duration = `duration`.

Placing `kegroll` as item A's `endEffect` and item B's `startEffect` (same params) produces the full roll-cover-then-roll-reveal handoff. There is no cover/reveal asymmetry (it is the same roll, direction-reversed), so a single `duration` is the honest knob.

## Pure helpers (geometry + rotation), node-tested

All in `js/transitions.js`, ES5, no DOM — the testable core:

- **`mmKegCoverRect(front, direction, GW, GH)` → `{x, y, w, h}`** — the directional cover rectangle in global px (the paint-roller model: the keg travels in `direction` and the region it has *already passed* — behind it, the edge it came from — is the covered area). The rect grows from the start edge in the travel direction as `front`→1 and always spans the full perpendicular dimension. Per direction (`direction` = keg travel direction):
  - `right`: keg enters from the left, cover anchored at the **left** edge, `w = front*GW` (`x=0`). `left`: keg enters from the right, cover anchored at the **right** edge, `w = front*GW` (`x=GW-w`). `down`: keg enters from the top, cover anchored at **top**, `h = front*GH`. `up`: keg enters from the bottom, cover anchored at **bottom**, `h = front*GH`.
  - Wall-coherent: under the mesh affine each panel sees the correct slice automatically; falls back to per-screen (quad bbox) for `scope:"screen"` / mirror / uncalibrated.
- **`mmKegPos(front, direction, GW, GH, kegD)` → `{cx, cy}`** — the keg's center, riding the leading edge of the cover. The keg travels its full path **plus its own diameter** at each end, so it enters fully off-screen at `front=0` and exits fully off-screen at `front=1` (no half-keg parked at an edge). `kegD` is the locked giant diameter (the perpendicular region dimension).
- **`mmKegAngle(distTraveled, kegRadius)` → radians** — **physical roll:** `angle = distTraveled / kegRadius` (arc length = radius × angle), so rotation is tied to distance. Sign flips for `left`/`up` so the keg appears to roll in the travel direction, not spin in place.

## Drawer (canvas glue — thin)

`mmDrawKegRoll(ctx, params, front, GW, GH, quad, scope, sprite_img)`, called from `mmDrawCoverMask`'s dispatch:

1. Resolve region geometry: wall → `[0,0,GW,GH]`; screen → quad bbox.
2. `kegD` = perpendicular region dimension (giant-roller constant); `kegR = kegD/2`.
3. `var rect = mmKegCoverRect(front, dir, ...)` → `ctx.fillStyle = bg; ctx.fillRect(rect.x, rect.y, rect.w, rect.h)`.
4. `var pos = mmKegPos(front, dir, ..., kegD)`; `var ang = mmKegAngle(distTraveled, kegR)`.
5. Stamp the giant keg via **`mmStampSprite`** (the screen-local cull-then-draw helper from the screen-local-mesh work) at `pos`/`ang`/`kegD`, so off-screen panels skip the `drawImage` (same perf win scatter got). Falls back to a plain rotated `drawImage` if `mmStampSprite`/viewport is unavailable.

**Sprite loading:** reuse scatter's once-decoded PNG mechanism (resolve `sprite` name → media URL → cached/memoized `Image`). If the PNG isn't decoded yet on the first frame, the cover rect still draws — you get a plain wipe until the keg image is ready (graceful, matching scatter).

## `effects.py` — new `Effect` subclass

`KegRollEffect(Effect)`, mirroring the wipe/scatter pattern:

- `name = "kegroll"`, label `"Keg Roll"`.
- `params`:
  - `sprite` — string, default `"keg"` (transparent PNG in the media library; scatter's resolution mechanism).
  - `direction` — choice `[left, right, up, down]`, default `"right"` (roll travel direction).
  - `scope` — choice `[screen, wall]`, default `"wall"`.
  - `duration` — number, default `2000`, min `0`.
  - `audioFade` — boolean, default `True`.
- `video_filters(role, params, ctx)` returns audio-only (`afade`) when `audioFade` is on, else `([], [])`. **No baked video filter** (visual is client-side).

## Render-token / server impact

**None beyond the catalog.** Visual effects are client-side; the render token's audio-fade signature (`_audio_fade_sig`) already covers `audioFade` only. The new visual params (`sprite`/`direction`/`scope`) and `duration` (while `audioFade` off) must NOT enter the render token, so editing them is **instant, no re-render** — consistent with every other visual effect. A regression-guard test confirms `_audio_fade_sig` ignores the new params.

## Testing

- **Node (`tests/unit/js/`):**
  - `mmKegCoverRect`: `front=0` → fully revealed (zero/empty cover), `front=1` → fully covered region, each of the four directions, rect spans the full perpendicular dimension.
  - `mmKegPos`: keg fully off-edge at `front=0` and `front=1` (center beyond the edge by ≥ radius); rides the leading edge monotonically.
  - `mmKegAngle`: monotonic with distance; equals `dist/radius`; sign correct per direction.
  - `mmTransitionState` `kegroll` branch: `effect.family === 'mask'`, correct `front`, `duration`, params; existing fade/wipe/slide/zoom/iris/dissolve tests **unchanged and green**.
- **Python (`tests/unit/test_effects.py`):** `kegroll` in `effect_catalog()` with params/defaults; `video_filters` audio-only + role-aware (`afade` in/out, duration honored); `audioFade:false` bakes nothing; render-token regression guard (visual-param edits don't change the audio signature).
- **On-wall iPad-1 sign-off (acceptance):** on the calibrated mesh, `kegroll` (sprite=keg, scope=wall, direction=right) as `endEffect`→`startEffect` — giant keg rolls across, covers cleanly, reveals, with visibly *rolling* (not sliding) rotation, smooth at wall scale.

## Demo / delivery

- A **Keg Roll Demo** playlist (two plasma mesh items handing off via `kegroll`, sprite=keg, scope=wall, direction=right), alongside the existing Beer / Scatter / Transition demos — runnable immediately after build.
- A `keg.png` transparent sprite in the media library as the default.

## Legacy / ES5 constraints

All client code ES5 only (no `let`/`const`/arrow/template-literal/`class`). Canvas ops used: `fillRect`, rotated `drawImage` (via `mmStampSprite`'s `translate`/`rotate`/`scale`) — all supported on Safari 5.1. No `clip()`, no `destination-*`, no CSS filters.

## Out of scope (YAGNI)

- Exposing keg size / roll-rotation factor / per-copy detail as params (locked constants).
- Multiple kegs / staggered rolls (that's the scatter family's territory).
- Non-PNG or non-transparent sprites; per-sprite tint/recolor.
- Server-side video compositing of the roll (visuals stay client-side).
- The other queued brewery effects (frost creep / coaster flip / splash crown / wheat part — separate specs).
