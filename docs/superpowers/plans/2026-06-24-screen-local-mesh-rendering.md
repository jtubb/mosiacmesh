# Screen-Local Mesh Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reusable screen-local mesh rendering primitive (`mmMeshViewport` + `mmStampSprite`) and adopt it in `scatter` so each screen culls off-screen sprite copies and draws only the source region it can see.

**Architecture:** A new ES5 module `js/mesh-viewport.js` inverts the affine `mmMeshTransform` already returns to describe this screen's window into the global wall (`mmMeshViewport`), and a draw helper (`mmStampSprite`) culls sprites outside that window and uses `drawImage`'s source-subrectangle form to bound the destination of large/partially-visible sprites to what the screen shows. `mmDrawScatter` routes its copies and giant through `mmStampSprite`. No server change.

**Tech Stack:** Hand-written ES5 JavaScript (no build step), `node --test` for unit tests, HTML5 canvas 2D.

## Global Constraints

- **ES5 only** in `js/mesh-viewport.js` and `js/transitions.js` and `index.html` inline scripts: no `let`/`const`, arrow functions, template literals, `class`, `Promise`, `fetch`. (1st-gen iPad / iOS 5.1 / Safari 5.1 display clients.)
- **Canvas primitives only:** `drawImage` / `fillRect` / `arc`. NO `clip()`, NO `globalCompositeOperation` / `destination-*`.
- **Module wrapper** (exact, copied from `js/transitions.js`): `(function (root) { ... })(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));` — attach public symbols via `root.X = X;`.
- **No server-side change** and **no `settings.dat` change** — everything derives from the existing `meshQuad` / `meshGlobal` client payload.
- `js/mesh-viewport.js` MUST be loaded **after** `js/animations.js` (which defines `mmMeshTransform`).
- **Quality is neutral:** drawing at device scale samples the same source to the same on-screen size as today; do not change the giant's size, spin, or the backing disc.
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Run JS tests with `node --test tests/unit/js/<file>.js` (or `python pytest_runner.py --js` for the full suite).

## Reference: `mmMeshTransform` (already exists, `js/animations.js:52`)

Returns an affine `{a,b,c,d,e,f}` mapping **global** wall coords → **device** canvas px:
`dx = a*gx + c*gy + e`, `dy = b*gx + d*gy + f`. Returns `null` for a degenerate quad.

## File Structure

- **Create** `js/mesh-viewport.js` — `mmMeshViewport` (descriptor) + `mmStampSprite` (draw helper). One responsibility: screen-local sprite rendering for mesh.
- **Create** `tests/unit/js/test_mesh_viewport.js` — node tests for both functions.
- **Modify** `js/transitions.js` — `mmDrawScatter` adopts the viewport (new `canvasW`/`canvasH` params; route copies + giant through `mmStampSprite`).
- **Modify** `tests/unit/js/test_scatter.js` — add a culling integration assertion (existing tests stay green because a `null` quad → `null` viewport → `mmStampSprite`'s legacy draw path).
- **Modify** `index.html` — load `js/mesh-viewport.js`; pass `canvas.width/height` (mesh branch) and `cvm.width/height` (overlay branch) into `mmDrawScatter`.

---

### Task 1: `mmMeshViewport` descriptor

**Files:**
- Create: `js/mesh-viewport.js`
- Test: `tests/unit/js/test_mesh_viewport.js`

**Interfaces:**
- Consumes: `root.mmMeshTransform(meshQuad, GW, GH, canvasW, canvasH) -> {a,b,c,d,e,f}|null` (from `js/animations.js`).
- Produces: `mmMeshViewport(meshQuad, GW, GH, canvasW, canvasH) -> vp | null` where `vp = { m:{a,b,c,d,e,f}, globalRect:{x,y,w,h}, scale:Number, toDevice:fn(globalLen)->Number, intersects:fn(gx,gy,gRadius)->Boolean }`. Returns `null` for a degenerate/un-invertible quad.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_mesh_viewport.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/animations.js');   // provides mmMeshTransform
await import('../../../js/mesh-viewport.js');
const g = globalThis;

// A full-wall quad [TL,TR,BR,BL] normalized: this screen IS the whole wall.
const FULL = [[0, 0], [1, 0], [1, 1], [0, 1]];

test('mmMeshViewport: full-wall quad -> globalRect is the whole wall, scale maps GW->canvas', () => {
  const vp = g.mmMeshViewport(FULL, 1000, 800, 200, 160);   // wall 1000x800 shown on a 200x160 canvas
  assert.ok(vp);
  // canvas corners map back to global (0,0)..(1000,800)
  assert.ok(Math.abs(vp.globalRect.x - 0) < 1e-6);
  assert.ok(Math.abs(vp.globalRect.y - 0) < 1e-6);
  assert.ok(Math.abs(vp.globalRect.w - 1000) < 1e-6);
  assert.ok(Math.abs(vp.globalRect.h - 800) < 1e-6);
  // device px per global px = 200/1000 = 0.2
  assert.ok(Math.abs(vp.scale - 0.2) < 1e-6);
  assert.ok(Math.abs(vp.toDevice(500) - 100) < 1e-6);
});

test('mmMeshViewport: half-wall quad -> globalRect is this screen half only', () => {
  // Left half of the wall: TL(0,0) TR(0.5,0) BR(0.5,1) BL(0,1)
  const LEFT = [[0, 0], [0.5, 0], [0.5, 1], [0, 1]];
  const vp = g.mmMeshViewport(LEFT, 1000, 800, 200, 160);
  assert.ok(vp);
  assert.ok(Math.abs(vp.globalRect.x - 0) < 1e-6);
  assert.ok(Math.abs(vp.globalRect.w - 500) < 1e-6);   // only the left 500 global px
});

test('mmMeshViewport: intersects culls outside, keeps inside + straddling', () => {
  const LEFT = [[0, 0], [0.5, 0], [0.5, 1], [0, 1]];     // visible global x in [0,500]
  const vp = g.mmMeshViewport(LEFT, 1000, 800, 200, 160);
  assert.equal(vp.intersects(250, 400, 10), true);       // well inside
  assert.equal(vp.intersects(900, 400, 10), false);      // far right, outside
  assert.equal(vp.intersects(505, 400, 20), true);       // just past the seam but radius straddles
});

test('mmMeshViewport: degenerate quad -> null', () => {
  const DEG = [[0, 0], [0, 0], [0, 0], [0, 0]];
  assert.equal(g.mmMeshViewport(DEG, 1000, 800, 200, 160), null);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/unit/js/test_mesh_viewport.js`
Expected: FAIL — `mmMeshViewport is not a function` (module/file doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

Create `js/mesh-viewport.js`:

```js
/* js/mesh-viewport.js — ES5, NO module syntax (valid classic <script> for the
 * iPad-1 client AND a side-effect import for node tests). Screen-local mesh
 * rendering: a viewport descriptor (this screen's window into the global wall,
 * by inverting mmMeshTransform) + a sprite stamp that culls off-screen and
 * bounds large draws to the visible region. drawImage/arc only — no clip. */
(function (root) {
  // This screen's window into the global wall. Pure; no canvas. Returns null
  // for a degenerate/un-invertible quad (caller draws nothing / goes black).
  function mmMeshViewport(meshQuad, GW, GH, canvasW, canvasH) {
    if (typeof root.mmMeshTransform !== 'function') { return null; }
    var m = root.mmMeshTransform(meshQuad, GW, GH, canvasW, canvasH);
    if (!m) { return null; }
    var detL = m.a * m.d - m.c * m.b;                  // det of the linear 2x2
    if (detL > -1e-12 && detL < 1e-12) { return null; }
    function toGlobal(dx, dy) {                          // device px -> global
      var px = dx - m.e, py = dy - m.f;
      return { x: (m.d * px - m.c * py) / detL,
               y: (-m.b * px + m.a * py) / detL };
    }
    var c0 = toGlobal(0, 0), c1 = toGlobal(canvasW, 0),
        c2 = toGlobal(canvasW, canvasH), c3 = toGlobal(0, canvasH);
    var minx = Math.min(c0.x, c1.x, c2.x, c3.x), maxx = Math.max(c0.x, c1.x, c2.x, c3.x);
    var miny = Math.min(c0.y, c1.y, c2.y, c3.y), maxy = Math.max(c0.y, c1.y, c2.y, c3.y);
    var scale = Math.sqrt(Math.abs(detL));
    return {
      m: m,
      globalRect: { x: minx, y: miny, w: maxx - minx, h: maxy - miny },
      scale: scale,
      toDevice: function (globalLen) { return globalLen * scale; },
      intersects: function (gx, gy, gRadius) {
        return (gx + gRadius) >= minx && (gx - gRadius) <= maxx &&
               (gy + gRadius) >= miny && (gy - gRadius) <= maxy;
      }
    };
  }

  root.mmMeshViewport = mmMeshViewport;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test tests/unit/js/test_mesh_viewport.js`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add js/mesh-viewport.js tests/unit/js/test_mesh_viewport.js
git commit -m "feat(mesh): mmMeshViewport — screen-local view descriptor

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `mmStampSprite` draw helper

**Files:**
- Modify: `js/mesh-viewport.js` (add `mmStampSprite` + export)
- Test: `tests/unit/js/test_mesh_viewport.js` (add cases)

**Interfaces:**
- Consumes: `vp` from Task 1 (`vp.m`, `vp.intersects`, `vp.globalRect`), or `null`.
- Produces: `mmStampSprite(ctx, vp, img, gx, gy, globalSize, angle)`. Draws `img` (an `Image` or a canvas) centered at global `(gx,gy)`, height `globalSize` global px, rotated `angle` rad. With a `vp`: culls if outside, else draws only the visible source sub-rectangle under the composed global→device transform (destination bounded to the screen). With `vp === null`: draws under the ctx's ambient transform (legacy global-coords path), skipping `ctx.rotate` when `angle` is 0.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/js/test_mesh_viewport.js`:

```js
// Recording ctx capturing transform + drawImage (incl. the 9-arg source-subrect form).
function recCtx() {
  return { imgs: 0, rots: 0, last: null,
    save(){}, restore(){}, translate(){}, rotate(){ this.rots++; }, scale(){},
    setTransform(){},
    drawImage() { this.imgs++; this.last = Array.prototype.slice.call(arguments); } };
}
function vpStub(rect) {   // identity-scale affine; AABB intersect against rect
  return { m: { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 }, globalRect: rect, scale: 1,
    toDevice: function (l) { return l; },
    intersects: function (gx, gy, gr) {
      return (gx + gr) >= rect.x && (gx - gr) <= (rect.x + rect.w) &&
             (gy + gr) >= rect.y && (gy - gr) <= (rect.y + rect.h);
    } };
}
const sprite = { width: 512, height: 512 };

test('mmStampSprite: culls a sprite fully outside the viewport', () => {
  const c = recCtx();
  g.mmStampSprite(c, vpStub({ x: 0, y: 0, w: 200, h: 200 }), sprite, 1000, 1000, 50, 0);
  assert.equal(c.imgs, 0);                       // culled, nothing drawn
});

test('mmStampSprite: small sprite fully inside -> draws the full source', () => {
  const c = recCtx();
  g.mmStampSprite(c, vpStub({ x: 0, y: 0, w: 200, h: 200 }), sprite, 100, 100, 40, 0);
  assert.equal(c.imgs, 1);
  // 9-arg form: img, sx, sy, sw, sh, dx, dy, dw, dh — full source means sw==sh==512
  assert.equal(c.last[3], 512);                  // sw
  assert.equal(c.last[4], 512);                  // sh
});

test('mmStampSprite: giant spanning beyond the viewport -> source sub-rect is bounded', () => {
  const c = recCtx();
  // wall-center giant 1000 global px tall; this screen sees only [0,200]x[0,200]
  g.mmStampSprite(c, vpStub({ x: 0, y: 0, w: 200, h: 200 }), sprite, 500, 500, 1000, 0);
  assert.equal(c.imgs, 1);
  assert.ok(c.last[3] < 512);                    // sw < full width: only the visible slice
  assert.ok(c.last[3] > 0);
});

test('mmStampSprite: null viewport -> legacy ambient draw, no rotate when angle 0', () => {
  const c = recCtx();
  g.mmStampSprite(c, null, sprite, 100, 100, 40, 0);
  assert.equal(c.imgs, 1);
  assert.equal(c.rots, 0);                       // angle 0 -> no ctx.rotate (atlas-bucket case)
  const c2 = recCtx();
  g.mmStampSprite(c2, null, sprite, 100, 100, 40, 1.2);
  assert.equal(c2.rots, 1);                      // non-zero angle -> one rotate
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/unit/js/test_mesh_viewport.js`
Expected: FAIL — `mmStampSprite is not a function`.

- [ ] **Step 3: Write minimal implementation**

In `js/mesh-viewport.js`, add `mmStampSprite` **before** the `root.mmMeshViewport = ...` line, and add its export:

```js
  // Draw img (Image or canvas) centered at global (gx,gy), height globalSize
  // global px, rotated `angle` rad. With a viewport: cull if off-screen, else
  // draw only the visible source sub-rect under the composed global->device
  // transform so the destination is bounded to the screen (no oversized blit).
  // With vp null: legacy draw under the ctx's ambient transform.
  function mmStampSprite(ctx, vp, img, gx, gy, globalSize, angle) {
    var iw = img.width, ih = img.height;
    if (!iw || !ih || !(globalSize > 0)) { return; }
    var k = globalSize / ih;                          // local px -> global px
    if (!vp) {                                         // legacy ambient global draw
      ctx.save();
      ctx.translate(gx, gy);
      if (angle) { ctx.rotate(angle); }
      ctx.scale(k, k);
      ctx.drawImage(img, -iw / 2, -ih / 2);
      ctx.restore();
      return;
    }
    var gRad = 0.5 * k * Math.sqrt(iw * iw + ih * ih);   // circumscribed global radius
    if (!vp.intersects(gx, gy, gRad)) { return; }
    var ca = Math.cos(angle), sa = Math.sin(angle);
    // Visible source sub-rect: map the viewport's global corners into local-
    // centered coords (inverse of translate(gx,gy).rotate(angle).scale(k)),
    // take the bbox, shift to image coords, pad 1px, clamp to [0,iw]x[0,ih].
    var gr = vp.globalRect, lx, ly, minx = 1e30, maxx = -1e30, miny = 1e30, maxy = -1e30;
    var cx4 = [gr.x, gr.x + gr.w, gr.x + gr.w, gr.x], cy4 = [gr.y, gr.y, gr.y + gr.h, gr.y + gr.h];
    for (var i = 0; i < 4; i++) {
      var dxg = cx4[i] - gx, dyg = cy4[i] - gy;
      lx = (ca * dxg + sa * dyg) / k;                  // inverse rotation + scale
      ly = (-sa * dxg + ca * dyg) / k;
      if (lx < minx) { minx = lx; } if (lx > maxx) { maxx = lx; }
      if (ly < miny) { miny = ly; } if (ly > maxy) { maxy = ly; }
    }
    var sx = Math.floor(minx + iw / 2) - 1, sy = Math.floor(miny + ih / 2) - 1;
    var ex = Math.ceil(maxx + iw / 2) + 1, ey = Math.ceil(maxy + ih / 2) + 1;
    if (sx < 0) { sx = 0; } if (sy < 0) { sy = 0; }
    if (ex > iw) { ex = iw; } if (ey > ih) { ey = ih; }
    var sw = ex - sx, sh = ey - sy;
    if (sw <= 0 || sh <= 0) { return; }                // nothing visible
    // Composed local-centered -> device: M . (translate(gx,gy).rotate.scale(k)).
    var m = vp.m;
    var Ca = m.a * (k * ca) + m.c * (k * sa);
    var Cc = m.a * (-k * sa) + m.c * (k * ca);
    var Cb = m.b * (k * ca) + m.d * (k * sa);
    var Cd = m.b * (-k * sa) + m.d * (k * ca);
    var Ce = m.a * gx + m.c * gy + m.e;
    var Cf = m.b * gx + m.d * gy + m.f;
    ctx.save();
    ctx.setTransform(Ca, Cb, Cc, Cd, Ce, Cf);
    ctx.drawImage(img, sx, sy, sw, sh, sx - iw / 2, sy - ih / 2, sw, sh);
    ctx.restore();
  }
```

And add the export line next to the existing one:

```js
  root.mmStampSprite = mmStampSprite;
  root.mmMeshViewport = mmMeshViewport;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test tests/unit/js/test_mesh_viewport.js`
Expected: PASS (8 tests total).

- [ ] **Step 5: Commit**

```bash
git add js/mesh-viewport.js tests/unit/js/test_mesh_viewport.js
git commit -m "feat(mesh): mmStampSprite — cull + source-subrect-bounded sprite draw

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Scatter adopts the viewport (copies cull, giant device-bounded)

**Files:**
- Modify: `js/transitions.js` — `mmDrawScatter` (signature `+canvasW, canvasH`; build `vp`; route copies + giant through `mmStampSprite`).
- Modify: `tests/unit/js/test_scatter.js` — add a culling integration test (imports `animations.js` + `mesh-viewport.js`).
- Modify: `index.html` — load `js/mesh-viewport.js`; pass canvas dims at both `mmDrawScatter` call sites.

**Interfaces:**
- Consumes: `mmMeshViewport`, `mmStampSprite` (Tasks 1-2); existing `mmScatterCover`, `mmScatterDist`, `mmScatterParticles`, `mmScatterGiantAngle`, `mmBuildSpriteAtlas`, `_mmMaskRegion`.
- Produces: `mmDrawScatter(ctx, params, phase, p, GW, GH, quad, scope, seed, img, bg, canvasW, canvasH)` — new trailing `canvasW`, `canvasH` (optional; when absent or `quad` null, `vp` is `null` and `mmStampSprite` uses its legacy path, preserving prior behavior).

- [ ] **Step 1: Write the failing test**

In `tests/unit/js/test_scatter.js`, add imports at the top (after the existing transitions import):

```js
await import('../../../js/animations.js');       // mmMeshTransform
await import('../../../js/mesh-viewport.js');     // mmMeshViewport + mmStampSprite
```

Then add this test (the `withFakeDocument` + `recCtx` helpers already exist in the file):

```js
test('mmDrawScatter: culls copies outside this screen when a viewport is given', () => {
  withFakeDocument(() => {
    const c = recCtx();
    const im = { width: 100, height: 120 };
    // Left-half quad: this screen sees only the left 50% of a 1000x800 wall.
    const LEFT = [[0, 0], [0.5, 0], [0.5, 1], [0, 1]];
    g.mmDrawScatter(c, { count: 40, scope: 'wall' }, 'cover', 0.6, 1000, 800, LEFT, 'wall', 7, im, '#140d06', 200, 160);
    assert.ok(c.imgs < 41, 'some wall-spanning copies should be culled off this screen');
    assert.ok(c.imgs > 0, 'copies overlapping this screen still draw');
  });
});

test('mmDrawScatter: full-wall viewport draws all copies + giant (no regression)', () => {
  withFakeDocument(() => {
    const c = recCtx();
    const im = { width: 100, height: 120 };
    const FULL = [[0, 0], [1, 0], [1, 1], [0, 1]];
    g.mmDrawScatter(c, { count: 40, scope: 'wall' }, 'cover', 0.6, 1000, 800, FULL, 'wall', 7, im, '#140d06', 1000, 800);
    assert.equal(c.imgs, 41);                    // 40 copies + giant, none culled
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/unit/js/test_scatter.js`
Expected: FAIL — the new culling test fails (current `mmDrawScatter` ignores `quad`/canvas dims and stamps all 41 in global coords, so `c.imgs` is 41, not `< 41`).

- [ ] **Step 3: Write minimal implementation**

In `js/transitions.js`, replace the `mmDrawScatter` signature line and its copy loop + giant block. The current function header is:

```js
  function mmDrawScatter(ctx, params, phase, p, GW, GH, quad, scope, seed, img, bg) {
```

Replace with (adds `canvasW, canvasH` and builds `vp`):

```js
  function mmDrawScatter(ctx, params, phase, p, GW, GH, quad, scope, seed, img, bg, canvasW, canvasH) {
    var vp = (quad && typeof mmMeshViewport === 'function')
      ? mmMeshViewport(quad, GW, GH, canvasW, canvasH) : null;
```

Then replace the copy loop and giant block. The current code is:

```js
    for (i = 0; i < parts.length; i++) {
      pt = parts[i]; d = dist * pt.sp; sz = baseH * (0.55 + 0.5 * c);
      ang = pt.rot0 + p * pt.rps * 6;
      x = cx + Math.cos(pt.ang) * d; y = cy + Math.sin(pt.ang) * d;
      if (atlas) {                                      // cheap blit of nearest pre-rotated bucket
        bi = Math.round(ang / (6.283185307 / atlas.buckets));
        bi = ((bi % atlas.buckets) + atlas.buckets) % atlas.buckets;
        spr = atlas.canvases[bi]; dd = atlas.dim * (sz / atlas.sh);
        ctx.drawImage(spr, x - dd / 2, y - dd / 2, dd, dd);
      } else {                                          // fallback: per-stamp rotate of the source
        sc = sz / img.height;
        ctx.save();
        ctx.translate(x, y); ctx.rotate(ang); ctx.scale(sc, sc);
        ctx.drawImage(img, -img.width / 2, -img.height / 2);
        ctx.restore();
      }
    }
    // giant center
    var gh = reg.h * 1.43 * c;
    if (gh > 2) {
      var gsc = gh / img.height;
      ctx.save(); ctx.translate(cx, cy); ctx.rotate(mmScatterGiantAngle(phase, p)); ctx.scale(gsc, gsc);
      ctx.drawImage(img, -img.width / 2, -img.height / 2); ctx.restore();
    }
```

Replace it with (route both through `mmStampSprite`; atlas bucket stamps at `angle 0` because its rotation is pre-baked, sized so the sprite-within-bucket renders at `sz`):

```js
    for (i = 0; i < parts.length; i++) {
      pt = parts[i]; d = dist * pt.sp; sz = baseH * (0.55 + 0.5 * c);
      ang = pt.rot0 + p * pt.rps * 6;
      x = cx + Math.cos(pt.ang) * d; y = cy + Math.sin(pt.ang) * d;
      if (atlas) {                                      // pre-rotated bucket: rotation baked, stamp upright
        bi = Math.round(ang / (6.283185307 / atlas.buckets));
        bi = ((bi % atlas.buckets) + atlas.buckets) % atlas.buckets;
        spr = atlas.canvases[bi];
        mmStampSprite(ctx, vp, spr, x, y, atlas.dim * (sz / atlas.sh), 0);
      } else {                                          // fallback: rotate the full source per stamp
        mmStampSprite(ctx, vp, img, x, y, sz, ang);
      }
    }
    // giant center
    var gh = reg.h * 1.43 * c;
    if (gh > 2) {
      mmStampSprite(ctx, vp, img, cx, cy, gh, mmScatterGiantAngle(phase, p));
    }
```

(The `sc`, `dd`, `gsc` locals are no longer used; they were declared in the `var` line at the top of the loop scope — leaving the unused names is harmless, but you may drop `sc`, `dd` from the `var parts = ...` declaration line if present. Do not remove `bi`, `spr`, `x`, `y`, `ang`, `sz`.)

- [ ] **Step 3b: Wire `index.html`**

Add the script tag after `js/transitions.js` (currently `index.html:24`):

```html
  <script src="/js/transitions.js"></script>
  <script src="/js/mesh-viewport.js"></script>
```

Mesh in-canvas call site (`index.html:674`) — add `canvas.width, canvas.height`:

```js
									mmDrawScatter(ctx, stc.effect.params, stc.effect.phase, stc.effect.front,
										it.meshGlobal[0], it.meshGlobal[1], it.meshQuad, stc.effect.scope,
										playback.seed | 0, mmSprite(mmScatterSpriteUrl(stc.effect.params && stc.effect.params.sprite)),
										it.backgroundColor || '#000000', canvas.width, canvas.height);
```

Overlay call site (`index.html:1075-1078`) — append `, cvm.width, cvm.height` (the overlay canvas `cvm`, sized to `window.innerWidth/Height`, matching the `mm` transform at line 1060). The current call is:

```js
						mmDrawScatter(cmx, st.effect.params, st.effect.phase, st.effect.front,
							GWm, GHm, quad, st.effect.scope, playback.seed | 0,
							mmSprite(mmScatterSpriteUrl(st.effect.params && st.effect.params.sprite)),
							item.backgroundColor || '#000000');
```

Change the last line to append the two canvas dims:

```js
							item.backgroundColor || '#000000', cvm.width, cvm.height);
```

(Only the trailing two arguments are added — `GWm, GHm, quad, st.effect.scope` stay verbatim.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/unit/js/test_scatter.js tests/unit/js/test_mesh_viewport.js`
Expected: PASS — existing scatter tests still green (they pass `quad === null` ⇒ `vp` null ⇒ legacy path, so `c.imgs`/`c.rots` are unchanged), plus the two new culling tests.

Then verify the iPad-1 ES5 constraint and the full JS suite:

Run: `node --check js/mesh-viewport.js && node --check js/transitions.js && python pytest_runner.py --js`
Expected: parse OK for both files; full JS suite passes.

- [ ] **Step 5: Commit**

```bash
git add js/transitions.js js/mesh-viewport.js index.html tests/unit/js/test_scatter.js
git commit -m "feat(scatter): adopt screen-local viewport (cull copies, bound giant)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: iPad-1 on-wall A/B sign-off

**Files:** none (manual acceptance on the physical wall).

**Interfaces:** Consumes the deployed Tasks 1-3 (`OEB Sign 1` group, `Scatter Demo` playlist).

This is the acceptance gate from the spec and the decision point for the reserved giant size-cap. It requires a server restart + fleet reload, which the **user must authorize** — do not deploy unprompted.

- [ ] **Step 1: Deploy** — on user authorization, restart the server and have the fleet reload so the iPad-1 clients pick up `js/mesh-viewport.js` + the new `js/transitions.js`.

- [ ] **Step 2: Run the Scatter Demo** on `OEB Sign 1` (`scope: wall`, `count: 40`, sprite `hop`).

- [ ] **Step 3: Verify copy culling** — scatter is visibly lighter than before (the ~6× per-screen copy reduction); the burst still shows the full complement of copies across the wall (all screens share the seed). PASS/FAIL.

- [ ] **Step 4: Verify the giant** — is the choppiness resolved? PASS = device-bounded source-subrect draw fixed it (WebKit was over-rasterizing). FAIL = the giant is still heavy ⇒ WebKit was already clipping and the giant's cost is inherent ⇒ escalate to the user to decide on the **reserved giant size-cap** (a follow-up spec, not in this plan).

- [ ] **Step 5: Record the outcome** in the conversation and (if PASS) mark the feature ready to finish via `superpowers:finishing-a-development-branch`.

---

## Plan Self-Review

**1. Spec coverage:**
- Architecture / new module `js/mesh-viewport.js` → Tasks 1-2. ✓
- `mmMeshViewport` fields (`globalRect`, `scale`, `m`, `intersects`, `toDevice`) → Task 1. ✓
- `mmStampSprite` (cull + device-scale + source-subrect bounding) → Task 2. ✓
- Scatter adoption (copies + giant via stamp; atlas retained; scope screen/wall; disc unchanged) → Task 3. ✓
- No server change → confirmed (viewport derived from existing payload). ✓
- Quality-neutral → enforced by not changing giant size/spin/disc. ✓
- Testing (node for viewport + stamp + scatter culling; on-wall A/B) → Tasks 1-4. ✓
- Honest giant caveat + reserved size-cap as the A/B decision → Task 4 Step 4. ✓
- Out of scope (plasma/field, beerfill migration, size-cap param) → not built. ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; the one "read first" note (overlay call site) is a precision instruction, not a placeholder. ✓

**3. Type consistency:** `vp` shape (`m`, `globalRect{x,y,w,h}`, `scale`, `toDevice`, `intersects`) is identical across Tasks 1-3. `mmStampSprite(ctx, vp, img, gx, gy, globalSize, angle)` signature identical in Tasks 2-3. `mmDrawScatter`'s new trailing `canvasW, canvasH` consistent across `transitions.js`, the tests, and both `index.html` call sites. ✓
