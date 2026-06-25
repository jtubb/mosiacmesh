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
  assert.equal(g.mmKegCoverRect(0, 'right', 'cover', REG, 200), null);      // nothing covered yet
  let r = g.mmKegCoverRect(0.5, 'right', 'cover', REG, 200);
  assert.deepEqual(r, { x: 0, y: 0, w: 150, h: 200 });                 // left half covered (axis@0.5 == 0.5S)
  r = g.mmKegCoverRect(1, 'right', 'cover', REG, 200);
  assert.deepEqual(r, { x: 0, y: 0, w: 300, h: 200 });                 // fully covered
});

test('mmKegCoverRect: reveal shrinks the ahead-of-keg region to nothing', () => {
  let r = g.mmKegCoverRect(0, 'right', 'reveal', REG, 200);
  assert.deepEqual(r, { x: 0, y: 0, w: 300, h: 200 });                 // fully covered at start
  r = g.mmKegCoverRect(0.5, 'right', 'reveal', REG, 200);
  assert.deepEqual(r, { x: 150, y: 0, w: 150, h: 200 });               // right half still covered
  assert.equal(g.mmKegCoverRect(1, 'right', 'reveal', REG, 200), null);     // fully revealed
});

test('mmKegCoverRect: cover edge clamps to the keg axis (waits while keg rolls in)', () => {
  // kegD=200, S=300 -> axis(f) = -100 + 500f, edge = clamp(axis,0,300).
  // f=0.2 -> axis=0 -> edge=0 -> still NO cover (keg only just reached the edge);
  // the OLD linear edge (prog*S=60) would have covered a 60px strip. Falsifiable.
  assert.equal(g.mmKegCoverRect(0.2, 'right', 'cover', REG, 200), null);
  // f=0.4 -> axis=100 -> 100px covered (vs old 0.4*300=120) -> edge tracks the keg.
  assert.deepEqual(g.mmKegCoverRect(0.4, 'right', 'cover', REG, 200), { x: 0, y: 0, w: 100, h: 200 });
  // the cover edge equals the keg center while on-screen (no lead/lag):
  const edgeX = g.mmKegCoverRect(0.4, 'right', 'cover', REG, 200).w;   // = covered width = edge
  const kegCx = g.mmKegPos(0.4, 'right', REG, 200).cx;                 // keg center x
  assert.ok(Math.abs(edgeX - kegCx) < 1e-9);
});

test('mmKegCoverRect: left anchors at the far (right) edge for cover', () => {
  const r = g.mmKegCoverRect(0.5, 'left', 'cover', REG, 200);
  assert.deepEqual(r, { x: 150, y: 0, w: 150, h: 200 });
});

test('mmKegCoverRect: down covers the vertical axis, full width', () => {
  const r = g.mmKegCoverRect(0.5, 'down', 'cover', REG, 200);
  assert.deepEqual(r, { x: 0, y: 0, w: 300, h: 100 });
});

test('mmKegCoverRect: honors region offset', () => {
  const reg = { x: 10, y: 20, w: 300, h: 200 };
  const r = g.mmKegCoverRect(0.5, 'right', 'cover', reg, 200);
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

function recCtxKeg() {
  return { rects: [], imgs: 0, fillStyle: '#000',
    save(){}, restore(){}, translate(){}, rotate(){}, scale(){}, setTransform(){},
    beginPath(){}, arc(){}, fill(){},
    fillRect(x, y, w, h){ this.rects.push({ x, y, w, h }); },
    drawImage(){ this.imgs++; } };
}
const kegImg = { width: 120, height: 120 };   // "loaded"
const kegNoImg = { width: 0, height: 0 };      // not decoded yet

test('mmDrawKegRoll: cover only (no stamp) when sprite not decoded', () => {
  const c = recCtxKeg();
  g.mmDrawKegRoll(c, { direction: 'right' }, 'cover', 0.5, 300, 200, null, 'wall', kegNoImg, '#3a241a');
  assert.equal(c.imgs, 0);            // no keg stamp
  assert.equal(c.rects.length, 1);    // cover rect drawn
  assert.deepEqual(c.rects[0], { x: 0, y: 0, w: 150, h: 200 });
});

test('mmDrawKegRoll: draws cover + keg stamp when loaded', () => {
  const c = recCtxKeg();
  g.mmDrawKegRoll(c, { direction: 'right' }, 'cover', 0.5, 300, 200, null, 'wall', kegImg, '#3a241a');
  assert.equal(c.rects.length, 1);    // cover
  assert.equal(c.imgs, 1);            // keg stamped (no viewport -> never culled)
});

test('mmDrawKegRoll: no cover rect at cover-phase start, still stamps keg', () => {
  const c = recCtxKeg();
  g.mmDrawKegRoll(c, { direction: 'right' }, 'cover', 0, 300, 200, null, 'wall', kegImg, '#3a241a');
  assert.equal(c.rects.length, 0);    // nothing covered at prog 0
  assert.equal(c.imgs, 1);            // keg present (rolling in from off-edge)
});

// --- auto-fill: opaque bbox + fit factor (pure) ---
// Build RGBA bytes for a w x h image with an opaque rect [x0,x1) x [y0,y1).
function rgbaWithBox(w, h, x0, y0, x1, y1) {
  const d = new Uint8ClampedArray(w * h * 4);   // all transparent (alpha 0)
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      d[(y * w + x) * 4 + 3] = 255;             // opaque alpha
    }
  }
  return d;
}

test('mmOpaqueBox: tight fractions of the opaque region', () => {
  // 10x10 image, opaque 4x6 block at (2,1)..(6,7) -> fracW=4/10, fracH=6/10
  const d = rgbaWithBox(10, 10, 2, 1, 6, 7);
  const b = g.mmOpaqueBox(d, 10, 10);
  assert.ok(Math.abs(b.fracW - 0.4) < 1e-9);
  assert.ok(Math.abs(b.fracH - 0.6) < 1e-9);
});

test('mmOpaqueBox: ignores sub-threshold alpha, null when fully transparent', () => {
  const d = new Uint8ClampedArray(4 * 4 * 4);
  d[3] = 8;                                       // exactly threshold -> NOT counted (> 8)
  assert.equal(g.mmOpaqueBox(d, 4, 4), null);
  d[3] = 9;                                       // just over -> a 1x1 box at (0,0)
  const b = g.mmOpaqueBox(d, 4, 4);
  assert.ok(Math.abs(b.fracW - 0.25) < 1e-9 && Math.abs(b.fracH - 0.25) < 1e-9);
});

test('mmKegFitFactor: smallest opaque dim scales to P', () => {
  // square sprite (iw=ih), opaque 0.5 x 0.8 -> min(0.8, 0.5) = 0.5 -> F = 2
  assert.ok(Math.abs(g.mmKegFitFactor({ fracW: 0.5, fracH: 0.8 }, 100, 100) - 2) < 1e-9);
  // full-bleed square -> min(1,1)=1 -> F=1
  assert.ok(Math.abs(g.mmKegFitFactor({ fracW: 1, fracH: 1 }, 100, 100) - 1) < 1e-9);
  // wide sprite iw=200 ih=100, opaque fracW=0.5 fracH=1 -> width term=0.5*200/100=1.0,
  //   min(1.0, 1.0)=1.0 -> F=1
  assert.ok(Math.abs(g.mmKegFitFactor({ fracW: 0.5, fracH: 1 }, 200, 100) - 1) < 1e-9);
  // null/degenerate -> 1
  assert.equal(g.mmKegFitFactor(null, 100, 100), 1);
});
