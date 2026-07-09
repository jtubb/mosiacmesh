// js/mmCacheBackendMmvideo.js — ES5. iOS-5 backend for the mmCache coordinator: drives
// the mmvideo native cache bridge (mmcache:// scheme JS->native; window.__mmCacheDone/
// __mmCacheFail native->JS). Mirrors the mmws bridge pattern. No Promise/fetch (ES5).
(function (root) {
  // localSrc uses mmvideo's http://127.0.0.1:8080/<name> convention, NOT a raw file://.
  // WebKit blocks a file:// media resource from an http-origin page (cross-origin), so
  // <video src="file://..."> never engages mmvideo's MediaPlayer hook. mmvideo intercepts
  // the 127.0.0.1:8080 URL (mm_url_to_path -> file://MosaicMeshCache/<name>) and plays the
  // LOCAL file via AVPlayer with no network fetch — proven on-device (spike, sign1screen1).
  // The native backend (Plan 2 Task 4) therefore downloads into /var/mobile/Media/
  // MosaicMeshCache/ so mm_url_to_path resolves the same <name>.
  var CACHE_DIR = 'http://127.0.0.1:8080/';
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
      // The server sends a root-relative url (/media/...); the mmvideo tweak's NSData
      // fetch needs an ABSOLUTE http:// url. Resolve against the page origin (the server
      // the webclip loaded from). Already-absolute urls pass through unchanged.
      var abs = url;
      if (url && url.indexOf('http') !== 0 && typeof window !== 'undefined' && window.location && window.location.host) {
        abs = (window.location.protocol || 'http:') + '//' + window.location.host + url;
      }
      _nav('mmcache://fetch?token=' + encodeURIComponent(token) + '&url=' + encodeURIComponent(abs));
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
