/**
 * sunMoonTransit: a body traversing an arc, day/night palette by nowMs.
 * Determinism in (tMs, nowMs); advances with nowMs; draws a gradient
 * background + the body (one arc).
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));

const W = 1024, H = 768;
const NOON = Date.UTC(2026, 0, 1, 12, 0, 0);
const MIDNIGHT = Date.UTC(2026, 0, 1, 0, 0, 0);

test('sunMoonTransit — deterministic at same (tMs, nowMs)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.sunMoonTransit(a, 0, W, H, NOON);
  byKey.sunMoonTransit(b, 0, W, H, NOON);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('sunMoonTransit — day vs night differ', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.sunMoonTransit(a, 0, W, H, NOON);
  byKey.sunMoonTransit(b, 0, W, H, MIDNIGHT);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('sunMoonTransit — draws a gradient background and the body', () => {
  const c = makeRecordingCtx();
  byKey.sunMoonTransit(c, 0, W, H, NOON);
  const grads = c.__ops.filter((o) => o.op === 'createLinearGradient').length;
  const arcs = c.__ops.filter((o) => o.op === 'arc').length;
  assert.equal(grads, 1);
  assert.ok(arcs >= 1, 'expected at least the body arc');
});
