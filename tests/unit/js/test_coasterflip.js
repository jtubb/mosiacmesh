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

// --- coasterflip v2: tumbling two-faced coaster ---
await import('../../../js/mesh-viewport.js');   // (mmStampSprite, for any draw glue)

test('mmCoasterPhase: out=cover, in=reveal', () => {
  assert.equal(g.mmCoasterPhase('out'), 'cover');
  assert.equal(g.mmCoasterPhase('in'), 'reveal');
});

test('mmCoasterTumble cover: round-in first, then tumble to edge-on', () => {
  const T = (f) => g.mmCoasterTumble(f, 'cover', 5, 0.25);
  // front=1 (phase start): lp=0 -> round 0, flat (scale 1), front face
  let s = T(1); assert.ok(C(s.round, 0) && C(s.scale, 1) && s.showFront);
  // lp=rf=0.25 -> front=0.75: rounded (round 1) but not yet spinning (scale 1)
  s = T(0.75); assert.ok(C(s.round, 1) && C(s.scale, 1));
  // front=0 (phase end): lp=1 -> full round, edge-on (scale 0)
  s = T(0); assert.ok(C(s.round, 1) && Math.abs(s.scale) < 1e-9);
});

test('mmCoasterTumble: 5 flips show the back (cos<0) somewhere mid-tumble', () => {
  // sweep cover front 1->0 and confirm showFront flips multiple times (front/back/...)
  let flips = 0, prev = null;
  for (let i = 0; i <= 200; i++) {
    const s = g.mmCoasterTumble(1 - i / 200, 'cover', 5, 0.25);
    if (prev !== null && s.showFront !== prev) flips++;
    prev = s.showFront;
  }
  assert.ok(flips >= 4, 'face alternates several times over the tumble: ' + flips);
});

test('mmCoasterTumble reveal: edge-on at start (continuous handoff), open at end', () => {
  const R = (f) => g.mmCoasterTumble(f, 'reveal', 5, 0.25);
  let s = R(0); assert.ok(Math.abs(s.scale) < 1e-9 && C(s.round, 1));   // edge-on, rounded (matches cover end)
  s = R(1); assert.ok(C(s.scale, 1) && C(s.round, 0) && s.showFront);   // full open rectangle
});

test('mmDrawCoasterCorners: fills 4 corners when r>0, nothing when r<=0', () => {
  const reg = { x: 0, y: 0, w: 300, h: 200 };
  let n = 0;
  const c = { fillStyle: '', beginPath(){}, moveTo(){}, lineTo(){}, arc(){}, closePath(){}, fill(){ n++; } };
  g.mmDrawCoasterCorners(c, reg, 40, '#000'); assert.equal(n, 4);     // 4 corner cutouts
  n = 0; g.mmDrawCoasterCorners(c, reg, 0, '#000'); assert.equal(n, 0); // no rounding
});

test('mmDrawCoasterDisc: masks outside a centered circle when round>0; nothing at round 0', () => {
  const reg = { x: 0, y: 0, w: 600, h: 200 };
  let fills = 0;
  const c = { fillStyle: '', beginPath(){}, moveTo(){}, lineTo(){}, arc(){}, closePath(){}, fill(){ fills++; }, fillRect(){ fills++; } };
  g.mmDrawCoasterDisc(c, reg, 0, '#000'); assert.equal(fills, 0);      // no mask
  fills = 0; g.mmDrawCoasterDisc(c, reg, 1, '#000');
  assert.ok(fills > 0, 'wide region at full round -> side strips + corner cutouts drawn');
});

test('mmCoasterTumble: scale = |cos θ| stays >= 0 and wobble = sin θ * 0.1 across the sweep', () => {
  let maxW = 0;
  for (let i = 0; i <= 40; i++) {
    const o = i / 40;
    const t = g.mmCoasterTumble(o, 'cover', 5, 0.25);
    assert.ok(t.scale >= 0 && t.scale <= 1.0000001, `scale in [0,1] at ${o} (got ${t.scale})`);
    assert.ok(C(t.wobble, Math.sin(t.theta) * 0.1), `wobble = sinθ*0.1 at ${o}`);
    if (Math.abs(t.wobble) > maxW) { maxW = Math.abs(t.wobble); }
  }
  assert.ok(maxW > 0.05, 'wobble actually swings (peaks near 0.1 at edge-on)');
});
