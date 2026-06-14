// tests/unit/js/store-cache-progress.test.js
// store.setCacheProgress — the live CACHE_PROGRESS hook that makes the Fleet
// cache chip climb without a page reload (fix 2026-06-14: the broadcast was
// never handled, so store.displays cache fields were frozen at hydrate()).
import { test } from 'node:test';
import assert from 'node:assert';
import { makeStore } from '../../../js/timeline/store.js';

test('setCacheProgress: status cached records the seg_key', () => {
  const s = makeStore();
  s.displays = [{ clientKey: 'ipad1', cacheMode: 'lighttpd-localhost', cachedSegments: [], expectedSegments: 1 }];
  s.setCacheProgress({ clientKey: 'ipad1', token: 't0', n: 0, status: 'cached', percent: 100, mbps: 1.2 });
  const d = s.displays[0];
  assert.deepEqual(d.cachedSegments, ['t0_0']);
  assert.equal(d.cachePushProgress.status, 'cached');
});

test('setCacheProgress: in-flight push reflects progress but adds no seg', () => {
  const s = makeStore();
  s.displays = [{ clientKey: 'ipad1', cachedSegments: [] }];
  s.setCacheProgress({ clientKey: 'ipad1', token: 't0', n: 0, status: 'pushing', percent: 40 });
  assert.deepEqual(s.displays[0].cachedSegments, []);
  assert.equal(s.displays[0].cachePushProgress.status, 'pushing');
  assert.equal(s.displays[0].cachePushProgress.percent, 40);
});

test('setCacheProgress: dedupes a seg_key already cached', () => {
  const s = makeStore();
  s.displays = [{ clientKey: 'ipad1', cachedSegments: ['t0_0'] }];
  s.setCacheProgress({ clientKey: 'ipad1', token: 't0', n: 0, status: 'cached' });
  assert.deepEqual(s.displays[0].cachedSegments, ['t0_0']);
});

test('setCacheProgress: unknown client is a no-op', () => {
  const s = makeStore();
  s.displays = [];
  s.setCacheProgress({ clientKey: 'ghost', status: 'cached', token: 't', n: 0 });
  assert.equal(s.displays.length, 0);
});

test('setCacheProgress: missing payload/clientKey is a no-op', () => {
  const s = makeStore();
  s.displays = [{ clientKey: 'ipad1', cachedSegments: [] }];
  s.setCacheProgress(null);
  s.setCacheProgress({});
  assert.deepEqual(s.displays[0].cachedSegments, []);
});
