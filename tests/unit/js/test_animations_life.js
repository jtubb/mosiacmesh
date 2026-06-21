/**
 * Conway helper behind gameOfLife. mmLifeStep is one pure toroidal generation
 * (tested with a blinker — the real rule check). The board cycle is now built
 * incrementally inside the gameOfLife draw closure (see test_animations_gameoflife.js);
 * the old all-at-once mmPrecomputeLife was removed.
 */
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/animations.js');
const { mmLifeStep } = globalThis;

test('mmLifeStep — blinker oscillates horizontal -> vertical', () => {
  var GW = 5, GH = 5;
  var b = new Uint8Array(GW * GH);
  b[2 * 5 + 1] = 1; b[2 * 5 + 2] = 1; b[2 * 5 + 3] = 1;
  var n = mmLifeStep(b, GW, GH);
  var expected = new Uint8Array(GW * GH);
  expected[1 * 5 + 2] = 1; expected[2 * 5 + 2] = 1; expected[3 * 5 + 2] = 1;
  assert.deepStrictEqual(n, expected);
});
