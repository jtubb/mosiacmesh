import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/transitions.js');

const Pal = globalThis.mmBeerPalette, Phase = globalThis.mmBeerPhase;
const Dur = globalThis.mmBeerDuration, Level = globalThis.mmBeerLevel;

test('mmBeerPalette: known types + default', () => {
  assert.equal(Pal('pale').beerTop, '#F6C744');
  assert.equal(Pal('stout').headH, 0.20);
  assert.equal(Pal('amber').foam, '#F3E0C0');
  assert.equal(Pal('nope').beerTop, '#F6C744');   // unknown -> pale
  assert.equal(Pal(undefined).beerTop, '#F6C744');
});

test('mmBeerPhase: out=fill, in=drain', () => {
  assert.equal(Phase('out'), 'fill');
  assert.equal(Phase('in'), 'drain');
});

test('mmBeerDuration: fillMs on out, drainMs on in, default 2500', () => {
  assert.equal(Dur({ fillMs: 1500, drainMs: 3000 }, 'out'), 1500);
  assert.equal(Dur({ fillMs: 1500, drainMs: 3000 }, 'in'), 3000);
  assert.equal(Dur({}, 'out'), 2500);
  assert.equal(Dur(null, 'in'), 2500);
});

test('mmBeerLevel: fill rises 0->1, drain falls 1->0, clamped', () => {
  assert.equal(Level('fill', 0), 0);
  assert.equal(Level('fill', 1), 1);
  assert.equal(Level('drain', 0), 1);
  assert.equal(Level('drain', 1), 0);
  assert.equal(Level('fill', -0.5), 0);
  assert.equal(Level('drain', 1.5), 0);
});

const Wave = globalThis.mmFoamWaveY, Bub = globalThis.mmBeerBubbles, Foam = globalThis.mmFoamBubbles;

test('mmFoamWaveY: deterministic + amp scaling around baseY', () => {
  const a = Wave(0.5, 1.0, 10, 100), b = Wave(0.5, 1.0, 10, 100);
  assert.equal(a, b);                                  // pure
  assert.ok(Math.abs(a - 100) <= 10 + 1e-9);           // within +/- amp*(0.5+0.3)
  assert.notEqual(Wave(0.5, 1.0, 10, 100), Wave(0.5, 2.0, 10, 100)); // t matters
});

test('mmBeerBubbles: deterministic per seed, ranges, count', () => {
  const x = mmBeerBubbles(7, 20), y = mmBeerBubbles(7, 20), z = mmBeerBubbles(8, 20);
  assert.equal(x.length, 20);
  assert.deepEqual(x, y);                              // same seed -> identical (wall-coherent)
  assert.notDeepEqual(x, z);                           // different seed -> different
  x.forEach(b => {
    assert.ok(b.x >= 0 && b.x < 1 && b.phase >= 0 && b.phase < 1);
    assert.ok(b.r >= 1 && b.r <= 3.4 && b.spd >= 0.45 && b.spd <= 1.25);
  });
});

test('mmFoamBubbles: deterministic, distinct stream from beer bubbles', () => {
  const f = mmFoamBubbles(7, 15), g = mmFoamBubbles(7, 15);
  assert.deepEqual(f, g);
  assert.notDeepEqual(f.map(b => b.x), mmBeerBubbles(7, 15).map(b => b.x)); // different stream
  f.forEach(b => { assert.ok(b.a >= 0.22 && b.a <= 0.62 && b.r >= 1 && b.r <= 4.2); });
});
