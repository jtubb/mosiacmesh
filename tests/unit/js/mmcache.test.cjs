// tests/unit/js/mmcache.test.cjs
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
// mmCache.js attaches to global (ES5 no-module). Load it into this process.
require(path.join(__dirname, '..', '..', '..', 'js', 'mmCache.js'));

function mockBackend() {
  return {
    name: 'mock', fetched: [], evicted: [], store: {},
    fetchToCache: function (url, token, onDone, onFail) { this.fetched.push([url, token]); this.store[token] = url; onDone(token); },
    localSrc: function (token) { return this.store[token] ? ('local://' + token) : null; },
    evict: function (token) { this.evicted.push(token); delete this.store[token]; },
    has: function (token) { return !!this.store[token]; }
  };
}

test('registerBackend sets the active backend', function () {
  mmCache._reset();
  assert.strictEqual(mmCache.backend, null);
  const b = mockBackend();
  mmCache.registerBackend(b);
  assert.strictEqual(mmCache.backend, b);
});
