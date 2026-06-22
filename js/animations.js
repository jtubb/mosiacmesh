/* js/animations.js — ES5, NO module syntax (so this same file is a valid
 * classic <script> for the iPad-1 display client AND a side-effect ESM import
 * for the admin + Node tests). Single source of truth for SCRIPT animations.
 * Each entry is self-describing; draw(ctx, tMs, w, h, nowMs, seed) is a PURE
 * function of elapsed time (tMs), canvas size, the shared GoTime.now() value
 * (nowMs, for wall-clock animations), and a per-run coordinated seed (seed, for
 * generative animations — same seed on every screen, fresh each playback). So
 * every display draws the same frame. Animations ignore the args they don't
 * need (e.g. a pure geometric one uses only ctx/tMs/w/h). Use MM_RNG(seed) for
 * randomness — never Math.random() (it would diverge per screen). */
(function (root) {
  // Seeded PRNG for coordinated randomness. xorshift32 — BITWISE ONLY
  // (^, <<, >>>), so output is bit-identical on Safari 5.1 / Node / modern V8.
  // imul() is absent on Safari 5.1 so we use bitwise shifts only; no multiply
  // that could exceed 2^53 (engine-divergent low bits).
  // MM_RNG(seed) -> function(): float in [0,1).
  function MM_RNG(seed) {
    var s = (seed >>> 0) || 0x9E3779B9;   // 0 -> non-degenerate default
    return function () {
      s ^= s << 13; s >>>= 0;
      s ^= s >>> 17;
      s ^= s << 5;  s >>>= 0;
      return (s >>> 0) / 4294967296;
    };
  }

  // Per-item seed from the run seed + a SMALL playlist index. The single
  // (idx+1)*const multiply stays << 2^53 because idx is a tiny index.
  function mmDeriveSeed(runSeed, idx) {
    var s = ((runSeed >>> 0) ^ (((idx >>> 0) + 1) * 0x9E3779B1)) >>> 0;
    s ^= s << 13; s >>>= 0;
    s ^= s >>> 17;
    s ^= s << 5;  s >>>= 0;
    return s >>> 0;
  }

  // Per-(loop, item) seed for continuously-looping playlists: fold the loop
  // index into the run seed, then the item index. Pure composition of the
  // tested mmDeriveSeed, so it stays bit-identical across Safari 5.1 / Node /
  // V8. loopIdx is client-derived from the shared clock (see runScriptLoop),
  // so every screen computes the same value at the same instant -> coordinated.
  function mmLoopItemSeed(runSeed, loopIdx, itemIdx) {
    return mmDeriveSeed(mmDeriveSeed(runSeed, loopIdx), itemIdx);
  }

  // Affine mapping GLOBAL wall coords -> this screen's canvas pixels for mesh
  // animations. Fixed by 3 corner correspondences (canvas TL/TR/BL <-> the
  // quad's TL/TR/BL scaled into the GW x GH global canvas; BR is ignored, as
  // an affine is determined by 3 points). meshQuad: [[u,v]x4] normalized 0..1
  // in TL,TR,BR,BL order. Returns {a,b,c,d,e,f} for ctx.setTransform, or null
  // for a degenerate (collinear-edge) quad so the caller can go black.
  function mmMeshTransform(meshQuad, GW, GH, canvasW, canvasH) {
    var g0x = meshQuad[0][0] * GW, g0y = meshQuad[0][1] * GH;   // TL -> (0,0)
    var g1x = meshQuad[1][0] * GW, g1y = meshQuad[1][1] * GH;   // TR -> (W,0)
    var g3x = meshQuad[3][0] * GW, g3y = meshQuad[3][1] * GH;   // BL -> (0,H)
    var e1x = g1x - g0x, e1y = g1y - g0y;
    var e3x = g3x - g0x, e3y = g3y - g0y;
    var det = e1x * e3y - e3x * e1y;
    if (det > -1e-9 && det < 1e-9) { return null; }
    var W = canvasW, H = canvasH;
    var a = (W * e3y) / det;
    var c = (-W * e3x) / det;
    var b = (-H * e1y) / det;
    var d = (H * e1x) / det;
    var e = -(a * g0x + c * g0y);
    var f = -(b * g0x + d * g0y);
    return { a: a, b: b, c: c, d: d, e: e, f: f };
  }

  // One pure Conway step (toroidal edges). prev/next are Uint8Array(GW*GH) of
  // 0/1. Live survives on 2-3 neighbours; dead is born on exactly 3.
  function mmLifeStep(prev, GW, GH) {
    var cells = GW * GH;
    var next = new Uint8Array(cells);
    var x, y, dx, dy;
    for (y = 0; y < GH; y++) {
      for (x = 0; x < GW; x++) {
        var n = 0;
        for (dy = -1; dy <= 1; dy++) {
          for (dx = -1; dx <= 1; dx++) {
            if (dx === 0 && dy === 0) { continue; }
            var nx = (x + dx + GW) % GW, ny = (y + dy + GH) % GH;
            n += prev[ny * GW + nx];
          }
        }
        var alive = prev[y * GW + x];
        next[y * GW + x] = (alive ? (n === 2 || n === 3) : (n === 3)) ? 1 : 0;
      }
    }
    return next;
  }

  // --- Field animations -----------------------------------------------------
  // A FULL-FIELD effect (plasma, life — every cell colored every frame) is
  // expensive the naive way: cols*rows ctx.fillRect calls plus a 'hsl(...)'
  // STRING allocation per cell. On the iPad-1's single thread that blocks for
  // ~100ms+/frame, which (because the item swap can only fire between frames)
  // delays playlist transitions and desyncs start/end across the wall.
  //
  // Instead, a field animation declares { grid:{cols,rows}, smooth, shade(...) }
  // and writes raw RGBA into a small fixed buffer; the framework paints it into
  // a cols x rows offscreen canvas ONCE and scales it to the target with a single
  // drawImage. cols*rows array writes + one blit — no per-cell fill, no strings.
  // mmMakeFieldDraw auto-wraps shade() into the standard draw(ctx,...) at module
  // load, so a FUTURE field script needs zero canvas/scaling code: just shade().
  // shade() is a PURE function of (tMs, nowMs, seed) -> buffer, so it stays unit-
  // testable without a DOM (the wrapper is the only browser-only part).

  // HSL -> RGB written straight into an RGBA buffer (no per-pixel string alloc).
  // h in [0,360), s & l in [0,1]. Writes data[o..o+2]; alpha is owned by the
  // wrapper (set once to 255). Bit-portable: only +,-,*,/ and Math.abs.
  function mmHsl2Rgb(h, s, l, data, o) {
    h = ((h % 360) + 360) % 360;
    var c = (1 - Math.abs(2 * l - 1)) * s;
    var hp = h / 60;
    var x = c * (1 - Math.abs((hp % 2) - 1));
    var m = l - c / 2;
    var r = 0, g = 0, b = 0;
    if (hp < 1) { r = c; g = x; }
    else if (hp < 2) { r = x; g = c; }
    else if (hp < 3) { g = c; b = x; }
    else if (hp < 4) { g = x; b = c; }
    else if (hp < 5) { r = x; b = c; }
    else { r = c; b = x; }
    data[o] = (r + m) * 255;
    data[o + 1] = (g + m) * 255;
    data[o + 2] = (b + m) * 255;
  }

  // Wrap a field entry's shade() into the standard draw(ctx,tMs,w,h,nowMs,seed).
  // Lazily allocates the cols x rows offscreen canvas on first paint so module
  // load stays Node-safe (no document at import; field draws are browser-only).
  // ES5 / Safari 5.1 safe (vendor-prefixed image-smoothing flag; CanvasPixelArray
  // indexed writes). Alpha is initialized to 255 once so shade() writes RGB only.
  function mmMakeFieldDraw(entry) {
    var cols = entry.grid.cols, rows = entry.grid.rows;
    var off = null, octx = null, img = null;
    return function (ctx, tMs, w, h, nowMs, seed) {
      if (off === null) {
        off = document.createElement('canvas');
        off.width = cols; off.height = rows;
        octx = off.getContext('2d');
        img = octx.createImageData(cols, rows);
        var a;
        for (a = 3; a < img.data.length; a += 4) { img.data[a] = 255; }
      }
      entry.shade(img.data, cols, rows, tMs, nowMs, seed);
      octx.putImageData(img, 0, 0);
      var sm = !!entry.smooth;
      ctx.imageSmoothingEnabled = sm;
      ctx.webkitImageSmoothingEnabled = sm;
      ctx.mozImageSmoothingEnabled = sm;
      ctx.msImageSmoothingEnabled = sm;
      ctx.drawImage(off, 0, 0, w, h);
    };
  }

  var animations = [
    {
      key: 'bouncingBalls',
      label: 'Bouncing balls',
      description: 'Four balls drifting around the screen.',
      draw: function (ctx, tMs, w, h) {
        var colors = ['#e74c3c', '#27ae60', '#2980b9', '#f1c40f'];
        var r = Math.max(12, Math.min(w, h) * 0.06), n = 4, i;
        for (i = 0; i < n; i++) {
          var px = (Math.sin(tMs / (900 + i * 220) + i) + 1) / 2;        // 0..1
          var py = (Math.sin(tMs / (700 + i * 180) + i * 1.7) + 1) / 2;  // 0..1
          ctx.fillStyle = colors[i % colors.length];
          ctx.beginPath();
          ctx.arc(r + px * (w - 2 * r), r + py * (h - 2 * r), r, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    },
    {
      key: 'lissajous',
      label: 'Lissajous curve',
      description: 'A single morphing parametric curve that breathes over time.',
      draw: function (ctx, tMs, w, h) {
        var N = 300, i;
        var a = 3 + 2 * Math.sin(tMs / 8000);
        var b = 4 + 2 * Math.sin(tMs / 11000);
        var phi = tMs / 3000;
        var cx = w / 2, cy = h / 2, ax = w * 0.35, ay = h * 0.35;
        ctx.strokeStyle = 'hsl(' + ((tMs / 40) % 360) + ', 70%, 60%)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        for (i = 0; i <= N; i++) {
          var s = (i / N) * Math.PI * 2;
          var x = cx + ax * Math.sin(a * s + phi);
          var y = cy + ay * Math.sin(b * s);
          if (i === 0) { ctx.moveTo(x, y); } else { ctx.lineTo(x, y); }
        }
        ctx.stroke();
      }
    },
    {
      key: 'phyllotaxis',
      label: 'Phyllotaxis spiral',
      description: 'A rotating golden-angle sunflower-seed spiral.',
      draw: function (ctx, tMs, w, h) {
        var N = 600, i;
        var GOLDEN = 137.508 * Math.PI / 180;
        var c = (Math.min(w, h) / (2 * Math.sqrt(N))) * 0.92;
        var cx = w / 2, cy = h / 2;
        var rot = tMs / 4000;
        for (i = 0; i < N; i++) {
          var theta = i * GOLDEN + rot;
          var r = c * Math.sqrt(i);
          var x = cx + r * Math.cos(theta);
          var y = cy + r * Math.sin(theta);
          var dotR = 3 + 2 * Math.sin(tMs / 1500 + i * 0.02);
          ctx.fillStyle = 'hsl(' + ((i / N) * 360) + ', 80%, 60%)';
          ctx.beginPath();
          ctx.arc(x, y, dotR, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    },
    {
      key: 'wireframeCube',
      label: 'Wireframe cube',
      description: 'A spinning 3D wireframe cube.',
      draw: function (ctx, tMs, w, h) {
        var V = [[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]];
        var E = [[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]];
        var ax = tMs / 2500, ay = tMs / 3700, az = tMs / 5300;
        var cosx = Math.cos(ax), sinx = Math.sin(ax);
        var cosy = Math.cos(ay), siny = Math.sin(ay);
        var cosz = Math.cos(az), sinz = Math.sin(az);
        var s = Math.min(w, h) / 4, persp = 0.5, cx = w / 2, cy = h / 2;
        var proj = [], i;
        for (i = 0; i < V.length; i++) {
          var x = V[i][0], y = V[i][1], z = V[i][2];
          var y1 = y * cosx - z * sinx, z1 = y * sinx + z * cosx;
          var x2 = x * cosy + z1 * siny, z2 = -x * siny + z1 * cosy;
          var x3 = x2 * cosz - y1 * sinz, y3 = x2 * sinz + y1 * cosz;
          var d = 1 + z2 * persp;
          proj.push([cx + s * x3 / d, cy + s * y3 / d]);
        }
        ctx.strokeStyle = 'hsl(' + ((tMs / 30) % 360) + ', 80%, 60%)';
        ctx.lineWidth = 3;
        ctx.beginPath();
        for (i = 0; i < E.length; i++) {
          var p0 = proj[E[i][0]], p1 = proj[E[i][1]];
          ctx.moveTo(p0[0], p0[1]);
          ctx.lineTo(p1[0], p1[1]);
        }
        ctx.stroke();
      }
    },
    {
      key: 'radialPulse',
      label: 'Radial pulse',
      description: 'Concentric color rings expanding from the center and fading out.',
      draw: function (ctx, tMs, w, h) {
        var K = 5, PERIOD = 4000, k;
        var cx = w / 2, cy = h / 2;
        var maxR = Math.sqrt(w * w + h * h) / 2;
        ctx.lineWidth = Math.max(0.1, 4 + 6 * Math.sin(tMs / 1000));
        for (k = 0; k < K; k++) {
          var frac = ((tMs / PERIOD) + (k / K)) % 1;
          var R = frac * maxR;
          var alpha = 1 - (R / maxR);
          ctx.globalAlpha = alpha < 0 ? 0 : (alpha > 1 ? 1 : alpha);
          ctx.strokeStyle = 'hsl(' + (((tMs / 40) + k * 30) % 360) + ', 80%, 60%)';
          ctx.beginPath();
          ctx.arc(cx, cy, R > 0.1 ? R : 0.1, 0, Math.PI * 2);
          ctx.stroke();
        }
        ctx.globalAlpha = 1;
      }
    },
    {
      key: 'particleGalaxy',
      label: 'Particle galaxy',
      description: 'A slow galactic swirl of particles on Keplerian orbits.',
      draw: function (ctx, tMs, w, h) {
        var N = 400, i;
        var cx = w / 2, cy = h / 2;
        var mn = Math.min(w, h);
        var RMIN = mn * 0.08, RMAX = mn * 0.45, W0 = 0.0008;
        var GOLD = 137.5 * Math.PI / 180;
        for (i = 0; i < N; i++) {
          var r = RMIN + (RMAX - RMIN) * ((i * 0.6180339887) % 1);
          var omega = W0 * Math.sqrt(RMIN / r);
          var phi = i * GOLD;
          var x = cx + r * Math.cos(omega * tMs + phi);
          var y = cy + r * Math.sin(omega * tMs + phi);
          ctx.fillStyle = 'hsl(' + (((tMs / 80) + i * 2) % 360) + ', 80%, 60%)';
          ctx.fillRect(x - 1, y - 1, 2, 2);
        }
      }
    },
    {
      key: 'plasma',
      label: 'Plasma',
      description: 'Classic demoscene plasma — smoothly shifting color clouds.',
      // Field animation: write the sum-of-sines color field into a 40x30 RGBA
      // buffer; the framework scales it (smoothed) to the canvas with one blit.
      // Same field math as before, but no per-cell fillRect / 'hsl()' string.
      grid: { cols: 40, rows: 30 },
      smooth: true,
      shade: function (data, cols, rows, tMs, nowMs, seed) {
        var k1 = 8, k2 = 12, k3 = 10, k4 = 14;
        var T1 = 2500, T2 = 3300, T3 = 4100, T4 = 1900;
        var rng = MM_RNG(seed);
        var hueShift = rng() * 360;          // per-run colorway rotation
        var ph1 = rng() * 6.283, ph2 = rng() * 6.283,
            ph3 = rng() * 6.283, ph4 = rng() * 6.283;
        var gx, gy, o = 0;
        for (gy = 0; gy < rows; gy++) {
          for (gx = 0; gx < cols; gx++) {
            var u = gx / cols, v = gy / rows;
            var du = u - 0.5, dv = v - 0.5;
            var c = Math.sin(u * k1 + tMs / T1 + ph1)
                  + Math.sin(v * k2 + tMs / T2 + ph2)
                  + Math.sin((u + v) * k3 + tMs / T3 + ph3)
                  + Math.sin(Math.sqrt(du * du + dv * dv) * k4 + tMs / T4 + ph4);
            mmHsl2Rgb((((c + 4) / 8) * 360 + hueShift) % 360, 1, 0.5, data, o);
            o += 4;
          }
        }
      }
    },
    {
      key: 'pendulumWave',
      label: 'Pendulum wave',
      description: 'Sixteen pendulums with staggered periods scrambling and re-syncing.',
      draw: function (ctx, tMs, w, h) {
        var N = 16, i;
        var TB = 4000, TS = 80, AMAX = Math.PI / 6;
        var L = h * 0.7, y0 = h * 0.15;
        for (i = 0; i < N; i++) {
          var xi = (i + 0.5) * w / N;
          var Ti = TB - i * TS;
          var theta = AMAX * Math.sin(2 * Math.PI * tMs / Ti);
          var bx = xi + L * Math.sin(theta);
          var by = y0 + L * Math.cos(theta);
          ctx.strokeStyle = '#ffffff';
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(xi, y0);
          ctx.lineTo(bx, by);
          ctx.stroke();
          ctx.fillStyle = 'hsl(' + ((i / N) * 360) + ', 80%, 60%)';
          ctx.beginPath();
          ctx.arc(bx, by, 8, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    },
    {
      key: 'dvdLogo',
      label: 'Bouncing logo',
      description: 'A MOSAICMESH logo bouncing off the edges, recoloring on each hit.',
      draw: function (ctx, tMs, w, h) {
        var lw = w * 0.18, lh = h * 0.06;
        var vx = 80, vy = 50;
        var rangeX = w - lw, rangeY = h - lh;
        var xRaw = vx * tMs / 1000, yRaw = vy * tMs / 1000;
        var periodX = 2 * rangeX, periodY = 2 * rangeY;
        var x = Math.abs((xRaw % periodX) - rangeX);
        var y = Math.abs((yRaw % periodY) - rangeY);
        var bounces = Math.floor(xRaw / rangeX) + Math.floor(yRaw / rangeY);
        var palette = ['#e74c3c', '#e67e22', '#f1c40f', '#2ecc71', '#1abc9c',
                       '#3498db', '#9b59b6', '#e84393', '#fd79a8', '#00cec9',
                       '#6c5ce7', '#fab1a0', '#55efc4', '#ffeaa7', '#74b9ff'];
        ctx.fillStyle = palette[((bounces % palette.length) + palette.length) % palette.length];
        ctx.font = 'bold ' + Math.round(lh * 0.9) + 'px sans-serif';
        ctx.textBaseline = 'top';
        ctx.fillText('MOSAICMESH', x, y);
      }
    },
    {
      key: 'analogClock',
      label: 'Analog clock',
      description: 'A synchronized analog clock face (hours, minutes, seconds).',
      draw: function (ctx, tMs, w, h, nowMs) {
        var cx = w / 2, cy = h / 2;
        var R = Math.min(w, h) * 0.45;
        var d = new Date(nowMs || 0);
        var H12 = d.getHours() % 12, M = d.getMinutes(), S = d.getSeconds();
        var k;
        ctx.fillStyle = '#000000';
        ctx.fillRect(0, 0, w, h);
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(cx, cy, R, 0, Math.PI * 2);
        ctx.stroke();
        for (k = 0; k < 12; k++) {
          var a = k * Math.PI / 6 - Math.PI / 2;
          var inner = (k % 3 === 0) ? 0.86 : 0.92;
          ctx.lineWidth = (k % 3 === 0) ? 4 : 2;
          ctx.beginPath();
          ctx.moveTo(cx + Math.cos(a) * R * inner, cy + Math.sin(a) * R * inner);
          ctx.lineTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R);
          ctx.stroke();
        }
        var ah = (H12 + M / 60) * Math.PI / 6 - Math.PI / 2;
        var am = (M + S / 60) * Math.PI / 30 - Math.PI / 2;
        var as = S * Math.PI / 30 - Math.PI / 2;
        ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 5;
        ctx.beginPath(); ctx.moveTo(cx, cy);
        ctx.lineTo(cx + Math.cos(ah) * R * 0.5, cy + Math.sin(ah) * R * 0.5); ctx.stroke();
        ctx.lineWidth = 3;
        ctx.beginPath(); ctx.moveTo(cx, cy);
        ctx.lineTo(cx + Math.cos(am) * R * 0.7, cy + Math.sin(am) * R * 0.7); ctx.stroke();
        ctx.strokeStyle = '#e74c3c'; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(cx, cy);
        ctx.lineTo(cx + Math.cos(as) * R * 0.75, cy + Math.sin(as) * R * 0.75); ctx.stroke();
      }
    },
    {
      key: 'wordClock',
      label: 'Word clock',
      description: 'A letter grid that lights up to spell the current time in words.',
      draw: function (ctx, tMs, w, h, nowMs) {
        var ROWS = [
          'ITLISHKMFIVEX',
          'TWENTYQUARTER',
          'HALFBTENPASTO',
          'ONETWOTHREEXX',
          'FOURFIVESIXXX',
          'SEVENEIGHTXXX',
          'NINETENELEVEN',
          'TWELVEOCLOCKX'
        ];
        var COLS = 13, NROW = 8;
        var P = {
          IT: [0, 0, 2], IS: [0, 3, 2], M_FIVE: [0, 8, 4],
          M_TWENTY: [1, 0, 6], M_QUARTER: [1, 6, 7],
          M_HALF: [2, 0, 4], M_TEN: [2, 5, 3], PAST: [2, 8, 4], TO: [2, 11, 2],
          H1: [3, 0, 3], H2: [3, 3, 3], H3: [3, 6, 5],
          H4: [4, 0, 4], H5: [4, 4, 4], H6: [4, 8, 3],
          H7: [5, 0, 5], H8: [5, 5, 5],
          H9: [6, 0, 4], H10: [6, 4, 3], H11: [6, 7, 6],
          H12: [7, 0, 6], OCLOCK: [7, 6, 6]
        };
        var HOURWORD = [null, 'H1', 'H2', 'H3', 'H4', 'H5', 'H6',
                        'H7', 'H8', 'H9', 'H10', 'H11', 'H12'];
        var d = new Date(nowMs || 0);
        var hr = d.getHours(), mn = d.getMinutes();
        var slot = Math.floor(mn / 5);
        var lit = ['IT', 'IS'], dispHour = hr % 12;
        if (slot === 0) { /* o'clock */ }
        else if (slot === 1) { lit.push('M_FIVE', 'PAST'); }
        else if (slot === 2) { lit.push('M_TEN', 'PAST'); }
        else if (slot === 3) { lit.push('M_QUARTER', 'PAST'); }
        else if (slot === 4) { lit.push('M_TWENTY', 'PAST'); }
        else if (slot === 5) { lit.push('M_TWENTY', 'M_FIVE', 'PAST'); }
        else if (slot === 6) { lit.push('M_HALF', 'PAST'); }
        else if (slot === 7) { lit.push('M_TWENTY', 'M_FIVE', 'TO'); }
        else if (slot === 8) { lit.push('M_TWENTY', 'TO'); }
        else if (slot === 9) { lit.push('M_QUARTER', 'TO'); }
        else if (slot === 10) { lit.push('M_TEN', 'TO'); }
        else { lit.push('M_FIVE', 'TO'); }
        if (slot >= 7) { dispHour = (hr + 1) % 12; }
        var hourIdx = (dispHour === 0) ? 12 : dispHour;
        lit.push(HOURWORD[hourIdx]);
        if (slot === 0) { lit.push('OCLOCK'); }
        var on = {}, i, j;
        for (i = 0; i < lit.length; i++) {
          var p = P[lit[i]];
          for (j = 0; j < p[2]; j++) { on[p[0] * COLS + (p[1] + j)] = true; }
        }
        var cell = Math.min(w / COLS, h / NROW);
        var fs = Math.floor(cell * 0.7);
        var ox = (w - cell * COLS) / 2, oy = (h - cell * NROW) / 2;
        ctx.fillStyle = '#000000';
        ctx.fillRect(0, 0, w, h);
        ctx.font = fs + 'px monospace';
        ctx.textBaseline = 'top';
        var r, c2;
        for (r = 0; r < NROW; r++) {
          for (c2 = 0; c2 < COLS; c2++) {
            ctx.fillStyle = on[r * COLS + c2] ? '#ffffff' : '#333333';
            ctx.fillText(ROWS[r].charAt(c2), ox + c2 * cell, oy + r * cell);
          }
        }
      }
    },
    {
      key: 'sunMoonTransit',
      label: 'Sun / moon transit',
      description: 'A sun (day) or moon (night) arcing across the sky by the wall clock.',
      draw: function (ctx, tMs, w, h, nowMs) {
        // Wall-clock animation: position + palette derive from nowMs only;
        // tMs is intentionally unused (the sky tracks the time of day).
        var d = new Date(nowMs || 0);
        var hh = d.getHours() + d.getMinutes() / 60;
        var isDay = (hh >= 6 && hh < 18);
        var t;
        if (isDay) { t = (hh - 6) / 12; }
        else { t = (hh < 6) ? (hh + 6) / 12 : (hh - 18) / 12; }
        if (t < 0) { t = 0; }
        if (t > 1) { t = 1; }
        var grad = ctx.createLinearGradient(0, 0, 0, h);
        if (isDay) {
          grad.addColorStop(0, '#4a90d9');
          grad.addColorStop(1, '#bfe3ff');
        } else {
          grad.addColorStop(0, '#06070f');
          grad.addColorStop(1, '#10233f');
        }
        ctx.fillStyle = grad;
        ctx.fillRect(0, 0, w, h);
        if (!isDay) {
          var s = 12345, i;
          for (i = 0; i < 30; i++) {
            s = (s * 1103515245 + 12345) & 0x7fffffff;
            var sx = (s % 1000) / 1000 * w;
            s = (s * 1103515245 + 12345) & 0x7fffffff;
            var sy = (s % 1000) / 1000 * h * 0.6;
            ctx.fillStyle = '#ffffff';
            ctx.fillRect(sx, sy, 2, 2);
          }
        }
        var cx = w * t;
        var cy = h * 0.4 - h * 0.3 * Math.sin(Math.PI * t);
        var rad = Math.min(w, h) * 0.05;
        ctx.fillStyle = isDay ? '#ffec70' : '#e8eef7';
        ctx.beginPath();
        ctx.arc(cx, cy, rad, 0, Math.PI * 2);
        ctx.fill();
      }
    },
    {
      key: 'gameOfLife',
      label: "Conway's Game of Life",
      description: "Conway's Game of Life evolving from a seeded random board — different every run.",
      // Field animation: the incremental board cache is unchanged; each frame
      // writes live=green / dead=black (or the dim "warming up" noise tint) into
      // a 48x36 RGBA buffer that the framework blits crisply (smooth:false). No
      // per-cell fillRect — the old hot path that blocked the iPad-1 thread.
      grid: { cols: 48, rows: 36 },
      smooth: false,
      shade: (function () {
        var GW = 48, GH = 36, G = 300, STEP_PER_FRAME = 12;
        // Live #7CFC00 = (124,252,0); dead = black; noise tint #3a5a3a = (58,90,58).
        var cache = { seed: null, boards: null, computed: 0, done: false };
        return function (data, cols, rows, tMs, nowMs, seed) {
          var s = (seed >>> 0);
          var cells = GW * GH;
          if (cache.seed !== s || !cache.boards) {
            cache.boards = new Uint8Array(G * cells);
            var rng = MM_RNG(s);
            var i;
            for (i = 0; i < cells; i++) { cache.boards[i] = (rng() < 0.35) ? 1 : 0; }
            cache.seed = s; cache.computed = 1; cache.done = false;
          }
          if (!cache.done) {
            var budget = STEP_PER_FRAME;
            while (cache.computed < G && budget-- > 0) {
              var prev = cache.boards.subarray((cache.computed - 1) * cells, cache.computed * cells);
              cache.boards.set(mmLifeStep(prev, GW, GH), cache.computed * cells);
              cache.computed++;
            }
            if (cache.computed >= G) { cache.done = true; }
          }
          var gen = Math.floor(tMs / 100) % G;
          if (gen < 0) { gen = 0; }
          var x, y, o;
          if (gen < cache.computed) {
            // Board for this generation is ready — live cells green, dead black.
            var base = gen * cells;
            for (y = 0; y < GH; y++) {
              for (x = 0; x < GW; x++) {
                o = (y * GW + x) * 4;
                if (cache.boards[base + y * GW + x]) { data[o] = 124; data[o + 1] = 252; data[o + 2] = 0; }
                else { data[o] = 0; data[o + 1] = 0; data[o + 2] = 0; }
              }
            }
          } else {
            // Not computed yet — seeded coordinated noise (shared tMs bucket ->
            // screens in the noise state at the same 100ms tick draw the same grid).
            var nrng = MM_RNG(mmDeriveSeed(s, Math.floor(tMs / 100)));
            for (y = 0; y < GH; y++) {
              for (x = 0; x < GW; x++) {
                o = (y * GW + x) * 4;
                if (nrng() < 0.5) { data[o] = 58; data[o + 1] = 90; data[o + 2] = 58; }
                else { data[o] = 0; data[o + 1] = 0; data[o + 2] = 0; }
              }
            }
          }
        };
      })()
    },
    {
      key: 'starfield',
      label: 'Starfield',
      description: 'Warp-speed stars streaking outward — a different field every run.',
      draw: function (ctx, tMs, w, h, nowMs, seed) {
        var rng = MM_RNG(seed);
        var N = 200, i;
        var cx = w / 2, cy = h / 2;
        var SPEED = 3000, SPREAD = Math.min(w, h) * 0.04;
        var maxR = Math.sqrt(w * w + h * h);
        for (i = 0; i < N; i++) {
          var ang = rng() * Math.PI * 2;
          var phase = rng();
          var b = 0.4 + rng() * 0.6;
          var f = ((tMs / SPEED + phase) % 1 + 1) % 1;
          var z = 1 - f;
          if (z < 0.001) { continue; }
          var r = (1 / z - 1) * SPREAD;
          if (r > maxR) { continue; }
          var zPrev = z + 0.04; if (zPrev > 1) { zPrev = 1; }
          var rPrev = (1 / zPrev - 1) * SPREAD;
          var ca = Math.cos(ang), sa = Math.sin(ang);
          var g = Math.round(b * 255);
          ctx.strokeStyle = 'rgb(' + g + ',' + g + ',' + g + ')';
          ctx.lineWidth = 1 + (1 - z) * 2;
          ctx.beginPath();
          ctx.moveTo(cx + ca * rPrev, cy + sa * rPrev);
          ctx.lineTo(cx + ca * r, cy + sa * r);
          ctx.stroke();
        }
      }
    },
    {
      key: 'fireworks',
      label: 'Fireworks',
      description: 'Rockets rise and burst — a continuous, never-repeating show.',
      draw: function (ctx, tMs, w, h, nowMs, seed) {
        var SLOT_MS = 800, RISE_MS = 450, LIFE_MS = 1400, G = 0.0009;
        var S = Math.floor(tMs / SLOT_MS), n, j;
        for (n = S - 2; n <= S; n++) {
          if (n < 0) { continue; }
          var dt = tMs - n * SLOT_MS;
          if (dt < 0 || dt >= LIFE_MS) { continue; }
          var brng = MM_RNG(mmDeriveSeed(seed, n));
          var lx = brng() * w;
          var py = h * (0.15 + brng() * 0.35);
          var hue = brng() * 360;
          var M = 30 + Math.floor(brng() * 20);
          var v = 0.12 + brng() * 0.08;
          if (dt < RISE_MS) {
            var rp = dt / RISE_MS;
            var ry = h - (h - py) * rp;
            ctx.fillStyle = 'hsl(' + hue + ', 90%, 70%)';
            ctx.fillRect(lx - 1, ry - 1, 3, 3);
          } else {
            var et = dt - RISE_MS;
            var alpha = 1 - et / (LIFE_MS - RISE_MS);
            if (alpha < 0) { alpha = 0; }
            ctx.fillStyle = 'hsla(' + hue + ', 90%, 60%, ' + alpha.toFixed(3) + ')';
            for (j = 0; j < M; j++) {
              var a = (j / M) * Math.PI * 2;
              var dx = Math.cos(a) * v * et;
              var dy = Math.sin(a) * v * et + 0.5 * G * et * et;
              ctx.fillRect(lx + dx - 1, py + dy - 1, 2, 2);
            }
          }
        }
      }
    },
    {
      key: 'truchet',
      label: 'Truchet tiles',
      description: 'A generative maze of flowing arcs with a traveling color wave.',
      draw: function (ctx, tMs, w, h, nowMs, seed) {
        var rng = MM_RNG(seed);
        var cell = Math.min(w, h) / 8;
        var GW = Math.round(w / cell), GH = Math.round(h / cell);
        var gx, gy;
        ctx.lineWidth = Math.max(2, cell * 0.12);
        for (gy = 0; gy < GH; gy++) {
          for (gx = 0; gx < GW; gx++) {
            var o = rng() < 0.5 ? 0 : 1;
            var x = gx * cell, y = gy * cell;
            var hue = (((gx + gy) * 8) + tMs / 40) % 360;
            var wave = (((gx + gy) - (tMs / 500)) % 8 + 8) % 8;
            var light = (wave < 1) ? 80 : 50;
            ctx.strokeStyle = 'hsl(' + hue + ', 70%, ' + light + '%)';
            if (o === 0) {
              ctx.beginPath(); ctx.arc(x, y, cell / 2, 0, Math.PI / 2); ctx.stroke();
              ctx.beginPath(); ctx.arc(x + cell, y + cell, cell / 2, Math.PI, Math.PI * 1.5); ctx.stroke();
            } else {
              ctx.beginPath(); ctx.arc(x + cell, y, cell / 2, Math.PI / 2, Math.PI); ctx.stroke();
              ctx.beginPath(); ctx.arc(x, y + cell, cell / 2, Math.PI * 1.5, Math.PI * 2); ctx.stroke();
            }
          }
        }
      }
    },
    {
      key: 'spirograph',
      label: 'Spirograph',
      description: 'A hypotrochoid curve traced over time — a new figure every run.',
      draw: function (ctx, tMs, w, h, nowMs, seed) {
        var rng = MM_RNG(seed);
        var R = 0.4 + rng() * 0.1;
        var r = 0.05 + rng() * 0.25;
        var d = 0.3 + rng() * 0.6;
        var N = 500, i;
        var scale = Math.min(w, h) * 0.45;
        var cx = w / 2, cy = h / 2;
        var rot = tMs / 9000;
        var ratio = (R - r) / r;
        var thetaMax = Math.PI * 2 * 8;
        var grow = (tMs / 6000) % 1;
        var tmax = thetaMax * (0.2 + 0.8 * grow);
        var cr = Math.cos(rot), sr = Math.sin(rot);
        ctx.strokeStyle = 'hsl(' + ((tMs / 40) % 360) + ', 70%, 60%)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        for (i = 0; i <= N; i++) {
          var th = (i / N) * tmax;
          var bx = (R - r) * Math.cos(th) + d * r * Math.cos(ratio * th);
          var by = (R - r) * Math.sin(th) - d * r * Math.sin(ratio * th);
          var rx = bx * cr - by * sr;
          var ry = bx * sr + by * cr;
          var px = cx + rx * scale, py = cy + ry * scale;
          if (i === 0) { ctx.moveTo(px, py); } else { ctx.lineTo(px, py); }
        }
        ctx.stroke();
      }
    }
  ];
  // Auto-wrap field entries (those exposing shade()) into a uniform draw(), once
  // at module load, so EVERY consumer (iPad client, admin preview, tests) reads a
  // ready-to-call entry.draw with no per-caller field handling. Imperative (vector)
  // entries keep their hand-written draw untouched.
  (function () {
    var i;
    for (i = 0; i < animations.length; i++) {
      if (typeof animations[i].draw !== 'function' && typeof animations[i].shade === 'function') {
        animations[i].draw = mmMakeFieldDraw(animations[i]);
      }
    }
  })();

  root.MM_ANIMATIONS = animations;
  root.MM_RNG = MM_RNG;
  root.mmDeriveSeed = mmDeriveSeed;
  root.mmLoopItemSeed = mmLoopItemSeed;
  root.mmMeshTransform = mmMeshTransform;
  root.mmLifeStep = mmLifeStep;
  root.mmHsl2Rgb = mmHsl2Rgb;
  root.mmMakeFieldDraw = mmMakeFieldDraw;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
