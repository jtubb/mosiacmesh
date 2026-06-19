/**
 * gameOfLife: renders a fillRect per live cell of the precomputed board at
 * gen=floor(tMs/100)%G. The op-log IS the board, so deep-equal proves the
 * board is identical across runs/screens (the sync guarantee).
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));
const W = 1024, H = 768;
const GW = 48, GH = 36;

test('gameOfLife — deterministic at same (tMs, seed)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.gameOfLife(a, 0, W, H, 0, 42);
  byKey.gameOfLife(b, 0, W, H, 0, 42);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('gameOfLife — animates (gen 0 vs gen 20 differ)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.gameOfLife(a, 0, W, H, 0, 42);
  byKey.gameOfLife(b, 2000, W, H, 0, 42);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('gameOfLife — seeded (different seed differs)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.gameOfLife(a, 0, W, H, 0, 1);
  byKey.gameOfLife(b, 0, W, H, 0, 2);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('gameOfLife — gen 0 draws a bounded number of live cells', () => {
  const c = makeRecordingCtx();
  byKey.gameOfLife(c, 0, W, H, 0, 42);
  const rects = c.__ops.filter((o) => o.op === 'fillRect').length;
  assert.ok(rects > 0 && rects <= GW * GH, `unexpected live count ${rects}`);
});

test('gameOfLife — gen wraps at G*100ms back to gen 0', () => {
  // G=300, frame interval 100ms -> tMs=30000 is gen 0 again.
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.gameOfLife(a, 0, W, H, 0, 42);
  byKey.gameOfLife(b, 300 * 100, W, H, 0, 42);
  assert.deepStrictEqual(a.__ops, b.__ops);
});
