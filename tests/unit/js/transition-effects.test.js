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
