/**
 * ballLights ("Roaming spotlight"): a FIELD animation — a soft radial glow bounces
 * around the wall, rendered as one scaled blit (smooth on the iPad-1, no tracked
 * vector sprite). shade() is a pure function of (tMs, seed) -> RGBA buffer; tested
 * without a DOM: determinism, motion over time, seed-varied path/color, and that
 * the glow is localized (not a full-bright or all-black field).
 */
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/animations.js');
const e = globalThis.MM_ANIMATIONS.find((a) => a.key === 'ballLights');
const C = e.grid.cols, R = e.grid.rows;
function buf(tMs, seed) { const d = new Uint8ClampedArray(C * R * 4); e.shade(d, C, R, tMs, 0, seed); return d; }
function brightCells(d) { let n = 0; for (let i = 0; i < d.length; i += 4) if (d[i] + d[i + 1] + d[i + 2] > 120) n++; return n; }

test('ballLights — declares a field grid + smoothing, auto-wrapped to draw()', () => {
  assert.equal(typeof e.shade, 'function');
  assert.equal(typeof e.draw, 'function');
  assert.equal(e.smooth, true);
});

test('ballLights — deterministic at same (tMs, seed)', () => {
  assert.deepStrictEqual(buf(3000, 9), buf(3000, 9));
});

test('ballLights — animates (glow moves over time)', () => {
  assert.notDeepStrictEqual(buf(1000, 9), buf(4000, 9));
});

test('ballLights — seed varies trajectory/colorway', () => {
  assert.notDeepStrictEqual(buf(3000, 1), buf(3000, 2));
});

test('ballLights — glow is localized (a spotlight, not full-bright or all-black)', () => {
  const lit = brightCells(buf(3000, 9));
  assert.ok(lit > 0, 'expected a lit glow region');
  assert.ok(lit < C * R, 'glow should not cover the entire field');
});
