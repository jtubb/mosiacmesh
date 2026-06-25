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

test('mmTransitionState: frostcreep end=cover (rises), start=reveal (mask family)', () => {
  const S = g.mmTransitionState;
  const end = { name: 'frostcreep', params: { duration: 2000, scope: 'wall' } };
  // end window [4000,6000]; offset 4500 -> p=(6000-4500)/2000=0.75 -> flp=1-p=0.25 -> cover=0.25
  let st = S(null, end, 4500, 6000, null, null);
  assert.equal(st.role, 'out');
  assert.equal(st.effect.name, 'frostcreep');
  assert.equal(st.effect.family, 'mask');
  assert.equal(st.effect.phase, 'cover');
  assert.ok(Math.abs(st.effect.front - 0.25) < 1e-9);
  assert.equal(st.effect.scope, 'wall');
  // later in the window -> MORE frost (rises): offset 5500 -> p=0.25 -> flp=0.75 -> cover=0.75
  st = S(null, end, 5500, 6000, null, null);
  assert.ok(Math.abs(st.effect.front - 0.75) < 1e-9);
  // start role (reveal): offset 500 -> p=0.25 -> flp=0.25 -> cover=1-0.25=0.75
  const start = { name: 'frostcreep', params: { duration: 2000 } };
  st = S(start, null, 500, 6000, null, null);
  assert.equal(st.effect.phase, 'reveal');
  assert.ok(Math.abs(st.effect.front - 0.75) < 1e-9);
  assert.equal(st.effect.scope, 'wall');     // default
});
