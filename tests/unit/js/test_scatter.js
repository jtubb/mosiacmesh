import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/transitions.js');
await import('../../../js/animations.js');       // mmMeshTransform
await import('../../../js/mesh-viewport.js');     // mmMeshViewport + mmStampSprite
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

function recCtx() {
  return { rects: [], imgs: 0, arcs: 0, fills: 0, rots: 0, fillStyle: '#000',
    save(){}, restore(){}, translate(){}, rotate(){ this.rots++; }, scale(){}, setTransform(){},
    beginPath(){}, arc(){ this.arcs++; }, fill(){ this.fills++; },
    fillRect(x,y,w,h){ this.rects.push({x,y,w,h}); },
    drawImage(){ this.imgs++; } };
}
const fakeImg = { width: 100, height: 120 };          // "loaded"
const noImg = { width: 0, height: 0 };                 // not yet decoded

// Install a minimal canvas-producing document so mmBuildSpriteAtlas can bake.
function withFakeDocument(fn) {
  const prev = globalThis.document;
  globalThis.document = { createElement() {
    const cx = { drawImage(){}, translate(){}, rotate(){}, scale(){} };
    return { width: 0, height: 0, getContext() { return cx; } };
  } };
  try { return fn(); } finally {
    if (prev === undefined) delete globalThis.document; else globalThis.document = prev;
  }
}

test('mmDrawScatter: disc only when sprite not loaded', () => {
  const c = recCtx();
  g.mmDrawScatter(c, { count: 40 }, 'cover', 0.5, 300, 200, null, 'wall', 7, noImg, '#140d06');
  assert.equal(c.imgs, 0);          // no stamps
  assert.ok(c.arcs >= 1);           // backing disc drawn
});
test('mmDrawScatter: stamps count copies + giant when loaded', () => {
  const c = recCtx();
  g.mmDrawScatter(c, { count: 40 }, 'cover', 0.6, 300, 200, null, 'wall', 7, fakeImg, '#140d06');
  assert.equal(c.imgs, 41);         // 40 copies + 1 giant
});
test('mmDrawScatter: cover=0 draws nothing visible', () => {
  const c = recCtx();
  g.mmDrawScatter(c, { count: 40 }, 'reveal', 1, 300, 200, null, 'wall', 7, fakeImg, '#140d06');  // c=0
  assert.equal(c.arcs, 0);          // no disc at c=0
});
test('mmDrawScatter: fallback path rotates per copy when no canvas API', () => {
  const c = recCtx();
  const im = { width: 100, height: 120 };               // fresh img (own _mmAtlas)
  g.mmDrawScatter(c, { count: 40 }, 'cover', 0.6, 300, 200, null, 'wall', 7, im, '#140d06');
  assert.equal(c.imgs, 41);                              // 40 copies + giant
  assert.equal(c.rots, 41);                              // every copy + giant rotates the main ctx
  assert.equal(im._mmAtlas, null);                       // atlas unavailable -> cached null, no rebuild
});
test('mmBuildSpriteAtlas: null without canvas API, N pre-rotated canvases with it', () => {
  assert.equal(g.mmBuildSpriteAtlas(fakeImg, 96, 24), null);   // node default: no document
  withFakeDocument(() => {
    const atlas = g.mmBuildSpriteAtlas(fakeImg, 96, 24);
    assert.equal(atlas.canvases.length, 24);
    assert.equal(atlas.dim, Math.ceil(96 * 1.42));
  });
});
test('mmDrawScatter: atlas path blits copies without per-copy rotate', () => {
  withFakeDocument(() => {
    const c = recCtx();
    const im = { width: 100, height: 120 };             // fresh img -> bakes atlas
    g.mmDrawScatter(c, { count: 40 }, 'cover', 0.6, 300, 200, null, 'wall', 7, im, '#140d06');
    assert.equal(c.imgs, 41);                            // 40 atlas blits + giant
    assert.equal(c.rots, 1);                             // ONLY the giant rotates; copies are plain blits
    assert.ok(im._mmAtlas && im._mmAtlas.canvases.length === 24);
  });
});
test('mmDrawScatter: ?sdisc=0 (nodisc) skips the backing disc, still stamps sprites', () => {
  const c = recCtx();
  g._mmSdbg = { nodisc: true };
  try {
    g.mmDrawScatter(c, { count: 40 }, 'cover', 0.6, 300, 200, null, 'wall', 7, fakeImg, '#140d06');
  } finally { delete g._mmSdbg; }
  assert.equal(c.arcs, 0);          // backing disc suppressed
  assert.equal(c.imgs, 41);         // copies + giant still drawn
});
test('mmDrawScatter: culls copies outside this screen when a viewport is given', () => {
  withFakeDocument(() => {
    const c = recCtx();
    const im = { width: 100, height: 120 };
    // Left-half quad: this screen sees only the left 50% of a 1000x800 wall.
    const LEFT = [[0, 0], [0.5, 0], [0.5, 1], [0, 1]];
    g.mmDrawScatter(c, { count: 40, scope: 'wall' }, 'cover', 0.6, 1000, 800, LEFT, 'wall', 7, im, '#140d06', 200, 160);
    assert.ok(c.imgs < 41, 'some wall-spanning copies should be culled off this screen');
    assert.ok(c.imgs > 0, 'copies overlapping this screen still draw');
  });
});

test('mmDrawScatter: giant size scales with giantScale (param, default 0.2, knob)', () => {
  const im = { width: 100, height: 120 };
  function giantSize(params, sd) {
    const calls = [];
    const orig = g.mmStampSprite;
    g.mmStampSprite = function (ctx, vp, img, gx, gy, globalSize, angle) { calls.push(globalSize); return true; };
    if (sd) { g._mmSdbg = sd; }
    try {
      g.mmDrawScatter(recCtx(), params, 'cover', 0.6, 1000, 800, null, 'wall', 7, im, '#000');
    } finally {
      g.mmStampSprite = orig;
      if (sd) { delete g._mmSdbg; }
    }
    return calls[calls.length - 1];           // last stamp == giant
  }
  const base = giantSize({ count: 5, giantScale: 0.6 });
  const half = giantSize({ count: 5, giantScale: 0.3 });
  assert.ok(Math.abs(half / base - 0.5) < 1e-6);   // linear in giantScale
  const dflt = giantSize({ count: 5 });             // missing giantScale -> default 0.2 (not 0.6, not old 1.43)
  const ref02 = giantSize({ count: 5, giantScale: 0.2 });
  assert.ok(Math.abs(dflt - ref02) < 1e-6);
  const knob = giantSize({ count: 5, giantScale: 0.6 }, { gscale: 0.3 });
  assert.ok(Math.abs(knob - half) < 1e-6);          // ?sgscale overrides the param
});

test('mmDrawScatter: full-wall viewport draws more than a half-wall view + keeps the giant', () => {
  withFakeDocument(() => {
    // A full-wall viewport's globalRect is the wall bbox itself, so copies that
    // scatter PAST the wall edge are still (correctly) culled. The regression
    // guarantee is directional: a wider view culls fewer copies than a partial
    // one, and the centered giant is always visible.
    const im = { width: 100, height: 120 };
    const FULL = [[0, 0], [1, 0], [1, 1], [0, 1]];
    const HALF = [[0, 0], [0.5, 0], [0.5, 1], [0, 1]];
    const full = recCtx();
    g.mmDrawScatter(full, { count: 40, scope: 'wall' }, 'cover', 0.6, 1000, 800, FULL, 'wall', 7, im, '#140d06', 1000, 800);
    const half = recCtx();
    g.mmDrawScatter(half, { count: 40, scope: 'wall' }, 'cover', 0.6, 1000, 800, HALF, 'wall', 7, im, '#140d06', 200, 160);
    assert.ok(full.imgs > half.imgs, 'a full-wall view culls fewer copies than a half-wall view');
    assert.ok(full.imgs <= 41, 'never more than 40 copies + giant');
    assert.ok(full.imgs >= 1, 'the centered giant is always within the full wall');
  });
});
