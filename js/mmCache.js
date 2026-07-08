// js/mmCache.js  — ES5 only. Client-agnostic cache coordinator.
(function (root) {
  var mmCache = {
    backend: null,
    _tokens: {},          // token -> { group: <id>, failed: <bool> }
    _order: [],           // tokens in insertion order (for size-cap eviction)
    registerBackend: function (b) { mmCache.backend = b; },
    _reset: function () { mmCache.backend = null; mmCache._tokens = {}; mmCache._order = []; }
  };

  mmCache._recordToken = function (token, group) {
    if (!mmCache._tokens[token]) { mmCache._order.push(token); }
    mmCache._tokens[token] = { group: group, failed: false };
  };

  mmCache.has = function (token) { return !!(mmCache.backend && mmCache.backend.has(token)); };

  mmCache.state = function (token) {
    var t = mmCache._tokens[token];
    if (!t) { return 'none'; }
    if (mmCache.has(token)) { return 'cached'; }
    return t.failed ? 'failed' : 'pending';
  };

  mmCache._markFailed = function (token) { if (mmCache._tokens[token]) { mmCache._tokens[token].failed = true; } };

  mmCache._forget = function (token) {
    delete mmCache._tokens[token];
    var i = mmCache._order.indexOf(token);
    if (i >= 0) { mmCache._order.splice(i, 1); }
  };

  mmCache._supersede = function (group, newToken) {
    var t, tok;
    for (tok in mmCache._tokens) {
      if (mmCache._tokens.hasOwnProperty(tok) && mmCache._tokens[tok].group === group && tok !== newToken) {
        if (mmCache.backend) { mmCache.backend.evict(tok); }
        mmCache._forget(tok);
      }
    }
    mmCache._recordToken(newToken, group);
  };

  mmCache.capBytes = 500 * 1024 * 1024;

  mmCache._totalBytes = function () {
    var sum = 0, i, tok;
    if (!mmCache.backend || !mmCache.backend.size) { return 0; }
    for (i = 0; i < mmCache._order.length; i++) { tok = mmCache._order[i]; sum += (mmCache.backend.size(tok) || 0); }
    return sum;
  };

  mmCache._enforceCap = function () {
    while (mmCache._order.length > 1 && mmCache._totalBytes() > mmCache.capBytes) {
      var oldest = mmCache._order[0];
      if (mmCache.backend) { mmCache.backend.evict(oldest); }
      mmCache._forget(oldest);
    }
  };

  root.mmCache = mmCache;
  if (typeof module !== 'undefined' && module.exports) { module.exports = mmCache; }
})(typeof window !== 'undefined' ? window : global);
