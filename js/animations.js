/* js/animations.js — ES5, NO module syntax (so this same file is a valid
 * classic <script> for the iPad-1 display client AND a side-effect ESM import
 * for the admin + Node tests). Single source of truth for SCRIPT animations.
 * Each entry is self-describing; draw(ctx, tMs, w, h, nowMs) is a PURE function
 * of elapsed time (tMs), canvas size, and — for wall-clock animations only —
 * the shared GoTime.now() value (nowMs), so every display draws the same frame.
 * Animations that don't need wall-clock time ignore the 5th argument. */
(function (root) {
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
      draw: function (ctx, tMs, w, h) {
        var GW = 40, GH = 30, gx, gy;
        var k1 = 8, k2 = 12, k3 = 10, k4 = 14;
        var T1 = 2500, T2 = 3300, T3 = 4100, T4 = 1900;
        var cw = w / GW, ch = h / GH;
        for (gy = 0; gy < GH; gy++) {
          for (gx = 0; gx < GW; gx++) {
            var u = gx / GW, v = gy / GH;
            var du = u - 0.5, dv = v - 0.5;
            var c = Math.sin(u * k1 + tMs / T1)
                  + Math.sin(v * k2 + tMs / T2)
                  + Math.sin((u + v) * k3 + tMs / T3)
                  + Math.sin(Math.sqrt(du * du + dv * dv) * k4 + tMs / T4);
            ctx.fillStyle = 'hsl(' + (((c + 4) / 8) * 360) + ', 100%, 50%)';
            ctx.fillRect(gx * cw, gy * ch, cw + 1, ch + 1);
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
    }
  ];
  root.MM_ANIMATIONS = animations;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
