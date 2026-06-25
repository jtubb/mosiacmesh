import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/transitions.js');
const g = globalThis;
const C = (a, b) => Math.abs(a - b) < 1e-9;

test('mmFlipFactor: horizontal drives sx; endpoints; alpha/edge ramps; clamp', () => {
  assert.deepEqual(g.mmFlipFactor(1, 'horizontal'), { sx: 1, sy: 1, alpha: 1, edge: 0 });   // open
  let f = g.mmFlipFactor(0, 'horizontal');
  assert.ok(C(f.sx, 0) && C(f.sy, 1) && C(f.alpha, 0.35) && C(f.edge, 1));                   // edge-on
  f = g.mmFlipFactor(0.5, 'horizontal');
  assert.ok(C(f.sx, 0.5) && C(f.sy, 1) && C(f.alpha, 0.675) && C(f.edge, 0.5));
  f = g.mmFlipFactor(1.5, 'horizontal');                                                     // clamp high
  assert.ok(C(f.sx, 1) && C(f.edge, 0));
  f = g.mmFlipFactor(-0.5, 'horizontal');                                                     // clamp low
  assert.ok(C(f.sx, 0) && C(f.edge, 1));
});

test('mmFlipFactor: vertical drives sy, sx stays 1', () => {
  const f = g.mmFlipFactor(0.4, 'vertical');
  assert.ok(C(f.sx, 1) && C(f.sy, 0.4));
});

test('mmCoasterColor: known tones + default', () => {
  assert.equal(g.mmCoasterColor('kraft'), '#b9935f');
  assert.equal(g.mmCoasterColor('slate'), '#5a5e63');
  assert.equal(g.mmCoasterColor('nope'), g.mmCoasterColor('kraft'));   // unknown -> kraft
});
