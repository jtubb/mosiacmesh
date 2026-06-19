/**
 * spirograph: seeded hypotrochoid (gear params R,r,d from the seed -> a
 * different figure each run); tMs traces + slowly rotates it.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));
const W = 1024, H = 768;

test('spirograph — deterministic at same (tMs, seed)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.spirograph(a, 5000, W, H, 0, 42);
  byKey.spirograph(b, 5000, W, H, 0, 42);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('spirograph — animates (different tMs differs)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.spirograph(a, 1000, W, H, 0, 42);
  byKey.spirograph(b, 4000, W, H, 0, 42);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('spirograph — seeded (different seed differs)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.spirograph(a, 5000, W, H, 0, 1);
  byKey.spirograph(b, 5000, W, H, 0, 2);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('spirograph — traces 500 segments', () => {
  const c = makeRecordingCtx();
  byKey.spirograph(c, 5000, W, H, 0, 42);
  assert.equal(c.__ops.filter((o) => o.op === 'moveTo').length, 1);
  assert.equal(c.__ops.filter((o) => o.op === 'lineTo').length, 500);
});
