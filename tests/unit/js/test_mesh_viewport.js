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
