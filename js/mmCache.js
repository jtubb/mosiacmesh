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

  // A PRECACHE token is a SEGMENT name ("seg_<rt>_<n>" / "full_<rt>_<n>"). Sibling
  // segments of ONE render share <rt>. Supersede must key on <rt>, NOT the full
  // segment name — else caching seg_1 evicts seg_0 of the SAME render, leaving the
  // device a segment short -> verr=3 decode-miss on the wall. Non-segment tokens
  // map to themselves. ES5 (runs on iPad-1).
  mmCache._renderTokenOf = function (token) {
    return String(token).replace(/^seg_/, '').replace(/^full_/, '').replace(/_\d+$/, '');
  };

  mmCache._supersede = function (group, newToken) {
    var tok, newRT = mmCache._renderTokenOf(newToken);
    for (tok in mmCache._tokens) {
      if (mmCache._tokens.hasOwnProperty(tok) && mmCache._tokens[tok].group === group
          && mmCache._renderTokenOf(tok) !== newRT) {
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

  mmCache.localSrc = function (token) {
    if (mmCache.backend && mmCache.backend.has(token)) { return mmCache.backend.localSrc(token); }
    return null;
  };

  // Clear the ENTIRE device cache: delegate to the backend's complete wipe (Modern:
  // caches.delete; mmvideo: native mmcache://clearall), then reset the coordinator's
  // token bookkeeping so mmCache.state() reports 'none' for every prior token. A backend
  // without clear() (old build) still resets the JS state. Fire-and-forget-friendly.
  mmCache.clear = function (onDone, onFail) {
    var b = mmCache.backend;
    function done() {
      mmCache._tokens = {};
      mmCache._order.length = 0;
      if (onDone) { onDone(); }
    }
    if (!b || !b.clear) { done(); return; }
    b.clear(done, function (reason) { if (onFail) { onFail(reason); } });
  };

  mmCache.onAck = null;   // client wires this to sendMsg("SRV", req, payload)

  mmCache.handlePrecache = function (msg) {
    if (!mmCache.backend) { if (mmCache.onAck) { mmCache.onAck('CACHE_FAILED', { token: msg.token, reason: 'no-backend' }); } return; }
    mmCache._supersede(msg.group, msg.token);
    mmCache.backend.fetchToCache(msg.url, msg.token,
      function (token) { mmCache._enforceCap(); if (mmCache.onAck) { mmCache.onAck('CACHED', { token: token }); } },
      function (token, reason) { mmCache._markFailed(token); if (mmCache.onAck) { mmCache.onAck('CACHE_FAILED', { token: token, reason: reason || 'err' }); } });
  };

  root.mmCache = mmCache;
  if (typeof module !== 'undefined' && module.exports) { module.exports = mmCache; }
})(typeof window !== 'undefined' ? window : global);
