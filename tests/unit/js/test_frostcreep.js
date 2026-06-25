import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/transitions.js');
await import('../../../js/mesh-viewport.js');     // mmStampSprite (mug draw)
const g = globalThis;

const C = (a, b) => Math.abs(a - b) < 1e-9;

test('mmFrostPhase: out=cover, in=reveal', () => {
  assert.equal(g.mmFrostPhase('out'), 'cover');
  assert.equal(g.mmFrostPhase('in'), 'reveal');
});

test('mmFrostField: deterministic per seed, values in [0,1), right length', () => {
  const a = g.mmFrostField(12, 99), b = g.mmFrostField(12, 99), c = g.mmFrostField(12, 100);
  assert.deepEqual(a, b);                 // same seed -> identical (wall-coherent)
  assert.notDeepEqual(a, c);              // different seed -> different
  assert.equal(a.length, 144);
  a.forEach(v => assert.ok(v >= 0 && v < 1, 'value in [0,1): ' + v));
});

test('mmFrostField: spatially correlated (smoother than random pairs)', () => {
  const blocks = 16, field = g.mmFrostField(blocks, 12345);
  let adjSum = 0, adjN = 0, r, c;
  for (r = 0; r < blocks; r++) for (c = 0; c < blocks - 1; c++) {
    adjSum += Math.abs(field[r * blocks + c] - field[r * blocks + c + 1]); adjN++;
  }
  const adjMean = adjSum / adjN;
  let rndSum = 0, rndN = 0, i;
  for (i = 0; i < 500; i++) {
    rndSum += Math.abs(field[(i * 7) % field.length] - field[(i * 13 + 3) % field.length]); rndN++;
  }
  const rndMean = rndSum / rndN;
  // smoothing -> adjacent cells much closer than arbitrary pairs
  assert.ok(adjMean < rndMean * 0.8, 'adjacent ' + adjMean + ' should be < 0.8 * random ' + rndMean);
});

test('mmFrostBlotch: off below threshold, grows 0->1 above (clamped)', () => {
  assert.deepEqual(g.mmFrostBlotch(0.5, 0.4, 0.25), { on: false, t: 0 });
  let b = g.mmFrostBlotch(0.5, 0.5, 0.25); assert.ok(b.on && Math.abs(b.t - 0) < 1e-9);
  b = g.mmFrostBlotch(0.5, 0.625, 0.25); assert.ok(Math.abs(b.t - 0.5) < 1e-9);   // halfway through grow
  b = g.mmFrostBlotch(0.5, 0.95, 0.25);  assert.ok(b.on && Math.abs(b.t - 1) < 1e-9);  // clamped
});

test('mmTransitionState: frostcreep end=cover (rises), start=reveal (mask family)', () => {
  const S = g.mmTransitionState;
  const end = { name: 'frostcreep', params: { duration: 2000, scope: 'wall' } };
  // end window [4000,6000]; offset 4500 -> p=(6000-4500)/2000=0.75 -> flp=1-p=0.25 -> cover=0.25
  let st = S(null, end, 4500, 6000, null, null);
  assert.equal(st.role, 'out');
  assert.equal(st.effect.name, 'frostcreep');
  assert.equal(st.effect.family, 'mask');
  assert.equal(st.effect.phase, 'cover');
  assert.ok(Math.abs(st.effect.front - 0.25) < 1e-9);
  assert.equal(st.effect.scope, 'wall');
  // later in the window -> MORE frost (rises): offset 5500 -> p=0.25 -> flp=0.75 -> cover=0.75
  st = S(null, end, 5500, 6000, null, null);
  assert.ok(Math.abs(st.effect.front - 0.75) < 1e-9);
  // start role (reveal): offset 500 -> p=0.25 -> flp=0.25 -> cover=1-0.25=0.75
  const start = { name: 'frostcreep', params: { duration: 2000 } };
  st = S(start, null, 500, 6000, null, null);
  assert.equal(st.effect.phase, 'reveal');
  assert.ok(Math.abs(st.effect.front - 0.75) < 1e-9);
  assert.equal(st.effect.scope, 'wall');     // default
});

function recCtxFrost() {
  return { rects: [], arcs: 0, lines: 0, imgs: 0, fillStyle: '#000',
    beginPath() {}, arc() { this.arcs++; }, fill() {},
    moveTo() {}, lineTo() { this.lines++; }, closePath() {},
    save() {}, restore() {}, translate() {}, rotate() {}, scale() {},
    drawImage() { this.imgs++; },
    fillRect(x, y, w, h) { this.rects.push({ x: x, y: y, w: w, h: h }); } };
}

test('mmFrostSeq cover: mug drops first (mp 0->1), frost starts only after it lands', () => {
  // mugFrac 0.3. lp == cover on the cover phase.
  let s = g.mmFrostSeq('cover', 0.0, 0.3); assert.ok(C(s.mp, 0) && C(s.fc, 0));   // off top, no frost
  s = g.mmFrostSeq('cover', 0.15, 0.3); assert.ok(C(s.mp, 0.5) && C(s.fc, 0));    // dropping, still no frost
  s = g.mmFrostSeq('cover', 0.3, 0.3);  assert.ok(C(s.mp, 1) && C(s.fc, 0));      // landed, frost about to start
  s = g.mmFrostSeq('cover', 0.65, 0.3); assert.ok(C(s.mp, 1) && C(s.fc, 0.5));    // centered, frost half
  s = g.mmFrostSeq('cover', 1.0, 0.3);  assert.ok(C(s.mp, 1) && C(s.fc, 1));      // full
});

test('mmFrostSeq reveal: frost recedes first, then mug rises out (reverse)', () => {
  // reveal: lp == 1-cover. cover counts 1->0 over the phase.
  let s = g.mmFrostSeq('reveal', 1.0, 0.3); assert.ok(C(s.fc, 1) && C(s.mp, 1));  // start: full frost, mug center
  s = g.mmFrostSeq('reveal', 0.3, 0.3);  assert.ok(C(s.fc, 0) && C(s.mp, 1));     // frost gone, mug still center
  s = g.mmFrostSeq('reveal', 0.0, 0.3);  assert.ok(C(s.fc, 0) && C(s.mp, 0));     // mug exited top
});

test('mmCrystalUnit: deterministic, 2*spikes points, jagged (outer>inner), within unit', () => {
  const a = g.mmCrystalUnit(7, 7), b = g.mmCrystalUnit(7, 7), c = g.mmCrystalUnit(8, 7);
  assert.deepEqual(a, b);                              // same seed -> identical (wall-coherent)
  assert.notDeepEqual(a, c);                           // different seed -> different
  assert.equal(a.length, 14);                          // 2 * spikes
  a.forEach(p => {
    const m = Math.sqrt(p.ux * p.ux + p.uy * p.uy);
    assert.ok(m <= 1.0 + 1e-9 && m >= 0.34 - 1e-9, 'magnitude in [inner,outer]: ' + m);
  });
  // alternating: even indices are OUTER spikes (>=0.78), odd are INNER (<=0.50)
  for (let i = 0; i < a.length; i++) {
    const m = Math.sqrt(a[i].ux * a[i].ux + a[i].uy * a[i].uy);
    if (i % 2 === 0) { assert.ok(m >= 0.78 - 1e-9, 'outer spike'); }
    else { assert.ok(m <= 0.50 + 1e-9, 'inner notch'); }
  }
});

test('mmFrostPalette: known tints + default', () => {
  assert.ok(g.mmFrostPalette('frost').core);
  assert.ok(g.mmFrostPalette('blue').core);
  assert.equal(g.mmFrostPalette('nope').core, g.mmFrostPalette('frost').core);   // unknown -> frost
});

test('mmDrawFrost: nothing at cover 0', () => {
  const c = recCtxFrost();
  g.mmDrawFrost(c, { tint: 'frost' }, 'cover', 0, 300, 200, null, 'wall', 5);
  assert.equal(c.lines, 0);
  assert.equal(c.rects.length, 0);
});

test('mmDrawFrost: jagged crystals mid-cover, consolidation fill near full', () => {
  let c = recCtxFrost();
  g.mmDrawFrost(c, { tint: 'frost' }, 'cover', 0.5, 300, 200, null, 'wall', 5);
  assert.ok(c.lines > 0, 'crystal polygons (lineTo) drawn mid-cover');   // jagged, not arcs
  c = recCtxFrost();
  g.mmDrawFrost(c, { tint: 'frost' }, 'cover', 1, 300, 200, null, 'wall', 5);
  assert.ok(c.rects.some(r => r.x === 0 && r.y === 0 && r.w === 300 && r.h === 200),
            'full-region consolidation fill present at cover 1');
});

const mugImg = { width: 120, height: 120 };   // "decoded" mug sprite

test('mmDrawFrost: mug drops in BEFORE frost (cover lead-in)', () => {
  // cover=0.15 with mugFrac 0.3 -> mp=0.5 (dropping), fc=0 -> mug only, no crystals yet
  const c = recCtxFrost();
  g.mmDrawFrost(c, { tint: 'frost', sprite: 'frostymug' }, 'cover', 0.15, 300, 200, null, 'wall', 5, mugImg);
  assert.equal(c.imgs, 1, 'mug drawn during the drop');
  assert.equal(c.lines, 0, 'no frost crystals before the mug lands');
});

test('mmDrawFrost: mug + frost once landed; no mug arg -> pure frost', () => {
  let c = recCtxFrost();
  g.mmDrawFrost(c, { tint: 'frost', sprite: 'frostymug' }, 'cover', 0.65, 300, 200, null, 'wall', 5, mugImg);
  assert.equal(c.imgs, 1, 'mug at center');
  assert.ok(c.lines > 0, 'frost crystals building');
  // backward compatible: omitting img -> pure frost (no mug), frost keyed to cover directly
  c = recCtxFrost();
  g.mmDrawFrost(c, { tint: 'frost' }, 'cover', 0.5, 300, 200, null, 'wall', 5);
  assert.equal(c.imgs, 0, 'no mug when no sprite');
  assert.ok(c.lines > 0, 'frost still drawn');
});
