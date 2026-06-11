/**
 * lissajous: a morphing parametric curve. The three guarantees:
 *   1. determinism — same tMs ⇒ identical draw-op log (sync property)
 *   2. animates    — different tMs ⇒ different log (not a static frame)
 *   3. draws       — non-empty log (it actually renders)
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));

const W = 1024, H = 768;

test('lissajous — deterministic at same tMs', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.lissajous(a, 12345, W, H);
  byKey.lissajous(b, 12345, W, H);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('lissajous — animates (different tMs ⇒ different output)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.lissajous(a, 1000, W, H);
  byKey.lissajous(b, 9000, W, H);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('lissajous — draws something', () => {
  const c = makeRecordingCtx();
  byKey.lissajous(c, 5000, W, H);
  assert.ok(c.__ops.length > 0, 'expected a non-empty draw-op log');
});
