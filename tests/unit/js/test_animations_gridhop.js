/**
 * gridHop: a grid-aware mesh animation — a stick figure that snakes cell-to-cell
 * across the physical screen grid. draw(ctx,tMs,w,h,nowMs,seed,grid) is a pure
 * function of (tMs, seed, grid), the cross-screen sync guarantee. Tests use the
 * recording-ctx stub: determinism, animation over time, grid-awareness (a
 * different grid relocates the figure), and a null-grid (mirror) fallback that
 * doesn't throw.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const gridHop = globalThis.MM_ANIMATIONS.find((a) => a.key === 'gridHop').draw;
const W = 4975, H = 4405;                 // OEB-scale global wall canvas
const GRID = { cols: 6, rows: 4 };

test('gridHop — deterministic at same (tMs, seed, grid)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  gridHop(a, 1234, W, H, 0, 7, GRID);
  gridHop(b, 1234, W, H, 0, 7, GRID);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('gridHop — animates (different tMs ⇒ different output)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  gridHop(a, 200, W, H, 0, 7, GRID);     // mid-hop
  gridHop(b, 1700, W, H, 0, 7, GRID);    // a couple hops later
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('gridHop — grid-aware (different grid relocates the figure)', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  gridHop(a, 400, W, H, 0, 7, { cols: 6, rows: 4 });
  gridHop(b, 400, W, H, 0, 7, { cols: 3, rows: 2 });
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('gridHop — seed varies the path/colorway', () => {
  const a = makeRecordingCtx(), b = makeRecordingCtx();
  gridHop(a, 400, W, H, 0, 11, GRID);
  gridHop(b, 400, W, H, 0, 22, GRID);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('gridHop — null grid (mirror) falls back to 1x1 without throwing', () => {
  const c = makeRecordingCtx();
  assert.doesNotThrow(() => gridHop(c, 400, 768, 928, 0, 7, null));
  assert.ok(c.__ops.length > 0, 'should still draw a figure on a single screen');
});
