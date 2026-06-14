import { test } from 'node:test';
import assert from 'node:assert';
import { playlistReadinessForGroup, deviceCacheStatus } from '../../../js/timeline/fleet/fleet-status.js';

test('deviceCacheStatus: cacheMode none → not applicable', () => {
  const s = deviceCacheStatus({ cacheMode: 'none', expectedSegments: 0, cachedSegments: [] });
  assert.equal(s.applicable, false);
  assert.equal(s.label, 'streams (no local cache)');
});

test('deviceCacheStatus: fully cached', () => {
  const s = deviceCacheStatus({ cacheMode: 'lighttpd-localhost', expectedSegments: 4,
    cachedSegments: ['a_0','a_1','a_2','a_3'], cachePushProgress: null });
  assert.equal(s.applicable, true);
  assert.equal(s.cached, 4); assert.equal(s.expected, 4);
  assert.equal(s.percent, 100); assert.equal(s.inFlight, false);
  assert.equal(s.label, 'cached 4/4');
});

test('deviceCacheStatus: in-flight shows mbps', () => {
  const s = deviceCacheStatus({ cacheMode: 'lighttpd-localhost', expectedSegments: 4,
    cachedSegments: ['a_0','a_1'], cachePushProgress: { status: 'active', mbps: 3.25 } });
  assert.equal(s.percent, 50); assert.equal(s.inFlight, true); assert.equal(s.stalled, false);
  assert.equal(s.label, 'downloading 3.2 MB/s');
});

test('deviceCacheStatus: stalled flagged', () => {
  const s = deviceCacheStatus({ cacheMode: 'lighttpd-localhost', expectedSegments: 4,
    cachedSegments: ['a_0'], cachePushProgress: { status: 'stalled', mbps: 0 } });
  assert.equal(s.stalled, true); assert.equal(s.label, 'stalled');
});

test('deviceCacheStatus: caching mode, expected 0 → applicable false, idle', () => {
  const s = deviceCacheStatus({ cacheMode: 'lighttpd-localhost', expectedSegments: 0, cachedSegments: [] });
  assert.equal(s.applicable, false);
  assert.equal(s.label, 'nothing to cache');
});

test('deviceCacheStatus: none -> network state', () => {
  const s = deviceCacheStatus({ cacheMode: 'none' });
  assert.equal(s.state, 'network');
  assert.equal(s.applicable, false);
});

test('deviceCacheStatus: capable + partial -> caching state', () => {
  const s = deviceCacheStatus({ cacheMode: 'lighttpd-localhost',
    cachedSegments: ['t_0'], expectedSegments: 4 });
  assert.equal(s.state, 'caching');
  assert.equal(s.percent, 25);
});

test('deviceCacheStatus: capable + full -> local state', () => {
  const s = deviceCacheStatus({ cacheMode: 'lighttpd-localhost',
    cachedSegments: ['t_0','t_1','t_2','t_3'], expectedSegments: 4 });
  assert.equal(s.state, 'local');
  assert.equal(s.percent, 100);
});

test('playlistReadinessForGroup labels each playlist', () => {
  const playlists = { A: { items: [{ playmode: 'SEGMENT' }] }, B: { items: [{ playmode: 'FULL' }] } };
  const renders = { G1: { A: { state: 'RENDERING', percent: 50 } } };
  const rows = playlistReadinessForGroup('G1', playlists, renders);
  assert.equal(rows.find(r => r.name === 'A').label, 'rendering… 50%');
  assert.equal(rows.find(r => r.name === 'B').label, 'ready'); // N/A → ready
  assert.equal(rows.find(r => r.name === 'B').ready, true);
});
