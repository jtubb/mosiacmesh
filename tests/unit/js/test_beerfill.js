import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/transitions.js');

const Pal = globalThis.mmBeerPalette, Phase = globalThis.mmBeerPhase;
const Dur = globalThis.mmBeerDuration, Level = globalThis.mmBeerLevel;

test('mmBeerPalette: known types + default', () => {
  assert.equal(Pal('pale').beerTop, '#F6C744');
  assert.equal(Pal('stout').headH, 0.20);
  assert.equal(Pal('amber').foam, '#F3E0C0');
  assert.equal(Pal('nope').beerTop, '#F6C744');   // unknown -> pale
  assert.equal(Pal(undefined).beerTop, '#F6C744');
});

test('mmBeerPhase: out=fill, in=drain', () => {
  assert.equal(Phase('out'), 'fill');
  assert.equal(Phase('in'), 'drain');
});

test('mmBeerDuration: fillMs on out, drainMs on in, default 2500', () => {
  assert.equal(Dur({ fillMs: 1500, drainMs: 3000 }, 'out'), 1500);
  assert.equal(Dur({ fillMs: 1500, drainMs: 3000 }, 'in'), 3000);
  assert.equal(Dur({}, 'out'), 2500);
  assert.equal(Dur(null, 'in'), 2500);
});

test('mmBeerLevel: fill rises 0->1, drain falls 1->0, clamped', () => {
  assert.equal(Level('fill', 0), 0);
  assert.equal(Level('fill', 1), 1);
  assert.equal(Level('drain', 0), 1);
  assert.equal(Level('drain', 1), 0);
  assert.equal(Level('fill', -0.5), 0);
  assert.equal(Level('drain', 1.5), 0);
});

const Wave = globalThis.mmFoamWaveY, Bub = globalThis.mmBeerBubbles, Foam = globalThis.mmFoamBubbles;

test('mmFoamWaveY: deterministic + amp scaling around baseY', () => {
  const a = Wave(0.5, 1.0, 10, 100), b = Wave(0.5, 1.0, 10, 100);
  assert.equal(a, b);                                  // pure
  assert.ok(Math.abs(a - 100) <= 10 + 1e-9);           // within +/- amp*(0.5+0.3)
  assert.notEqual(Wave(0.5, 1.0, 10, 100), Wave(0.5, 2.0, 10, 100)); // t matters
});

test('mmBeerBubbles: deterministic per seed, ranges, count', () => {
  const x = mmBeerBubbles(7, 20), y = mmBeerBubbles(7, 20), z = mmBeerBubbles(8, 20);
  assert.equal(x.length, 20);
  assert.deepEqual(x, y);                              // same seed -> identical (wall-coherent)
  assert.notDeepEqual(x, z);                           // different seed -> different
  x.forEach(b => {
    assert.ok(b.x >= 0 && b.x < 1 && b.phase >= 0 && b.phase < 1);
    assert.ok(b.r >= 1 && b.r <= 3.4 && b.spd >= 0.45 && b.spd <= 1.25);
  });
});

test('mmFoamBubbles: deterministic, distinct stream from beer bubbles', () => {
  const f = mmFoamBubbles(7, 15), g = mmFoamBubbles(7, 15);
  assert.deepEqual(f, g);
  assert.notDeepEqual(f.map(b => b.x), mmBeerBubbles(7, 15).map(b => b.x)); // different stream
  f.forEach(b => { assert.ok(b.a >= 0.22 && b.a <= 0.62 && b.r >= 1 && b.r <= 4.2); });
});

const State = globalThis.mmTransitionState;

test('mmTransitionState: beerfill end-role = fill phase, level rises', () => {
  const end = { name: 'beerfill', params: { fillMs: 2000, drainMs: 2000, scope: 'wall' } };
  // duration 6000, offset 5000 -> 1000ms into the 2000ms fill (out), progress p=0.5 -> level 0.5
  const st = State(null, end, 5000, 6000, null, null);
  assert.equal(st.role, 'out');
  assert.equal(st.effect.name, 'beerfill');
  assert.equal(st.effect.family, 'mask');
  assert.equal(st.effect.phase, 'fill');
  assert.ok(Math.abs(st.effect.front - 0.5) < 1e-9);   // fill: level == progress
  assert.equal(st.effect.scope, 'wall');
});

test('mmTransitionState: beerfill fill level RISES bottom-up over the window', () => {
  // The midpoint test above can't see direction (1-p == p at 0.5). Check non-midpoint
  // offsets: as the item nears its end (offset grows toward duration), the fill level
  // must INCREASE 0->1 (beer rises). The pre-fix code fed raw p (1->0 on the out role),
  // so it fell 1->0 here (beer receded top->bottom). Falsifiable against that bug.
  const end = { name: 'beerfill', params: { fillMs: 2000, drainMs: 2000, scope: 'wall' } };
  // window = [4000, 6000]. early (offset 4500): p=0.75 -> level 1-0.75 = 0.25
  const early = State(null, end, 4500, 6000, null, null);
  // late (offset 5500): p=0.25 -> level 1-0.25 = 0.75
  const late = State(null, end, 5500, 6000, null, null);
  assert.ok(Math.abs(early.effect.front - 0.25) < 1e-9);   // near-empty early in the fill
  assert.ok(Math.abs(late.effect.front - 0.75) < 1e-9);    // near-full late in the fill
  assert.ok(late.effect.front > early.effect.front);       // rises, not falls
});

test('mmTransitionState: beerfill start-role = drain phase, level falls', () => {
  const start = { name: 'beerfill', params: { fillMs: 2000, drainMs: 2000 } };
  // offset 500 -> p=0.25 into drain -> level 1-0.25 = 0.75
  const st = State(start, null, 500, 6000, null, null);
  assert.equal(st.role, 'in');
  assert.equal(st.effect.phase, 'drain');
  assert.ok(Math.abs(st.effect.front - 0.75) < 1e-9);
  assert.equal(st.effect.scope, 'wall');               // default when unset
});

test('mmTransitionState: beerfill inactive mid-item', () => {
  const end = { name: 'beerfill', params: { fillMs: 2000, drainMs: 2000 } };
  assert.equal(State(null, end, 1000, 6000, null, null).role, 'none');  // 1000 < 6000-2000
});

const Beer = globalThis.mmDrawBeer;

function recCtx() {
  return {
    rects: [], fills: 0, arcs: 0, _grad: { addColorStop() {} },
    fillStyle: '#000', _started: false,
    createLinearGradient() { return this._grad; },
    fillRect(x, y, w, h) { this.rects.push({ x, y, w, h }); },
    beginPath() {}, moveTo() {}, lineTo() {}, closePath() {},
    arc() { this.arcs++; }, fill() { this.fills++; }
  };
}

test('mmDrawBeer: level 0 draws nothing', () => {
  const c = recCtx();
  Beer(c, { beerType: 'pale' }, 'fill', 0, 0, 300, 200, null, 'wall', 1);
  assert.equal(c.rects.length, 0);
});

test('mmDrawBeer: fill draws beer body covering bottom level fraction + pour', () => {
  const c = recCtx();
  Beer(c, { beerType: 'pale' }, 'fill', 0.5, 0, 300, 200, null, 'wall', 1);
  // beer body: a rect whose top is at y=100 (half of 200), height ~100
  const body = c.rects.find(r => Math.abs(r.y - 100) < 1 && Math.abs(r.h - 100) < 1 && r.w === 300);
  assert.ok(body, 'beer body rect present');
  // pour stream present in fill phase: a narrow rect starting at region top (y=0)
  assert.ok(c.rects.some(r => r.y === 0 && r.w < 300 * 0.5), 'pour stream present');
  assert.ok(c.arcs > 0, 'bubbles drawn');
});

test('mmDrawBeer: drain phase draws no pour stream', () => {
  const c = recCtx();
  Beer(c, { beerType: 'pale' }, 'drain', 0.5, 0, 300, 200, null, 'wall', 1);
  assert.ok(!c.rects.some(r => r.y === 0), 'no pour stream rect at region top');
});
