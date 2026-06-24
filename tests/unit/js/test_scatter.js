import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/transitions.js');
const g = globalThis;

test('mmScatterPhase: out=cover, in=reveal', () => {
  assert.equal(g.mmScatterPhase('out'), 'cover');
  assert.equal(g.mmScatterPhase('in'), 'reveal');
});
test('mmScatterDuration: fillMs on out, drainMs on in, default 2500', () => {
  assert.equal(g.mmScatterDuration({ fillMs: 1500, drainMs: 3000 }, 'out'), 1500);
  assert.equal(g.mmScatterDuration({ fillMs: 1500, drainMs: 3000 }, 'in'), 3000);
  assert.equal(g.mmScatterDuration({}, 'out'), 2500);
});
test('mmScatterCover: cover rises, reveal falls, clamped', () => {
  assert.equal(g.mmScatterCover('cover', 0), 0);
  assert.equal(g.mmScatterCover('cover', 1), 1);
  assert.equal(g.mmScatterCover('reveal', 0), 1);
  assert.equal(g.mmScatterCover('reveal', 1), 0);
  assert.equal(g.mmScatterCover('cover', -1), 0);
});
test('mmScatterDist: monotonic per phase, continuous at handoff', () => {
  let prev = -1;
  for (let i = 0; i <= 10; i++) { const d = g.mmScatterDist('cover', i / 10); assert.ok(d >= prev - 1e-9); prev = d; }
  assert.ok(Math.abs(g.mmScatterDist('cover', 1) - 1) < 1e-9);
  assert.ok(Math.abs(g.mmScatterDist('reveal', 0) - 1) < 1e-9);   // continuous: cover@1 == reveal@0
  prev = -1;
  for (let i = 0; i <= 10; i++) { const d = g.mmScatterDist('reveal', i / 10); assert.ok(d >= prev - 1e-9); prev = d; }
  assert.ok(g.mmScatterDist('reveal', 1) > 2);
});
test('mmScatterGiantAngle: full turn by cover end, keeps turning on reveal', () => {
  assert.ok(Math.abs(g.mmScatterGiantAngle('cover', 1) - 2 * Math.PI) < 1e-9);
  assert.ok(g.mmScatterGiantAngle('reveal', 1) > 2 * Math.PI);
});
test('mmScatterSpriteUrl: name vs path', () => {
  assert.equal(g.mmScatterSpriteUrl('hop'), '/media/server/images/hop.png');
  assert.equal(g.mmScatterSpriteUrl('/media/server/images/x.png'), '/media/server/images/x.png');
});
test('mmScatterParticles: deterministic per seed, ranges, count', () => {
  const a = g.mmScatterParticles(9, 40), b = g.mmScatterParticles(9, 40), c = g.mmScatterParticles(10, 40);
  assert.equal(a.length, 40);
  assert.deepEqual(a, b);
  assert.notDeepEqual(a, c);
  a.forEach(p => {
    assert.ok(p.ang >= 0 && p.ang < 6.2832);
    assert.ok(p.sp >= 0.6 && p.sp < 1.5);
    assert.ok(p.rot0 >= 0 && p.rot0 < 6.2832);
    assert.ok(p.rps >= -0.7 && p.rps < 0.7);
  });
});
test('mmTransitionState: scatter end=cover, start=reveal (mask family)', () => {
  const S = g.mmTransitionState;
  const end = { name: 'scatter', params: { fillMs: 2000, drainMs: 2000, scope: 'wall' } };
  // offset 4500 of 6000 with ed=2000 -> raw p=(6000-4500)/2000=0.75 -> front=1-0.75=0.25
  // (distinguishes local-progress from raw p; near the end of the item, cover is only 25% in)
  let st = S(null, end, 4500, 6000, null, null);
  assert.equal(st.role, 'out');
  assert.equal(st.effect.name, 'scatter');
  assert.equal(st.effect.family, 'mask');
  assert.equal(st.effect.phase, 'cover');
  assert.ok(Math.abs(st.effect.front - 0.25) < 1e-9);   // local progress, NOT raw p (0.75)
  assert.equal(st.effect.scope, 'wall');
  const start = { name: 'scatter', params: { fillMs: 2000, drainMs: 2000 } };
  st = S(start, null, 500, 6000, null, null);       // in-window: raw p=0.25, front=0.25
  assert.equal(st.effect.phase, 'reveal');
  assert.ok(Math.abs(st.effect.front - 0.25) < 1e-9);
  assert.equal(st.effect.scope, 'wall');            // default
});
