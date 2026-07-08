// tests/unit/js/mmcache-backend.test.js
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

function loadBackend() {
  const code = fs.readFileSync(new URL('../../../js/mmCacheBackendMmvideo.js', import.meta.url), 'utf8');
  const navs = [];
  const sandbox = {
    window: {},
    document: {
      documentElement: { appendChild: function () {} },
      createElement: function () { return { style: {}, parentNode: null,
        set src(v) { navs.push(v); } }; }
    },
    setTimeout: function (fn) { return 0; },   // don't auto-remove during the test
    encodeURIComponent: encodeURIComponent
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return { w: sandbox.window, navs: navs };
}

test('fetchToCache navigates mmcache://fetch and resolves on __mmCacheDone', function () {
  const { w, navs } = loadBackend();
  const b = w._mmCacheBackendMmvideo;
  let done = null;
  b.fetchToCache('http://c/seg-a.mp4', 'T1', function (t) { done = t; }, function () {});
  assert.ok(navs[0].indexOf('mmcache://fetch?token=T1&url=') === 0);
  assert.strictEqual(b.has('T1'), false);            // not present until acked
  w.__mmCacheDone('T1', 12345);                       // native fires back
  assert.strictEqual(done, 'T1');
  assert.strictEqual(b.has('T1'), true);
  assert.strictEqual(b.size('T1'), 12345);
  assert.strictEqual(b.localSrc('T1'), 'file:///var/mobile/Media/mmcache/T1.mp4');
  assert.strictEqual(b.localSrc('T2'), null);
});

test('fetchToCache rejects on __mmCacheFail; evict clears + navigates', function () {
  const { w, navs } = loadBackend();
  const b = w._mmCacheBackendMmvideo;
  let failed = null;
  b.fetchToCache('http://c/x.mp4', 'T9', function () {}, function (t, r) { failed = [t, r]; });
  w.__mmCacheFail('T9', 'net');
  assert.deepStrictEqual(failed, ['T9', 'net']);
  assert.strictEqual(b.has('T9'), false);
  w.__mmCacheDone('T5', 10); assert.strictEqual(b.has('T5'), true);
  b.evict('T5');
  assert.strictEqual(b.has('T5'), false);
  assert.ok(navs.some(function (u) { return u.indexOf('mmcache://evict?token=T5') === 0; }));
});
