/**
 * pendulumWave: N=16 pendulums with staggered periods that scramble and
 * re-sync over minutes. Determinism, animates, one bob (arc) per pendulum.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));

const W = 1024, H = 768;

test('pendulumWave — deterministic at same tMs', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.pendulumWave(a, 44444, W, H);
  byKey.pendulumWave(b, 44444, W, H);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('pendulumWave — animates (different tMs ⇒ different output)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.pendulumWave(a, 1000, W, H);
  byKey.pendulumWave(b, 5000, W, H);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('pendulumWave — draws 16 bobs', () => {
  const c = makeRecordingCtx();
  byKey.pendulumWave(c, 5000, W, H);
  const arcs = c.__ops.filter((o) => o.op === 'arc').length;
  assert.equal(arcs, 16);
});
