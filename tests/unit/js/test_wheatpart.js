import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/transitions.js');
const g = globalThis;
const C = (a, b) => Math.abs(a - b) < 1e-9;

test('mmWheatOpenness: hold-then-ramp per role, default hold 0.2, clamps', () => {
  // reveal: holds closed (0) for the first 20%, then opens linearly over the last 80%.
  assert.ok(C(g.mmWheatOpenness('reveal', 0), 0));
  assert.ok(C(g.mmWheatOpenness('reveal', 0.1), 0));   // within the hold
  assert.ok(C(g.mmWheatOpenness('reveal', 0.2), 0));   // hold edge
  assert.ok(C(g.mmWheatOpenness('reveal', 0.6), 0.5)); // (0.6-0.2)/0.8
  assert.ok(C(g.mmWheatOpenness('reveal', 1), 1));
  // cover: closes linearly over the first 80%, then holds closed (0) the last 20%.
  assert.ok(C(g.mmWheatOpenness('cover', 0), 1));      // cover starts open
  assert.ok(C(g.mmWheatOpenness('cover', 0.4), 0.5));  // 1 - 0.4/0.8
  assert.ok(C(g.mmWheatOpenness('cover', 0.8), 0));    // hold edge
  assert.ok(C(g.mmWheatOpenness('cover', 0.9), 0));    // within the hold
  assert.ok(C(g.mmWheatOpenness('cover', 1), 0));
  assert.ok(C(g.mmWheatOpenness('reveal', -1), 0));    // clamp
  assert.ok(C(g.mmWheatOpenness('reveal', 2), 1));
});

test('mmWheatOpenness: explicit hold fraction widens/narrows the dwell', () => {
  // hold 0.4: reveal holds closed until front 0.4, opens over the last 0.6.
  assert.ok(C(g.mmWheatOpenness('reveal', 0.4, 0.4), 0));
  assert.ok(C(g.mmWheatOpenness('reveal', 0.7, 0.4), 0.5)); // (0.7-0.4)/0.6
  // hold 0: pure passthrough (no dwell).
  assert.ok(C(g.mmWheatOpenness('reveal', 0.3, 0), 0.3));
  assert.ok(C(g.mmWheatOpenness('cover', 0.3, 0), 0.7));
});

test('mmWheatOpenness: both roles reach closed (0) at the handoff', () => {
  // endEffect (cover) ends at front=1 -> openness 0; startEffect (reveal) starts at
  // front=0 -> openness 0. Both are full-wheat at the seam -> continuous.
  assert.ok(C(g.mmWheatOpenness('cover', 1), 0));
  assert.ok(C(g.mmWheatOpenness('reveal', 0), 0));
});

test('mmWheatPartGeom: endpoints, symmetry, monotonic gap/lean', () => {
  const GW = 800, GH = 200;
  const closed = g.mmWheatPartGeom(0, GW, GH);
  assert.ok(C(closed.g, 0));
  assert.ok(C(closed.leftEdge, 400) && C(closed.rightEdge, 400)); // walls meet at cx
  assert.ok(C(closed.lean, 0));
  const open = g.mmWheatPartGeom(1, GW, GH);
  assert.ok(C(open.g, 400));
  assert.ok(C(open.leftEdge, 0) && C(open.rightEdge, 800));        // cleared to edges
  assert.ok(C(open.lean, 0.5));
  const half = g.mmWheatPartGeom(0.5, GW, GH);
  assert.ok(half.g > closed.g && half.g < open.g);                // monotonic
  assert.ok(half.lean > closed.lean && half.lean < open.lean);
  assert.ok(C(half.cx - half.leftEdge, half.rightEdge - half.cx)); // symmetric about cx
});

test('mmWheatPartGeom: clamps out-of-range openness', () => {
  const o = g.mmWheatPartGeom(2, 800, 200);
  assert.ok(C(o.g, 400) && C(o.lean, 0.5));
});

test('mmWheatField: deterministic, sized, in-bounds, side-split about cx', () => {
  const GW = 800, GH = 200;
  const a = g.mmWheatField(12345, 70, GW, GH);
  const b = g.mmWheatField(12345, 70, GW, GH);
  assert.equal(a.length, 70);
  assert.deepEqual(a, b);                                   // same seed -> identical
  const c = g.mmWheatField(99, 70, GW, GH);
  assert.notDeepEqual(a, c);                                // different seed -> different
  for (const s of a) {
    assert.ok(s.bx >= 0 && s.bx < GW, 'bx in [0,GW)');
    assert.ok(s.h >= 0.6 && s.h < 1.0, 'h in [0.6,1.0)');
    assert.ok(s.sway >= 0 && s.sway < 6.2832, 'sway in [0,2pi)');
    assert.equal(s.side, s.bx < GW / 2 ? 'left' : 'right');
  }
});

test('mmWheatColor: known tints return 4 keys; unknown -> golden', () => {
  for (const t of ['golden', 'amber', 'pale']) {
    const pal = g.mmWheatColor(t);
    for (const k of ['backdrop', 'base', 'stalk', 'head']) {
      assert.equal(typeof pal[k], 'string');
    }
  }
  assert.deepEqual(g.mmWheatColor('nope'), g.mmWheatColor('golden'));
  assert.deepEqual(g.mmWheatColor(undefined), g.mmWheatColor('golden'));
});

test('mmTransitionState: wheatpart is a mask effect with phase + rising local front', () => {
  // endEffect on item A: an 'out' role. Use an offset late in an 8000ms item so the
  // end window is active. duration 2000ms.
  const endEff = { name: 'wheatpart', params: { duration: 2000, scope: 'wall' } };
  // Sample two points inside the end window to confirm front rises 0->1 (local progress).
  const near = g.mmTransitionState(null, endEff, 6200, 8000, null, null);  // just into window
  const late = g.mmTransitionState(null, endEff, 7800, 8000, null, null);  // near end
  assert.equal(near.effect.name, 'wheatpart');
  assert.equal(near.effect.family, 'mask');
  assert.equal(near.effect.phase, 'cover');                // out role
  assert.ok(near.effect.front >= 0 && near.effect.front <= 1);
  assert.ok(late.effect.front > near.effect.front, 'local front rises across the cover window');
  assert.equal(near.wipe, null);

  // startEffect on item B: an 'in' role.
  const startEff = { name: 'wheatpart', params: { duration: 2000, scope: 'wall' } };
  const s = g.mmTransitionState(startEff, null, 200, 8000, null, null);
  assert.equal(s.effect.phase, 'reveal');                  // in role
  assert.ok(s.effect.front >= 0 && s.effect.front <= 1);
});

function stubCtx() {
  const calls = { fillRect: 0, save: 0, restore: 0, beginPath: 0, fill: 0, arc: 0, gradients: 0 };
  return {
    calls,
    fillStyle: '#000', globalAlpha: 1,
    save() { calls.save++; }, restore() { calls.restore++; },
    translate() {}, rotate() {}, scale() {},
    beginPath() { calls.beginPath++; }, moveTo() {}, lineTo() {}, quadraticCurveTo() {},
    arc() { calls.arc++; }, closePath() {},
    fill() { calls.fill++; }, stroke() {}, fillRect() { calls.fillRect++; },
    createLinearGradient() { calls.gradients++; return { addColorStop() {} }; }
  };
}

test('mmDrawWheat: closed (openness 0) fills backdrops; open (openness 1) draws ~nothing', () => {
  // cover phase, front=1 -> openness 0 (fully closed): two backdrop rects expected.
  const closed = stubCtx();
  g.mmDrawWheat(closed, { tint: 'golden', density: 40 }, 'cover', 1, 800, 200, null, 'wall', 7, 0);
  assert.ok(closed.calls.fillRect >= 2, 'closed wheat fills the two backdrop walls');
  assert.ok(closed.calls.save >= 1 && closed.calls.restore === closed.calls.save, 'balanced save/restore');

  // reveal phase, front=1 -> openness 1 (fully open): walls cleared, ~no backdrop.
  const open = stubCtx();
  g.mmDrawWheat(open, { tint: 'golden', density: 40 }, 'reveal', 1, 800, 200, null, 'wall', 7, 0);
  assert.ok(open.calls.fillRect <= closed.calls.fillRect, 'open wheat fills less/none vs closed');
});

test('mmDrawWheat: never throws on degenerate inputs', () => {
  const c = stubCtx();
  assert.doesNotThrow(() => g.mmDrawWheat(c, {}, 'cover', 0.5, 800, 200, null, 'wall', 0, 123));
  assert.doesNotThrow(() => g.mmDrawWheat(c, { density: 0 }, 'reveal', 0.5, 800, 200, null, 'wall', 0, 0));
});

test('mmDrawWheat: screen scope with quad executes without error and draws backdrop + stalks', () => {
  // screen scope at mid-openness with a typical quad. Should draw backdrop (fillRect) and stalks (save/restore).
  const quad = [[0.25, 0.5], [0.75, 0.5], [0.75, 1.0], [0.25, 1.0]];
  const ctx = stubCtx();
  assert.doesNotThrow(() => g.mmDrawWheat(ctx, { tint: 'golden', density: 50 }, 'cover', 0.5, 800, 200, quad, 'screen', 42, 500));
  assert.ok(ctx.calls.fillRect >= 1, 'backdrop present in screen scope');
  assert.ok(ctx.calls.restore === ctx.calls.save && ctx.calls.save >= 1, 'balanced save/restore for stalks');
});
