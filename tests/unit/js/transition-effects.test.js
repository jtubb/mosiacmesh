import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/transitions.js');
const S = globalThis.mmTransitionState;

const Slide = globalThis.mmSlideOffset;

test('mmSlideOffset: front 0 fully off-edge, front 1 in place', () => {
  assert.deepEqual(Slide(0, 'left', 200, 100), { dx: 200, dy: 0 });
  assert.deepEqual(Slide(1, 'left', 200, 100), { dx: 0, dy: 0 });
});
test('mmSlideOffset: direction signs', () => {
  assert.deepEqual(Slide(0.5, 'left', 200, 100), { dx: 100, dy: 0 });
  assert.deepEqual(Slide(0.5, 'right', 200, 100), { dx: -100, dy: 0 });
  assert.deepEqual(Slide(0.5, 'up', 200, 100), { dx: 0, dy: 50 });
  assert.deepEqual(Slide(0.5, 'down', 200, 100), { dx: 0, dy: -50 });
});

const Zoom = globalThis.mmZoomFactor;

test('mmZoomFactor: scale ramps to 1, alpha ramps to 1', () => {
  assert.deepEqual(Zoom(0, 0.6), { s: 0.6, alpha: 0 });
  assert.deepEqual(Zoom(1, 0.6), { s: 1, alpha: 1 });
  const mid = Zoom(0.5, 0.6);
  assert.ok(Math.abs(mid.s - 0.8) < 1e-9 && Math.abs(mid.alpha - 0.5) < 1e-9);
});
test('mmZoomFactor: default scale 0.6 when omitted', () => {
  assert.deepEqual(Zoom(0, null), { s: 0.6, alpha: 0 });
});

const Iris = globalThis.mmIrisCircle;
const QUAD = [[0.5, 0], [1, 0], [1, 1], [0.5, 1]]; // right half, full height

test('mmIrisCircle: wall scope centered, radius 0->halfDiagonal', () => {
  assert.deepEqual(Iris(0, 200, 100, 'wall', null), { cx: 100, cy: 50, r: 0 });
  const full = Iris(1, 200, 100, 'wall', null);
  assert.ok(full.cx === 100 && full.cy === 50);
  assert.ok(Math.abs(full.r - Math.sqrt(100 * 100 + 50 * 50)) < 1e-9);
});
test('mmIrisCircle: screen scope centers on the panel bbox', () => {
  const c = Iris(0.5, 200, 100, 'screen', QUAD);
  assert.ok(Math.abs(c.cx - 150) < 1e-9 && Math.abs(c.cy - 50) < 1e-9);
  assert.ok(Math.abs(c.r - 0.5 * Math.sqrt(50 * 50 + 50 * 50)) < 1e-9);
});
