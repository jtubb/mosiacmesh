# Scatter Giant Size-Cap ("giantScale") — Design

**Date:** 2026-06-24
**Status:** Approved (pending spec review)

## Overview

The `scatter` transition's central "giant" sprite is the dominant per-frame cost
on the 1st-gen iPad-1. On-wall telemetry (screen3, `?tdbg` heartbeat) measured the
scatter transition at **~16 fps with the giant** vs **~30 fps with it suppressed**
(`?sgiant=0`) — the giant roughly halves the frame rate. The copies are not the
bottleneck (screen-local culling draws only 0–2 of 40 per screen). Cause: the giant
peaks at `1.43 × the wall height` (≈5807 global px for `OEB Sign 1`'s
`meshGlobal=[5684,4061]`), an ~11× upscale of the 512px sprite that fills the whole
screen on every central panel.

This change caps the giant's peak size: the hardcoded `1.43` multiplier becomes a
tunable **`giantScale`** (fraction of the region height) with a smaller, fps-friendly
default, plus a live on-wall tuning knob. The giant stays centered on the wall, spins
360°, and draws via the established fast path — only its peak size shrinks.

It runs on the iPad-1 (iOS 5.1 / Safari 5.1): **ES5 only**, `drawImage`/`fillRect`/
`arc` only.

## What changes

In `mmDrawScatter` (`js/transitions.js`), the giant height becomes:

```
gh = reg.h * giantScale * c          // was: reg.h * 1.43 * c
```

where `c = mmScatterCover(phase, p)` (unchanged). Lower `giantScale` → smaller giant
→ less per-screen coverage → cheaper. The giant's center, 360° spin
(`mmScatterGiantAngle`), and fast-path draw (`mmStampSprite`) are unchanged.

## Parameter

Add to `ScatterEffect.params` in `effects.py`:

| key | type | default | range | meaning |
|-----|------|---------|-------|---------|
| `giantScale` | number | `0.6` | min `0`, max `2` | giant peak height as a fraction of the region height |

- It appears in the playlist editor like the other scatter params (number field).
- **The default drops from the old hardcoded `1.43` to `0.6`**, so scatter is faster
  out of the box.
- **Legacy items** already in `settings.dat` have no `giantScale` field;
  `mmDrawScatter` resolves a missing value to `0.6` (via a `!= null` fallback), so the
  fix applies to existing scatter items without migration.
- `giantScale = 0` disables the giant entirely (the disc + copies still cover).

## Live on-wall tuning knob

Extend the existing `?tdbg` scatter knobs (`window._mmSdbg`, in `index.html`):

- `?sgscale=N` (N is a decimal, e.g. `0.4`) → `window._mmSdbg.gscale` → overrides
  `giantScale` live, so the size can be dialed against the fps heartbeat on the wall
  with no redeploy.

`mmDrawScatter` resolves the effective scale as:

```
gs = (sd.gscale != null) ? sd.gscale
   : (params && params.giantScale != null) ? params.giantScale
   : 0.6
```

(`sd = root._mmSdbg || {}`, the same object that already carries `count`/`nogiant`/
`nocull`.) The existing `?sgiant=0` knob still wins (drops the giant outright).

## Cost relationship (why 0.6 is a starting default, not final)

Per-screen cost falls only once the giant shrinks below ~one screen on the panels
that were slow. On a ~4-row wall each panel is ≈¼ the wall height, so values around
**0.3–0.6** are where per-screen coverage (and cost) actually drops; above ~1.0 the
central panels are still fully covered and stay slow. `0.6` is therefore a *starting*
default — the `?sgscale` knob is how the real sweet spot is found on the wall, then
baked into the param default or the per-item value. The acceptance step records the
chosen value.

## Architecture / files

- `effects.py` — add the `giantScale` `ParamSpec` to `ScatterEffect`. No other server
  change (visuals stay client-side; `video_filters` is unchanged).
- `js/transitions.js` — `mmDrawScatter` resolves `gs` and uses `gh = reg.h * gs * c`.
- `index.html` — parse `?sgscale=N` into `window._mmSdbg.gscale`.
- No new module; no `mmStampSprite` / viewport / culling change; no `settings.dat`
  schema change.

## Testing

- **Python** (`tests/unit/test_effects.py`): `scatter` catalog entry includes
  `giantScale` (type number, default `0.6`, min `0`, max `2`); full-catalog assertions
  still pass.
- **Node** (`tests/unit/js/test_scatter.js`): with a recording ctx, a small
  `giantScale` yields a smaller giant `globalSize` into the giant stamp than a large
  one (giant height scales linearly with `giantScale`); a missing `giantScale` resolves
  to `0.6` (not the old `1.43`); `window._mmSdbg.gscale` overrides the param; `?sgiant=0`
  still suppresses the giant regardless of `giantScale`.
- **On-wall iPad-1** (acceptance): on `OEB Sign 1`, dial `?tdbg&sgscale=…` on screen3,
  read the fps heartbeat from the server log, pick the value that recovers frame rate
  while keeping an acceptable giant; record it.

## Out of scope (YAGNI)

- Any change to copies, culling, the backing disc, or the spin.
- A per-screen device-relative cap (the rejected "cap to one screen" option).
- Auto-tuning `giantScale` from measured fps.
- The separate ~20 fps plasma-mesh baseline cost (a distinct optimization).
