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
