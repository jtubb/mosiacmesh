/* js/transitions.js — ES5, NO module syntax (valid classic <script> for the
 * iPad-1 client AND a side-effect import for node tests). Pure transition math
 * + DOM apply helpers. mmTransitionState is a pure function of the shared-clock
 * offset, so every panel computes the same state and transitions in lockstep. */
(function (root) {
  function _dur(eff) { return (eff && eff.params && +eff.params.duration) || 0; }

  // Per-screen reveal for a wall-spanning wipe: the global front F sweeps 0..1
  // along the wipe axis; a panel whose center+half-size spans [a,b] reveals
  // (F-a)/(b-a) clamped. rect = {x,y,w,h} normalized global bbox where x,y are
  // the CENTER coordinates and w,h are the full extent; so a = x-w/2, b = x+w/2.
  // right/down invert the axis.
  function _wallReveal(F, direction, rect) {
    var a, b;
    if (direction === 'left' || direction === 'right') {
      a = rect.x - rect.w / 2; b = rect.x + rect.w / 2;
    } else {
      a = rect.y - rect.h / 2; b = rect.y + rect.h / 2;
    }
    if (direction === 'right' || direction === 'down') {
      var na = 1 - b, nb = 1 - a; a = na; b = nb;     // sweep from the far edge
    }
    if (b <= a) { return F >= b ? 1 : 0; }
    var r = (F - a) / (b - a);
    return r < 0 ? 0 : (r > 1 ? 1 : r);
  }

  // startEff/endEff: {name,params}|null. offsetMs, durationMs in ms. rect: normalized
  // global bbox for wall wipes, or null. Returns {role,opacity,wipe}.
  //
  // Wall-wipe special case: scope:'wall' sweeps the global front F across the full
  // item (F = offsetMs/durationMs, 0->1), so the transition is active for the whole
  // item and each panel reveals over its own [a,b] sub-window. params.duration is
  // irrelevant for wall scope — the item duration IS the sweep window.
  function mmTransitionState(startEff, endEff, offsetMs, durationMs, rect) {
    // Check for wall-wipe first — it overrides the normal role/gating logic.
    var wallEff = null;
    if (startEff && startEff.name === 'wipe' &&
        startEff.params && startEff.params.scope === 'wall') { wallEff = startEff; }
    else if (endEff && endEff.name === 'wipe' &&
        endEff.params && endEff.params.scope === 'wall') { wallEff = endEff; }
    if (wallEff) {
      var dir = (wallEff.params && wallEff.params.direction) || 'left';
      var wallRole = (wallEff === startEff) ? 'in' : 'out';
      var reveal;
      if (rect) {
        // Wall scope with known panel rect: F sweeps 0->1 over the full item duration.
        var F = durationMs > 0 ? offsetMs / durationMs : 0;
        if (F < 0) { F = 0; } if (F > 1) { F = 1; }
        reveal = _wallReveal(F, dir, rect);
      } else {
        // No rect — fall back to per-screen progress using params.duration.
        var sd2 = _dur(wallEff);
        var p2 = (sd2 > 0) ? offsetMs / sd2 : 0;
        if (p2 < 0) { p2 = 0; } if (p2 > 1) { p2 = 1; }
        reveal = p2;
      }
      return { role: wallRole, opacity: 1, wipe: { reveal: reveal, direction: dir } };
    }

    var sd = _dur(startEff), ed = _dur(endEff), role = 'none', eff = null, p = 1;
    if (startEff && sd > 0 && offsetMs < sd) { role = 'in'; eff = startEff; p = offsetMs / sd; }
    else if (endEff && ed > 0 && offsetMs > durationMs - ed) {
      role = 'out'; eff = endEff; p = (durationMs - offsetMs) / ed;
    }
    if (p < 0) { p = 0; } if (p > 1) { p = 1; }
    if (role === 'none') { return { role: 'none', opacity: 1, wipe: null }; }
    if (eff.name === 'wipe') {
      var wdir = (eff.params && eff.params.direction) || 'left';
      // Per-screen wipe: p is the reveal fraction (0->1 for 'in', 1->0 for 'out').
      return { role: role, opacity: 1, wipe: { reveal: p, direction: wdir } };
    }
    return { role: role, opacity: p, wipe: null };   // fade
  }

  root.mmTransitionState = mmTransitionState;
  root._mmWallReveal = _wallReveal;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
