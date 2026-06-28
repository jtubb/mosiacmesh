import { test } from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, dirname } from 'node:path';
await import('../../../js/transitions.js');
const g = globalThis;

const __dir = dirname(fileURLToPath(import.meta.url));
const gold = JSON.parse(readFileSync(join(__dir, 'seeded_golden.json'), 'utf8'));

test('mmScatterParticles(12345,6) matches golden snapshot', () => {
  assert.deepStrictEqual(g.mmScatterParticles(12345, 6), gold.scatter);
});

test('mmCrownSpikes(777,5) matches golden snapshot', () => {
  assert.deepStrictEqual(g.mmCrownSpikes(777, 5), gold.crown);
});

test('mmWheatField(999,5,800,200) matches golden snapshot', () => {
  assert.deepStrictEqual(g.mmWheatField(999, 5, 800, 200), gold.wheat);
});

test('mmBeerBubbles(54321,6) matches golden snapshot', () => {
  assert.deepStrictEqual(g.mmBeerBubbles(54321, 6), gold.beerBubbles);
});

test('mmFoamBubbles(2468,6) matches golden snapshot', () => {
  assert.deepStrictEqual(g.mmFoamBubbles(2468, 6), gold.foamBubbles);
});
