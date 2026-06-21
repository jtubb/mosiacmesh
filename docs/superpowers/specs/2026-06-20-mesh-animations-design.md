# Mesh-Display Animations — Design

**Date:** 2026-06-20
**Status:** Approved — ready for implementation plan.
**Depends on:** PR #47 (`mmLoopItemSeed` / per-loop coordinated seed). Branch from `feature/per-loop-reseed` or rebase onto it once merged — the client mesh path reuses the per-(loop,item) seed so all screens render the same global frame.
**Builds on:** the coordinated seed infra (`MM_RNG`, `mmDeriveSeed`, `mmLoopItemSeed`, `Display.playSeed`), the ArUco calibration data (`Client.measuredPerimeter`, `Display.boundingBox` — photo-pixel coords), and the SEGMENT media mesh pipeline (`calibration.warp_image_for_screen`, `render._per_client_items`).

## Context

Today a SCRIPT (animation) playlist item **mirrors**: every screen in the group runs the full animation in its own canvas. This makes one animation **mesh** across the physical wall instead — each screen renders its slice of a single virtual wall-sized canvas, so the wall behaves like one big display (the animation analog of SEGMENT media, which slices one image across the wall).

Each screen renders animations **live** on the iPad-1 in ES5 canvas-2D. Canvas-2D supports affine transforms (translate/scale/rotate/shear) but **not** true 4-point perspective, and iOS 5.1 has no reliable WebGL. So unlike the SEGMENT media path (which does a server-side `cv2.warpPerspective` per screen), the mesh-animation mapping is an **affine approximation** of each screen's calibrated quad — exact when the calibration photo is roughly head-on, with small distortion on steeply-angled shots.

Coordination is already solved: a shared clock (`GoTime`) + a shared `Display.playSeed` mean every screen can compute the *same global frame* at the same instant and simply show a different slice. No new sync mechanism is needed.

This is a **client-side render change plus a small data-model + payload addition**; no new server render assets, no protocol rewrite.

## Decisions (settled during brainstorming)

- **Geometry source: ArUco calibration only.** Mesh geometry comes from `Client.measuredPerimeter` + `Display.boundingBox` (the same data SEGMENT uses). No manual grid layout. OEB Sign 1 is **23/24 calibrated**, so this is testable on the real wall today.
- **Mapping fidelity: affine-from-quad.** Derive a 2D affine from the screen quad's corners (`ctx.setTransform(a,b,c,d,e,f)`). Handles rotated/tilted panels and keeps seams continuous. True perspective is ruled out (no WebGL on iPad-1).
- **Selection: per-item flag.** `MediaElement.scriptSpan` ∈ {`'mirror'` (default), `'mesh'`}, parallel to media's FULL (mirror) vs SEGMENT (mesh). A playlist may mix mirrored and meshed animations.
- **Uncalibrated screen in a mesh group → BLACK.** A client in a mesh group with no `measuredPerimeter` paints black and idles (does NOT mirror), so it doesn't show a stray full-animation panel among the slices. (23/24 calibrated ⇒ at most one black panel until the last screen is calibrated.)

## Three-way client behavior (the core decision)

For a SCRIPT item, the per-client item payload always carries `scriptSpan`; it carries `meshQuad` only when that client is calibrated in a mesh group. The client decides:

| `scriptSpan` | `meshQuad` present? | Behavior |
|---|---|---|
| `'mesh'` | yes (+ non-degenerate) | **Mesh** — render this screen's affine slice of the global wall canvas |
| `'mesh'` | no / degenerate quad | **Black** — fill black, skip `draw`, keep the RAF loop alive cheaply |
| `'mirror'` | (ignored) | **Mirror** — full animation at `canvas.width/height` (today's behavior, byte-identical) |

## Data model

`MediaElement` (in `mosaicmesh/state.py`) gains `scriptSpan` (str, default `'mirror'`).
- Persisted in `settings.dat` like other `MediaElement` fields.
- `migrate_client_objects` (or its MediaElement migration helper) backfills `scriptSpan='mirror'` on `MediaElement` objects loaded from an older `settings.dat`.
- All read sites use `getattr(me, 'scriptSpan', 'mirror')` defensively.
- `render._media_item_payload(me)` echoes `scriptSpan` into every item payload (so the client always knows mirror vs mesh, independent of calibration).

## Server geometry (reuses calibration normalization)

In `render._per_client_items(display, key, c)`, add a branch for SCRIPT mesh items. For `me.playmode == SCRIPT`:

```
if getattr(me, 'scriptSpan', 'mirror') == 'mesh'
   and c.measuredPerimeter is not None
   and display.boundingBox:
    bx, by, bw, bh = display.boundingBox
    item["meshQuad"]   = [[(px-bx)/bw, (py-by)/bh] for (px, py) in c.measuredPerimeter]  # 4 corners, 0..1, stored TL/TR/BR/BL order
    item["meshAspect"] = [bw, bh]
# else: omit meshQuad -> client goes black (mesh) or the item is mirror
```

This is the same normalization `warp_image_for_screen` already performs (`(px-bx)/bw, (py-by)/bh`), minus the homography — no new geometry math server-side. `meshAspect = [bw, bh]` is the group bbox and is **identical for every client in the group**, so the global canvas dimensions the clients derive from it match across screens (coordination preserved). SCRIPT items are not `_is_renderable`, so this adds no render assets and no render-readiness gating; `item["file"]` stays the animation name (the existing `else` branch).

**Delivery:** mesh requires calibration, and calibrated groups already receive playback via the **per-client** PLAY/PREPARE broadcasts that call `_per_client_items` (`_broadcast_per_client_play`, the PREPARE seed sites). Mesh geometry rides that existing path; no new broadcast plumbing. (A group-wide PLAY path also exists for simple/uncalibrated groups; those carry no `meshQuad`, so a mesh item there → black, which is the correct "uncalibrated" outcome.)

## Client mapping (ES5 — the core math)

### `mmMeshTransform` (new pure helper in `js/animations.js`, exposed on `root`, node-tested)

Maps **global wall coordinates → this screen's canvas pixels** via an affine fixed by 3 corner correspondences (canvas TL/TR/BL ↔ the quad's TL/TR/BL scaled into the global canvas; BR is ignored — affine is determined by 3 points).

```js
// meshQuad: [[u0,v0],[u1,v1],[u2,v2],[u3,v3]] normalized 0..1 (TL,TR,BR,BL).
// GW,GH: global wall canvas size. Returns {a,b,c,d,e,f} for ctx.setTransform,
// or null if the quad is degenerate (collinear edges) -> caller goes black.
function mmMeshTransform(meshQuad, GW, GH, canvasW, canvasH) {
  var g0x = meshQuad[0][0]*GW, g0y = meshQuad[0][1]*GH;   // TL -> canvas (0,0)
  var g1x = meshQuad[1][0]*GW, g1y = meshQuad[1][1]*GH;   // TR -> canvas (W,0)
  var g3x = meshQuad[3][0]*GW, g3y = meshQuad[3][1]*GH;   // BL -> canvas (0,H)
  var e1x = g1x - g0x, e1y = g1y - g0y;                   // top edge vector
  var e3x = g3x - g0x, e3y = g3y - g0y;                   // left edge vector
  var det = e1x*e3y - e3x*e1y;
  if (det > -1e-9 && det < 1e-9) { return null; }         // degenerate
  var W = canvasW, H = canvasH;
  var a = (W * e3y) / det;
  var c = (-W * e3x) / det;
  var b = (-H * e1y) / det;
  var d = (H * e1x) / det;
  var e = -(a*g0x + c*g0y);                               // TL maps to (0,0)
  var f = -(b*g0x + d*g0y);
  return { a:a, b:b, c:c, d:d, e:e, f:f };
}
```

Derivation: with global edge vectors `E1=Q1-Q0`, `E3=Q3-Q0`, any global point's coordinates in the `(E1,E3)` basis are `(α,β)`; the screen shows `(α·W, β·H)`. The basis inverse (det = `E1x·E3y − E3x·E1y`) gives `(α,β)`, and `e,f` pin `Q0 → (0,0)`. Degenerate det (collinear quad edges) ⇒ `null`.

### Global wall size

Derive a stable global canvas from `meshAspect = [bw, bh]`: `GH = 1000`, `GW = round(1000 * bw / bh)`. Identical on every client (same `meshAspect`). Absolute scale only sets how large animation features are relative to the wall; fixing `GH` keeps feature sizes predictable and the wall aspect undistorted.

### `runScriptLoop` (`index.html`)

Three-way branch inside the existing per-frame `frame()` (the per-loop `itemSeed` from `mmLoopItemSeed` is already identical across screens — same seed, same `pos.index` — so all screens draw the same global frame):

```js
var it = playback.items[pos.index];                         // the item being drawn
var span = (it && it.scriptSpan) || 'mirror';
var mq = it && it.meshQuad;
if (span === 'mesh') {
  if (mq) {
    var bw = it.meshAspect[0], bh = it.meshAspect[1];
    var GH = 1000, GW = Math.round(1000 * bw / bh);
    var m = mmMeshTransform(mq, GW, GH, canvas.width, canvas.height);
    if (m) {
      ctx.save();
      ctx.setTransform(m.a, m.b, m.c, m.d, m.e, m.f);
      animations[name](ctx, pos.offsetMs, GW, GH, GoTime.now(), itemSeed);
      ctx.restore();
    } else { paintBlack(ctx, canvas); }    // degenerate quad
  } else {
    paintBlack(ctx, canvas);               // uncalibrated screen in a mesh group
  }
} else {
  animations[name](ctx, pos.offsetMs, canvas.width, canvas.height, GoTime.now(), itemSeed);  // mirror (today)
}
```

`paintBlack` = `ctx.setTransform(1,0,0,1,0,0); ctx.fillStyle='#000'; ctx.fillRect(0,0,canvas.width,canvas.height);` (and no `draw` call). The loop keeps running cheaply so a later PLAY (e.g. after that screen is calibrated) updates it.

The item object must be reachable in the loop. `playlistIndex` already returns `{index, offsetMs}`; the loop has `playback.items`, so use `playback.items[pos.index]` for `scriptSpan`/`meshQuad`/`meshAspect` (no change to `playlistIndex` needed). **Animation `draw` functions are unchanged** — mesh just hands them global wall dimensions with a pre-applied transform; the canvas auto-clips drawing to its own bounds.

### Why coordination is free

`itemSeed = mmLoopItemSeed(playback.seed, loopIdx, pos.index)` and `pos.offsetMs`/`GoTime.now()` are all derived from group-shared state, so every calibrated screen renders the identical global frame and shows its own slice. Physical bezels/gaps fall in global regions covered by no screen — correct video-wall continuity, for free from using photo-bbox-normalized coords.

## Editor (admin, modern JS)

`js/timeline/modals/playlist-editor.js`: for a selected SCRIPT item, add a **mirror / mesh** control bound to `item.scriptSpan` (default `'mirror'`), with a hint that mesh requires a calibrated group (and that an uncalibrated screen goes black). No save-gating beyond persisting the field; non-SCRIPT items don't show it.

## Performance note

In mesh mode each screen computes the **full** global animation and the canvas clips to its slice (≈ N× the primitives of mirror, for N screens, per screen). Light animations (a few dozen primitives) are fine. Fixed-grid animations (e.g. `gameOfLife`'s 48×36) meshed span the whole wall — each screen shows a few large cells and still computes the full grid (same compute as today). No gating; the operator chooses which animations to mesh. Revisit only if the iPad-1 sign-off shows frame drops.

## Testing

- **`mmMeshTransform` (node, pure):**
  - Full-bbox quad `[[0,0],[1,0],[1,1],[0,1]]` → maps global `(0,0)→(0,0)`, `(GW,0)→(canvasW,0)`, `(0,GH)→(0,canvasH)` (identity-like up to W/H scale).
  - Right-half quad `[[0.5,0],[1,0],[1,1],[0.5,1]]` → global `(GW/2,0)→(0,0)`, `(GW,0)→(canvasW,0)` (correct offset+scale).
  - Seam continuity: two horizontally-adjacent quads sharing an edge map that shared global edge to the touching canvas edges of both screens (content is continuous across the seam).
  - Determinism: same inputs → same matrix.
  - Degenerate quad (collinear edges, det≈0) → `null`.
- **Mirror path unchanged:** existing `tests/unit/js/test_animations_*.js` op-log suites still pass (no signature change to `draw`).
- **Server:** a unit test that `_per_client_items` adds `meshQuad`+`meshAspect` for a calibrated client on a SCRIPT mesh item, omits them for an uncalibrated client (→ black) and for a mirror item, and that `_media_item_payload` echoes `scriptSpan`. Plus a `MediaElement` migration test (old object → `scriptSpan='mirror'`).
- **iPad-1 sign-off:** on the calibrated OEB group, a meshed animation spans the wall in lockstep across screens; the one uncalibrated screen is black; a mirror item still shows the full animation on every screen.

## Non-goals

- True perspective per screen (affine only; no WebGL on iPad-1).
- Manual grid layout (ArUco-only per the decision).
- Meshing video/image items (those already mesh via SEGMENT server-side) or DOM/non-canvas animations.
- Per-screen rotation/skew beyond what the 3-corner affine captures.
- Server-side pre-rendering of animation frames (defeats live coordination).
