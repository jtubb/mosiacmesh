// js/mmCacheBackendModern.js — modern-browser mmCache backend (Cache API + a Service
// Worker registered separately). No native code. localSrc returns the ORIGINAL url — the
// SW intercepts the fetch and serves it from cache, so no file:// is needed. ES5 SYNTAX
// so the file parses on iPad-1 (its Cache-API methods only RUN on a modern browser, where
// this backend is the one registered; iOS-5 registers the mmvideo backend instead).
(function (root) {
  var CACHE_NAME = 'mm-seg';
  var _present = {};   // token -> url
  function _caches() { return (root.caches ? root.caches : (root.window && root.window.caches)); }

  var backend = {
    name: 'modern',
    fetchToCache: function (url, token, onDone, onFail) {
      var cs = _caches();
      if (!cs) { onFail(token, 'no-cache-api'); return; }
      cs.open(CACHE_NAME).then(function (c) {
        return c.add(url).then(function () {
          _present[token] = url;
          onDone(token);
        });
      })['catch'](function () { onFail(token, 'add-failed'); });
    },
    localSrc: function (token) { return _present.hasOwnProperty(token) ? _present[token] : null; },
    evict: function (token) {
      var url = _present[token]; delete _present[token];
      var cs = _caches();
      if (cs && url) { cs.open(CACHE_NAME).then(function (c) { c['delete'](url); })['catch'](function () {}); }
    },
    has: function (token) { return _present.hasOwnProperty(token); },
    size: function (token) { return _present.hasOwnProperty(token) ? 1 : 0; }
  };

  root._mmCacheBackendModern = backend;
  root.__mmRegisterModernBackend = function () {
    if (root.mmCache && root.mmCache.registerBackend) { root.mmCache.registerBackend(backend); }
  };
})(typeof window !== 'undefined' ? window : global);
