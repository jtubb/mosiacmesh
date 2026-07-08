import { test } from 'node:test';
import assert from 'node:assert';
import { loadMmCache, mockBackend } from './_mmcache_load.js';

test('registerBackend sets the active backend', function () {
  const mmCache = loadMmCache();
  mmCache._reset();
  assert.strictEqual(mmCache.backend, null);
  const b = mockBackend();
  mmCache.registerBackend(b);
  assert.strictEqual(mmCache.backend, b);
});

test('token state: pending -> cached via backend.has', function () {
  const mmCache = loadMmCache();
  mmCache._reset();
  const b = mockBackend();
  mmCache.registerBackend(b);
  assert.strictEqual(mmCache.state('T1'), 'none');
  mmCache._recordToken('T1', 'G1');
  assert.strictEqual(mmCache.state('T1'), 'pending');   // recorded, backend has nothing yet
  b.store['T1'] = 'u';                                  // simulate backend cached it
  assert.strictEqual(mmCache.has('T1'), true);
  assert.strictEqual(mmCache.state('T1'), 'cached');
});

test('evict-on-supersede: new token for a group drops the old file', function () {
  const mmCache = loadMmCache();
  mmCache._reset();
  const b = mockBackend();
  mmCache.registerBackend(b);
  b.store['T1'] = 'u1';
  mmCache._recordToken('T1', 'G1');
  mmCache._supersede('G1', 'T2');                       // new token same group
  assert.deepStrictEqual(b.evicted, ['T1']);            // old evicted
  assert.strictEqual(mmCache.state('T2'), 'pending');   // new recorded
  assert.strictEqual(mmCache._tokens['T1'], undefined); // old forgotten
});

test('size-cap: evicts oldest until under cap', function () {
  const mmCache = loadMmCache();
  mmCache._reset();
  const b = mockBackend();
  b.sizes = {};
  b.size = function (t) { return b.sizes[t] || 0; };
  mmCache.registerBackend(b);
  mmCache.capBytes = 100;
  b.store['A'] = 'u';
  b.sizes['A'] = 60;
  mmCache._recordToken('A', 'G1');
  b.store['B'] = 'u';
  b.sizes['B'] = 60;
  mmCache._recordToken('B', 'G2'); // total 120 > 100
  mmCache._enforceCap();
  assert.deepStrictEqual(b.evicted, ['A']);   // oldest evicted, now 60 <= 100
  assert.strictEqual(mmCache._tokens['A'], undefined);
});
