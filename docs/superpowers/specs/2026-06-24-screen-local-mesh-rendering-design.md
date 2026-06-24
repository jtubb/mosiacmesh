# Screen-Local Mesh Rendering ("mmMeshViewport") — Design

**Date:** 2026-06-24
**Status:** Approved (pending spec review)

## Overview

A reusable, screen-local rendering primitive for mesh animations, introduced to
fix scatter's choppiness and shared so other sprite animations can adopt it. The
giant hop in the `scatter` transition is choppy on the 1st-gen iPad-1 because of
how the mesh renders: **every screen draws the entire wall in global coordinates
and the mesh affine "zooms" to that screen's slice.** Two costs fall out of that:

1. **Every screen draws all `count` erupting copies** — most land outside its own
   slice and are transformed off-canvas. Pure wasted work (≈6× on a 6-wide wall).
2. **The giant is drawn at wall scale.** For `OEB Sign 1`, `meshGlobal = [5684, 4061]`,
   so the giant is `4061 × 1.43 ≈ 5807` global px tall, drawn from a **512 px**
   sprite — `gsc = 5807/512 ≈ 11.3×` upscale, rotated, every frame.

This design adds a client-side primitive that lets a mesh animation draw **only
what its screen can see, at device-pixel scale** — culling off-screen elements and
issuing destination quads sized in device px rather than wall px.

It must run on the iPad-1 display clients (iOS 5.1 / Safari 5.1): **ES5 only**,
`drawImage`/`fillRect`/`arc` only (no `clip()`, no `destination-*` compositing).

## Honest caveat — what is and isn't guaranteed

- **Copy culling is an unambiguous win.** A copy's device footprint is ~1× the
  source (small); skipping the copies that don't overlap a screen is a clean cut
  with no caveat and no look change.
- **The giant is conditional.** The giant is *legitimately screen-filling* on every
  central screen, so its visible cost is ~one full-screen rotated blit per frame no
  matter how the draw is issued. Device-scale stamping helps **only if** iOS5 WebKit
  rasterizes the full oversized (~6250 px device) quad instead of clipping to the
  ~1024 px canvas first. If WebKit already clips, the giant's remaining cost is
  inherent and the real lever becomes its *size* or *spin*. We cannot know which
  without measuring on the wall. Therefore the giant's device-scale draw is the
  **first on-wall checkpoint**, and a giant size-cap is held **in reserve** (not
  built now — YAGNI) for if the A/B shows the giant still heavy.

## Quality is neutral for the giant

Today the code upscales the 512 source to 5807 px **then** the affine scales it back
down to the screen, so each screen already shows only 512-source detail at ~screen
size. Drawing at device scale samples the **same** 512 source to the **same**
on-screen size — same pixels, less work. This is not a look-for-speed trade.

## No server change

The screen's global view rect and its device-scale are both recovered by **inverting
the affine `mmMeshTransform` already returns**. Everything derives from the existing
`meshQuad` / `meshGlobal` payload — purely client-side, like the rest of the mesh UI
work. No new server data, no `settings.dat` change.

## Architecture

A new ES5 module **`js/mesh-viewport.js`**, loaded by `index.html` alongside
`js/transitions.js`, exporting two symbols on `root`:

- `mmMeshViewport(meshQuad, GW, GH, canvasW, canvasH)` — a pure descriptor of *this
  screen's* window into the global wall.
- `mmStampSprite(ctx, vp, img, gx, gy, globalSize, angle)` — the screen-local sprite
  draw built on the descriptor.

`scatter` adopts both now (proving the primitive out). Other sprite animations
(beerfill, future ones) can adopt incrementally later. Per-pixel field animations
(plasma, game-of-life) are out of scope.

### `mmMeshViewport` — the testable core

Pure function, no canvas. Inverts the affine from `mmMeshTransform` and returns:

| field | meaning |
|-------|---------|
| `globalRect {x,y,w,h}` | the screen's visible region in **global** coords: the four device-canvas corners `(0,0),(cw,0),(cw,ch),(0,ch)` mapped back through the inverse affine, then the axis-aligned bounding box. |
| `scale` | device px per global px, `≈ sqrt(abs(a*d - b*c))` of the affine. |
| `intersects(gx, gy, gRadius)` | boolean: does a sprite centered at global `(gx,gy)` with global half-extent `gRadius` overlap `globalRect`? AABB-vs-expanded-rect test (expand `globalRect` by `gRadius` on each side, point-in-rect). This is the cull test. |
| `toDevice(globalLen)` | `globalLen * scale` — convert a global length to device px. |
| `m {a,b,c,d,e,f}` | the source affine itself (global→device), retained so `mmStampSprite` can compose its own device-space transform after resetting the ctx. |

The affine is invertible (6-param affine, not a homography). `globalRect` carries an
implicit margin only via the caller's `gRadius`; the descriptor itself is the exact
bbox of the visible region.

### `mmStampSprite` — the shared draw primitive

`mmStampSprite(ctx, vp, img, gx, gy, globalSize, angle)`:

1. **Cull:** if `!vp.intersects(gx, gy, globalSize/2)` → return without drawing.
2. **Draw screen-local:** compose the device-space matrix for *this sprite alone*
   (affine ∘ translate(gx,gy) ∘ rotate(angle) ∘ scale-to-device), so the destination
   quad is sized in **device px** (bounded), not via a large logical scale, then
   `drawImage` the sprite centered. `save`/`setTransform`/`restore` around the stamp.
   `img` may be a decoded `Image` or a pre-rotated atlas-bucket canvas (scatter's
   copies pass the atlas bucket; both are valid `drawImage` sources).

No `clip()`, no compositing. Copies and the giant both route through this — copies get
culled, the giant gets device-bounded.

## Scatter adoption (`js/transitions.js`)

`mmDrawScatter` builds a `vp = mmMeshViewport(quad, GW, GH, ...)` once per call from
the args it already receives, then:

- **Copy loop:** for each particle, route through `mmStampSprite` (passing the
  pre-rotated atlas bucket as `img`). Off-screen copies are culled.
- **Giant:** route through `mmStampSprite` (full-res `img`, `angle =
  mmScatterGiantAngle(...)`). Device-bounded destination.
- **Backing disc:** unchanged — one `arc` + `fill`, already cheap and correct under
  the existing transform.
- **`scope`:** `wall` and `screen` both fall out naturally. `scope:'screen'` yields a
  viewport equal to the screen's own region; `scope:'wall'` spans the global wall.

The scatter atlas optimization (`mmBuildSpriteAtlas`, pre-rotated buckets) stays.
`mmMeshViewport` needs the screen's `canvasW/canvasH`; `mmDrawScatter`'s call sites in
`index.html` (mesh in-canvas + overlay) pass the canvas dimensions through.

## Testing

**Node** (`tests/unit/js/`, `--test`):
- `mmMeshViewport`: inverse mapping of a known affine → expected `globalRect`; `scale`
  for a pure-scale and a rotated affine; `intersects` true for a sprite inside / on
  the seam and false for one fully outside; `toDevice` linear.
- `mmStampSprite`: with a recording ctx (same style as `test_scatter.js`) — **no**
  `drawImage` when the sprite is fully outside the viewport (culled); exactly one
  `drawImage` when inside.
- `mmDrawScatter` updated: with a viewport covering only part of the wall, fewer than
  `count` copies are stamped (culling works); with a full-wall viewport, all `count`
  + giant are stamped (no regression).

**On-wall iPad-1 A/B** (acceptance gate): scatter at `count` 40 before/after on the
calibrated mesh. Confirm (a) copies are culled per screen (per-screen draw count
drops) and (b) whether the giant's device-scale draw resolves the choppiness. **This
checkpoint decides whether the reserve giant size-cap is needed.**

## Out of scope (YAGNI)

- Per-pixel field animations (plasma, game-of-life) adopting the viewport.
- Migrating beerfill (and other sprite animations) onto the primitive — they adopt it
  later; this round only proves it with scatter.
- A giant size-cap parameter (held in reserve; only built if the A/B shows the giant
  still heavy after device-scale stamping).
- Any server-side change.

## Delivery

The existing **Scatter Demo** playlist is the A/B vehicle; no new demo content needed.
