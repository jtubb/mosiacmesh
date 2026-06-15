/**
 * The shared animations module: one ES5 source of truth. Importing it for
 * side-effect sets globalThis.MM_ANIMATIONS. Each entry is self-describing.
 */
import { test } from 'node:test';
import assert from 'node:assert';

test('importing js/animations.js populates MM_ANIMATIONS with well-formed entries', async () => {
  await import('../../../js/animations.js');
  const list = globalThis.MM_ANIMATIONS;
  assert.ok(Array.isArray(list), 'MM_ANIMATIONS should be an array');
  const keys = list.map((a) => a.key);
  for (const k of ['bouncingBalls', 'lissajous', 'phyllotaxis', 'wireframeCube']) {
    assert.ok(keys.includes(k), `missing animation "${k}"`);
  }
  for (const a of list) {
    assert.equal(typeof a.key, 'string');
    assert.equal(typeof a.label, 'string');
    assert.equal(typeof a.description, 'string');
    assert.equal(typeof a.draw, 'function');
  }
});
