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

  root.mmTransitionState = mmTransitionState;
  root.mmApplyTransition = mmApplyTransition;
  root.mmWipeSlide = mmWipeSlide;
  root.mmWipeCoverRect = mmWipeCoverRect;
  root._mmWallReveal = _wallReveal;
  root.mmSlideOffset = mmSlideOffset;
  root.mmZoomFactor = mmZoomFactor;
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
  root.mmOpaqueBox = mmOpaqueBox;
  root.mmKegFitFactor = mmKegFitFactor;
  root.mmSpriteFit = mmSpriteFit;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
