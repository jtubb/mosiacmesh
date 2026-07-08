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
