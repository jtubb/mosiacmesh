/**
 * matrixRain: a full-bleed CRISP field animation — per-column falling bright head
 * with a fading green trail + time-bucketed flicker. shade() is a pure function of
 * (tMs, seed) -> buffer. Tested without a DOM.
 */
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/animations.js');
const e = globalThis.MM_ANIMATIONS.find((a) => a.key === 'matrixRain');
const C = e.grid.cols, R = e.grid.rows;
function buf(tMs, seed) { const d = new Uint8ClampedArray(C * R * 4); e.shade(d, C, R, tMs, 0, seed); return d; }

test('matrixRain — declares a crisp field (smooth:false)', () => {
  assert.equal(e.smooth, false);
});
test('matrixRain — deterministic at same (tMs, seed)', () => {
  assert.deepStrictEqual(buf(2000, 8), buf(2000, 8));
});
test('matrixRain — animates (rain falls over time)', () => {
  assert.notDeepStrictEqual(buf(1000, 8), buf(2000, 8));
});
test('matrixRain — green dominant: streaks are green, never red/blue trails', () => {
  const d = buf(2000, 8);
  let green = 0, red = 0;
  for (let i = 0; i < d.length; i += 4) { if (d[i+1] > 40) green++; if (d[i] > 40 && d[i+1] < 40) red++; }
  assert.ok(green > 0, 'expected lit green cells');
  assert.equal(red, 0, 'no red-only pixels in matrix rain');
});
