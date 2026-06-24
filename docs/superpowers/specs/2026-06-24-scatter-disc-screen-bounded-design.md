# Scatter Backing-Disc Screen-Bounding — Design

**Date:** 2026-06-24
**Status:** Approved (pending spec review)

## Overview

The `scatter` transition's backing disc (the `backgroundColor` circle that
guarantees gap-free cover) is drawn as `ctx.arc(cx, cy, c * maxR)` under the mesh
transform. At `scope:'wall'`, `maxR` is the wall's half-diagonal (~3500 global px for
`OEB Sign 1`), so every screen rasterizes a huge filled circle every cover frame —
even on the long tail where the disc has *fully covered* that screen and a cheap
`fillRect` would look identical. On-wall A/B (`?sdisc=0`) measured the disc costing
~5–10 fps during the burst.

This change makes the disc **screen-bounded**: each screen draws only what its own
region needs, keyed on the disc's current radius. It preserves the wall-wide
expanding-circle reveal (the design choice) while turning the expensive
fully-covered frames into cheap fills. iPad-1 / ES5 / `arc`+`fillRect` only.

## What changes

In `mmDrawScatter` (`js/transitions.js`), when a viewport `vp` is available (mesh item
with a quad), the single wall-diagonal arc is replaced by a per-screen decision on the
disc's current radius `r = c * maxR` vs this screen's global region (`vp.globalRect`):

- **`r < nearR`** (disc hasn't reached this screen) → draw nothing.
- **`nearR <= r < farR`** (edge sweeping across this screen) → `ctx.arc(cx, cy, r)` —
  the curved growing edge, exactly as today.
- **`r >= farR`** (disc fully covers this screen) → `ctx.fillRect(vp.globalRect …)` —
  a cheap solid, no huge-circle tessellation.

`nearR` = distance from the disc center `(cx,cy)` to the screen's global rect (0 when
the center is inside it); `farR` = the max distance from `(cx,cy)` to the rect's four
corners. Per screen this reconstructs the same coordinated expanding circle (covered
interiors solid, the moving edge an arc), but the long fully-covered tail — most cover
frames on most screens — becomes a `fillRect`.

When **no `vp`** (scope:`screen`, or an uncalibrated / quad-less item), the disc keeps
its current arc unchanged: there `maxR` is the screen's own half-diagonal, so the arc
is already screen-sized and not expensive.

## Pure helper

`mmScatterDiscCase(cx, cy, r, rect)` → `'none' | 'arc' | 'fill'` (added in
`js/transitions.js`, exported on `root`, node-tested):

- `nearR`: point-to-axis-aligned-rect distance. `dx = max(rect.x - cx, 0, cx - (rect.x + rect.w))`,
  `dy = max(rect.y - cy, 0, cy - (rect.y + rect.h))`, `nearR = sqrt(dx*dx + dy*dy)`
  (0 when the center is inside the rect).
- `farR`: `max` over the four corners of the distance from `(cx,cy)`.
- Returns `'none'` if `r < nearR`, `'fill'` if `r >= farR`, else `'arc'`.

## Wiring (`mmDrawScatter`)

The existing guard stays: `if (!sd.nodisc && c * maxR >= 0.5) { … }`. Inside:

- **`vp` present:** `switch (mmScatterDiscCase(cx, cy, c * maxR, vp.globalRect))` —
  `'fill'` → `ctx.fillStyle = bg; ctx.fillRect(vp.globalRect.x, vp.globalRect.y, vp.globalRect.w, vp.globalRect.h)`;
  `'arc'` → the current `beginPath`/`arc`/`fill`; `'none'` → nothing.
- **`vp` null:** the current `beginPath`/`arc(cx,cy,c*maxR)`/`fill`, unchanged.

`fillStyle` is set to `bg || '#000000'` in both the fill and arc paths (as today). The
`?sdisc=0` knob still suppresses the disc entirely (it gates the whole block).

## Architecture / files

- `js/transitions.js` — add `mmScatterDiscCase` (+ export); branch the disc draw in
  `mmDrawScatter` on `vp`. No other file changes; no server change; no new module.

## Testing

- **Node** (`tests/unit/js/test_scatter.js`):
  - `mmScatterDiscCase`: for an off-center rect, returns `'none'` when `r` is below the
    near edge, `'arc'` when `r` is between near and far, `'fill'` when `r` exceeds the
    far corner; returns `'fill'` for a center-inside-rect case once `r >= farR`;
    `nearR = 0` path (center inside) never returns `'none'`.
  - `mmDrawScatter` with a viewport whose rect the disc fully covers → records a
    `fillRect` and **zero** `arc`s for the disc; with a viewport the disc only partially
    reaches → records an `arc`; with `vp` null (quad null) → an `arc` as before (existing
    disc tests stay green).
- **On-wall iPad-1** (acceptance): re-run the `?sdisc` A/B on screen3 — the default
  (disc on, now screen-bounded) should sit near the `?sdisc=0` frame rate, confirming
  the fully-covered frames are now cheap, while the cover stays gap-free.

## Out of scope (YAGNI)

- Any change to the erupting copies, the giant, the spin, or the `?sdisc=0` knob.
- The scope:`screen` / uncalibrated path (keeps the current arc).
- Replacing the moving-edge arc with a cheaper curve approximation (the arc only runs
  during the brief edge-crossing window now).
