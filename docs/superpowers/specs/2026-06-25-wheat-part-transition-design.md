# Wheat Part Transition ("wheatpart") — Design

**Date:** 2026-06-25 (updated 2026-06-26 to match the shipped on-wall design)
**Status:** Implemented; on-wall verified
**Area:** `effects.py` (catalog), `js/transitions.js` (pure helpers + `mmDrawWheat` draw glue + `mmTransitionState` branch), `index.html` (one mask-family in-canvas branch), `media/server/images/wheatfield.png` (baked texture). Editor is data-driven and needs no change.

> **Revision note.** The original spec described a purely procedural full-height-stalk curtain. On-wall the design evolved (recorded here): a baked **wheat-field texture** carries the field density (cheaper + denser), sparse **foreground ear-stalks** with **upward teardrop grain clusters** add parting-edge depth, and a **center-hold dwell** pauses the wall at full-wheat across the A→B handoff. Per-screen culling + batched grain fills keep it smooth on iPad-1.

## Overview

A **mask-family** transition, `wheatpart` — a curtain of golden wheat that **parts from a center seam**. As a `startEffect` the wheat starts closed (a full wall of wheat), **holds** briefly, then **parts open** (the two halves sliding outward) to reveal the incoming content; as an `endEffect` the wheat **closes** over the outgoing content and holds. Placed as item A's `endEffect` and item B's `startEffect`, both dwell at full-wheat across the handoff → one continuous "the field fills the wall, holds, then parts."

Each wheat wall is a **baked, tileable wheat-field texture** (dense field for cheap) with sparse **procedural ear-stalks** drawn on top at the parting edge for depth; each stalk is topped with a **cluster of upward teardrop grains**. Runs on the 1st-gen iPad-1 (iOS 5.1 / Safari 5.1): client code is **ES5 only**, 2D canvas, **no `clip()` / `matrix3d` / WebGL / `ctx.ellipse`**.

## Architecture — mask family, local-progress + hold

`wheatpart` rides the same path the other mask effects use:

- `mmTransitionState` returns, for `name === 'wheatpart'` (branch beside scatter/frostcreep): `effect: { name:'wheatpart', family:'mask', front:<localProgress>, scope, params, phase }`, `wipe:null`. `phase = mmWheatPhase(role)` → `'cover'` (out) / `'reveal'` (in). `front` = LOCAL phase progress rising `0→1` for BOTH roles (out-role `1-p`, in-role `p`) — the scatter convention.
- The draw helper resolves cover-vs-reveal + the dwell from `phase` + `hold` via `mmWheatOpenness`.

## Pure helpers (testable core), in `js/transitions.js`

- **`mmWheatPhase(role)`** → `'cover'` (out) | `'reveal'` (in).
- **`mmWheatOpenness(phase, front, hold)`** → openness `0..1` (0 = closed/full-wheat, 1 = open/content-visible). `hold` (default 0.2 client-side; catalog default 0.5) is the window fraction the wheat **dwells fully closed** at the seam: a cover closes over the first `(1-hold)` then holds closed; a reveal holds closed the first `hold` then opens. Both roles reach 0 at the handoff (`cover` at `front=1`, `reveal` at `front=0`), so the full-wheat dwell is continuous across A→B.
- **`mmWheatPartGeom(openness, GW, GH)`** → `{ cx, g, leftEdge, rightEdge, slide, lean }`: `cx=GW/2`, `g=openness·cx`, edges `cx∓g`, `slide=g`, `lean=openness·0.5`.
- **`mmWheatField(seed, density, GW, GH)`** → deterministic array of `density` stalks `{ bx, h, sway, headR, side }` via the shared seeded `_mmLcg` (identical on every screen → wall-coherent). `bx∈[0,GW)`, `h∈[0.6,1.0)`, `headR∈[0.006,0.012)` (fraction of GH), `side` split at `GW/2`.
- **`mmWheatColor(tint)`** → `{ backdrop, base, stalk, head }`; `_WHEAT` table golden/amber/pale; unknown → golden.

## Draw glue, in `js/transitions.js`

- **`_tileWheatRect(ctx, img, x0, x1, top, h, tileW)`** — tiles a texture across `[x0,x1]` at height `h`, scaled so the texture is `h` tall (one vertical tile), repeated horizontally **anchored to global x=0** (so every screen aligns and the seam is continuous), with partial edge tiles clipped via the `drawImage` source-subrect (no `clip()`).
- **`_grainTear(ctx, x, y, hw, hh, tipDx)`** — one wheat grain: an upward teardrop (rounded base, pointed tip nudged by `tipDx` for an outward fan), as 5 `quadraticCurveTo` arcs added to the current path (caller batches many grains then fills once).
- **`mmDrawWheat(ctx, params, phase, front, GW, GH, quad, scope, seed, now, sprite)`** — `globalAlpha=1` (opaque-cover guarantee); `openness = mmWheatOpenness(phase, front, params.hold)`; region via `_mmMaskRegion`. Per wall:
  1. **Backdrop:** opaque straw gradient (`pal.base→pal.backdrop`) as the base/fallback, then the wheat **texture** tiled over it via `_tileWheatRect` (when `sprite` is loaded).
  2. **Foreground ear-stalks:** for each field stalk that survives culling — a tapered stalk triangle, a **cluster of paired teardrop grains** up the top ~45% (6 rows, fanning outward, tapering), a crowning tip grain (all grains in ONE `beginPath`/`fill`), and awn bristles. Stalks slide outward by `g`, lean by `geom.lean`, sway with `now`.
  - **Per-screen cull (perf):** the field is global, so each client skips stalks whose base ± lean/ear reach can't land on this screen's `quad` (margin → ~0 during the dwell where lean=0). Cuts the redundant full-field redraw across all screens. `quad` null (uncalibrated) → no cull.

ES5 / Safari-5.1 safe: `fillRect`, `createLinearGradient`, `save`/`restore`, `translate`/`rotate`/`scale`, `beginPath`/`moveTo`/`lineTo`/`closePath`/`quadraticCurveTo`, `arc`, `drawImage` (incl. source-subrect), `fill`/`stroke`. No `clip()`, `ctx.ellipse`, `matrix3d`, 3D, or filters.

## `effects.py` — `WheatPartEffect`

Single-`duration` (covers OR reveals, never both). Params (locked defaults):
- `tint` — choice `[golden, amber, pale]`, default `"golden"`.
- `sprite` — string, default `"wheatfield"` (backdrop texture; any tileable PNG; `""` = gradient only).
- `density` — number, default `30`, min `10`, max `200` (foreground ear-stalks; the texture carries field density).
- `hold` — number, default `0.5`, min `0`, max `0.5` (fully-closed dwell fraction).
- `scope` — choice `[screen, wall]`, default `"wall"`.
- `duration` — number, default `4000`, min `0`.
- `audioFade` — boolean, default `True`.
- `video_filters` → audio-only `_afade`; no baked video.

## Apply — one mask-family site (`index.html`)

A `wheatpart` branch in `runScriptLoop`'s mask block (after `frostcreep`, before the `mmDrawMaskInCanvas` fallback), drawn under the mesh affine in global coords, passing the texture image:
```js
} else if (stc.effect.name === 'wheatpart' && typeof mmDrawWheat === 'function') {
    mmDrawWheat(ctx, stc.effect.params, stc.effect.phase, stc.effect.front,
        it.meshGlobal[0], it.meshGlobal[1], it.meshQuad, stc.effect.scope,
        playback.seed | 0, GoTime.now(),
        mmSprite(mmScatterSpriteUrl((stc.effect.params && stc.effect.params.sprite) || 'wheatfield')));
}
```
**Mesh-only** — no element/media path (consistent with the other procedural draw helpers); no-ops to a plain cut on media items. Seeded field uses `playback.seed`.

## Back-face / texture asset

`media/server/images/wheatfield.png` — a 768×768 (~0.59 MP, under the iPad-1 ~3 MP decode cap) cv2-baked dense wheat field: warm straw vertical gradient packed with golden stalks + clustered upward teardrop grains, stalks wrapped at ±W so it tiles horizontally. `tools/_make_wheatfield_texture.py` (deterministic).

## Render-token / server impact

**None beyond the catalog.** Visual is client-side; only the audio-fade signature enters the token. Editing `tint`/`sprite`/`density`/`hold`/`scope` (or `duration` while `audioFade` off) is instant, no re-render. A `test_mosaic.py` guard confirms `tint`/`density` are token-neutral.

## Testing

- **Node (`tests/unit/js/test_wheatpart.js`, 12 cases):** `mmWheatOpenness` hold-then-ramp per role + explicit-hold + clamps + handoff-closed; `mmWheatPartGeom` endpoints/symmetry/monotonic; `mmWheatField` determinism/bounds/side-split; `mmWheatColor` tints + fallback; `mmTransitionState` branch (mask, phase, rising front); `mmDrawWheat` stub-ctx smoke (closed fills backdrops, open draws ~none, balanced save/restore, screen-scope, sprite tiles via drawImage, degenerate no-throw).
- **Python (`test_effects.py`):** catalog params/locked-defaults; audio-only role-aware `video_filters`; `audioFade:false` bakes nothing. **`test_mosaic.py`:** token-neutrality guard (tint/density).
- **On-wall iPad-1 sign-off:** dense wheat field closes + holds + parts; full opaque wheat at the handoff; teardrop-grain ear clusters read as wheat; sparse foreground stalks lean/slide; one shared field; smooth at wall scale.

## Demo / delivery

A **Wheat Part Demo** playlist (two plasma mesh items handing off via `wheatpart`, golden, wheatfield texture, density 30, hold 0.5, duration 4000) — `tools/_make_wheat_demo.py`.

## Out of scope (YAGNI)

- Configurable seam position / parting axis (center-vertical only).
- The element/media path (mesh-only).
- The remaining brewery effect (splash crown — separate spec).
