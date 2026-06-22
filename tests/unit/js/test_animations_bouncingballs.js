/**
 * bouncingBalls: a FIELD animation — four soft colored glows drift on sinusoidal
 * paths and their light adds where they overlap, rendered as one scaled blit
 * (smooth on the iPad-1). shade() is a pure function of tMs -> RGBA buffer; tested
 * without a DOM: determinism, motion over time, and that color is actually present.
 */
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/animations.js');
const e = globalThis.MM_ANIMATIONS.find((a) => a.key === 'bouncingBalls');
const C = e.grid.cols, R = e.grid.rows;
function buf(tMs) { const d = new Uint8ClampedArray(C * R * 4); e.shade(d, C, R, tMs, 0, 0); return d; }

test('bouncingBalls — declares a field grid + smoothing, auto-wrapped to draw()', () => {
  assert.equal(typeof e.shade, 'function');
  assert.equal(typeof e.draw, 'function');
  assert.equal(e.smooth, true);
});

test('bouncingBalls — deterministic at same tMs', () => {
  assert.deepStrictEqual(buf(2500), buf(2500));
});

test('bouncingBalls — animates (orbs drift over time)', () => {
  assert.notDeepStrictEqual(buf(500), buf(5000));
});

test('bouncingBalls — paints colored glows (not an empty field)', () => {
  const d = buf(2500);
  let lit = 0;
  for (let i = 0; i < d.length; i += 4) if (d[i] + d[i + 1] + d[i + 2] > 80) lit++;
  assert.ok(lit > 0, 'expected lit colored orbs');
  assert.ok(lit < C * R, 'orbs should be localized, not fill the whole field');
});
