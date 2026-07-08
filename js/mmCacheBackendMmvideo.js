// js/mmCacheBackendMmvideo.js — ES5. iOS-5 backend for the mmCache coordinator: drives
// the mmvideo native cache bridge (mmcache:// scheme JS->native; window.__mmCacheDone/
// __mmCacheFail native->JS). Mirrors the mmws bridge pattern. No Promise/fetch (ES5).
(function (root) {
  var CACHE_DIR = 'file:///var/mobile/Media/mmcache/';
  var _present = {};   // token -> bytes (download acked-present on device)
  var _pending = {};   // token -> { onDone: fn, onFail: fn }

  function _nav(url) {
    // JS->native trigger: a hidden iframe nav to mmcache:// so the mmvideo tweak's
    // shouldStartLoadWithRequest hook intercepts it. An iframe (not location.href)
    // keeps the page from navigating away. Removed on the next tick.
    var f = document.createElement('iframe');
    f.style.display = 'none';
    f.src = url;
    document.documentElement.appendChild(f);
    setTimeout(function () { if (f.parentNode) { f.parentNode.removeChild(f); } }, 0);
  }

  var backend = {
    name: 'mmvideo',
    fetchToCache: function (url, token, onDone, onFail) {
      _pending[token] = { onDone: onDone, onFail: onFail };
      _nav('mmcache://fetch?token=' + encodeURIComponent(token) + '&url=' + encodeURIComponent(url));
    },
    localSrc: function (token) {
      return _present.hasOwnProperty(token) ? (CACHE_DIR + token + '.mp4') : null;
    },
    evict: function (token) {
      delete _present[token];
      _nav('mmcache://evict?token=' + encodeURIComponent(token));
    },
    has: function (token) { return _present.hasOwnProperty(token); },
    size: function (token) { return _present[token] || 0; }
  };

  // native -> JS: the tweak invokes these via stringByEvaluatingJavaScriptFromString.
  root.__mmCacheDone = function (token, bytes) {
    _present[token] = bytes || 1;
    var p = _pending[token]; delete _pending[token];
    if (p && p.onDone) { p.onDone(token); }
  };
  root.__mmCacheFail = function (token, reason) {
    var p = _pending[token]; delete _pending[token];
    if (p && p.onFail) { p.onFail(token, reason); }
  };

  root._mmCacheBackendMmvideo = backend;
  root.__mmRegisterMmvideoBackend = function () {
    if (root.mmCache && root.mmCache.registerBackend) { root.mmCache.registerBackend(backend); }
  };
})(typeof window !== 'undefined' ? window : global);
