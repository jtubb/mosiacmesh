/**
 * phyllotaxis: a rotating golden-angle sunflower spiral of dots.
 * Same three guarantees as lissajous.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { mirror } from './_animations_mirror.js';
import { makeRecordingCtx } from './_canvas_stub.js';

const W = 1024, H = 768;

test('phyllotaxis — deterministic at same tMs', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  mirror.phyllotaxis(a, 33333, W, H);
  mirror.phyllotaxis(b, 33333, W, H);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('phyllotaxis — animates (different tMs ⇒ different output)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  mirror.phyllotaxis(a, 2000, W, H);
  mirror.phyllotaxis(b, 7000, W, H);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('phyllotaxis — draws 600 dots (one arc + fill each)', () => {
  const c = makeRecordingCtx();
  mirror.phyllotaxis(c, 5000, W, H);
  const arcs = c.__ops.filter((o) => o.op === 'arc').length;
  assert.equal(arcs, 600);
});
