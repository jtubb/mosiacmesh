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

test('wipe wall (left): a panel reveals over its own bbox sub-window', () => {
  // wipe duration 1000ms = sweep window; front F = offsetMs/1000 sweeps 0..1.
  // Panel bbox spans global x [0.5, 0.667] (edge x=0.5, width 1/6).
  const rect = { x: 0.5, y: 0, w: 1 / 6, h: 1 };
  assert.equal(S(WIPE_WALL, null, 400, 10000, rect).wipe.reveal, 0);          // F=0.40 < 0.5  -> 0
  assert.ok(Math.abs(S(WIPE_WALL, null, 583, 10000, rect).wipe.reveal - 0.5) < 1e-2); // F=0.583 mid
  assert.equal(S(WIPE_WALL, null, 700, 10000, rect).wipe.reveal, 1);          // F=0.70 > 0.667 -> 1
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
