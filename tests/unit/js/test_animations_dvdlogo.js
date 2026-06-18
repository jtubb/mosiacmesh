/**
 * dvdLogo: a logo bouncing off edges via a closed-form triangle wave on tMs
 * (not an integrator — so every screen agrees on position and bounce color).
 * Determinism, animates, draws the label text.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));

const W = 1024, H = 768;

test('dvdLogo — deterministic at same tMs', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.dvdLogo(a, 17000, W, H);
  byKey.dvdLogo(b, 17000, W, H);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('dvdLogo — animates (different tMs ⇒ different output)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.dvdLogo(a, 1000, W, H);
  byKey.dvdLogo(b, 12000, W, H);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('dvdLogo — draws the label', () => {
  const c = makeRecordingCtx();
  byKey.dvdLogo(c, 5000, W, H);
  const texts = c.__ops.filter((o) => o.op === 'fillText');
  assert.equal(texts.length, 1);
  assert.equal(texts[0].args[0], 'MOSAICMESH');
});
