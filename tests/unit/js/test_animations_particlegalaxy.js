/**
 * particleGalaxy: N=400 particles on golden-ratio-spread Keplerian orbits,
 * drawn as 2px fillRect dots. Determinism, animates, exact dot count.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));

const W = 1024, H = 768;

test('particleGalaxy — deterministic at same tMs', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.particleGalaxy(a, 22222, W, H);
  byKey.particleGalaxy(b, 22222, W, H);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('particleGalaxy — animates (different tMs ⇒ different output)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.particleGalaxy(a, 1000, W, H);
  byKey.particleGalaxy(b, 9000, W, H);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('particleGalaxy — draws 400 particles', () => {
  const c = makeRecordingCtx();
  byKey.particleGalaxy(c, 5000, W, H);
  const rects = c.__ops.filter((o) => o.op === 'fillRect').length;
  assert.equal(rects, 400);
});
