import { test } from 'node:test';
import assert from 'node:assert';
import { mediaItemsMissingPlayType, playTypeLabel } from '../../../js/timeline/content/content-items.js';

test('mediaItemsMissingPlayType: media without playmode is flagged', () => {
  const items = [{ file: 'a.mp4' }, { file: 'b.png', playmode: 'SEGMENT' }];
  const missing = mediaItemsMissingPlayType(items);
  assert.equal(missing.length, 1);
  assert.equal(missing[0].file, 'a.mp4');
});

test('mediaItemsMissingPlayType: animations are exempt', () => {
  assert.equal(mediaItemsMissingPlayType([{ file: 'x', playmode: 'SCRIPT' }]).length, 0);
});

test('mediaItemsMissingPlayType: all valid modes satisfy', () => {
  const items = [{file:'a',playmode:'SEGMENT'},{file:'b',playmode:'FULL'},{file:'c',playmode:'INDIVIDUAL'}];
  assert.equal(mediaItemsMissingPlayType(items).length, 0);
});

test('mediaItemsMissingPlayType: empty/undefined safe', () => {
  assert.equal(mediaItemsMissingPlayType([]).length, 0);
  assert.equal(mediaItemsMissingPlayType(undefined).length, 0);
});

test('playTypeLabel maps modes to labels', () => {
  assert.equal(playTypeLabel('SEGMENT'), 'Mesh');
  assert.equal(playTypeLabel('FULL'), 'Mirror');
  assert.equal(playTypeLabel('INDIVIDUAL'), 'Per-screen');
  assert.equal(playTypeLabel('SCRIPT'), 'Animation');
  assert.equal(playTypeLabel(undefined), '— pick play type —');
});
