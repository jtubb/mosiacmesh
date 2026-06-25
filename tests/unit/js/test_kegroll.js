import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/transitions.js');
await import('../../../js/animations.js');       // mmMeshTransform (for the drawer task later)
await import('../../../js/mesh-viewport.js');     // mmMeshViewport + mmStampSprite
const g = globalThis;
const REG = { x: 0, y: 0, w: 300, h: 200 };

test('mmKegPhase: out=cover, in=reveal', () => {
  assert.equal(g.mmKegPhase('out'), 'cover');
  assert.equal(g.mmKegPhase('in'), 'reveal');
});

test('mmKegCoverRect: cover grows from start edge in travel direction', () => {
  assert.equal(g.mmKegCoverRect(0, 'right', 'cover', REG), null);      // nothing covered yet
  let r = g.mmKegCoverRect(0.5, 'right', 'cover', REG);
  assert.deepEqual(r, { x: 0, y: 0, w: 150, h: 200 });                 // left half covered
  r = g.mmKegCoverRect(1, 'right', 'cover', REG);
  assert.deepEqual(r, { x: 0, y: 0, w: 300, h: 200 });                 // fully covered
});

test('mmKegCoverRect: reveal shrinks the ahead-of-keg region to nothing', () => {
  let r = g.mmKegCoverRect(0, 'right', 'reveal', REG);
  assert.deepEqual(r, { x: 0, y: 0, w: 300, h: 200 });                 // fully covered at start
  r = g.mmKegCoverRect(0.5, 'right', 'reveal', REG);
  assert.deepEqual(r, { x: 150, y: 0, w: 150, h: 200 });               // right half still covered
  assert.equal(g.mmKegCoverRect(1, 'right', 'reveal', REG), null);     // fully revealed
});

test('mmKegCoverRect: left anchors at the far (right) edge for cover', () => {
  const r = g.mmKegCoverRect(0.5, 'left', 'cover', REG);
  assert.deepEqual(r, { x: 150, y: 0, w: 150, h: 200 });
});

test('mmKegCoverRect: down covers the vertical axis, full width', () => {
  const r = g.mmKegCoverRect(0.5, 'down', 'cover', REG);
  assert.deepEqual(r, { x: 0, y: 0, w: 300, h: 100 });
});

test('mmKegCoverRect: honors region offset', () => {
  const reg = { x: 10, y: 20, w: 300, h: 200 };
  const r = g.mmKegCoverRect(0.5, 'right', 'cover', reg);
  assert.deepEqual(r, { x: 10, y: 20, w: 150, h: 200 });
});

test('mmKegPos: keg fully off both edges at the ends, centered perpendicular', () => {
  const kegD = 200;                                  // = REG.h for a horizontal roll
  let p = g.mmKegPos(0, 'right', REG, kegD);
  assert.ok(Math.abs(p.cx - (-100)) < 1e-9);          // center off the left by a radius
  assert.ok(Math.abs(p.cy - 100) < 1e-9);             // perpendicular center
  assert.ok(Math.abs(p.dist - 0) < 1e-9);
  p = g.mmKegPos(1, 'right', REG, kegD);
  assert.ok(Math.abs(p.cx - 400) < 1e-9);             // off the right by a radius (300 + 100)
  assert.ok(Math.abs(p.dist - 500) < 1e-9);           // span(300) + diameter(200)
  p = g.mmKegPos(0, 'left', REG, kegD);
  assert.ok(Math.abs(p.cx - 400) < 1e-9);             // left-roll starts off the right
});

test('mmKegAngle: physical roll = dist/radius, sign per direction', () => {
  assert.ok(Math.abs(g.mmKegAngle(500, 100, 'right') - 5) < 1e-9);
  assert.ok(Math.abs(g.mmKegAngle(500, 100, 'left') - (-5)) < 1e-9);
  assert.ok(Math.abs(g.mmKegAngle(500, 100, 'up') - (-5)) < 1e-9);
  assert.equal(g.mmKegAngle(500, 0, 'right'), 0);     // degenerate radius
  let prev = -1;
  for (let i = 0; i <= 10; i++) { const a = g.mmKegAngle(i * 50, 100, 'right'); assert.ok(a >= prev - 1e-9); prev = a; }
});

test('mmTransitionState: kegroll end=cover, start=reveal (mask family)', () => {
  const S = g.mmTransitionState;
  const end = { name: 'kegroll', params: { duration: 2000, scope: 'wall', direction: 'right' } };
  // offset 4500 of 6000, ed=2000 -> raw p=(6000-4500)/2000=0.75 -> front=1-0.75=0.25
  let st = S(null, end, 4500, 6000, null, null);
  assert.equal(st.role, 'out');
  assert.equal(st.effect.name, 'kegroll');
  assert.equal(st.effect.family, 'mask');
  assert.equal(st.effect.phase, 'cover');
  assert.ok(Math.abs(st.effect.front - 0.25) < 1e-9);     // local progress, not raw p
  assert.equal(st.effect.scope, 'wall');
  const start = { name: 'kegroll', params: { duration: 2000 } };
  st = S(start, null, 500, 6000, null, null);             // in-window: raw p=0.25, front=0.25
  assert.equal(st.effect.phase, 'reveal');
  assert.ok(Math.abs(st.effect.front - 0.25) < 1e-9);
  assert.equal(st.effect.scope, 'wall');                  // default when param omitted
});
