/**
 * radialPulse: K=5 concentric rings expanding from center, fading out.
 * Determinism (sync), animates, and draws exactly K stroked rings.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));

const W = 1024, H = 768;

test('radialPulse — deterministic at same tMs', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.radialPulse(a, 12345, W, H);
  byKey.radialPulse(b, 12345, W, H);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('radialPulse — animates (different tMs ⇒ different output)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.radialPulse(a, 1000, W, H);
  byKey.radialPulse(b, 3000, W, H);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('radialPulse — strokes 5 rings', () => {
  const c = makeRecordingCtx();
  byKey.radialPulse(c, 5000, W, H);
  const arcs = c.__ops.filter((o) => o.op === 'arc').length;
  assert.equal(arcs, 5);
});
