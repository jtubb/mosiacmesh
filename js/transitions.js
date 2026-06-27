/* js/transitions.js — ES5, NO module syntax (valid classic <script> for the
 * iPad-1 client AND a side-effect import for node tests). Pure transition math
 * + DOM apply helpers. mmTransitionState is a pure function of the shared-clock
 * offset, so every panel computes the same state and transitions in lockstep. */
(function (root) {
  function _dur(eff, role) {
    if (!eff || !eff.params) { return 0; }
    if (eff.name === 'beerfill') { return mmBeerDuration(eff.params, role); }
    if (eff.name === 'scatter') { return mmScatterDuration(eff.params, role); }
    return (+eff.params.duration) || 0;
  }

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
    var sd = _dur(startEff, 'in'), ed = _dur(endEff, 'out'), role = 'none', eff = null, p = 1;
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
    if (eff.name === 'beerfill') {
      var bsc = (eff.params && eff.params.scope) || 'wall';
      var phase = mmBeerPhase(role === 'out' ? 'out' : 'in');
      // front = LOCAL phase progress 0->1 (like scatter): `p` counts DOWN on the 'out'
      // window (1->0), so invert there. Without this the fill level fell 1->0 and the
      // beer receded top->bottom instead of rising bottom->top.
      var blp = (role === 'out') ? (1 - p) : p;
      return { role: role, opacity: 1, wipe: null,
               effect: { name: 'beerfill', family: 'mask', front: mmBeerLevel(phase, blp),
                         scope: bsc, params: eff.params || {}, phase: phase } };
    }
    if (eff.name === 'scatter') {
      var ssc = (eff.params && eff.params.scope) || 'wall';
      // front = LOCAL phase progress 0->1. mmTransitionState's `p` counts down on
      // the 'out' window (1->0), so invert there; 'in' already counts up.
      var slp = (role === 'out') ? (1 - p) : p;
      return { role: role, opacity: 1, wipe: null,
               effect: { name: 'scatter', family: 'mask', front: slp,
                         scope: ssc, params: eff.params || {}, phase: mmScatterPhase(role) } };
    }
    if (eff.name === 'kegroll') {
      var kgsc = (eff.params && eff.params.scope) || 'wall';
      // front = LOCAL phase progress 0->1 (like scatter): `p` counts DOWN on the
      // 'out' window (1->0), so invert there; 'in' already counts up.
      var kglp = (role === 'out') ? (1 - p) : p;
      return { role: role, opacity: 1, wipe: null,
               effect: { name: 'kegroll', family: 'mask', front: kglp,
                         scope: kgsc, params: eff.params || {}, phase: mmKegPhase(role) } };
    }
    if (eff.name === 'frostcreep') {
      var frsc = (eff.params && eff.params.scope) || 'wall';
      var frlp = (role === 'out') ? (1 - p) : p;       // LOCAL phase progress 0->1
      var frph = mmFrostPhase(role);
      var frcov = (frph === 'cover') ? frlp : (1 - frlp);   // coverage front: cover rises, reveal falls
      return { role: role, opacity: 1, wipe: null,
               effect: { name: 'frostcreep', family: 'mask', front: frcov,
                         scope: frsc, params: eff.params || {}, phase: frph } };
    }
    if (eff.name === 'wheatpart') {
      var wpsc = (eff.params && eff.params.scope) || 'wall';
      // front = LOCAL phase progress 0->1 (like scatter): `p` counts DOWN on the
      // 'out' window (1->0), so invert there; 'in' already counts up. mmDrawWheat
      // maps front->openness via phase (mmWheatOpenness).
      var wplp = (role === 'out') ? (1 - p) : p;
      return { role: role, opacity: 1, wipe: null,
               effect: { name: 'wheatpart', family: 'mask', front: wplp,
                         scope: wpsc, params: eff.params || {}, phase: mmWheatPhase(role) } };
    }
    if (eff.name === 'splashcrown') {
      var spsc = (eff.params && eff.params.scope) || 'wall';
      // front = LOCAL phase progress 0->1 (scatter convention): invert on the 'out'
      // window; mmDrawSplash maps front->sequence via phase (mmSplashSeq).
      var splp = (role === 'out') ? (1 - p) : p;
      return { role: role, opacity: 1, wipe: null,
               effect: { name: 'splashcrown', family: 'mask', front: splp,
                         scope: spsc, params: eff.params || {}, phase: mmSplashPhase(role) } };
    }
    if (eff.name === 'coasterflip') {
      var cesc = (eff.params && eff.params.scope) || 'wall';
      // transform family + raw front p; carries `phase` so the apply can sequence the
      // round-in / tumble (cover) vs tumble / un-round (reveal).
      return { role: role, opacity: 1, wipe: null,
               effect: { name: 'coasterflip', family: 'transform', front: p, scope: cesc,
                         params: eff.params || {}, phase: mmCoasterPhase(role) } };
    }
    if (eff.name === 'slide' || eff.name === 'zoom' || eff.name === 'iris' || eff.name === 'dissolve') {
      var fam = (eff.name === 'iris' || eff.name === 'dissolve') ? 'mask' : 'transform';
      var esc = (eff.params && eff.params.scope) || 'wall';
      return { role: role, opacity: 1, wipe: null,
               effect: { name: eff.name, family: fam, front: p, scope: esc, params: eff.params || {} } };
    }
    return { role: role, opacity: p, wipe: null };   // fade
  }

  // Scale + opacity for a Zoom. s ramps scale->1, alpha ramps 0->1 with front. Pure.
  function mmZoomFactor(front, scale) {
    var f = front < 0 ? 0 : (front > 1 ? 1 : front);
    if (scale == null) { scale = 0.6; }
    return { s: scale + (1 - scale) * f, alpha: f };
  }

  // Coaster flip (transform family). front = flip openness (1 open .. 0 edge-on). sx/sy
  // scale the chosen axis only; alpha dims the content toward edge-on; edge is the
  // cardboard edge-sliver opacity (strongest at edge-on). Pure.
  function mmFlipFactor(front, axis) {
    var f = front < 0 ? 0 : (front > 1 ? 1 : front);
    var vert = (axis === 'vertical');
    return { sx: vert ? 1 : f, sy: vert ? f : 1, alpha: 0.35 + 0.65 * f, edge: 1 - f };
  }

  var _COASTER = { kraft: '#b9935f', cork: '#c8a06a', slate: '#5a5e63' };
  function mmCoasterColor(name) { return _COASTER[name] || _COASTER.kraft; }

  function mmCoasterPhase(role) { return role === 'out' ? 'cover' : 'reveal'; }

  // Tumbling-coaster sequencing (transform family). front = raw progress; phase 'cover'
  // (fold A out) or 'reveal' (open B in). flips = number of half-turns; roundFrac = the
  // fraction of the phase spent rounding-in (cover) / un-rounding-out (reveal). Returns:
  //   scale    = |cos theta|, the fold openness (1 flat .. 0 edge-on)
  //   round    = corner-round fraction (0 square .. 1 full coaster)
  //   showFront= cos theta >= 0 (front face -> content; else back face -> sprite)
  //   wobble   = small in-plane rotation (rad) oscillating with the spin
  // COVER rounds-in FIRST then tumbles open->edge; REVEAL tumbles edge->open then un-rounds.
  // Both end/start edge-on at front=0, so the A->B handoff is continuous. Pure.
  function mmCoasterTumble(front, phase, flips, roundFrac) {
    var o = front < 0 ? 0 : (front > 1 ? 1 : front);
    var N = flips > 0 ? flips : 5;
    var rf = (roundFrac > 0 && roundFrac < 1) ? roundFrac : 0.25;
    var lp = (phase === 'reveal') ? o : (1 - o);     // local phase progress 0->1
    var round, tp;
    if (phase === 'reveal') {
      if (lp <= 1 - rf) { round = 1; tp = 1 - lp / (1 - rf); }   // tumble edge->open
      else { round = (1 - lp) / rf; tp = 0; }                    // then un-round
    } else {
      if (lp <= rf) { round = lp / rf; tp = 0; }                 // round-in first
      else { round = 1; tp = (lp - rf) / (1 - rf); }             // then tumble open->edge
    }
    if (round < 0) { round = 0; } else if (round > 1) { round = 1; }
    if (tp < 0) { tp = 0; } else if (tp > 1) { tp = 1; }
    var theta = tp * (N - 0.5) * Math.PI;
    var ct = Math.cos(theta);
    return { scale: ct < 0 ? -ct : ct, round: round, showFront: ct >= 0, wobble: Math.sin(theta) * 0.1, theta: theta };
  }

  // Round the region's corners by filling the 4 corner cutouts (square corner minus the
  // inscribed quarter-circle) with the background color -> the rectangle reads as a coaster.
  // radius clamped to half the smaller side. No clip; arc/lineTo/fill only. Drawn under
  // whatever transform the caller set, so it folds with the coaster.
  function mmDrawCoasterCorners(ctx, reg, radius, bg) {
    var r = radius, hw = reg.w / 2, hh = reg.h / 2;
    if (r <= 0) { return; }
    if (r > hw) { r = hw; } if (r > hh) { r = hh; }
    var x0 = reg.x, y0 = reg.y, x1 = reg.x + reg.w, y1 = reg.y + reg.h, P = Math.PI;
    ctx.fillStyle = bg;
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x0 + r, y0);          // TL
    ctx.arc(x0 + r, y0 + r, r, -P / 2, P, true); ctx.lineTo(x0, y0); ctx.closePath(); ctx.fill();
    ctx.beginPath(); ctx.moveTo(x1, y0); ctx.lineTo(x1, y0 + r);          // TR
    ctx.arc(x1 - r, y0 + r, r, 0, -P / 2, true); ctx.lineTo(x1, y0); ctx.closePath(); ctx.fill();
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x1 - r, y1);          // BR
    ctx.arc(x1 - r, y1 - r, r, P / 2, 0, true); ctx.lineTo(x1, y1); ctx.closePath(); ctx.fill();
    ctx.beginPath(); ctx.moveTo(x0, y1); ctx.lineTo(x0, y1 - r);          // BL
    ctx.arc(x0 + r, y1 - r, r, P, P / 2, true); ctx.lineTo(x0, y1); ctx.closePath(); ctx.fill();
  }

  // Mask the region down to a CENTERED CIRCLE (a round coaster) by filling everything
  // outside the circle with the background color -> the content rounds into a disc, not a
  // rounded rectangle. `round` 0 = no mask (huge circle), 1 = full coaster disc. No clip:
  // 4 outer strips (region beyond the circle bbox) + the bbox corners rounded by R (which
  // turns the bbox square into the circle). Drawn under the caller's transform (folds with it).
  function mmDrawCoasterDisc(ctx, reg, round, bg) {
    var rd = round < 0 ? 0 : (round > 1 ? 1 : round);
    if (rd <= 0) { return; }
    var cx = reg.x + reg.w / 2, cy = reg.y + reg.h / 2;
    var halfDiag = Math.sqrt(reg.w * reg.w + reg.h * reg.h) / 2;   // circle that covers the whole region
    var discR = (reg.w < reg.h ? reg.w : reg.h) * 0.48;            // target coaster radius
    var R = halfDiag + (discR - halfDiag) * rd;                    // lerp huge -> disc
    var x0 = reg.x, y0 = reg.y, x1 = reg.x + reg.w, y1 = reg.y + reg.h;
    var bx0 = cx - R, by0 = cy - R, bx1 = cx + R, by1 = cy + R;
    ctx.fillStyle = bg;
    if (by0 > y0) { ctx.fillRect(x0, y0, reg.w, by0 - y0); }       // top strip
    if (by1 < y1) { ctx.fillRect(x0, by1, reg.w, y1 - by1); }      // bottom strip
    var ty = by0 > y0 ? by0 : y0, byb = by1 < y1 ? by1 : y1;
    if (bx0 > x0) { ctx.fillRect(x0, ty, bx0 - x0, byb - ty); }    // left strip
    if (bx1 < x1) { ctx.fillRect(bx1, ty, x1 - bx1, byb - ty); }   // right strip
    mmDrawCoasterCorners(ctx, { x: bx0, y: by0, w: 2 * R, h: 2 * R }, R, bg);  // round bbox -> circle
  }

  // Circle (global px) for an Iris reveal. Center = wall center (wall) or panel bbox
  // center (screen); radius ramps 0 -> half the region diagonal (so front 1 fully
  // covers the region's farthest corner). Pure.
  function mmIrisCircle(front, GW, GH, scope, quad) {
    var f = front < 0 ? 0 : (front > 1 ? 1 : front);
    var loX = 0, loY = 0, hiX = GW, hiY = GH, i;
    if (scope !== 'wall' && quad && quad.length >= 4) {
      var xs = [], ys = [];
      for (i = 0; i < quad.length; i++) { xs.push(quad[i][0]); ys.push(quad[i][1]); }
      loX = Math.min.apply(null, xs) * GW; hiX = Math.max.apply(null, xs) * GW;
      loY = Math.min.apply(null, ys) * GH; hiY = Math.max.apply(null, ys) * GH;
    }
    var hx = (hiX - loX) / 2, hy = (hiY - loY) / 2;
    return { cx: loX + hx, cy: loY + hy, r: f * Math.sqrt(hx * hx + hy * hy) };
  }

  // Tiny deterministic LCG (per-seed) -> [0,1) generator. Pure; ES5/bit-portable.
  function _mmLcg(seed) {
    var s = (seed >>> 0) || 1;
    return function () { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
  }
  // Seeded reveal order: a permutation of 0..n-1 (Fisher-Yates over _mmLcg). The SAME
  // seed (playback.seed) on every screen -> identical order -> wall-coherent dissolve.
  function mmDissolveOrder(n, seed) {
    var arr = [], i;
    for (i = 0; i < n; i++) { arr.push(i); }
    var rnd = _mmLcg(seed);
    for (i = n - 1; i > 0; i--) {
      var j = Math.floor(rnd() * (i + 1));
      var t = arr[i]; arr[i] = arr[j]; arr[j] = t;
    }
    return arr;
  }
  // Count of cells still covered at this front (cells revealed = floor(front*n)). Pure.
  function mmDissolveCovered(front, n) {
    var f = front < 0 ? 0 : (front > 1 ? 1 : front);
    return n - Math.floor(f * n);
  }

  // Global-px offset for a Slide. 'direction' is the motion direction; content enters
  // from the opposite edge. front 0 -> one wall off; front 1 -> {0,0}. Pure.
  function mmSlideOffset(front, direction, GW, GH) {
    var f = front < 0 ? 0 : (front > 1 ? 1 : front);
    var k = 1 - f, dx = 0, dy = 0;
    if (direction === 'left') { dx = k * GW; }
    else if (direction === 'right') { dx = -k * GW; }
    else if (direction === 'up') { dy = k * GH; }
    else { dy = -k * GH; }   // down
    return { dx: dx, dy: dy };
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

  function _mmMaskRegion(scope, quad, GW, GH) {
    if (scope !== 'wall' && quad && quad.length >= 4) {
      var xs = [], ys = [], i;
      for (i = 0; i < quad.length; i++) { xs.push(quad[i][0]); ys.push(quad[i][1]); }
      var lx = Math.min.apply(null, xs) * GW, ly = Math.min.apply(null, ys) * GH;
      return { x: lx, y: ly, w: Math.max.apply(null, xs) * GW - lx, h: Math.max.apply(null, ys) * GH - ly };
    }
    return { x: 0, y: 0, w: GW, h: GH };
  }

  // No-clip iris geometry (smooth on iPad-1: per-frame uses only drawImage + fillRect,
  // never clip/composite). Returns the circle's bounding BOX (where the caller draws a
  // ONCE-baked black-with-transparent-hole sprite) and the BLACK STRIPS that cover the
  // rest of the region (region minus the box). box is null when the iris is fully closed
  // (front 0) -> strips cover the whole region. The 4 strips are the standard
  // rect-around-a-box decomposition, clamped to the region. Pure.
  function mmIrisMaskRects(front, GW, GH, scope, quad) {
    var reg = _mmMaskRegion(scope, quad, GW, GH);
    var rx = reg.x, ry = reg.y, rR = reg.x + reg.w, rB = reg.y + reg.h;
    var c = mmIrisCircle(front, GW, GH, scope, quad);
    var r = c.r;
    if (r <= 0) { return { box: null, strips: [{ x: rx, y: ry, w: reg.w, h: reg.h }] }; }
    var bx = c.cx - r, by = c.cy - r, bX = c.cx + r, bY = c.cy + r;
    var strips = [];
    if (by > ry) { strips.push({ x: rx, y: ry, w: reg.w, h: by - ry }); }   // top
    if (bY < rB) { strips.push({ x: rx, y: bY, w: reg.w, h: rB - bY }); }   // bottom
    var ty = by > ry ? by : ry, bb = bY < rB ? bY : rB;
    if (bx > rx && bb > ty) { strips.push({ x: rx, y: ty, w: bx - rx, h: bb - ty }); }  // left
    if (bX < rR && bb > ty) { strips.push({ x: bX, y: ty, w: rR - bX, h: bb - ty }); }  // right
    return { box: { x: bx, y: by, w: 2 * r, h: 2 * r }, strips: strips };
  }

  // OVERLAY canvas (content is on a layer BELOW). Cover the region with bg, then REVEAL.
  // iris: fill, then destination-out a growing circle (hole shows content below).
  // dissolve: fill ONLY not-yet-revealed cells (revealed cells stay clear -> show content).
  function mmDrawMaskOverlay(ctx, name, params, front, GW, GH, quad, scope, seed, bg) {
    var reg = _mmMaskRegion(scope, quad, GW, GH);
    if (name === 'iris') {
      // Donut clip (rect MINUS circle) + fillRect bg, NOT destination-out (unreliable on
      // iPad-1/iOS-5). Leaves the circle transparent so the content layer below shows.
      ctx.save();
      ctx.beginPath();
      ctx.rect(reg.x, reg.y, reg.w, reg.h);
      var c = mmIrisCircle(front, GW, GH, scope, quad);
      if (c.r > 0) { ctx.arc(c.cx, c.cy, c.r, 0, 6.2831853, true); }
      ctx.clip();
      ctx.fillStyle = bg; ctx.fillRect(reg.x, reg.y, reg.w, reg.h);
      ctx.restore();
    } else if (name === 'dissolve') {
      var blocks = (params && params.blocks) || 16, n = blocks * blocks;
      var revealed = n - mmDissolveCovered(front, n);
      var order = mmDissolveOrder(n, seed | 0);
      var cw = reg.w / blocks, ch = reg.h / blocks, k, cell, col, rw;
      ctx.fillStyle = bg;
      for (k = revealed; k < n; k++) {
        cell = order[k]; col = cell % blocks; rw = Math.floor(cell / blocks);
        ctx.fillRect(reg.x + col * cw, reg.y + rw * ch, cw + 1, ch + 1);
      }
    }
  }

  // IN-CANVAS (drawn onto the SAME canvas as content, AFTER the content draw).
  // iris: destination-in a growing circle (keeps content inside circle, clears rest ->
  //       item background shows through). dissolve: fill not-yet-revealed cells with bg.
  function mmDrawMaskInCanvas(ctx, name, params, front, GW, GH, quad, scope, seed, bg) {
    // iris is NOT handled here: the in-canvas iris uses a once-baked sprite + fillRect
    // strips (no per-frame clip/composite — see index.html), because clip() is too slow
    // and destination-* is unreliable on the iPad-1. Only dissolve (cheap fillRects) here.
    if (name === 'dissolve') {
      var reg = _mmMaskRegion(scope, quad, GW, GH);
      var blocks = (params && params.blocks) || 16, n = blocks * blocks;
      var revealed = n - mmDissolveCovered(front, n);
      var order = mmDissolveOrder(n, seed | 0);
      var cw = reg.w / blocks, ch = reg.h / blocks, k, cell, col, rw;
      ctx.fillStyle = bg;
      for (k = revealed; k < n; k++) {
        cell = order[k]; col = cell % blocks; rw = Math.floor(cell / blocks);
        ctx.fillRect(reg.x + col * cw, reg.y + rw * ch, cw + 1, ch + 1);
      }
    }
  }

  var _BEER = {
    pale:  { beerTop: '#F6C744', beerBot: '#E0A21A', foam: '#FFF8E7', foamTop: '#FFFFFF', foamBot: '#E7CE92', headH: 0.11, bubbleDensity: 72, foamBubbles: 56 },
    amber: { beerTop: '#C9791C', beerBot: '#8A4A0E', foam: '#F3E0C0', foamTop: '#FBF1DE', foamBot: '#D2AB78', headH: 0.14, bubbleDensity: 52, foamBubbles: 48 },
    stout: { beerTop: '#3A241A', beerBot: '#160C07', foam: '#E8C9A0', foamTop: '#F4E5CC', foamBot: '#BE9460', headH: 0.20, bubbleDensity: 34, foamBubbles: 70 }
  };
  function mmBeerPalette(beerType) { return _BEER[beerType] || _BEER.pale; }
  function mmBeerPhase(role) { return role === 'out' ? 'fill' : 'drain'; }
  function mmBeerDuration(params, role) {
    // single `duration` (current schema); fall back to legacy fillMs/drainMs so
    // beerfill items saved before the consolidation still animate. role kept for the
    // legacy path (out -> fillMs, in -> drainMs).
    var ms = +(params && params.duration);
    if (!(ms > 0)) { ms = +(role === 'out' ? (params && params.fillMs) : (params && params.drainMs)); }
    return (ms > 0) ? ms : 2500;
  }
  function mmBeerLevel(phase, p) {
    var lv = phase === 'fill' ? p : (1 - p);
    return lv < 0 ? 0 : (lv > 1 ? 1 : lv);
  }

  function mmFoamWaveY(xFrac, t, amp, baseY) {
    return baseY
      + Math.sin(xFrac * 15.0 + t * 9.4) * amp * 0.5
      + Math.sin(xFrac * 41.0 - t * 6.3) * amp * 0.3;
  }

  function mmBeerBubbles(seed, count) {
    var rnd = _mmLcg(seed >>> 0), arr = [], i;
    for (i = 0; i < count; i++) {
      arr.push({ x: rnd(), phase: rnd(), r: 1 + rnd() * 2.4, spd: 0.45 + rnd() * 0.8 });
    }
    return arr;
  }

  function mmFoamBubbles(seed, count) {
    var rnd = _mmLcg(((seed >>> 0) ^ 0x9e3779b9) >>> 0), arr = [], i;
    for (i = 0; i < count; i++) {
      arr.push({ x: rnd(), yf: rnd(), r: 1 + rnd() * 3.2, a: 0.22 + rnd() * 0.4 });
    }
    return arr;
  }

  // Draw opaque beer covering the bottom `level` fraction of the region.
  // phase 'fill' adds a pour stream from the region top. Used both in-canvas
  // (mesh SCRIPT, drawn in global coords then warped) and on the overlay (media).
  // ctx primitives only: fillRect / arc+fill / polyline / linear gradient. No clip.
  function mmDrawBeer(ctx, params, phase, level, t, GW, GH, quad, scope, seed) {
    var lv = level < 0 ? 0 : (level > 1 ? 1 : level);
    if (lv <= 0) { return; }
    var pal = mmBeerPalette(params && params.beerType);
    var reg = _mmMaskRegion(scope, quad, GW, GH);
    var bottom = reg.y + reg.h, ts = t * 0.001;   // ms -> s-ish for wave/bubble motion

    // FILL has a pour LEAD-IN: for the first POUR_FRAC of the phase the stream falls
    // from the top to the bottom and the beer hasn't started rising; after that the
    // beer rises 0->1. DRAIN uses lv directly as the remaining level (no lead-in, no
    // pour). streamTipY = where the stream's leading edge currently is.
    var POUR_FRAC = 0.20, bodyLv = lv, streamTipY = bottom;
    if (phase === 'fill') {
      if (lv < POUR_FRAC) { bodyLv = 0; streamTipY = reg.y + (lv / POUR_FRAC) * reg.h; }
      else { bodyLv = (lv - POUR_FRAC) / (1 - POUR_FRAC); }
    }
    var beerH = bodyLv * reg.h, surfaceY = bottom - beerH;
    var fh = reg.h * pal.headH, topBase = surfaceY - fh;
    if (phase === 'fill' && lv >= POUR_FRAC) { streamTipY = surfaceY; }

    // pour stream (fill only) -> drawn BEFORE the foam so the foam overlays where the
    // stream enters (the splash sits BEHIND the foam, not awkwardly on top). The stream
    // falls from the top to its tip: descending during the lead-in, then the beer surface.
    if (phase === 'fill') {
      var pw = reg.w * 0.10, px = reg.x + reg.w / 2, ph = streamTipY - reg.y;
      if (ph < 0) { ph = 0; }
      ctx.fillStyle = pal.beerTop; ctx.fillRect(px - pw / 2, reg.y, pw, ph);
      ctx.fillStyle = 'rgba(255,255,255,0.18)'; ctx.fillRect(px - pw * 0.18, reg.y, pw * 0.36, ph);
      // frothy tip: a soft foam head at the leading edge + a seeded cluster of little
      // bubbles (wall-coherent) so the stream tip reads as froth, not a flat disc.
      var tipR = pw * 0.7;
      ctx.fillStyle = pal.foamTop || pal.foam;
      ctx.beginPath(); ctx.arc(px, streamTipY, tipR, 0, 6.2832); ctx.fill();
      var tipB = mmFoamBubbles((seed >>> 0) ^ 0x5bd1e995, 16), tj, tb;
      for (tj = 0; tj < tipB.length; tj++) {
        tb = tipB[tj];
        ctx.fillStyle = 'rgba(255,255,255,' + (0.45 + tb.a * 0.5) + ')';
        ctx.beginPath();
        ctx.arc(px + (tb.x - 0.5) * tipR * 2.4, streamTipY + (tb.yf - 0.5) * tipR * 2.4,
                tb.r * (pw * 0.18), 0, 6.2832);
        ctx.fill();
      }
    }

    if (bodyLv > 0) {
      // beer body (vertical gradient)
      var g = ctx.createLinearGradient(0, surfaceY, 0, bottom);
      g.addColorStop(0, pal.beerTop); g.addColorStop(1, pal.beerBot);
      ctx.fillStyle = g; ctx.fillRect(reg.x, surfaceY, reg.w, beerH);

      // rising carbonation bubbles (seeded -> identical across screens for a wall). Radii
      // are REGION-RELATIVE: an absolute 1-3px radius would warp to a sub-pixel speck.
      var bubs = mmBeerBubbles(seed >>> 0, pal.bubbleDensity), i, by;
      var bubBase = reg.h * 0.0045;          // beer-bubble size unit (r 1..3.4 -> ~0.45-1.5% of wall height)
      ctx.fillStyle = 'rgba(255,255,255,0.6)';
      for (i = 0; i < bubs.length; i++) {
        by = bottom - (((bubs[i].phase + ts * bubs[i].spd * 0.35) % 1) * beerH);
        if (by < surfaceY + bubBase * 2) { continue; }
        ctx.beginPath(); ctx.arc(reg.x + bubs[i].x * reg.w, by, bubs[i].r * bubBase, 0, 6.2832); ctx.fill();
      }

      // foam head, wavy top, foamTop->foamBot gradient for depth. Drawn OVER the pour
      // stream so the stream visibly enters the foam.
      var amp = fh * 0.4 < 2.5 ? 2.5 : fh * 0.4, steps = 60, s, sx;
      var fg = ctx.createLinearGradient(0, topBase - amp, 0, surfaceY);
      fg.addColorStop(0, pal.foamTop || pal.foam);
      fg.addColorStop(1, pal.foamBot || pal.foam);
      ctx.fillStyle = fg;
      ctx.beginPath(); ctx.moveTo(reg.x, surfaceY + 2); ctx.lineTo(reg.x, topBase);
      for (s = 0; s <= steps; s++) {
        sx = s / steps;
        ctx.lineTo(reg.x + sx * reg.w, mmFoamWaveY(sx, ts, amp, topBase));
      }
      ctx.lineTo(reg.x + reg.w, surfaceY + 2); ctx.closePath(); ctx.fill();
      // soft shadow band where foam meets beer -> separates the layers for depth
      ctx.fillStyle = 'rgba(90,55,15,0.20)';
      ctx.fillRect(reg.x, surfaceY - fh * 0.12, reg.w, fh * 0.12);

      // scattered foam bubbles (radii relative to the foam-band height)
      var fbs = mmFoamBubbles(seed >>> 0, pal.foamBubbles), k, f, fbubBase = fh * 0.07;
      for (k = 0; k < fbs.length; k++) {
        f = fbs[k];
        ctx.fillStyle = 'rgba(255,255,255,' + f.a + ')';
        ctx.beginPath(); ctx.arc(reg.x + f.x * reg.w, topBase + f.yf * fh, f.r * fbubBase, 0, 6.2832); ctx.fill();
      }
    }
  }

  // --- Wheat part (mask family): a center-seam wheat curtain. Pure geometry +
  // role->openness mapping; the draw glue (mmDrawWheat) consumes these. ---
  var _WHEAT_MAX_LEAN = 0.5;                  // outward stalk lean at full-open (~29 deg)

  // Role -> openness (0 closed/full-wheat .. 1 open/content-visible). front is LOCAL
  // phase progress rising 0->1 for both roles. `hold` (default 0.2) is the fraction of
  // the window the wheat DWELLS fully closed at the center seam: a cover closes over the
  // first (1-hold) then holds closed; a reveal holds closed for the first `hold` then
  // opens over the rest. So both roles dwell full-wheat across the A->B handoff.
  function mmWheatOpenness(phase, front, hold) {
    var f = front < 0 ? 0 : (front > 1 ? 1 : front);
    var h = (hold >= 0 && hold < 1) ? hold : 0.2;
    if (phase === 'reveal') {
      return f <= h ? 0 : (f - h) / (1 - h);          // hold closed, then open 0->1
    }
    return f >= (1 - h) ? 0 : (1 - f / (1 - h));        // close 1->0, then hold closed
  }

  // Parting geometry at a given openness. Single vertical seam at wall center cx.
  // The two wheat walls' inner edges sit at cx-g / cx+g; each has slid outward by g
  // and its stalks lean by `lean` toward their outer edge.
  function mmWheatPartGeom(openness, GW, GH) {
    var o = openness < 0 ? 0 : (openness > 1 ? 1 : openness);
    var cx = GW / 2, g = o * cx;
    return { cx: cx, g: g, leftEdge: cx - g, rightEdge: cx + g,
             slide: g, lean: o * _WHEAT_MAX_LEAN };
  }

  var _WHEAT = {
    golden: { backdrop: '#b8901f', base: '#8a6a14', stalk: '#d9b23a', head: '#f0d169' },
    amber:  { backdrop: '#9c6f1a', base: '#6f4d10', stalk: '#c8912e', head: '#e6b85a' },
    pale:   { backdrop: '#d8c478', base: '#b6a256', stalk: '#e8dca0', head: '#f7efc8' }
  };
  function mmWheatColor(tint) { return _WHEAT[tint] || _WHEAT.golden; }

  // Deterministic stalk field across the whole wall (seeded -> identical on every
  // screen, like mmScatterParticles/mmBeerBubbles). h/headR are FRACTIONS so the
  // draw scales them to GH and they never warp to sub-pixel specks.
  function mmWheatField(seed, density, GW, GH) {
    var n = density > 0 ? (density | 0) : 1;
    var rnd = _mmLcg(seed >>> 0), arr = [], i, bx;
    var cx = GW / 2;
    for (i = 0; i < n; i++) {
      bx = rnd() * GW;
      arr.push({ bx: bx, h: 0.6 + rnd() * 0.4, sway: rnd() * 6.283185307,
                 headR: 0.006 + rnd() * 0.006, side: bx < cx ? 'left' : 'right' });
    }
    return arr;
  }
  function mmWheatPhase(role) { return role === 'out' ? 'cover' : 'reveal'; }

  // --- Splash crown (mask family): a beer droplet impacts the wall center, a crown
  // leaps, and an opaque beer disc blooms outward. Pure sequencing + geometry; the
  // draw glue (mmDrawSplash) consumes these. ---
  function mmSplashPhase(role) { return role === 'out' ? 'cover' : 'reveal'; }

  // Lead-in (drop falls) -> bloom (disc grows). cover plays forward, reveal time-reverses
  // so both roles sit at full beer (bloom 1) at the A->B handoff. dropY 0=top..1=center.
  function mmSplashSeq(phase, front, leadFrac) {
    var f = front < 0 ? 0 : (front > 1 ? 1 : front);
    var lf = (leadFrac > 0 && leadFrac < 1) ? leadFrac : 0.18;
    var lp = (phase === 'cover') ? f : (1 - f);
    if (lp < lf) { return { dropY: lp / lf, bloom: 0, impacted: false }; }
    return { dropY: 1, bloom: (lp - lf) / (1 - lf), impacted: true };
  }

  function mmSplashRadius(bloom, GW, GH) {
    var b = bloom < 0 ? 0 : (bloom > 1 ? 1 : bloom);
    return b * 0.5 * Math.sqrt(GW * GW + GH * GH);
  }

  // Deterministic crown: `count` spikes around the rim (seeded -> identical on every
  // screen, like mmScatterParticles). Evenly spaced + per-spike jitter so the rim isn't
  // a perfect ring. lenF/beadF/flyF/phase shape each spike + its flung bead.
  function mmCrownSpikes(seed, count) {
    var n = count > 0 ? (count | 0) : 1;
    var rnd = _mmLcg(seed >>> 0), arr = [], i, base, step = 6.283185307 / n, ang;
    for (i = 0; i < n; i++) {
      base = i * step;
      ang = base + (rnd() - 0.5) * step * 0.8;
      if (ang < 0) { ang += 6.283185307; } else if (ang >= 6.283185307) { ang -= 6.283185307; }
      arr.push({ ang: ang,
                 lenF: 0.5 + rnd() * 0.5,
                 beadF: 0.5 + rnd() * 0.6,
                 flyF: 0.6 + rnd() * 0.9,
                 phase: rnd() * 6.283185307 });
    }
    return arr;
  }

  function _clamp01(x) { return x < 0 ? 0 : (x > 1 ? 1 : x); }
  function mmScatterParticles(seed, count) {
    var rnd = _mmLcg(seed >>> 0), arr = [], i;
    for (i = 0; i < count; i++) {
      arr.push({ ang: rnd() * 6.283185307, sp: 0.6 + rnd() * 0.9,
                 rot0: rnd() * 6.283185307, rps: (rnd() - 0.5) * 1.4 });
    }
    return arr;
  }
  function mmScatterPhase(role) { return role === 'out' ? 'cover' : 'reveal'; }
  function mmScatterDuration(params, role) {
    var ms = +(role === 'out' ? (params && params.fillMs) : (params && params.drainMs));
    return ms > 0 ? ms : 2500;
  }
  function mmScatterCover(phase, p) { return _clamp01(phase === 'cover' ? p : 1 - p); }
  function mmScatterDist(phase, p) {
    var c = _clamp01(p);
    return phase === 'cover' ? Math.pow(c, 0.72) : (1 + c * 1.4);
  }
  function mmScatterGiantAngle(phase, p) {
    var c = _clamp01(p);
    return (phase === 'cover' ? c : 1 + c) * 6.283185307;
  }
  function mmScatterSpriteUrl(sprite) {
    if (!sprite) { sprite = 'hop'; }
    sprite = String(sprite);
    if (sprite.charAt(0) === '/') { return sprite; }              // explicit path, used as-is
    if (/\.png$/i.test(sprite)) { return '/media/server/images/' + sprite; }   // name already has .png
    return '/media/server/images/' + sprite + '.png';             // bare name -> append .png
  }

  // --- Keg roll (mask family): a giant keg sprite rolls across as the moving
  // boundary of a directional cover. Pure geometry; the wipe's reveal MATH is
  // re-derived here, the wipe's CODE is untouched. ---
  function mmKegPhase(role) { return role === 'out' ? 'cover' : 'reveal'; }

  // The keg center's offset along the travel axis within region coords, range
  // -kegD/2 .. S+kegD/2 (so the keg starts/ends fully off the entry/exit edge).
  // The cover edge is just this clamped to [0,S], so the cover edge and the keg
  // center COINCIDE whenever the keg is on-screen (no lead/lag), and the cover
  // simply waits at the boundary while the keg rolls in/out off-screen. Shared by
  // mmKegCoverRect + mmKegPos so they can never drift apart. Pure.
  function _kegAxisOffset(f, plus, S, kegD) {
    var d = -kegD / 2 + f * (S + kegD);            // + direction: -kegD/2 -> S+kegD/2
    return plus ? d : (S - d);                     // - direction mirrors
  }

  // Directional cover rect in global px (or null when nothing is covered). prog =
  // keg local phase progress 0->1. The cover edge = the keg-center axis clamped to
  // [0,S], so the edge tracks the keg exactly while on-screen. 'cover' fills BEHIND
  // the keg (where it has been; grows 0->full); 'reveal' fills AHEAD (where it hasn't
  // reached; shrinks full->0). direction = keg travel direction; the rect spans the
  // full perpendicular dimension. reg = {x,y,w,h}; kegD = keg diameter in global px.
  function mmKegCoverRect(prog, direction, phase, reg, kegD) {
    var f = prog < 0 ? 0 : (prog > 1 ? 1 : prog);
    var horiz = (direction === 'left' || direction === 'right');
    var plus = (direction === 'right' || direction === 'down');   // travels toward the hi edge
    var S = horiz ? reg.w : reg.h;
    var axis = _kegAxisOffset(f, plus, S, kegD || 0);
    var edge = axis < 0 ? 0 : (axis > S ? S : axis);              // cover boundary, clamped to region
    var lo, hi;
    if (phase === 'cover') {
      if (plus) { lo = 0; hi = edge; } else { lo = edge; hi = S; }
    } else {                                       // reveal: the not-yet-reached side
      if (plus) { lo = edge; hi = S; } else { lo = 0; hi = edge; }
    }
    var len = hi - lo;
    if (len <= 1e-9) { return null; }
    if (horiz) { return { x: reg.x + lo, y: reg.y, w: len, h: reg.h }; }
    return { x: reg.x, y: reg.y + lo, w: reg.w, h: len };
  }

  // Keg center (global px) + distance traveled. The center rides the same axis the
  // cover edge clamps from, so it sits exactly on the cover edge while on-screen and
  // is fully off the entry/exit edge at the ends (clean fully-covered handoff). kegD =
  // keg diameter in global px. dist = axis travel (for rotation). Returns {cx,cy,dist>=0}.
  function mmKegPos(prog, direction, reg, kegD) {
    var f = prog < 0 ? 0 : (prog > 1 ? 1 : prog);
    var horiz = (direction === 'left' || direction === 'right');
    var plus = (direction === 'right' || direction === 'down');
    var S = horiz ? reg.w : reg.h;
    var axis = _kegAxisOffset(f, plus, S, kegD);
    var dist = f * (S + kegD);                     // monotonic travel -> physical roll angle
    if (horiz) { return { cx: reg.x + axis, cy: reg.y + reg.h / 2, dist: dist }; }
    return { cx: reg.x + reg.w / 2, cy: reg.y + axis, dist: dist };
  }

  // Physical roll: rotation tied to distance (arc length = radius * angle). Sign
  // negative for left/up so the keg appears to roll in its travel direction. Pure.
  function mmKegAngle(dist, kegRadius, direction) {
    if (!(kegRadius > 0)) { return 0; }
    var sign = (direction === 'left' || direction === 'up') ? -1 : 1;
    return sign * dist / kegRadius;
  }

  // --- Frost creep (mask family): a spatially-correlated seeded noise field thresholded
  // by a rising coverage front; soft growing blotches. Pure. ---
  function mmFrostPhase(role) { return role === 'out' ? 'cover' : 'reveal'; }

  // blocks*blocks thresholds in [0, 0.98), precomputed: seeded per-cell randoms ->
  // 2 box-blur passes (4-neighbour avg, edge-clamped) for spatial correlation (frost
  // PATCHES, not speckle) -> renormalize to [0, 0.98) (strictly < 1 so every cell frosts
  // before cover hits 1; the consolidation fill then guarantees full opacity). Pure.
  function mmFrostField(blocks, seed) {
    var n = blocks * blocks, rnd = _mmLcg(seed >>> 0), raw = [], i, pass, out, r, c, idx, sum, cnt;
    for (i = 0; i < n; i++) { raw.push(rnd()); }
    for (pass = 0; pass < 2; pass++) {
      out = [];
      for (r = 0; r < blocks; r++) {
        for (c = 0; c < blocks; c++) {
          idx = r * blocks + c; sum = raw[idx]; cnt = 1;
          if (c > 0)          { sum += raw[idx - 1]; cnt++; }
          if (c < blocks - 1) { sum += raw[idx + 1]; cnt++; }
          if (r > 0)          { sum += raw[idx - blocks]; cnt++; }
          if (r < blocks - 1) { sum += raw[idx + blocks]; cnt++; }
          out.push(sum / cnt);
        }
      }
      raw = out;
    }
    var mn = raw[0], mx = raw[0];
    for (i = 1; i < n; i++) { if (raw[i] < mn) { mn = raw[i]; } if (raw[i] > mx) { mx = raw[i]; } }
    var span = mx - mn;
    if (span < 1e-9) { for (i = 0; i < n; i++) { raw[i] = 0; } return raw; }
    for (i = 0; i < n; i++) { raw[i] = ((raw[i] - mn) / span) * 0.98; }
    return raw;
  }

  // Per-cell frost growth from the rising coverage front. on once cover reaches the
  // cell's threshold; t ramps 0->1 over the `grow` window after crossing. Pure.
  function mmFrostBlotch(fieldVal, cover, grow) {
    if (cover < fieldVal) { return { on: false, t: 0 }; }
    var g = grow > 0 ? grow : 0.25;
    var t = (cover - fieldVal) / g;
    if (t < 0) { t = 0; } else if (t > 1) { t = 1; }
    return { on: true, t: t };
  }

  // Opaque bounding box of an RGBA buffer, as fractions of (w,h). data[i*4+3] is
  // alpha; pixels with alpha > 8 count as opaque. Returns {fracW, fracH} or null
  // (no opaque pixel). Pure — the canvas/getImageData glue lives in mmSpriteFit.
  function mmOpaqueBox(data, w, h) {
    var minx = w, miny = h, maxx = -1, maxy = -1, x, y, a;
    for (y = 0; y < h; y++) {
      for (x = 0; x < w; x++) {
        a = data[(y * w + x) * 4 + 3];
        if (a > 8) {
          if (x < minx) { minx = x; }
          if (x > maxx) { maxx = x; }
          if (y < miny) { miny = y; }
          if (y > maxy) { maxy = y; }
        }
      }
    }
    if (maxx < 0) { return null; }                 // fully transparent
    return { fracW: (maxx - minx + 1) / w, fracH: (maxy - miny + 1) / h };
  }

  // kegD/P multiplier so the SMALLEST opaque dimension lands on the mesh perp dim P.
  // mmStampSprite scales uniformly by globalSize/ih, so after stamping the opaque
  // height is fracH*kegD and width is fracW*iw*kegD/ih; setting their min to P gives
  // kegD = P / min(fracH, fracW*iw/ih). Null/degenerate box -> 1 (full-bleed). Pure.
  function mmKegFitFactor(box, iw, ih) {
    if (!box || !(ih > 0) || !(iw > 0)) { return 1; }
    var wTerm = box.fracW * iw / ih, hTerm = box.fracH;
    var m = wTerm < hTerm ? wTerm : hTerm;
    return (m > 1e-6) ? (1 / m) : 1;
  }

  // Measure a sprite's opaque-content fit factor ONCE (memoized on img._mmKegFit).
  // Downsamples to <=64px on an offscreen canvas, reads alpha via getImageData, and
  // computes mmKegFitFactor. Returns null when measurement isn't possible (no canvas
  // API, undecoded image, or a security/getImageData error) so callers fall back.
  function mmSpriteFit(img) {
    if (!img || !img.width || !img.height) { return null; }
    if (img._mmKegFit != null) { return img._mmKegFit; }
    if (typeof document === 'undefined' || !document.createElement) { return null; }
    var iw = img.width, ih = img.height;
    var s = 64 / (iw > ih ? iw : ih); if (s > 1) { s = 1; }
    var sw = Math.max(1, Math.round(iw * s)), sh = Math.max(1, Math.round(ih * s));
    try {
      var cv = document.createElement('canvas'); cv.width = sw; cv.height = sh;
      var cx = cv.getContext('2d');
      cx.drawImage(img, 0, 0, sw, sh);
      var id = cx.getImageData(0, 0, sw, sh);
      var box = mmOpaqueBox(id.data, sw, sh);
      var f = mmKegFitFactor(box, iw, ih);
      img._mmKegFit = f;
      return f;
    } catch (e) { return null; }                   // tainted canvas / no data -> fall back
  }

  // Pre-bake a sprite into `buckets` pre-rotated, downscaled canvases. On the
  // iPad-1, drawImage is the cheap path but per-frame rotate + resample-from-a-
  // large-source is not — so we do the rotate + downscale ONCE here and stamp
  // plain (translate+scale) copies forever after. Returns null where there is no
  // canvas API (node tests), and mmDrawScatter falls back to per-stamp rotate.
  function mmBuildSpriteAtlas(img, base, buckets) {
    if (typeof document === 'undefined' || !document.createElement) { return null; }
    if (!img || !img.width || !img.height) { return null; }
    var aspect = img.width / img.height, sw, sh;
    if (aspect >= 1) { sw = base; sh = base / aspect; } else { sh = base; sw = base * aspect; }
    var dim = Math.ceil(base * 1.42);          // square big enough to hold any rotation, no corner clip
    var canvases = [], i, cv, cx;
    for (i = 0; i < buckets; i++) {
      cv = document.createElement('canvas'); cv.width = dim; cv.height = dim;
      cx = cv.getContext('2d');
      cx.translate(dim / 2, dim / 2); cx.rotate(i * 6.283185307 / buckets);
      cx.drawImage(img, -sw / 2, -sh / 2, sw, sh);
      canvases.push(cv);
    }
    return { canvases: canvases, dim: dim, sh: sh, buckets: buckets };
  }

  // Per-screen backing-disc case for a radial disc of radius r centered at global
  // (cx,cy) vs this screen's global rect {x,y,w,h}: 'none' (disc not yet reached),
  // 'arc' (edge crossing -> draw the curved edge), 'fill' (fully covers -> cheap
  // solid, no huge-circle tessellation). Pure.
  function mmScatterDiscCase(cx, cy, r, rect) {
    var dx = Math.max(rect.x - cx, 0, cx - (rect.x + rect.w));
    var dy = Math.max(rect.y - cy, 0, cy - (rect.y + rect.h));
    var nearR = Math.sqrt(dx * dx + dy * dy);
    if (r < nearR) { return 'none'; }
    var x2 = rect.x + rect.w, y2 = rect.y + rect.h;
    var xs = [rect.x, x2, x2, rect.x], ys = [rect.y, rect.y, y2, y2];
    var farR = 0, i, ex, ey, d;
    for (i = 0; i < 4; i++) {
      ex = xs[i] - cx; ey = ys[i] - cy; d = Math.sqrt(ex * ex + ey * ey);
      if (d > farR) { farR = d; }
    }
    return (r >= farR) ? 'fill' : 'arc';
  }

  // Draw the scatter cover: backing disc (clean coverage) + erupting sprite copies + giant center.
  // drawImage/arc only; no clip/composite. No-op stamps until img is decoded.
  function mmDrawScatter(ctx, params, phase, p, GW, GH, quad, scope, seed, img, bg, canvasW, canvasH) {
    var sd = root._mmSdbg || {};                        // ?tdbg live tuning knobs (no redeploy)
    var vp = (quad && !sd.nocull && typeof mmMeshViewport === 'function')
      ? mmMeshViewport(quad, GW, GH, canvasW, canvasH) : null;
    var reg = _mmMaskRegion(scope, quad, GW, GH);
    var cx = reg.x + reg.w / 2, cy = reg.y + reg.h / 2;
    var maxR = Math.sqrt((reg.w / 2) * (reg.w / 2) + (reg.h / 2) * (reg.h / 2));
    var c = mmScatterCover(phase, p);
    // backing disc (item bg) — guarantees gap-free cover. Screen-bounded when a
    // viewport is known: per screen, skip until the disc arrives, draw the arc
    // while its edge crosses, and a cheap fillRect once it fully covers (the long
    // covered tail, which used to be a wall-diagonal arc). ?sdisc=0 drops it.
    if (!sd.nodisc && c * maxR >= 0.5) {
      ctx.fillStyle = bg || '#000000';
      var _dc = vp ? mmScatterDiscCase(cx, cy, c * maxR, vp.globalRect) : 'arc';
      if (_dc === 'fill') {
        ctx.fillRect(vp.globalRect.x, vp.globalRect.y, vp.globalRect.w, vp.globalRect.h);
      } else if (_dc === 'arc') {
        ctx.beginPath(); ctx.arc(cx, cy, c * maxR, 0, 6.283185307); ctx.fill();
      }
      // 'none' -> disc hasn't reached this screen -> draw nothing
    }
    if (!img || !img.width) { return; }                 // sprite not decoded yet -> disc only
    var count = (sd.count > 0 ? sd.count : ((params && params.count) || 40));
    var dist = mmScatterDist(phase, p) * maxR;
    var parts = mmScatterParticles(seed >>> 0, count), i, pt, d, sz, ang, x, y, bi, spr;
    var baseH = reg.h * 0.12;
    var _dbg = root._mmDbg, _drawn = 0, _culled = 0, _gdrew = false, _ok;
    // Bake the pre-rotated/downscaled atlas once per image (iPad-1 fast path);
    // null on platforms without a canvas API -> per-stamp rotate fallback below.
    if (img._mmAtlas === undefined) { img._mmAtlas = mmBuildSpriteAtlas(img, 96, 24); }
    var atlas = img._mmAtlas;
    for (i = 0; i < parts.length; i++) {
      pt = parts[i]; d = dist * pt.sp; sz = baseH * (0.55 + 0.5 * c);
      ang = pt.rot0 + p * pt.rps * 6;
      x = cx + Math.cos(pt.ang) * d; y = cy + Math.sin(pt.ang) * d;
      if (atlas) {                                      // pre-rotated bucket: rotation baked, stamp upright
        bi = Math.round(ang / (6.283185307 / atlas.buckets));
        bi = ((bi % atlas.buckets) + atlas.buckets) % atlas.buckets;
        spr = atlas.canvases[bi];
        _ok = mmStampSprite(ctx, vp, spr, x, y, atlas.dim * (sz / atlas.sh), 0);
      } else {                                          // fallback: rotate the full source per stamp
        _ok = mmStampSprite(ctx, vp, img, x, y, sz, ang);
      }
      if (_dbg) { if (_ok) { _drawn++; } else { _culled++; } }
    }
    // giant center. giantScale = peak height as a fraction of the region height
    // (?tdbg: ?sgscale=N overrides it live; ?sgiant=0 drops it). Default 0.2 —
    // on-wall tuning showed >~0.2 lets the centered giant blanket off-center
    // screens (still ~16fps); <=0.2 confines it to the center, holding ~30fps.
    // legacy items with no giantScale fall back to 0.2 (not the old 1.43).
    var gs = (sd.gscale != null) ? sd.gscale
           : ((params && params.giantScale != null) ? params.giantScale : 0.2);
    var gh = reg.h * gs * c;
    if (gh > 2 && !sd.nogiant) {
      _gdrew = mmStampSprite(ctx, vp, img, cx, cy, gh, mmScatterGiantAngle(phase, p));
    }
    if (_dbg) { root._mmScatterStat = { drawn: _drawn, culled: _culled, total: count, giant: !!_gdrew }; }
  }

  // Draw the keg-roll cover: a directional cover rect (item bg) + the giant rolling
  // keg sprite at the boundary. fillRect + (culled) drawImage only; no clip/composite.
  // Cover-only (graceful plain wipe) until the keg PNG decodes. Mirrors mmDrawScatter
  // (minus the seed arg). ctx is already under the mesh affine (in-canvas) or the
  // overlay matrix, so everything is drawn in GLOBAL coords -> wall-coherent.
  function mmDrawKegRoll(ctx, params, phase, prog, GW, GH, quad, scope, img, bg, canvasW, canvasH) {
    var vp = (quad && typeof mmMeshViewport === 'function')
      ? mmMeshViewport(quad, GW, GH, canvasW, canvasH) : null;
    var reg = _mmMaskRegion(scope, quad, GW, GH);
    var dir = (params && params.direction) || 'right';
    var horiz = (dir === 'left' || dir === 'right');
    // Auto-fit: size the keg so its SMALLEST opaque dimension lands on the mesh perp
    // dim (covers the straight cover edge for any sprite/padding). Falls back to 1.3
    // until the sprite is measured/decoded. `fudge` is a margin ON TOP of the fit —
    // default 1.1 (10% larger than the measured fit) for a little overhang; ?kgfill=N
    // overrides it for live fine-tuning.
    var auto = (typeof mmSpriteFit === 'function') ? mmSpriteFit(img) : null;
    var base = (auto != null) ? auto : 1.3;
    var fudge = (root._mmKegFill != null) ? root._mmKegFill : 1.1;
    var kegD = (horiz ? reg.h : reg.w) * base * fudge;
    var rect = mmKegCoverRect(prog, dir, phase, reg, kegD);   // same kegD -> edge tracks the keg
    if (rect) { ctx.fillStyle = bg || '#000000'; ctx.fillRect(rect.x, rect.y, rect.w, rect.h); }
    if (!img || !img.width) { return; }            // sprite not decoded -> cover only (plain wipe)
    var pos = mmKegPos(prog, dir, reg, kegD);
    var ang = mmKegAngle(pos.dist, kegD / 2, dir);
    mmStampSprite(ctx, vp, img, pos.cx, pos.cy, kegD, ang);   // globalSize (height) = kegD
  }

  // Icy-blue palettes (frost reads bluer than white so it doesn't look like cloud).
  var _FROST = {
    frost: { core: '196,224,250', spark: '236,247,255' },
    blue:  { core: '150,198,244', spark: '214,236,255' },
    clear: { core: '210,230,248', spark: '255,255,255' }
  };
  function mmFrostPalette(tint) { return _FROST[tint] || _FROST.frost; }

  // Unit jagged-crystal outline: 2*spikes points alternating OUTER (0.78..1.0) and
  // INNER (0.34..0.50) magnitude around a seeded rotation, as unit offsets {ux,uy}
  // (multiply by a radius to place). Seeded -> deterministic per cell -> wall-coherent.
  // The trig runs ONCE per cell (precomputed into the cache), never per frame. Pure.
  function mmCrystalUnit(seed, spikes) {
    if (!(spikes > 2)) { spikes = 7; }
    var rnd = _mmLcg(seed >>> 0), pts = [], i, rot = rnd() * 6.283185307;
    var step = 6.283185307 / (spikes * 2), ang, mag;
    for (i = 0; i < spikes * 2; i++) {
      ang = rot + i * step;
      mag = (i % 2 === 0) ? (0.78 + rnd() * 0.22) : (0.34 + rnd() * 0.16);
      pts.push({ ux: Math.cos(ang) * mag, uy: Math.sin(ang) * mag });
    }
    return pts;
  }

  // Mug-drop sequencing for frostcreep. Splits the phase so the sprite drops in / rises
  // out and the frost is sequenced around it. Returns {fc, mp}: fc = frost coverage front
  // (feeds the field), mp = mug position 0 (off above the top) .. 1 (at center). COVER:
  // mug drops over the first `mugFrac`, then holds center while frost builds. REVEAL:
  // frost recedes first, then the mug rises out over the last `mugFrac`. Pure.
  function mmFrostSeq(phase, cover, mugFrac) {
    var c = cover < 0 ? 0 : (cover > 1 ? 1 : cover);
    var mf = (mugFrac > 0 && mugFrac < 1) ? mugFrac : 0.3;
    var lp = (phase === 'cover') ? c : (1 - c);     // local phase progress 0->1
    var fc, mp;
    if (phase === 'cover') {
      mp = lp / mf; if (mp > 1) { mp = 1; }                 // drop in, then hold at center
      fc = (lp <= mf) ? 0 : (lp - mf) / (1 - mf);           // frost AFTER the mug lands
    } else {
      fc = 1 - lp / (1 - mf); if (fc < 0) { fc = 0; }       // frost recedes first
      mp = 1 - (lp - (1 - mf)) / mf;                         // then the mug rises out
      if (mp > 1) { mp = 1; } else if (mp < 0) { mp = 0; }
    }
    if (fc < 0) { fc = 0; } else if (fc > 1) { fc = 1; }
    return { fc: fc, mp: mp };
  }

  // Draw frost: a jagged ice crystal per frozen cell (grows with the coverage front),
  // + an optional mug sprite that drops in (cover) / rises out (reveal) with the frost
  // sequenced around it, + a consolidation fill near full cover so the outgoing item is
  // fully hidden at the handoff. moveTo/lineTo/fill + drawImage + fillRect; no clip/
  // composite. Global coords under the mesh affine -> wall-coherent. Field + per-cell
  // crystal shapes memoized on root._mmFrostCache (seed,blocks); crystal trig precomputed.
  // img: optional decoded mug sprite (null -> pure frost, no lead-in).
  function mmDrawFrost(ctx, params, phase, cover, GW, GH, quad, scope, seed, img) {
    var cov = cover < 0 ? 0 : (cover > 1 ? 1 : cover);
    var reg = _mmMaskRegion(scope, quad, GW, GH);
    var hasMug = !!(img && img.width && img.height);
    var seq = hasMug ? mmFrostSeq(phase, cov, 0.30) : null;
    var fc = seq ? seq.fc : cov;                    // frost coverage front (sequenced if a mug exists)
    var mp = seq ? seq.mp : 0;                       // mug position 0 (off top) .. 1 (center)
    if (fc <= 0 && mp <= 0) { return; }
    var fdbg = root._mmFrostDbg || {};
    var blocks = (fdbg.blocks > 0) ? fdbg.blocks : 18;
    var sk = seed >>> 0, cache = root._mmFrostCache;
    if (!cache || cache.seed !== sk || cache.blocks !== blocks) {
      var nn = blocks * blocks, fld = mmFrostField(blocks, sk), cr = [], ci;
      for (ci = 0; ci < nn; ci++) { cr.push(mmCrystalUnit((sk ^ (ci * 374761393)) >>> 0, 7)); }
      cache = { seed: sk, blocks: blocks, field: fld, crystals: cr };
      root._mmFrostCache = cache;
    }
    var field = cache.field, crystals = cache.crystals, pal = mmFrostPalette(params && params.tint);
    var cw = reg.w / blocks, ch = reg.h / blocks, cell = (cw < ch ? cw : ch);
    var r, c, idx, fb, cx, cy, rad, pts, pj;
    if (fc > 0) {
      for (r = 0; r < blocks; r++) {
        for (c = 0; c < blocks; c++) {
          idx = r * blocks + c;
          fb = mmFrostBlotch(field[idx], fc, 0.25);
          if (!fb.on) { continue; }
          cx = reg.x + (c + 0.5) * cw; cy = reg.y + (r + 0.5) * ch;
          rad = cell * (0.7 + 0.8 * fb.t);            // crystal slightly overscans the cell -> jagged overlap
          pts = crystals[idx];
          ctx.fillStyle = 'rgba(' + pal.core + ',' + (0.85 * fb.t) + ')';
          ctx.beginPath();
          ctx.moveTo(cx + pts[0].ux * rad, cy + pts[0].uy * rad);
          for (pj = 1; pj < pts.length; pj++) { ctx.lineTo(cx + pts[pj].ux * rad, cy + pts[pj].uy * rad); }
          ctx.closePath(); ctx.fill();
          if (fb.t > 0.7) {                           // sparkle glint on settled frost
            ctx.fillStyle = 'rgba(' + pal.spark + ',0.9)';
            ctx.beginPath(); ctx.arc(cx, cy, cell * 0.1, 0, 6.2832); ctx.fill();
          }
        }
      }
    }
    // mug: drops in / rises out, centered, sized so its OPAQUE content is 50% of the
    // region height (mmSpriteFit auto-fit, same as keg roll). Over the frost, under the
    // consolidation fill (so it ices over at full cover). Ease-out on the drop/rise.
    if (hasMug && mp > 0 && typeof mmStampSprite === 'function') {
      var fit = (typeof mmSpriteFit === 'function') ? mmSpriteFit(img) : null;
      var mugH = reg.h * 0.5 * (fit != null ? fit : 1);
      var e = 1 - (1 - mp) * (1 - mp);                // ease-out
      var topY = reg.y - mugH, ctrY = reg.y + reg.h / 2;
      mmStampSprite(ctx, null, img, reg.x + reg.w / 2, topY + (ctrY - topY) * e, mugH, 0);
    }
    if (fc >= 0.88) {                               // consolidation -> opaque by fc=1
      var a = (fc - 0.88) / 0.12; if (a > 1) { a = 1; }
      ctx.fillStyle = 'rgba(' + pal.core + ',' + a + ')';
      ctx.fillRect(reg.x, reg.y, reg.w, reg.h);
    }
  }

  // Tile a texture across [x0,x1] at height [top, top+h], scaled so the texture is `h`
  // tall (one vertical tile) and repeated horizontally, anchored to GLOBAL x=0 so every
  // screen aligns and the seam stays continuous. Partial edge tiles are clipped via the
  // drawImage source-subrect (no ctx.clip). img must be loaded (width>0).
  function _tileWheatRect(ctx, img, x0, x1, top, h, tileW) {
    if (x1 <= x0 || tileW <= 0) { return; }
    var iw = img.width, ih = img.height;
    var tx = Math.floor(x0 / tileW) * tileW, dL, dR, dw, sx, sw;
    while (tx < x1) {
      dL = tx < x0 ? x0 : tx;
      dR = (tx + tileW) > x1 ? x1 : (tx + tileW);
      dw = dR - dL;
      if (dw > 0) {
        sx = ((dL - tx) / tileW) * iw;
        sw = (dw / tileW) * iw;
        ctx.drawImage(img, sx, 0, sw, ih, dL, top, dw, h);
      }
      tx += tileW;
    }
  }

  // A single wheat grain: an upward teardrop (rounded base, pointed tip) centered at (x,y),
  // half-width hw, half-height hh, tip nudged by tipDx for an outward fan. Adds one subpath
  // to the CURRENT path (caller batches many grains then fills once). quadraticCurveTo is
  // Safari-5.1 safe; no clip / cubic bezier.
  function _grainTear(ctx, x, y, hw, hh, tipDx) {
    var tx = x + tipDx, ty = y - hh;                                     // pointed tip (top)
    ctx.moveTo(tx, ty);
    ctx.quadraticCurveTo(x + hw, y - hh * 0.15, x + hw, y + hh * 0.30);  // tip -> right bulge
    ctx.quadraticCurveTo(x + hw, y + hh, x, y + hh);                     // -> rounded bottom
    ctx.quadraticCurveTo(x - hw, y + hh, x - hw, y + hh * 0.30);         // -> left
    ctx.quadraticCurveTo(x - hw, y - hh * 0.15, tx, ty);                 // -> back to tip
  }

  // Draw the wheat curtain covering the two outer walls; the center gap (content)
  // grows as openness rises. A dense wheat TEXTURE (over an opaque straw gradient base)
  // fills each wall; leaning, swaying procedural ear-stalks sit on top for parting-edge
  // depth. Global coords (warped by the mesh affine). ctx primitives only -- no clip.
  function mmDrawWheat(ctx, params, phase, front, GW, GH, quad, scope, seed, now, sprite) {
    ctx.globalAlpha = 1;
    var o = mmWheatOpenness(phase, front, (params && params.hold));
    var geom = mmWheatPartGeom(o, GW, GH);
    var pal = mmWheatColor(params && params.tint);
    var reg = _mmMaskRegion(scope, quad, GW, GH);
    // In 'screen' scope the seam is the region center; in 'wall' it's GW/2. Recompute
    // the local edges within the region span.
    var cx = reg.x + reg.w / 2, g = o * (reg.w / 2);
    var leftEdge = cx - g, rightEdge = cx + g, top = reg.y, bot = reg.y + reg.h;

    // backdrop walls (opaque) -- left [reg.x, leftEdge], right [rightEdge, reg.x+reg.w]
    var grL = ctx.createLinearGradient(0, top, 0, bot);
    grL.addColorStop(0, pal.base); grL.addColorStop(1, pal.backdrop);
    ctx.fillStyle = grL;
    if (leftEdge > reg.x) { ctx.fillRect(reg.x, top, leftEdge - reg.x, reg.h); }
    if (rightEdge < reg.x + reg.w) { ctx.fillRect(rightEdge, top, (reg.x + reg.w) - rightEdge, reg.h); }

    // dense wheat TEXTURE over the gradient base (scaled to wall height, tiled across each
    // wall, clipped to the wall edges). The gradient stays as the fallback if not loaded.
    if (sprite && sprite.width) {
      var tW = reg.h * (sprite.width / sprite.height);
      if (leftEdge > reg.x) { _tileWheatRect(ctx, sprite, reg.x, leftEdge, top, reg.h, tW); }
      if (rightEdge < reg.x + reg.w) { _tileWheatRect(ctx, sprite, rightEdge, reg.x + reg.w, top, reg.h, tW); }
    }

    // Screen-bounds cull (wall scope): the field is GLOBAL, so without this every screen
    // redraws ALL stalks (most off-screen). Skip stalks whose base + lean/ear horizontal
    // reach can't land on THIS screen's quad. Biggest win during the dwell (lean->0 =>
    // tight margin). quad is normalized global coords; null (uncalibrated) -> no cull.
    var qLo = -1, qHi = 0, cullM = 0;
    if (quad && quad.length >= 4) {
      qLo = Math.min(quad[0][0], quad[1][0], quad[2][0], quad[3][0]) * GW;
      qHi = Math.max(quad[0][0], quad[1][0], quad[2][0], quad[3][0]) * GW;
      cullM = Math.abs(Math.sin(geom.lean)) * reg.h + reg.h * 0.05;   // lean reach + ear/stalk width
    }

    // stalks: rooted at the bottom of each wall, leaning toward the outer edge,
    // sliding outward with the wall, swaying with `now`.
    var field = mmWheatField(seed, (params && params.density) || 30, reg.w, reg.h);
    var stalkW = reg.h * 0.012, ts = (now || 0) * 0.001, i, s, baseX, leanDir, ang;
    var headRpx, hY;
    for (i = 0; i < field.length; i++) {
      s = field[i];
      // s.bx is in [0,reg.w); map to region x, then slide outward with its wall
      if (s.side === 'left') { baseX = reg.x + s.bx - g; leanDir = -1; }
      else { baseX = reg.x + s.bx + g; leanDir = 1; }
      // cull stalks whose base has slid off its visible wall
      if (s.side === 'left' && (baseX < reg.x || baseX > leftEdge)) { continue; }
      if (s.side === 'right' && (baseX > reg.x + reg.w || baseX < rightEdge)) { continue; }
      // cull stalks that can't reach this screen (global field -> per-screen skip)
      if (qLo >= 0 && (baseX < qLo - cullM || baseX > qHi + cullM)) { continue; }
      ang = leanDir * geom.lean + Math.sin(ts * 1.6 + s.sway) * 0.05;   // lean + gentle sway
      hY = s.h * reg.h;                                                 // stalk height (px)
      ctx.save();
      ctx.translate(baseX, bot);
      ctx.rotate(ang);
      // tapered stalk: a thin triangle base->tip
      ctx.fillStyle = pal.stalk;
      ctx.beginPath();
      ctx.moveTo(-stalkW / 2, 0);
      ctx.lineTo(stalkW / 2, 0);
      ctx.lineTo(0, -hY);
      ctx.closePath();
      ctx.fill();
      // grain EAR: a tight cluster of upward TEARDROP grains (rounded base, pointed tip)
      // fanning slightly outward up the spike -> reads like a real wheat head. All grains
      // in ONE path/fill (the per-stalk hot path on iPad-1).
      headRpx = s.headR * reg.h;                 // per-stalk grain size unit (seeded variety)
      var earLen = hY * 0.45, rows = 6, kr, kf, ky, tap, gw, gh, koff, fan;
      ctx.fillStyle = pal.head;
      ctx.beginPath();
      for (kr = 0; kr < rows; kr++) {
        kf = kr / (rows - 1);                       // 0 at ear base, 1 at the tip
        ky = -hY + earLen * (1 - kf);               // ear base up to the tip
        tap = 1 - kf * 0.5;                          // grains shrink toward the tip
        gw = headRpx * 1.0 * tap;                    // grain half-width
        gh = headRpx * 1.9 * tap;                    // grain half-height (tall teardrop)
        koff = (stalkW * 0.5 + headRpx * 0.55) * tap; // pair spread off the axis
        fan = headRpx * 0.5 * tap;                   // tips fan outward
        _grainTear(ctx, -koff, ky, gw, gh, -fan);
        _grainTear(ctx, koff, ky, gw, gh, fan);
      }
      _grainTear(ctx, 0, -hY - headRpx * 0.4, headRpx * 0.85, headRpx * 2.0, 0);  // crowning tip grain
      ctx.fill();
      // awns: fine bristles fanning up from the tip
      ctx.strokeStyle = pal.head; ctx.lineWidth = stalkW * 0.22;
      ctx.beginPath();
      ctx.moveTo(0, -hY); ctx.lineTo(-stalkW * 1.1, -hY - earLen * 0.5);
      ctx.moveTo(0, -hY); ctx.lineTo(0, -hY - earLen * 0.62);
      ctx.moveTo(0, -hY); ctx.lineTo(stalkW * 1.1, -hY - earLen * 0.5);
      ctx.stroke();
      ctx.restore();
    }
  }

  // Draw the splash crown: a beer droplet lead-in, then an OPAQUE beer disc blooming
  // from the center with a crown of spikes + flung beads on the advancing edge. Global
  // coords (warped by the mesh affine). ctx primitives only -- no clip.
  function mmDrawSplash(ctx, params, phase, front, GW, GH, quad, scope, seed, now) {
    var seq = mmSplashSeq(phase, front, 0.18);
    var pal = mmBeerPalette(params && params.beerType);
    var reg = _mmMaskRegion(scope, quad, GW, GH);
    var cx = reg.x + reg.w / 2, cy = reg.y + reg.h / 2, TWO = 6.283185307;

    if (!seq.impacted) {
      if (seq.dropY <= 0) { return; }
      var dy = reg.y + seq.dropY * (cy - reg.y);          // top -> center
      var dw = reg.h * 0.018, dh = reg.h * 0.045;
      ctx.fillStyle = pal.beerTop;
      ctx.globalAlpha = 0.35;                              // motion streak above the drop
      ctx.fillRect(cx - dw * 0.25, reg.y, dw * 0.5, dy - reg.y);
      ctx.globalAlpha = 1;
      ctx.beginPath();                                     // beer teardrop (pointed top)
      ctx.moveTo(cx, dy - dh);
      ctx.quadraticCurveTo(cx + dw, dy - dh * 0.1, cx + dw, dy + dh * 0.2);
      ctx.quadraticCurveTo(cx + dw, dy + dh, cx, dy + dh);
      ctx.quadraticCurveTo(cx - dw, dy + dh, cx - dw, dy + dh * 0.2);
      ctx.quadraticCurveTo(cx - dw, dy - dh * 0.1, cx, dy - dh);
      ctx.fill();
      return;
    }

    var R = mmSplashRadius(seq.bloom, reg.w, reg.h);
    if (R <= 0) { return; }
    var g = ctx.createLinearGradient(0, cy - R, 0, cy + R);  // beer disc body
    g.addColorStop(0, pal.beerTop); g.addColorStop(1, pal.beerBot);
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, TWO); ctx.fill();

    var spikes = mmCrownSpikes(seed, (params && params.crownCount) || 28);
    var spikeMax = reg.h * 0.06, ts = (now || 0) * 0.001, i, s, sa, slen, tipx, tipy, sw, px, py, fb;
    var cullActive = false, qLo = 0, qHi = 0;
    if (quad && quad.length >= 4) {
      cullActive = true;
      qLo = Math.min(quad[0][0], quad[1][0], quad[2][0], quad[3][0]) * GW - spikeMax * 3;
      qHi = Math.max(quad[0][0], quad[1][0], quad[2][0], quad[3][0]) * GW + spikeMax * 3;
    }
    for (i = 0; i < spikes.length; i++) {
      s = spikes[i]; sa = s.ang;
      px = cx + R * Math.cos(sa); py = cy + R * Math.sin(sa);     // rim base
      if (cullActive && (px < qLo || px > qHi)) { continue; }     // off this screen
      slen = spikeMax * s.lenF * (0.6 + 0.4 * Math.sin(ts * 3 + s.phase));
      tipx = cx + (R + slen) * Math.cos(sa); tipy = cy + (R + slen) * Math.sin(sa);
      sw = reg.h * 0.012 * s.beadF;
      ctx.fillStyle = pal.beerTop;                               // spike triangle
      ctx.beginPath();
      ctx.moveTo(px - (-Math.sin(sa)) * sw, py - (Math.cos(sa)) * sw);
      ctx.lineTo(px + (-Math.sin(sa)) * sw, py + (Math.cos(sa)) * sw);
      ctx.lineTo(tipx, tipy); ctx.closePath(); ctx.fill();
      ctx.fillStyle = pal.foamTop || pal.foam || pal.beerTop;    // foam-highlighted tip bead
      ctx.beginPath(); ctx.arc(tipx, tipy, sw, 0, TWO); ctx.fill();
      fb = R + slen + spikeMax * s.flyF * (0.5 + seq.bloom);     // flung bead ahead
      ctx.globalAlpha = 0.6 * (1 - seq.bloom * 0.3);
      ctx.beginPath(); ctx.arc(cx + fb * Math.cos(sa), cy + fb * Math.sin(sa), sw * 0.7, 0, TWO); ctx.fill();
      ctx.globalAlpha = 1;
    }
  }

  root.mmTransitionState = mmTransitionState;
  root.mmApplyTransition = mmApplyTransition;
  root.mmWipeSlide = mmWipeSlide;
  root.mmWipeCoverRect = mmWipeCoverRect;
  root._mmWallReveal = _wallReveal;
  root.mmSlideOffset = mmSlideOffset;
  root.mmZoomFactor = mmZoomFactor;
  root.mmFlipFactor = mmFlipFactor;
  root.mmCoasterColor = mmCoasterColor;
  root.mmCoasterPhase = mmCoasterPhase;
  root.mmCoasterTumble = mmCoasterTumble;
  root.mmDrawCoasterCorners = mmDrawCoasterCorners;
  root.mmDrawCoasterDisc = mmDrawCoasterDisc;
  root.mmIrisCircle = mmIrisCircle;
  root.mmIrisMaskRects = mmIrisMaskRects;
  root.mmDissolveOrder = mmDissolveOrder;
  root.mmDissolveCovered = mmDissolveCovered;
  root.mmDrawMaskOverlay = mmDrawMaskOverlay;
  root.mmDrawMaskInCanvas = mmDrawMaskInCanvas;
  root.mmBeerPalette = mmBeerPalette;
  root.mmBeerPhase = mmBeerPhase;
  root.mmBeerDuration = mmBeerDuration;
  root.mmBeerLevel = mmBeerLevel;
  root.mmFoamWaveY = mmFoamWaveY;
  root.mmBeerBubbles = mmBeerBubbles;
  root.mmFoamBubbles = mmFoamBubbles;
  root.mmDrawBeer = mmDrawBeer;
  root.mmBuildSpriteAtlas = mmBuildSpriteAtlas;
  root.mmScatterParticles = mmScatterParticles;
  root.mmScatterPhase = mmScatterPhase;
  root.mmScatterDuration = mmScatterDuration;
  root.mmScatterCover = mmScatterCover;
  root.mmScatterDist = mmScatterDist;
  root.mmScatterGiantAngle = mmScatterGiantAngle;
  root.mmScatterSpriteUrl = mmScatterSpriteUrl;
  root.mmScatterDiscCase = mmScatterDiscCase;
  root.mmDrawScatter = mmDrawScatter;
  root.mmDrawKegRoll = mmDrawKegRoll;
  root.mmKegPhase = mmKegPhase;
  root.mmKegCoverRect = mmKegCoverRect;
  root.mmKegPos = mmKegPos;
  root.mmKegAngle = mmKegAngle;
  root.mmFrostPhase = mmFrostPhase;
  root.mmFrostField = mmFrostField;
  root.mmFrostBlotch = mmFrostBlotch;
  root.mmFrostPalette = mmFrostPalette;
  root.mmCrystalUnit = mmCrystalUnit;
  root.mmFrostSeq = mmFrostSeq;
  root.mmDrawFrost = mmDrawFrost;
  root.mmOpaqueBox = mmOpaqueBox;
  root.mmKegFitFactor = mmKegFitFactor;
  root.mmSpriteFit = mmSpriteFit;
  root.mmWheatOpenness = mmWheatOpenness;
  root.mmWheatPartGeom = mmWheatPartGeom;
  root.mmWheatField = mmWheatField;
  root.mmWheatColor = mmWheatColor;
  root.mmWheatPhase = mmWheatPhase;
  root.mmDrawWheat = mmDrawWheat;
  root.mmSplashPhase = mmSplashPhase;
  root.mmSplashSeq = mmSplashSeq;
  root.mmSplashRadius = mmSplashRadius;
  root.mmCrownSpikes = mmCrownSpikes;
  root.mmDrawSplash = mmDrawSplash;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
