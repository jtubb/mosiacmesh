/**
 * analogClock: hour/minute/second hands driven by the shared wall clock
 * (nowMs = GoTime.now()). Determinism is in (tMs, nowMs); the clock advances
 * with nowMs, so the "animates" check varies nowMs.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));

const W = 1024, H = 768;
const NOON = Date.UTC(2026, 0, 1, 12, 0, 0);

test('analogClock — deterministic at same (tMs, nowMs)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.analogClock(a, 0, W, H, NOON);
  byKey.analogClock(b, 0, W, H, NOON);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('analogClock — advances with nowMs (different time ⇒ different output)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.analogClock(a, 0, W, H, NOON);
  byKey.analogClock(b, 0, W, H, NOON + 7 * 60 * 1000); // +7 minutes
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('analogClock — draws face, 12 ticks, 3 hands', () => {
  const c = makeRecordingCtx();
  byKey.analogClock(c, 0, W, H, NOON);
  const arcs = c.__ops.filter((o) => o.op === 'arc').length;
  const strokes = c.__ops.filter((o) => o.op === 'stroke').length;
  assert.equal(arcs, 1);          // the face circle
  assert.equal(strokes, 1 + 12 + 3); // face + 12 ticks + 3 hands
});
