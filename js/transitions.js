/* js/transitions.js — ES5, NO module syntax (valid classic <script> for the
 * iPad-1 client AND a side-effect import for node tests). Pure transition math
 * + DOM apply helpers. mmTransitionState is a pure function of the shared-clock
 * offset, so every panel computes the same state and transitions in lockstep. */
(function (root) {
  function _dur(eff) { return (eff && eff.params && +eff.params.duration) || 0; }

  // Per-screen reveal for a wall-spanning wipe. The global front F sweeps 0..1
  // along the wipe axis over the wipe's duration; a panel whose normalized global
  // bbox spans [a,b] on that axis reveals (F-a)/(b-a), clamped. rect = {x,y,w,h}
  // with x/y the LEFT/TOP edges (bbox, not center). right/down sweep from the far
  // edge (axis inverted).
  function _wallReveal(F, direction, rect) {
    var a, b;
    if (direction === 'left' || direction === 'right') { a = rect.x; b = rect.x + rect.w; }
    else { a = rect.y; b = rect.y + rect.h; }
    if (direction === 'right' || direction === 'down') {
      var na = 1 - b, nb = 1 - a; a = na; b = nb;
    }
    if (b <= a) { return F >= b ? 1 : 0; }
    var r = (F - a) / (b - a);
    return r < 0 ? 0 : (r > 1 ? 1 : r);
  }

  // startEff/endEff: {name,params}|null. offsetMs, durationMs in ms. rect: normalized
  // global bbox for wall wipes, or null. Returns {role,opacity,wipe}.
  function mmTransitionState(startEff, endEff, offsetMs, durationMs, rect) {
    var sd = _dur(startEff), ed = _dur(endEff), role = 'none', eff = null, p = 1;
    if (startEff && sd > 0 && offsetMs < sd) { role = 'in'; eff = startEff; p = offsetMs / sd; }
    else if (endEff && ed > 0 && offsetMs > durationMs - ed) {
      role = 'out'; eff = endEff; p = (durationMs - offsetMs) / ed;
    }
    if (p < 0) { p = 0; } if (p > 1) { p = 1; }
    if (role === 'none') { return { role: 'none', opacity: 1, wipe: null }; }
    if (eff.name === 'wipe') {
      var dir = (eff.params && eff.params.direction) || 'left';
      var scope = (eff.params && eff.params.scope) || 'screen';
      // p already encodes the front: 'in' 0->1, 'out' 1->0. Wall maps it through
      // the panel's bbox sub-window; per-screen reveals = p directly.
      var reveal = (scope === 'wall' && rect) ? _wallReveal(p, dir, rect) : p;
      return { role: role, opacity: 1, wipe: { reveal: reveal, direction: dir } };
    }
    return { role: role, opacity: p, wipe: null };   // fade
  }

  root.mmTransitionState = mmTransitionState;
  root._mmWallReveal = _wallReveal;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
