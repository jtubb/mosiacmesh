import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/transitions.js');
const S = globalThis.mmTransitionState;

const FADE = { name: 'fade', params: { duration: 1000, audioFade: true } };
const WIPE_SCREEN = { name: 'wipe', params: { direction: 'left', scope: 'screen', duration: 1000 } };
const WIPE_WALL = { name: 'wipe', params: { direction: 'left', scope: 'wall', duration: 1000 } };

test('fade-in: opacity ramps 0->1 over startDur', () => {
  assert.equal(S(FADE, null, 0, 10000, null).opacity, 0);
  assert.ok(Math.abs(S(FADE, null, 500, 10000, null).opacity - 0.5) < 1e-9);
  assert.equal(S(FADE, null, 1000, 10000, null).role, 'none');   // past the window
});

test('fade-out: opacity ramps 1->0 over endDur before the end', () => {
  const st = S(null, FADE, 9500, 10000, null);    // 500ms into the 1000ms out-window
  assert.equal(st.role, 'out');
  assert.ok(Math.abs(st.opacity - 0.5) < 1e-9);
  assert.ok(Math.abs(S(null, FADE, 10000, 10000, null).opacity - 0) < 1e-9);
});

test('no transition mid-item -> role none, opacity 1', () => {
  const st = S(FADE, FADE, 5000, 10000, null);
  assert.equal(st.role, 'none');
  assert.equal(st.opacity, 1);
  assert.equal(st.wipe, null);
});

test('wipe per-screen: reveal == progress, opacity 1', () => {
  const st = S(WIPE_SCREEN, null, 250, 10000, null);
  assert.equal(st.opacity, 1);
  assert.ok(Math.abs(st.wipe.reveal - 0.25) < 1e-9);
  assert.equal(st.wipe.direction, 'left');
});

test('wipe wall (left): front sweeps right->left over a panel\'s mirrored window', () => {
  // 'left' front travels right->left, so a panel at global x [0.5, 0.667] reveals
  // over the MIRRORED window F in [1-0.667, 1-0.5] = [0.333, 0.5]. front F = offsetMs/dur.
  const rect = { x: 0.5, y: 0, w: 1 / 6, h: 1 };
  assert.equal(S(WIPE_WALL, null, 300, 10000, rect).wipe.reveal, 0);          // F=0.30 < 0.333 -> 0
  assert.ok(Math.abs(S(WIPE_WALL, null, 417, 10000, rect).wipe.reveal - 0.5) < 2e-2); // F=0.417 mid
  assert.equal(S(WIPE_WALL, null, 500, 10000, rect).wipe.reveal, 1);          // F=0.50 >= 0.5 -> 1
});

test('wipe wall falls back to per-screen when rect is null', () => {
  assert.ok(Math.abs(S(WIPE_WALL, null, 250, 10000, null).wipe.reveal - 0.25) < 1e-9);
});

test('mmApplyTransition sets opacity for fade', () => {
  const el = { style: {} };
  globalThis.mmApplyTransition(el, null, S(FADE, null, 500, 10000, null));
  assert.equal(el.style.opacity, '0.5');
});

test('mmApplyTransition slides the cover for a wipe (reveal 0.25, left)', () => {
  const el = { style: {} };
  const cover = { style: {} };
  globalThis.mmApplyTransition(el, cover, S(WIPE_SCREEN, null, 250, 10000, null));
  assert.equal(el.style.opacity, '1');
  // left wipe revealing 25%: cover gets a translate transform
  assert.ok(/translate/.test(cover.style.webkitTransform || cover.style.transform));
});

// --- orientation-aware wall wipe (rotated panels) -------------------------
// meshQuad corner order is [TL, TR, BR, BL] in normalized GLOBAL coords; a
// physically rotated panel has its corners placed accordingly. The cover must
// slide along the LOCAL framebuffer axis that maps to the global wipe direction
// (same quad the content warp consumes), so rotated rows don't wipe backwards.
const Slide = globalThis.mmWipeSlide;
const QUAD_UP = [[0, 0], [1, 0], [1, 1], [0, 1]];   // upright
const QUAD_180 = [[1, 1], [0, 1], [0, 0], [1, 0]];  // 180°-rotated
const QUAD_90 = [[1, 0], [1, 1], [0, 1], [0, 0]];   // 90°-rotated

test('mmWipeSlide: upright panel slides along the global direction', () => {
  const s = Slide('down', QUAD_UP);
  assert.ok(Math.abs(s.x) < 1e-9 && Math.abs(s.y - 1) < 1e-9);   // local +Y (down)
});

test('mmWipeSlide: 180° panel inverts the local slide axis', () => {
  const s = Slide('down', QUAD_180);
  assert.ok(Math.abs(s.x) < 1e-9 && Math.abs(s.y + 1) < 1e-9);   // local -Y (up)
});

test('mmWipeSlide: 90° panel swaps to the horizontal local axis', () => {
  const s = Slide('down', QUAD_90);
  assert.ok(Math.abs(s.y) < 1e-9 && Math.abs(Math.abs(s.x) - 1) < 1e-9);
});

test('mmWipeSlide: degenerate/missing quad falls back to global axis', () => {
  const s = Slide('left', null);
  assert.ok(Math.abs(s.x + 1) < 1e-9 && Math.abs(s.y) < 1e-9);
});

const WIPE_WALL_DOWN = { name: 'wipe', params: { direction: 'down', scope: 'wall', duration: 1000 } };
const WIPE_SCREEN_DOWN = { name: 'wipe', params: { direction: 'down', scope: 'screen', duration: 1000 } };

test('wipe wall (down): a single coherent front sweeps top->bottom', () => {
  // The user-facing acceptance: a 'down' wall wipe must reveal the TOP panel before
  // the BOTTOM panel (one front travelling top->bottom), not the reverse.
  const top = { x: 0, y: 0, w: 1, h: 1 / 3 };       // global y [0, 0.333]
  const bot = { x: 0, y: 2 / 3, w: 1, h: 1 / 3 };   // global y [0.667, 1]
  // Early (F=0.2): top revealing, bottom still fully covered.
  assert.ok(S(WIPE_WALL_DOWN, null, 200, 1000, top).wipe.reveal > 0);
  assert.equal(S(WIPE_WALL_DOWN, null, 200, 1000, bot).wipe.reveal, 0);
  // Late (F=0.8): top fully done, bottom now revealing.
  assert.equal(S(WIPE_WALL_DOWN, null, 800, 1000, top).wipe.reveal, 1);
  assert.ok(S(WIPE_WALL_DOWN, null, 800, 1000, bot).wipe.reveal > 0);
});

test('screen scope is orientation-aware too: synchronized timing, grid-correct direction', () => {
  // A panel mid-wall, rotated 180°. screen scope must NOT stagger by position
  // (reveal == progress, every panel in lockstep) but MUST still slide along the
  // grid-correct local axis (down -> local-up on a 180° panel).
  const rect = { x: 0.5, y: 0, w: 1 / 6, h: 1 };
  const st = S(WIPE_SCREEN_DOWN, null, 250, 1000, rect, QUAD_180);
  assert.ok(Math.abs(st.wipe.reveal - 0.25) < 1e-9);   // synchronized: reveal == p, not _wallReveal
  assert.ok(Math.abs(st.wipe.slide.y + 1) < 1e-9);     // grid-aware: slides local-up
});

test('wall wipe carries an orientation-aware slide for a rotated panel', () => {
  const rect = { x: 0, y: 0, w: 1, h: 1 };
  const st = S(WIPE_WALL_DOWN, null, 250, 1000, rect, QUAD_180);
  assert.ok(st.wipe.slide && Math.abs(st.wipe.slide.y + 1) < 1e-9);   // slides local-up
});

test('wipe state exposes front(=p) and scope for the affine cover path', () => {
  const st = S(WIPE_WALL_DOWN, null, 500, 1000, { x: 0, y: 0, w: 1, h: 1 }, QUAD_UP);
  assert.ok(Math.abs(st.wipe.front - 0.5) < 1e-9);   // raw progress, not per-screen reveal
  assert.equal(st.wipe.scope, 'wall');
});

// --- affine cover fill geometry (global-space black rect) -----------------
const Cover = globalThis.mmWipeCoverRect;

test('cover rect (wall down): covers the un-revealed BOTTOM of the global canvas', () => {
  assert.deepEqual(Cover('wall', 'down', 0, 200, 100, null), { x: 0, y: 0, w: 200, h: 100 }); // f=0 all black
  assert.deepEqual(Cover('wall', 'down', 0.5, 200, 100, null), { x: 0, y: 50, w: 200, h: 50 });
  assert.equal(Cover('wall', 'down', 1, 200, 100, null), null);                                // fully revealed
});

test('cover rect (wall up): covers the un-revealed TOP', () => {
  assert.deepEqual(Cover('wall', 'up', 0.5, 200, 100, null), { x: 0, y: 0, w: 200, h: 50 });
});

test('cover rect (wall right/left): horizontal split', () => {
  assert.deepEqual(Cover('wall', 'right', 0.25, 200, 100, null), { x: 50, y: 0, w: 150, h: 100 });
  assert.deepEqual(Cover('wall', 'left', 0.25, 200, 100, null), { x: 0, y: 0, w: 150, h: 100 });
});

test('cover rect (screen scope): confined to the panel quad bbox', () => {
  // quad spans global x[0.5,0.667], full height; GW=GH=100. down at f=0.5.
  const quad = [[0.5, 0], [2 / 3, 0], [2 / 3, 1], [0.5, 1]];
  const r = Cover('screen', 'down', 0.5, 100, 100, quad);
  assert.ok(Math.abs(r.x - 50) < 1e-6 && Math.abs(r.w - (200 / 3 - 50)) < 1e-6); // x within bbox
  assert.ok(Math.abs(r.y - 50) < 1e-6 && Math.abs(r.h - 50) < 1e-6);             // lower half of full-height bbox
});

test('mmApplyTransition uses slide vector (180° panel slides up for a down wipe)', () => {
  const el = { style: {} }, cover = { style: {} };
  const st = S(WIPE_WALL_DOWN, null, 250, 1000, { x: 0, y: 0, w: 1, h: 1 }, QUAD_180);
  globalThis.mmApplyTransition(el, cover, st);
  // reveal 0.25, 180° down -> translate(0%,-25%)
  assert.ok(/translate\(0%,-25%\)/.test(cover.style.webkitTransform || cover.style.transform));
});
