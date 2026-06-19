/**
 * truchet: seeded grid of quarter-arc tiles (a different "maze" each run);
 * tMs animates only the hue/highlight (arcs static -> trivially pure).
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));
const W = 1024, H = 768;

test('truchet — deterministic at same (tMs, seed)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.truchet(a, 5000, W, H, 0, 42);
  byKey.truchet(b, 5000, W, H, 0, 42);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('truchet — animates (different tMs differs via hue/highlight)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.truchet(a, 1000, W, H, 0, 42);
  byKey.truchet(b, 4000, W, H, 0, 42);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('truchet — seeded (different seed differs)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.truchet(a, 5000, W, H, 0, 1);
  byKey.truchet(b, 5000, W, H, 0, 2);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('truchet — draws two arcs per grid cell', () => {
  const c = makeRecordingCtx();
  byKey.truchet(c, 5000, W, H, 0, 42);
  const cell = Math.min(W, H) / 8;
  const GW = Math.round(W / cell), GH = Math.round(H / cell);
  const arcs = c.__ops.filter((o) => o.op === 'arc').length;
  assert.equal(arcs, GW * GH * 2);
});
