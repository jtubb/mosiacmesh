import { test } from 'node:test';
import assert from 'node:assert';
import { buildContentItems, contentItemToPlaylistItem } from '../../../js/timeline/content/content-items.js';

const media = {
  images: ['/media/server/images/logo.png'],
  videos: ['/media/server/videos/promo.mp4'],
  videoDurations: { '/media/server/videos/promo.mp4': 30 },
};
const animations = [{ key: 'lissajous', label: 'Lissajous curve', description: 'x' }];

test('buildContentItems merges media + animations with correct kinds/refs', () => {
  const items = buildContentItems({ media, animations });
  const byRef = Object.fromEntries(items.map((i) => [i.ref, i]));
  assert.equal(byRef['/media/server/images/logo.png'].kind, 'image');
  assert.equal(byRef['/media/server/images/logo.png'].name, 'logo.png');
  assert.equal(byRef['/media/server/videos/promo.mp4'].kind, 'video');
  assert.equal(byRef['/media/server/videos/promo.mp4'].duration, 30);
  assert.equal(byRef['lissajous'].kind, 'animation');
  assert.equal(byRef['lissajous'].label, 'Lissajous curve');
});

test('buildContentItems tolerates empty inputs', () => {
  assert.deepEqual(buildContentItems({}), []);
});

test('contentItemToPlaylistItem: animation -> SCRIPT, no duration (Auto)', () => {
  const it = contentItemToPlaylistItem({ kind: 'animation', ref: 'lissajous', name: 'lissajous' });
  assert.deepEqual(it, { file: 'lissajous', playmode: 'SCRIPT' });
});

test('contentItemToPlaylistItem: media -> just file (Auto duration, default playmode)', () => {
  const vid = contentItemToPlaylistItem({ kind: 'video', ref: '/media/server/videos/promo.mp4', duration: 30 });
  assert.deepEqual(vid, { file: '/media/server/videos/promo.mp4' });
  const img = contentItemToPlaylistItem({ kind: 'image', ref: '/media/server/images/logo.png' });
  assert.deepEqual(img, { file: '/media/server/images/logo.png' });
});
