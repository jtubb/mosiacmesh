/**
 * hyperTunnel: a full-bleed field animation. shade() fills an RGBA buffer with a
 * perspective tunnel scrolling inward. Pure function of (tMs, seed) -> buffer
 * (the cross-screen sync guarantee), tested without a DOM.
 */
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/animations.js');
const e = globalThis.MM_ANIMATIONS.find((a) => a.key === 'hyperTunnel');
const C = e.grid.cols, R = e.grid.rows;
function buf(tMs, seed) { const d = new Uint8ClampedArray(C * R * 4); e.shade(d, C, R, tMs, 0, seed); return d; }
function colored(d) { let n = 0; for (let i = 0; i < d.length; i += 4) if (d[i] || d[i+1] || d[i+2]) n++; return n; }

test('hyperTunnel — declares a field grid + smoothing, auto-wrapped to draw()', () => {
  assert.equal(typeof e.shade, 'function');
  assert.equal(typeof e.draw, 'function');
  assert.equal(e.smooth, true);
});
test('hyperTunnel — deterministic at same (tMs, seed)', () => {
  assert.deepStrictEqual(buf(2000, 3), buf(2000, 3));
});
test('hyperTunnel — animates (scrolls inward over time)', () => {
  assert.notDeepStrictEqual(buf(500, 3), buf(2500, 3));
});
test('hyperTunnel — seed varies the colorway', () => {
  assert.notDeepStrictEqual(buf(2000, 1), buf(2000, 2));
});
test('hyperTunnel — colors most of the field', () => {
  assert.ok(colored(buf(2000, 1)) > C * R * 0.5);
});
