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
