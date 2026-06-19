/**
 * fireworks: time-slotted bursts. Each ~800ms slot's burst params come from
 * mmDeriveSeed(seed, slotIndex) — deterministic, non-repeating, synced.
 * tMs=900 puts slot 0 (launched at t0=0) mid-explosion (dt=900, et=450).
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));
const W = 1024, H = 768;

test('fireworks — deterministic at same (tMs, seed)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.fireworks(a, 900, W, H, 0, 42);
  byKey.fireworks(b, 900, W, H, 0, 42);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('fireworks — animates (different tMs differs)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.fireworks(a, 900, W, H, 0, 42);
  byKey.fireworks(b, 1100, W, H, 0, 42);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('fireworks — seeded (different seed differs)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.fireworks(a, 900, W, H, 0, 1);
  byKey.fireworks(b, 900, W, H, 0, 2);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('fireworks — explosion draws a bounded particle count', () => {
  const c = makeRecordingCtx();
  byKey.fireworks(c, 900, W, H, 0, 42);
  const rects = c.__ops.filter((o) => o.op === 'fillRect').length;
  assert.ok(rects > 0 && rects <= 150, `unexpected particle count ${rects}`);
});
