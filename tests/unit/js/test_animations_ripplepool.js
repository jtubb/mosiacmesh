/**
 * ripplePool: a full-bleed STATELESS field animation — sum of expanding rings from
 * seeded drops. shade() is a pure function of (tMs, seed) -> buffer. Tested
 * without a DOM: determinism, animation over time, seed-varied drop pattern.
 */
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/animations.js');
const e = globalThis.MM_ANIMATIONS.find((a) => a.key === 'ripplePool');
const C = e.grid.cols, R = e.grid.rows;
function buf(tMs, seed) { const d = new Uint8ClampedArray(C * R * 4); e.shade(d, C, R, tMs, 0, seed); return d; }

test('ripplePool — deterministic at same (tMs, seed)', () => {
  assert.deepStrictEqual(buf(3000, 6), buf(3000, 6));
});
test('ripplePool — animates (waves spread over time)', () => {
  assert.notDeepStrictEqual(buf(1200, 6), buf(3200, 6));
});
test('ripplePool — seed varies the drop pattern', () => {
  assert.notDeepStrictEqual(buf(3000, 6), buf(3000, 7));
});
test('ripplePool — fills the whole field (base water level is non-black)', () => {
  const d = buf(3000, 6);
  let nonzero = 0; for (let i = 0; i < d.length; i += 4) if (d[i] || d[i+1] || d[i+2]) nonzero++;
  assert.ok(nonzero > C * R * 0.8);
});
