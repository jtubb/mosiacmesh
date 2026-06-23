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
