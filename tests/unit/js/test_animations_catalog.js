/**
 * The admin-side animation catalog. The playlist editor reads this to
 * populate the SCRIPT-mode <select>. It must mirror the index.html
 * registry keys (bouncingBalls + the batch-1 three) and supply a
 * human label + description for each.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { ANIMATIONS } from '../../../js/timeline/animations-catalog.js';
import { mirror } from './_animations_mirror.js';

test('catalog — entries have key/label/description', () => {
  assert.ok(Array.isArray(ANIMATIONS) && ANIMATIONS.length >= 4);
  for (const a of ANIMATIONS) {
    assert.equal(typeof a.key, 'string');
    assert.equal(typeof a.label, 'string');
    assert.equal(typeof a.description, 'string');
    assert.ok(a.key.length > 0 && a.label.length > 0);
  }
});

test('catalog — includes the batch-1 animations', () => {
  const keys = ANIMATIONS.map((a) => a.key);
  for (const k of ['bouncingBalls', 'lissajous', 'phyllotaxis', 'wireframeCube']) {
    assert.ok(keys.includes(k), `catalog missing "${k}"`);
  }
});

test('catalog — every batch-1 mirror animation has a catalog entry', () => {
  const keys = ANIMATIONS.map((a) => a.key);
  for (const k of Object.keys(mirror)) {
    assert.ok(keys.includes(k), `catalog missing mirrored animation "${k}"`);
  }
});
