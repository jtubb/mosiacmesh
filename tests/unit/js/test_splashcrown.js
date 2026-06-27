import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/transitions.js');
const g = globalThis;
const C = (a, b) => Math.abs(a - b) < 1e-9;

test('mmSplashPhase: out->cover, in->reveal', () => {
  assert.equal(g.mmSplashPhase('out'), 'cover');
  assert.equal(g.mmSplashPhase('in'), 'reveal');
});

test('mmSplashSeq: lead-in then bloom; cover forward, reveal reversed; handoff full-beer', () => {
  // cover: front 0 -> drop at top, no beer; front 1 -> full bloom
  let s = g.mmSplashSeq('cover', 0, 0.18);
  assert.ok(!s.impacted && C(s.dropY, 0) && C(s.bloom, 0));
  s = g.mmSplashSeq('cover', 0.09, 0.18);          // mid lead-in
  assert.ok(!s.impacted && C(s.dropY, 0.5) && C(s.bloom, 0));
  s = g.mmSplashSeq('cover', 0.18, 0.18);          // impact edge
  assert.ok(s.impacted && C(s.bloom, 0));
  s = g.mmSplashSeq('cover', 1, 0.18);
  assert.ok(s.impacted && C(s.bloom, 1));
  // reveal is the time-reverse: front 0 -> full beer (handoff), front 1 -> drop up/no beer
  s = g.mmSplashSeq('reveal', 0, 0.18);
  assert.ok(s.impacted && C(s.bloom, 1), 'reveal starts full-beer at the handoff');
  s = g.mmSplashSeq('reveal', 1, 0.18);
  assert.ok(!s.impacted && C(s.bloom, 0) && C(s.dropY, 0), 'reveal ends drop-up, no beer');
});

test('mmSplashSeq: clamps and defaults leadFrac', () => {
  const a = g.mmSplashSeq('cover', 2, 0);          // leadFrac 0 -> default 0.18
  assert.ok(a.impacted && C(a.bloom, 1));
  const b = g.mmSplashSeq('cover', -1, 0.18);
  assert.ok(!b.impacted && C(b.dropY, 0));
});
