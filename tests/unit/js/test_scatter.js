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
