/**
 * gameOfLife (incremental): gen 0 is seeded on the first frame, so tMs=0 renders
 * the live board deterministically + seeded (the cross-screen sync guarantee).
 * A far-ahead gen on a fresh seed isn't computed yet in a single call, so it
 * renders a seeded coordinated noise grid. Each test uses a DISTINCT seed so the
 * shared draw-closure cache resets deterministically regardless of call order.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));
const W = 1024, H = 768;
const GW = 48, GH = 36;

test('gameOfLife — gen 0 deterministic at same (tMs=0, seed)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.gameOfLife(a, 0, W, H, 0, 42);
  byKey.gameOfLife(b, 0, W, H, 0, 42);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('gameOfLife — gen 0 seeded (different seed differs)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  byKey.gameOfLife(a, 0, W, H, 0, 101);
  byKey.gameOfLife(b, 0, W, H, 0, 202);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('gameOfLife — gen 0 draws a bounded number of live cells', () => {
  const c = makeRecordingCtx();
  byKey.gameOfLife(c, 0, W, H, 0, 303);
  const rects = c.__ops.filter((o) => o.op === 'fillRect').length;
  assert.ok(rects > 0 && rects <= GW * GH, `unexpected live count ${rects}`);
});

test('gameOfLife — far-ahead gen on a fresh seed renders seeded noise', () => {
  // G=300, 100ms/gen. tMs for gen 250 is way past STEP_PER_FRAME(=12) computed
  // in a single call with a fresh seed, so it hits the noise branch (#3a5a3a).
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  const tFar = 250 * 100;
  byKey.gameOfLife(a, tFar, W, H, 0, 404);
  byKey.gameOfLife(b, tFar, W, H, 0, 404);
  assert.deepStrictEqual(a.__ops, b.__ops, 'noise must be deterministic for same (seed, tMs)');
  const fills = a.__ops.filter((o) => o.set === 'fillStyle').map((o) => o.value);
  assert.ok(fills.includes('#3a5a3a'), 'expected the warming-up noise tint');
  const rects = a.__ops.filter((o) => o.op === 'fillRect').length;
  assert.ok(rects > 0 && rects <= GW * GH, `unexpected noise cell count ${rects}`);
});

test('gameOfLife — noise differs from the live board for the same seed', () => {
  // Fresh seed: tMs=0 renders the green board; a far-ahead gen renders dim noise.
  const board = makeRecordingCtx(), noise = makeRecordingCtx();
  byKey.gameOfLife(board, 0, W, H, 0, 505);
  byKey.gameOfLife(noise, 250 * 100, W, H, 0, 606);
  const boardFills = board.__ops.filter((o) => o.set === 'fillStyle').map((o) => o.value);
  const noiseFills = noise.__ops.filter((o) => o.set === 'fillStyle').map((o) => o.value);
  assert.ok(boardFills.includes('#7CFC00'), 'gen 0 should use the live-cell green');
  assert.ok(noiseFills.includes('#3a5a3a'), 'far-ahead gen should use the noise tint');
});
