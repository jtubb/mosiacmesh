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

  root.mmMeshViewport = mmMeshViewport;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
