/**
 * starfield: seeded warp-stars. Seed fixes the star directions/phases (so the
 * field differs per run, identical across screens); tMs drives the outward warp.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));
const W = 1024, H = 768;

test('starfield — deterministic at same (tMs, seed)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.starfield(a, 5000, W, H, 0, 42);
  byKey.starfield(b, 5000, W, H, 0, 42);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('starfield — animates (different tMs differs)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.starfield(a, 1000, W, H, 0, 42);
  byKey.starfield(b, 4000, W, H, 0, 42);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('starfield — seeded (different seed differs)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.starfield(a, 5000, W, H, 0, 1);
  byKey.starfield(b, 5000, W, H, 0, 2);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('starfield — draws a bounded number of streaks', () => {
  const c = makeRecordingCtx();
  byKey.starfield(c, 5000, W, H, 0, 42);
  const strokes = c.__ops.filter((o) => o.op === 'stroke').length;
  assert.ok(strokes > 0 && strokes <= 200, `unexpected streak count ${strokes}`);
});
