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

test('_renderTokenOf strips seg_/full_ prefix + _N suffix; passthrough otherwise', function () {
  const mmCache = loadMmCache();
  assert.strictEqual(mmCache._renderTokenOf('seg_5ad20e2c98c3_0'), '5ad20e2c98c3');
  assert.strictEqual(mmCache._renderTokenOf('seg_5ad20e2c98c3_1'), '5ad20e2c98c3');
  assert.strictEqual(mmCache._renderTokenOf('full_9c303e784e6c_2'), '9c303e784e6c');
  assert.strictEqual(mmCache._renderTokenOf('T1'), 'T1');
});

test('supersede KEEPS sibling segments of the same render (no evict of seg_0 by seg_1)', function () {
  const mmCache = loadMmCache();
  mmCache._reset();
  const b = mockBackend();
  mmCache.registerBackend(b);
  b.store['seg_5ad20e2c98c3_0'] = 'u0';
  mmCache._recordToken('seg_5ad20e2c98c3_0', 'G1');
  mmCache._supersede('G1', 'seg_5ad20e2c98c3_1');       // sibling seg, SAME render token
  assert.deepStrictEqual(b.evicted, []);                 // seg_0 must NOT be evicted
  assert.ok(mmCache._tokens['seg_5ad20e2c98c3_0']);      // seg_0 still tracked
  assert.ok(mmCache._tokens['seg_5ad20e2c98c3_1']);      // seg_1 recorded
});

test('supersede DOES evict a DIFFERENT render token in the group', function () {
  const mmCache = loadMmCache();
  mmCache._reset();
  const b = mockBackend();
  mmCache.registerBackend(b);
  b.store['seg_oldtoken1234_0'] = 'u';
  mmCache._recordToken('seg_oldtoken1234_0', 'G1');
  mmCache._supersede('G1', 'seg_5ad20e2c98c3_0');       // NEW render supersedes the old
  assert.deepStrictEqual(b.evicted, ['seg_oldtoken1234_0']);
  assert.strictEqual(mmCache._tokens['seg_oldtoken1234_0'], undefined);
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

test('localSrc delegates to backend, null when uncached', function () {
  const mmCache = loadMmCache();
  mmCache._reset();
  const b = mockBackend();
  mmCache.registerBackend(b);
  assert.strictEqual(mmCache.localSrc('T1'), null);
  b.store['T1'] = 'u';
  assert.strictEqual(mmCache.localSrc('T1'), 'local://T1');
});

test('handlePrecache: success acks CACHED; failure acks CACHE_FAILED', function () {
  const mmCache = loadMmCache();
  mmCache._reset();
  const acks = [];
  mmCache.onAck = function (req, payload) { acks.push([req, payload.token]); };
  // success backend
  const ok = mockBackend(); mmCache.registerBackend(ok);
  mmCache.handlePrecache({ group: 'G1', url: 'http://c/seg', token: 'T1' });
  assert.deepStrictEqual(ok.fetched, [['http://c/seg', 'T1']]);
  assert.deepStrictEqual(acks, [['CACHED', 'T1']]);
  assert.strictEqual(mmCache.state('T1'), 'cached');
  // failure backend
  const bad = mockBackend();
  bad.fetchToCache = function (url, token, onDone, onFail) { onFail(token, 'net'); };
  mmCache.registerBackend(bad); acks.length = 0;
  mmCache.handlePrecache({ group: 'G2', url: 'http://c/seg2', token: 'T2' });
  assert.deepStrictEqual(acks, [['CACHE_FAILED', 'T2']]);
  assert.strictEqual(mmCache.state('T2'), 'failed');
});

test('clear: delegates to backend.clear and resets token bookkeeping', function () {
  const mmCache = loadMmCache();
  mmCache._reset();
  let cleared = false;
  const b = mockBackend();
  b.clear = function (onDone) { cleared = true; if (onDone) onDone(); };
  mmCache.registerBackend(b);
  mmCache._recordToken('seg_T1_0', 'G1');           // seed state
  let doneCalled = false;
  mmCache.clear(function () { doneCalled = true; });
  assert.strictEqual(cleared, true);
  assert.strictEqual(doneCalled, true);
  assert.strictEqual(mmCache._order.length, 0);
  assert.strictEqual(Object.keys(mmCache._tokens).length, 0);
  assert.strictEqual(mmCache.state('seg_T1_0'), 'none');  // _tokens truly reset (was 'pending')
});

test('clear: no backend (or backend without clear) just resets + calls onDone', function () {
  const mmCache = loadMmCache();
  mmCache._reset();
  mmCache._recordToken('seg_T1_0', 'G1');
  let doneCalled = false;
  mmCache.clear(function () { doneCalled = true; });   // backend is null
  assert.strictEqual(doneCalled, true);
  assert.strictEqual(mmCache._order.length, 0);
  assert.strictEqual(mmCache.state('seg_T1_0'), 'none');  // _tokens truly reset (was 'pending')
});
