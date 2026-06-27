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

test('mmSplashRadius: 0 at bloom 0, half-diagonal at bloom 1, clamps', () => {
  assert.ok(C(g.mmSplashRadius(0, 800, 600), 0));
  assert.ok(C(g.mmSplashRadius(1, 800, 600), 0.5 * Math.sqrt(800 * 800 + 600 * 600)));
  assert.ok(C(g.mmSplashRadius(2, 800, 600), 0.5 * Math.sqrt(800 * 800 + 600 * 600)));  // clamp
});

test('mmCrownSpikes: deterministic, sized, in-bounds', () => {
  const a = g.mmCrownSpikes(123, 28);
  const b = g.mmCrownSpikes(123, 28);
  assert.equal(a.length, 28);
  assert.deepEqual(a, b);
  assert.notDeepEqual(a, g.mmCrownSpikes(999, 28));
  for (const s of a) {
    assert.ok(s.ang >= 0 && s.ang < 6.2832 + 1, 'ang ~ [0,2pi)');
    assert.ok(s.lenF >= 0.5 && s.lenF < 1.0);
    assert.ok(s.beadF >= 0.5 && s.beadF < 1.11);
    assert.ok(s.flyF >= 0.6 && s.flyF < 1.51);
  }
});

test('mmTransitionState: splashcrown is a mask effect with phase + rising local front', () => {
  const endEff = { name: 'splashcrown', params: { scope: 'wall', duration: 2000 } };
  const near = g.mmTransitionState(null, endEff, 6200, 8000, null, null);
  const late = g.mmTransitionState(null, endEff, 7800, 8000, null, null);
  assert.equal(near.effect.name, 'splashcrown');
  assert.equal(near.effect.family, 'mask');
  assert.equal(near.effect.phase, 'cover');
  assert.ok(near.effect.front >= 0 && near.effect.front <= 1);
  assert.ok(late.effect.front > near.effect.front, 'local front rises across the cover window');
  assert.equal(near.wipe, null);

  const startEff = { name: 'splashcrown', params: { scope: 'wall', duration: 2000 } };
  const s = g.mmTransitionState(startEff, null, 200, 8000, null, null);
  assert.equal(s.effect.phase, 'reveal');
});
