/**
 * ballLights: a grid-aware mesh animation — a ball bounces across the global wall
 * canvas (deterministic edge bounce) and the cell it occupies flashes. Pure
 * function of (tMs, seed, grid). Tests: determinism, animation over time, grid-
 * awareness, and a null-grid (mirror) fallback that doesn't throw.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const ballLights = globalThis.MM_ANIMATIONS.find((a) => a.key === 'ballLights').draw;
const W = 4975, H = 4405;
const GRID = { cols: 6, rows: 4 };

test('ballLights — deterministic at same (tMs, seed, grid)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  ballLights(a, 3000, W, H, 0, 9, GRID);
  ballLights(b, 3000, W, H, 0, 9, GRID);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('ballLights — animates (ball moves ⇒ different output)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  ballLights(a, 1000, W, H, 0, 9, GRID);
  ballLights(b, 4000, W, H, 0, 9, GRID);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('ballLights — seed varies trajectory/colorway', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  ballLights(a, 3000, W, H, 0, 1, GRID);
  ballLights(b, 3000, W, H, 0, 2, GRID);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('ballLights — grid-aware (different grid ⇒ different lit cell rect)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  ballLights(a, 3000, W, H, 0, 9, { cols: 6, rows: 4 });
  ballLights(b, 3000, W, H, 0, 9, { cols: 2, rows: 2 });
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('ballLights — null grid (mirror) falls back to 1x1 without throwing', () => {
  const c = makeRecordingCtx();
  assert.doesNotThrow(() => ballLights(c, 3000, 768, 928, 0, 9, null));
  assert.ok(c.__ops.length > 0);
});
