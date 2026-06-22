/**
 * stadiumWave: a grid-aware mesh animation — a light front sweeps the screen grid,
 * each cell lighting as it passes. Pure function of (tMs, seed, grid). Tests:
 * determinism, animation over time, one fillRect per cell (cols*rows), grid-
 * awareness, and a null-grid (mirror) fallback that doesn't throw.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const stadiumWave = globalThis.MM_ANIMATIONS.find((a) => a.key === 'stadiumWave').draw;
const W = 4975, H = 4405;
const GRID = { cols: 6, rows: 4 };

test('stadiumWave — deterministic at same (tMs, seed, grid)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  stadiumWave(a, 1000, W, H, 0, 5, GRID);
  stadiumWave(b, 1000, W, H, 0, 5, GRID);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('stadiumWave — fills exactly cols*rows cells', () => {
  const c = makeRecordingCtx();
  stadiumWave(c, 1000, W, H, 0, 5, GRID);
  const rects = c.__ops.filter((o) => o.op === 'fillRect').length;
  assert.equal(rects, GRID.cols * GRID.rows);
});

test('stadiumWave — animates (front advances ⇒ different output)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  stadiumWave(a, 200, W, H, 0, 5, GRID);
  stadiumWave(b, 1500, W, H, 0, 5, GRID);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('stadiumWave — grid-aware (different grid ⇒ different cell count)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  stadiumWave(a, 1000, W, H, 0, 5, { cols: 6, rows: 4 });
  stadiumWave(b, 1000, W, H, 0, 5, { cols: 4, rows: 3 });
  const ra = a.__ops.filter((o) => o.op === 'fillRect').length;
  const rb = b.__ops.filter((o) => o.op === 'fillRect').length;
  assert.equal(ra, 24);
  assert.equal(rb, 12);
});

test('stadiumWave — null grid (mirror) falls back to 1x1 without throwing', () => {
  const c = makeRecordingCtx();
  assert.doesNotThrow(() => stadiumWave(c, 1000, 768, 928, 0, 5, null));
  assert.equal(c.__ops.filter((o) => o.op === 'fillRect').length, 1);
});
