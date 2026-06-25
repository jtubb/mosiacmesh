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

test('mmTransitionState: coasterflip = transform family, front=p (raw, both roles)', () => {
  const S = g.mmTransitionState;
  // end window [5300,6000], ed=700; offset 5650 -> p=(6000-5650)/700=0.5
  const end = { name: 'coasterflip', params: { axis: 'horizontal', duration: 700 } };
  let st = S(null, end, 5650, 6000, null, null);
  assert.equal(st.role, 'out');
  assert.equal(st.effect.name, 'coasterflip');
  assert.equal(st.effect.family, 'transform');
  assert.ok(Math.abs(st.effect.front - 0.5) < 1e-9);    // raw p, NOT inverted
  assert.equal(st.effect.scope, 'wall');                // default
  // start window [0,700], sd=700; offset 350 -> p=0.5
  const start = { name: 'coasterflip', params: { duration: 700 } };
  st = S(start, null, 350, 6000, null, null);
  assert.equal(st.role, 'in');
  assert.equal(st.effect.family, 'transform');
  assert.ok(Math.abs(st.effect.front - 0.5) < 1e-9);
});
