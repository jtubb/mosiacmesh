import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/transitions.js');
const g = globalThis;

test('mmFrostPhase: out=cover, in=reveal', () => {
  assert.equal(g.mmFrostPhase('out'), 'cover');
  assert.equal(g.mmFrostPhase('in'), 'reveal');
});

test('mmFrostField: deterministic per seed, values in [0,1), right length', () => {
  const a = g.mmFrostField(12, 99), b = g.mmFrostField(12, 99), c = g.mmFrostField(12, 100);
  assert.deepEqual(a, b);                 // same seed -> identical (wall-coherent)
  assert.notDeepEqual(a, c);              // different seed -> different
  assert.equal(a.length, 144);
  a.forEach(v => assert.ok(v >= 0 && v < 1, 'value in [0,1): ' + v));
});

test('mmFrostField: spatially correlated (smoother than random pairs)', () => {
  const blocks = 16, field = g.mmFrostField(blocks, 12345);
  let adjSum = 0, adjN = 0, r, c;
  for (r = 0; r < blocks; r++) for (c = 0; c < blocks - 1; c++) {
    adjSum += Math.abs(field[r * blocks + c] - field[r * blocks + c + 1]); adjN++;
  }
  const adjMean = adjSum / adjN;
  let rndSum = 0, rndN = 0, i;
  for (i = 0; i < 500; i++) {
    rndSum += Math.abs(field[(i * 7) % field.length] - field[(i * 13 + 3) % field.length]); rndN++;
  }
  const rndMean = rndSum / rndN;
  // smoothing -> adjacent cells much closer than arbitrary pairs
  assert.ok(adjMean < rndMean * 0.8, 'adjacent ' + adjMean + ' should be < 0.8 * random ' + rndMean);
});

test('mmFrostBlotch: off below threshold, grows 0->1 above (clamped)', () => {
  assert.deepEqual(g.mmFrostBlotch(0.5, 0.4, 0.25), { on: false, t: 0 });
  let b = g.mmFrostBlotch(0.5, 0.5, 0.25); assert.ok(b.on && Math.abs(b.t - 0) < 1e-9);
  b = g.mmFrostBlotch(0.5, 0.625, 0.25); assert.ok(Math.abs(b.t - 0.5) < 1e-9);   // halfway through grow
  b = g.mmFrostBlotch(0.5, 0.95, 0.25);  assert.ok(b.on && Math.abs(b.t - 1) < 1e-9);  // clamped
});
