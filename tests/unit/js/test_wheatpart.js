import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/transitions.js');
const g = globalThis;
const C = (a, b) => Math.abs(a - b) < 1e-9;

test('mmWheatOpenness: reveal passes through, cover inverts, clamps', () => {
  assert.ok(C(g.mmWheatOpenness('reveal', 0), 0));
  assert.ok(C(g.mmWheatOpenness('reveal', 1), 1));
  assert.ok(C(g.mmWheatOpenness('reveal', 0.3), 0.3));
  assert.ok(C(g.mmWheatOpenness('cover', 0), 1));   // cover starts open
  assert.ok(C(g.mmWheatOpenness('cover', 1), 0));   // cover ends closed
  assert.ok(C(g.mmWheatOpenness('cover', 0.3), 0.7));
  assert.ok(C(g.mmWheatOpenness('reveal', -1), 0)); // clamp
  assert.ok(C(g.mmWheatOpenness('reveal', 2), 1));
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
