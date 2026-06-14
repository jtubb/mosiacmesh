import { test } from 'node:test';
import assert from 'node:assert';
import { makeStore } from '../../../js/timeline/store.js';

test('contentItems getter merges store.media + MM_ANIMATIONS', () => {
  globalThis.MM_ANIMATIONS = [{ key: 'lissajous', label: 'Lissajous curve', description: 'x' }];
  const s = makeStore();
  s.media = { images: ['/media/server/images/logo.png'], videos: [], videoDurations: {} };
  const items = s.contentItems;
  const kinds = items.map((i) => i.kind).sort();
  assert.deepEqual(kinds, ['animation', 'image']);
});
