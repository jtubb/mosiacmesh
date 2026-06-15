/**
 * wireframeCube: a spinning 3D wireframe cube projected to 2D.
 * Same three guarantees, plus an edge-count check (12 edges →
 * 12 moveTo + 12 lineTo).
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));

const W = 1024, H = 768;

test('wireframeCube — deterministic at same tMs', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.wireframeCube(a, 44444, W, H);
  byKey.wireframeCube(b, 44444, W, H);
  assert.deepStrictEqual(a.__ops, b.__ops);
});

test('wireframeCube — animates (different tMs ⇒ different output)', () => {
  const a = makeRecordingCtx();
  const b = makeRecordingCtx();
  byKey.wireframeCube(a, 1000, W, H);
  byKey.wireframeCube(b, 8000, W, H);
  assert.notDeepStrictEqual(a.__ops, b.__ops);
});

test('wireframeCube — strokes 12 edges', () => {
  const c = makeRecordingCtx();
  byKey.wireframeCube(c, 5000, W, H);
  const moves = c.__ops.filter((o) => o.op === 'moveTo').length;
  const lines = c.__ops.filter((o) => o.op === 'lineTo').length;
  assert.equal(moves, 12);
  assert.equal(lines, 12);
});
