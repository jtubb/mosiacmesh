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
