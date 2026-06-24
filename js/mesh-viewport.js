/* js/mesh-viewport.js — ES5, NO module syntax (valid classic <script> for the
 * iPad-1 client AND a side-effect import for node tests). Screen-local mesh
 * rendering: a viewport descriptor (this screen's window into the global wall,
 * by inverting mmMeshTransform) + a sprite stamp that culls off-screen and
 * bounds large draws to the visible region. drawImage/arc only — no clip. */
(function (root) {
  // This screen's window into the global wall. Pure; no canvas. Returns null
  // for a degenerate/un-invertible quad (caller draws nothing / goes black).
  function mmMeshViewport(meshQuad, GW, GH, canvasW, canvasH) {
    if (typeof root.mmMeshTransform !== 'function') { return null; }
    var m = root.mmMeshTransform(meshQuad, GW, GH, canvasW, canvasH);
    if (!m) { return null; }
    var detL = m.a * m.d - m.c * m.b;                  // det of the linear 2x2
    if (detL > -1e-12 && detL < 1e-12) { return null; }
    function toGlobal(dx, dy) {                          // device px -> global
      var px = dx - m.e, py = dy - m.f;
      return { x: (m.d * px - m.c * py) / detL,
               y: (-m.b * px + m.a * py) / detL };
    }
    var c0 = toGlobal(0, 0), c1 = toGlobal(canvasW, 0),
        c2 = toGlobal(canvasW, canvasH), c3 = toGlobal(0, canvasH);
    var minx = Math.min(c0.x, c1.x, c2.x, c3.x), maxx = Math.max(c0.x, c1.x, c2.x, c3.x);
    var miny = Math.min(c0.y, c1.y, c2.y, c3.y), maxy = Math.max(c0.y, c1.y, c2.y, c3.y);
    var scale = Math.sqrt(Math.abs(detL));
    return {
      m: m,
      globalRect: { x: minx, y: miny, w: maxx - minx, h: maxy - miny },
      scale: scale,
      toDevice: function (globalLen) { return globalLen * scale; },
      intersects: function (gx, gy, gRadius) {
        return (gx + gRadius) >= minx && (gx - gRadius) <= maxx &&
               (gy + gRadius) >= miny && (gy - gRadius) <= maxy;
      }
    };
  }

  // Draw img (Image or canvas) centered at global (gx,gy), height globalSize
  // global px, rotated `angle` rad. With a viewport: cull if off-screen, else
  // draw only the visible source sub-rect under the composed global->device
  // transform so the destination is bounded to the screen (no oversized blit).
  // With vp null: legacy draw under the ctx's ambient transform.
  function mmStampSprite(ctx, vp, img, gx, gy, globalSize, angle) {
    var iw = img.width, ih = img.height;
    if (!iw || !ih || !(globalSize > 0)) { return; }
    var k = globalSize / ih;                          // local px -> global px
    if (!vp) {                                         // legacy ambient global draw
      ctx.save();
      ctx.translate(gx, gy);
      if (angle) { ctx.rotate(angle); }
      ctx.scale(k, k);
      ctx.drawImage(img, -iw / 2, -ih / 2);
      ctx.restore();
      return;
    }
    var gRad = 0.5 * k * Math.sqrt(iw * iw + ih * ih);   // circumscribed global radius
    if (!vp.intersects(gx, gy, gRad)) { return; }
    var ca = Math.cos(angle), sa = Math.sin(angle);
    // Visible source sub-rect: map the viewport's global corners into local-
    // centered coords (inverse of translate(gx,gy).rotate(angle).scale(k)),
    // take the bbox, shift to image coords, pad 1px, clamp to [0,iw]x[0,ih].
    var gr = vp.globalRect, lx, ly, minx = 1e30, maxx = -1e30, miny = 1e30, maxy = -1e30;
    var cx4 = [gr.x, gr.x + gr.w, gr.x + gr.w, gr.x], cy4 = [gr.y, gr.y, gr.y + gr.h, gr.y + gr.h];
    for (var i = 0; i < 4; i++) {
      var dxg = cx4[i] - gx, dyg = cy4[i] - gy;
      lx = (ca * dxg + sa * dyg) / k;                  // inverse rotation + scale
      ly = (-sa * dxg + ca * dyg) / k;
      if (lx < minx) { minx = lx; } if (lx > maxx) { maxx = lx; }
      if (ly < miny) { miny = ly; } if (ly > maxy) { maxy = ly; }
    }
    var sx = Math.floor(minx + iw / 2) - 1, sy = Math.floor(miny + ih / 2) - 1;
    var ex = Math.ceil(maxx + iw / 2) + 1, ey = Math.ceil(maxy + ih / 2) + 1;
    if (sx < 0) { sx = 0; } if (sy < 0) { sy = 0; }
    if (ex > iw) { ex = iw; } if (ey > ih) { ey = ih; }
    var sw = ex - sx, sh = ey - sy;
    if (sw <= 0 || sh <= 0) { return; }                // nothing visible
    // Composed local-centered -> device: M . (translate(gx,gy).rotate.scale(k)).
    var m = vp.m;
    var Ca = m.a * (k * ca) + m.c * (k * sa);
    var Cc = m.a * (-k * sa) + m.c * (k * ca);
    var Cb = m.b * (k * ca) + m.d * (k * sa);
    var Cd = m.b * (-k * sa) + m.d * (k * ca);
    var Ce = m.a * gx + m.c * gy + m.e;
    var Cf = m.b * gx + m.d * gy + m.f;
    ctx.save();
    ctx.setTransform(Ca, Cb, Cc, Cd, Ce, Cf);
    ctx.drawImage(img, sx, sy, sw, sh, sx - iw / 2, sy - ih / 2, sw, sh);
    ctx.restore();
  }

  root.mmStampSprite = mmStampSprite;
  root.mmMeshViewport = mmMeshViewport;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
