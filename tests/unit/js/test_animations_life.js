/**
 * Conway helpers behind gameOfLife. mmLifeStep is one pure toroidal generation
 * (tested with a blinker — the real rule check). mmPrecomputeLife builds the
 * G-board cycle from MM_RNG(seed): deterministic + seeded, the sync guarantee.
 */
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/animations.js');
const { mmLifeStep, mmPrecomputeLife } = globalThis;

test('mmLifeStep — blinker oscillates horizontal -> vertical', () => {
  var GW = 5, GH = 5;
  var b = new Uint8Array(GW * GH);
  b[2 * 5 + 1] = 1; b[2 * 5 + 2] = 1; b[2 * 5 + 3] = 1;
  var n = mmLifeStep(b, GW, GH);
  var expected = new Uint8Array(GW * GH);
  expected[1 * 5 + 2] = 1; expected[2 * 5 + 2] = 1; expected[3 * 5 + 2] = 1;
  assert.deepStrictEqual(n, expected);
});

test('mmPrecomputeLife — dimensions G*GW*GH', () => {
  assert.equal(mmPrecomputeLife(42, 8, 6, 5).length, 5 * 8 * 6);
});

test('mmPrecomputeLife — deterministic for a seed', () => {
  assert.deepStrictEqual(mmPrecomputeLife(42, 16, 12, 10), mmPrecomputeLife(42, 16, 12, 10));
});

test('mmPrecomputeLife — different seeds differ', () => {
  assert.notDeepStrictEqual(mmPrecomputeLife(1, 16, 12, 10), mmPrecomputeLife(2, 16, 12, 10));
});

test('mmPrecomputeLife — gen 0 ~35% density and it evolves', () => {
  var GW = 32, GH = 32, cells = GW * GH, i;
  var b = mmPrecomputeLife(42, GW, GH, 3);
  var alive = 0;
  for (i = 0; i < cells; i++) { alive += b[i]; }
  var frac = alive / cells;
  assert.ok(frac > 0.25 && frac < 0.45, 'gen0 density ' + frac);
  assert.notDeepStrictEqual(b.subarray(cells, 2 * cells), b.subarray(0, cells));
});
