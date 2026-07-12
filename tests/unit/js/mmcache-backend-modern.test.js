import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

function loadModern() {
  const code = fs.readFileSync(new URL('../../../js/mmCacheBackendModern.js', import.meta.url), 'utf8');
  // minimal Cache API mock
  const store = {};               // cacheName -> { url -> {bytes} }
  const caches = {
    open: function (name) {
      store[name] = store[name] || {};
      const c = store[name];
      return Promise.resolve({
        add: function (url) { c[url] = { bytes: 1234 }; return Promise.resolve(); },
        match: function (url) { return Promise.resolve(c[url] ? { _mm: c[url] } : undefined); },
        'delete': function (url) { delete c[url]; return Promise.resolve(true); }
      });
    },
    'delete': function (name) { delete store[name]; return Promise.resolve(true); }
  };
  const sandbox = { window: { caches: caches }, caches: caches };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox.window;
}

test('modern backend: fetchToCache adds to cache + localSrc returns the url', async function () {
  const w = loadModern();
  const b = w._mmCacheBackendModern;
  await new Promise(function (res, rej) {
    b.fetchToCache('http://s/seg_a_0.mp4', 'T1', function () { res(); }, function (t, r) { rej(new Error(r)); });
  });
  assert.strictEqual(b.has('T1'), true);
  assert.strictEqual(b.localSrc('T1'), 'http://s/seg_a_0.mp4');   // SW serves the same url from cache
  assert.strictEqual(b.localSrc('T2'), null);
  b.evict('T1');
  assert.strictEqual(b.has('T1'), false);
});

test('modern backend: clear() deletes the whole named cache + resets _present', async function () {
  const w = loadModern();
  const b = w._mmCacheBackendModern;
  await new Promise(function (res, rej) {
    b.fetchToCache('http://s/seg_a_0.mp4', 'T1', function () { res(); }, function (t, r) { rej(new Error(r)); });
  });
  assert.strictEqual(b.has('T1'), true);
  await new Promise(function (res, rej) { b.clear(function () { res(); }, function (r) { rej(new Error(r)); }); });
  assert.strictEqual(b.has('T1'), false);          // _present reset
});
