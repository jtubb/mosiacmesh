/**
 * lissajous: a morphing parametric curve. The three guarantees:
 *   1. determinism — same tMs ⇒ identical draw-op log (sync property)
 *   2. animates    — different tMs ⇒ different log (not a static frame)
 *   3. draws       — non-empty log (it actually renders)
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { mirror } from './_animations_mirror.js';
import { makeRecordingCtx } from './_canvas_stub.js';

const W = 1024, H = 768;

test('lissajous — deterministic at same tMs', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  mirror.lissajous(a, 12345, W, H);
  mirror.lissajous(b, 12345, W, H);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('lissajous — animates (different tMs ⇒ different output)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  mirror.lissajous(a, 1000, W, H);
  mirror.lissajous(b, 9000, W, H);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('lissajous — draws something', () => {
  const c = makeRecordingCtx();
  mirror.lissajous(c, 5000, W, H);
  assert.ok(c.__ops.length > 0, 'expected a non-empty draw-op log');
});
