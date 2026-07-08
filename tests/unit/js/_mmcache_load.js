// Shared test loader for the ES5 browser-global coordinator js/mmCache.js, mirroring
// the vm-sandbox pattern used by gotime-ready.test.js. mmCache.js has no ESM exports
// (it must stay ES5 for iPad-1 / iOS-5), so we run it in a vm context with a stub
// `window` and return the global it attaches. Each call yields a FRESH mmCache, so
// tests are isolated without cross-test state.
import fs from 'node:fs';
import vm from 'node:vm';

export function loadMmCache() {
  const code = fs.readFileSync(new URL('../../../js/mmCache.js', import.meta.url), 'utf8');
  const sandbox = { window: {}, console: { log: function () {} } };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox.window.mmCache;
}

// Base mock backend implementing the coordinator's backend interface
// (fetchToCache/localSrc/evict/has). Tasks that need size-cap add `.size`/`.sizes`
// or override `fetchToCache` (failure path) inline, per their briefs.
export function mockBackend() {
  return {
    name: 'mock', fetched: [], evicted: [], store: {},
    fetchToCache: function (url, token, onDone, onFail) { this.fetched.push([url, token]); this.store[token] = url; onDone(token); },
    localSrc: function (token) { return this.store[token] ? ('local://' + token) : null; },
    evict: function (token) { this.evicted.push(token); delete this.store[token]; },
    has: function (token) { return !!this.store[token]; }
  };
}
