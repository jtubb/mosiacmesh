/**
 * phyllotaxis: a rotating golden-angle sunflower spiral of dots.
 * Same three guarantees as lissajous.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));

const W = 1024, H = 768;

test('phyllotaxis — deterministic at same tMs', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.phyllotaxis(a, 33333, W, H);
  byKey.phyllotaxis(b, 33333, W, H);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('phyllotaxis — animates (different tMs ⇒ different output)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.phyllotaxis(a, 2000, W, H);
  byKey.phyllotaxis(b, 7000, W, H);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('phyllotaxis — draws 600 dots (one arc + fill each)', () => {
  const c = makeRecordingCtx();
  byKey.phyllotaxis(c, 5000, W, H);
  const arcs = c.__ops.filter((o) => o.op === 'arc').length;
  assert.equal(arcs, 600);
});
