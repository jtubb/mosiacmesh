// js/mmCache.js  — ES5 only. Client-agnostic cache coordinator.
(function (root) {
  var mmCache = {
    backend: null,
    _tokens: {},          // token -> { group: <id> }
    _order: [],           // tokens in insertion order (for size-cap eviction)
    registerBackend: function (b) { mmCache.backend = b; },
    _reset: function () { mmCache.backend = null; mmCache._tokens = {}; mmCache._order = []; }
  };
  root.mmCache = mmCache;
  if (typeof module !== 'undefined' && module.exports) { module.exports = mmCache; }
})(typeof window !== 'undefined' ? window : global);
