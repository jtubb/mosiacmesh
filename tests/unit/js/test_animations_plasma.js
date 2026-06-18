/**
 * plasma: a 40x30 fillRect grid colored by a sum-of-sines field. The
 * synchronization-critical property is determinism of the field; we assert
 * deterministic op log, animation over time, and the exact 40*30 cell count.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));

const W = 1024, H = 768;

test('plasma — deterministic at same tMs', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.plasma(a, 31415, W, H);
  byKey.plasma(b, 31415, W, H);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('plasma — animates (different tMs ⇒ different output)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.plasma(a, 1000, W, H);
  byKey.plasma(b, 6000, W, H);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('plasma — fills a 40x30 grid', () => {
  const c = makeRecordingCtx();
  byKey.plasma(c, 5000, W, H);
  const rects = c.__ops.filter((o) => o.op === 'fillRect').length;
  assert.equal(rects, 1200);
});
