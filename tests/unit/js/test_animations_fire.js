/**
 * fire: a full-bleed STATELESS field animation (procedural sin-turbulence over a
 * bottom-hot gradient). shade() is a pure function of (tMs, seed) -> buffer, so
 * late-joining screens match without shared history. Tested without a DOM.
 */
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/animations.js');
const e = globalThis.MM_ANIMATIONS.find((a) => a.key === 'fire');
const C = e.grid.cols, R = e.grid.rows;
function buf(tMs, seed) { const d = new Uint8ClampedArray(C * R * 4); e.shade(d, C, R, tMs, 0, seed); return d; }
function rowRed(d, gy) { let s = 0; for (let gx = 0; gx < C; gx++) s += d[(gy * C + gx) * 4]; return s; }

test('fire — deterministic at same (tMs, seed)', () => {
  assert.deepStrictEqual(buf(1500, 4), buf(1500, 4));
});
test('fire — animates (flames flicker upward over time)', () => {
  assert.notDeepStrictEqual(buf(400, 4), buf(1800, 4));
});
test('fire — hot at the bottom, cool at the top', () => {
  const d = buf(1500, 4);
  assert.ok(rowRed(d, R - 1) > rowRed(d, 0), 'bottom row should be hotter (more red) than top');
});
test('fire — stateless: value at tMs is independent of call history', () => {
  buf(99999, 4);                       // "advance" — but shade keeps no state
  assert.deepStrictEqual(buf(1500, 4), buf(1500, 4));
});
