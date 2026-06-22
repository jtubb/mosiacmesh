/**
 * plasma: a FIELD animation. shade() fills a 40x30 RGBA buffer from a
 * sum-of-sines color field and the framework scales it (smoothed) to the
 * canvas with one blit — no per-cell fillRect/'hsl()' string (the old hot path
 * that blocked the iPad-1 thread and delayed item transitions). The
 * sync-critical property is determinism of the field, so we test shade()
 * directly: pure, no DOM, returns the same buffer for the same (tMs, seed).
 */
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/animations.js');
const plasma = globalThis.MM_ANIMATIONS.find((a) => a.key === 'plasma');
const COLS = 40, ROWS = 30;

function shadeBuf(tMs, seed) {
  const data = new Uint8ClampedArray(COLS * ROWS * 4);
  plasma.shade(data, COLS, ROWS, tMs, 0, seed);
  return data;
}

test('plasma — declares a 40x30 field grid + smoothing, auto-wrapped to draw()', () => {
  assert.deepStrictEqual(plasma.grid, { cols: COLS, rows: ROWS });
  assert.equal(plasma.smooth, true);
  assert.equal(typeof plasma.shade, 'function');
  assert.equal(typeof plasma.draw, 'function');   // framework auto-wraps shade()
});

test('plasma — deterministic at same (tMs, seed)', () => {
  assert.deepStrictEqual(shadeBuf(31415, 7), shadeBuf(31415, 7));
});

test('plasma — animates (different tMs ⇒ different field)', () => {
  assert.notDeepStrictEqual(shadeBuf(1000, 7), shadeBuf(6000, 7));
});

test('plasma — same seed deterministic, different seed differs', () => {
  assert.deepStrictEqual(shadeBuf(5000, 111), shadeBuf(5000, 111));
  assert.notDeepStrictEqual(shadeBuf(5000, 111), shadeBuf(5000, 222));
});

test('plasma — colors the whole 40x30 field (full-saturation, rarely black)', () => {
  const data = shadeBuf(5000, 1);
  let nonzero = 0;
  for (let i = 0; i < data.length; i += 4) {
    if (data[i] || data[i + 1] || data[i + 2]) nonzero++;
  }
  assert.ok(nonzero > COLS * ROWS * 0.5, `expected most cells colored, got ${nonzero}`);
});
