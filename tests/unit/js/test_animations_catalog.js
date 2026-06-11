import { test } from 'node:test';
import assert from 'node:assert';

test('MM_ANIMATIONS entries are well-formed + the four keys exist', async () => {
  await import('../../../js/animations.js');
  const list = globalThis.MM_ANIMATIONS;
  const keys = list.map((a) => a.key);
  for (const k of ['bouncingBalls', 'lissajous', 'phyllotaxis', 'wireframeCube']) {
    assert.ok(keys.includes(k), `missing "${k}"`);
  }
  for (const a of list) {
    assert.equal(typeof a.key, 'string');
    assert.equal(typeof a.label, 'string');
    assert.equal(typeof a.description, 'string');
    assert.equal(typeof a.draw, 'function');
  }
});
