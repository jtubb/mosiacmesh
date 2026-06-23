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

const Order = globalThis.mmDissolveOrder;
const Covered = globalThis.mmDissolveCovered;

test('mmDissolveOrder: deterministic permutation per seed', () => {
  const a = Order(16, 42), b = Order(16, 42), c = Order(16, 99);
  assert.deepEqual(a, b);                              // same seed -> same order
  assert.notDeepEqual(a, c);                           // different seed -> different
  assert.deepEqual(a.slice().sort((x, y) => x - y), Array.from({ length: 16 }, (_, i) => i)); // valid perm
});
test('mmDissolveCovered: monotonic, n at front 0, 0 at front 1', () => {
  assert.equal(Covered(0, 256), 256);
  assert.equal(Covered(1, 256), 0);
  assert.ok(Covered(0.25, 256) > Covered(0.75, 256));
});

const SLIDE = { name: 'slide', params: { direction: 'left', scope: 'wall', duration: 1000 } };
const ZOOM  = { name: 'zoom',  params: { scale: 0.6, scope: 'wall', duration: 1000 } };
const IRIS  = { name: 'iris',  params: { scope: 'wall', duration: 1000 } };
const DISS  = { name: 'dissolve', params: { blocks: 16, duration: 1000 } };

test('mmTransitionState: new effects yield an effect descriptor with family + front', () => {
  const s1 = S(SLIDE, null, 250, 1000, null, null);
  assert.equal(s1.effect.name, 'slide');
  assert.equal(s1.effect.family, 'transform');
  assert.ok(Math.abs(s1.effect.front - 0.25) < 1e-9);
  assert.equal(s1.effect.scope, 'wall');
  assert.equal(s1.wipe, null);
  assert.equal(S(ZOOM, null, 250, 1000, null, null).effect.family, 'transform');
  assert.equal(S(IRIS, null, 250, 1000, null, null).effect.family, 'mask');
  assert.equal(S(DISS, null, 250, 1000, null, null).effect.family, 'mask');
});

test('mmTransitionState: fade and wipe descriptors are unchanged (no effect field)', () => {
  const fade = { name: 'fade', params: { duration: 1000 } };
  const wipe = { name: 'wipe', params: { direction: 'down', scope: 'wall', duration: 1000 } };
  const f = S(fade, null, 500, 10000, null, null);
  assert.ok(Math.abs(f.opacity - 0.5) < 1e-9 && !f.effect && f.wipe === null);
  const w = S(wipe, null, 250, 1000, { x: 0, y: 0, w: 1, h: 1 }, null);
  assert.ok(w.wipe && !w.effect);
});
