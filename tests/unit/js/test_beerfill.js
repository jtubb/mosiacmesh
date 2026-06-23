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
