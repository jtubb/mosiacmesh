/**
 * wordClock: a 13x8 letter grid; lit letters spell the rounded time.
 * Determinism in (tMs, nowMs); advances with nowMs. Every cell is drawn
 * (dim or lit) so fillText count == 13*8 = 104.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));

const W = 1024, H = 768;
const T_1010 = Date.UTC(2026, 0, 1, 10, 10, 0); // "ten past ten"
const T_0345 = Date.UTC(2026, 0, 1, 3, 45, 0);  // "quarter to four"

test('wordClock — deterministic at same (tMs, nowMs)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.wordClock(a, 0, W, H, T_1010);
  byKey.wordClock(b, 0, W, H, T_1010);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('wordClock — different times light different words', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.wordClock(a, 0, W, H, T_1010);
  byKey.wordClock(b, 0, W, H, T_0345);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('wordClock — draws every cell of the 13x8 grid', () => {
  const c = makeRecordingCtx();
  byKey.wordClock(c, 0, W, H, T_1010);
  const texts = c.__ops.filter((o) => o.op === 'fillText').length;
  assert.equal(texts, 104);
});
