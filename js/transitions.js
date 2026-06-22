/* js/transitions.js — ES5, NO module syntax (valid classic <script> for the
 * iPad-1 client AND a side-effect import for node tests). Pure transition math
 * + DOM apply helpers. mmTransitionState is a pure function of the shared-clock
 * offset, so every panel computes the same state and transitions in lockstep. */
(function (root) {
  function _dur(eff) { return (eff && eff.params && +eff.params.duration) || 0; }

  // Per-screen reveal for a wall-spanning wipe. The global front F sweeps 0..1
  // along the wipe axis over the wipe's duration; a panel whose normalized global
  // bbox spans [a,b] on that axis reveals (F-a)/(b-a), clamped. rect = {x,y,w,h}
  // with x/y the LEFT/TOP edges (bbox, not center). 'down'/'right' travel in the
  // +axis so the window is the bbox as-is (top/left panels reveal first); 'up'/
  // 'left' travel in the -axis, so the window is mirrored to 1-b..1-a (bottom/
  // right panels reveal first) -> a single coherent front in the named direction.
  function _wallReveal(F, direction, rect) {
    var a, b;
    if (direction === 'left' || direction === 'right') { a = rect.x; b = rect.x + rect.w; }
    else { a = rect.y; b = rect.y + rect.h; }
    if (direction === 'left' || direction === 'up') {
      var na = 1 - b, nb = 1 - a; a = na; b = nb;
    }
    if (b <= a) { return F >= b ? 1 : 0; }
    var r = (F - a) / (b - a);
    return r < 0 ? 0 : (r > 1 ? 1 : r);
  }

  // Local cover-slide vector for a wipe given the panel's global quad. The cover
  // must slide along the LOCAL framebuffer axis that maps to the GLOBAL wipe
  // direction, so physically-rotated panels don't wipe backwards. meshQuad is
  // [TL,TR,BR,BL] in normalized global coords; the panel's local axes in global
  // space are right=TL->TR and down=TL->BL (the SAME basis mmMeshTransform warps
  // content through). We solve g = u*right + v*down for the global slide vector g
  // (down=(0,1) etc.), then normalize so the dominant local axis travels a full
  // 100%. No quad (per-screen scope, or uncalibrated) -> the global axis verbatim.
  function mmWipeSlide(direction, quad) {
    var gx = 0, gy = 0;
    if (direction === 'left') { gx = -1; }
    else if (direction === 'right') { gx = 1; }
    else if (direction === 'up') { gy = -1; }
    else { gy = 1; }                                  // down (default)
    if (!quad || quad.length < 4) { return { x: gx, y: gy }; }
    var rx = quad[1][0] - quad[0][0], ry = quad[1][1] - quad[0][1];   // TL->TR
    var dx = quad[3][0] - quad[0][0], dy = quad[3][1] - quad[0][1];   // TL->BL
    var det = rx * dy - dx * ry;
    if (det > -1e-12 && det < 1e-12) { return { x: gx, y: gy }; }     // degenerate
    var u = (gx * dy - dx * gy) / det;
    var v = (rx * gy - gx * ry) / det;
    var m = Math.abs(u) > Math.abs(v) ? Math.abs(u) : Math.abs(v);
    if (m < 1e-12) { return { x: gx, y: gy }; }
    return { x: u / m, y: v / m };
  }

  // Global-space black-fill rect for the AFFINE cover. The cover element spans the
  // whole global wall canvas (GW x GH) and is warped to the screen by the SAME
  // mmMeshTransform affine the content uses, so the fill (the un-revealed region)
  // inherits the panel's exact rotation/shear and the front is a single global line.
  // scope 'wall' uses the full canvas (one coordinated front); 'screen' uses this
  // panel's quad bbox (every panel in lockstep). front = transition progress p [0,1]:
  // down/right reveal grows from the lo edge; up/left from the hi edge. Returns
  // {x,y,w,h} in global px, or null when fully revealed (no cover needed). Pure.
  function mmWipeCoverRect(scope, direction, front, GW, GH, quad) {
    var loX = 0, hiX = GW, loY = 0, hiY = GH, i;
    if (scope !== 'wall' && quad && quad.length >= 4) {
      var xs = [], ys = [];
      for (i = 0; i < quad.length; i++) { xs.push(quad[i][0]); ys.push(quad[i][1]); }
      loX = Math.min.apply(null, xs) * GW; hiX = Math.max.apply(null, xs) * GW;
      loY = Math.min.apply(null, ys) * GH; hiY = Math.max.apply(null, ys) * GH;
    }
    var f = front < 0 ? 0 : (front > 1 ? 1 : front);
    var x = loX, y = loY, w = hiX - loX, h = hiY - loY;
    if (direction === 'left' || direction === 'right') {
      var spanX = hiX - loX;
      if (direction === 'right') { x = loX + f * spanX; }   // reveal from lo (left)
      w = spanX - f * spanX;                                // up/left: x stays loX
    } else {
      var spanY = hiY - loY;
      if (direction === 'down') { y = loY + f * spanY; }     // reveal from lo (top)
      h = spanY - f * spanY;                                 // up: y stays loY
    }
    if (w <= 1e-9 || h <= 1e-9) { return null; }
    return { x: x, y: y, w: w, h: h };
  }

  // startEff/endEff: {name,params}|null. offsetMs, durationMs in ms. rect: normalized
  // global bbox for wall wipes, or null. quad: this panel's normalized global
  // [TL,TR,BR,BL] for orientation-aware wall wipes, or null. Returns {role,opacity,wipe}.
  function mmTransitionState(startEff, endEff, offsetMs, durationMs, rect, quad) {
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
      // scope controls COORDINATION only: 'wall' = one front sweeping the whole
      // wall (each panel staggered by its global position via _wallReveal); 'screen'
      // = every panel wipes in lockstep (reveal == progress). DIRECTION is always
      // taken from the panel's calibrated grid quad (rotation-aware) in BOTH modes,
      // so a 'down' wipe looks like it goes down on every panel regardless of how
      // the panel is physically mounted. quad null (uncalibrated) -> global axis.
      var reveal = (scope === 'wall' && rect) ? _wallReveal(p, dir, rect) : p;
      var slide = mmWipeSlide(dir, quad);
      // front (= raw progress p) + scope drive the AFFINE cover (mmWipeCoverRect),
      // which warps the cover exactly like content; reveal + slide drive the legacy
      // translate cover used as the uncalibrated fallback.
      return { role: role, opacity: 1,
               wipe: { reveal: reveal, direction: dir, slide: slide, front: p, scope: scope } };
    }
    return { role: role, opacity: p, wipe: null };   // fade
  }

  // Apply a transition state to a mounted element. `cover` is an opaque overlay
  // div (item background color), sized over the element, used for wipes; pass null
  // for fade. ES5 / Safari-5.1: opacity + -webkit-transform only (no clip-path).
  // For a wipe, the cover starts fully covering the element (reveal 0) and slides
  // off in `direction` as reveal -> 1. (Direction sense can be flipped on-wall; the
  // per-screen vs wall sweep is already encoded in st.wipe.reveal.)
  function mmApplyTransition(el, cover, st) {
    if (!el) { return; }
    if (st.wipe && cover) {
      el.style.opacity = '1';
      cover.style.display = 'block';
      // slide encodes BOTH axis and sign (orientation-aware for wall wipes); the
      // cover starts fully covering (r=0) and slides off along it as r->1.
      var r = st.wipe.reveal;
      var sl = st.wipe.slide || { x: 0, y: 0 };
      var tx = sl.x * r * 100, ty = sl.y * r * 100;
      var t = 'translate(' + tx + '%,' + ty + '%)';
      cover.style.webkitTransform = t; cover.style.transform = t;
      if (r >= 1) { cover.style.display = 'none'; }      // fully revealed
    } else {
      if (cover) { cover.style.display = 'none'; }
      el.style.opacity = '' + st.opacity;
    }
  }

  root.mmTransitionState = mmTransitionState;
  root.mmApplyTransition = mmApplyTransition;
  root.mmWipeSlide = mmWipeSlide;
  root.mmWipeCoverRect = mmWipeCoverRect;
  root._mmWallReveal = _wallReveal;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
