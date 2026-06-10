/**
 * MIRROR of the `animations` registry in index.html.
 *
 * index.html is ES5 (must run on a 1st-gen iPad / Safari 5.1), so
 * these functions are written ES5-style (var / function, no arrows,
 * no template literals) — they are COPY-PASTE IDENTICAL to the
 * entries in index.html's `var animations = {...}`. The Node
 * determinism tests import from here; the real index.html copy is
 * covered by the Playwright smoke (renders non-blank) and the
 * registry-sync test (key presence).
 *
 * When you add/change an animation: edit it HERE and paste the exact
 * same function body into index.html (or vice-versa). Keep them in
 * lockstep.
 */
export const mirror = {};

mirror.lissajous = function (ctx, tMs, w, h) {
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
};
