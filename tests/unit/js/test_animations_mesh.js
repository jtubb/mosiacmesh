/**
 * mmMeshTransform maps GLOBAL wall coords -> a screen's canvas pixels via an
 * affine fixed by 3 corner correspondences (canvas TL/TR/BL <-> the quad's
 * TL/TR/BL). The op is the cross-screen continuity guarantee for mesh
 * animations: adjacent screens map the shared global edge to their touching
 * canvas edges, so content flows continuously across the seam.
 */
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/animations.js');
const { mmMeshTransform } = globalThis;

// Apply {a,b,c,d,e,f} to a global point -> canvas pixel.
function apply(m, x, y) { return [m.a * x + m.c * y + m.e, m.b * x + m.d * y + m.f]; }
function near(p, q) { return Math.abs(p[0] - q[0]) < 1e-6 && Math.abs(p[1] - q[1]) < 1e-6; }

test('mmMeshTransform — full-bbox quad maps wall corners to canvas corners', () => {
  const m = mmMeshTransform([[0,0],[1,0],[1,1],[0,1]], 1000, 1000, 100, 100);
  assert.ok(near(apply(m, 0, 0), [0, 0]));        // TL
  assert.ok(near(apply(m, 1000, 0), [100, 0]));   // TR
  assert.ok(near(apply(m, 0, 1000), [0, 100]));   // BL
});

test('mmMeshTransform — right-half quad offsets+scales correctly', () => {
  const m = mmMeshTransform([[0.5,0],[1,0],[1,1],[0.5,1]], 1000, 1000, 100, 100);
  assert.ok(near(apply(m, 500, 0), [0, 0]));      // quad TL -> canvas (0,0)
  assert.ok(near(apply(m, 1000, 0), [100, 0]));   // quad TR -> canvas (100,0)
  assert.ok(near(apply(m, 500, 1000), [0, 100])); // quad BL -> canvas (0,100)
});

test('mmMeshTransform — adjacent screens are continuous across the seam', () => {
  const left  = mmMeshTransform([[0,0],[0.5,0],[0.5,1],[0,1]], 1000, 1000, 100, 100);
  const right = mmMeshTransform([[0.5,0],[1,0],[1,1],[0.5,1]], 1000, 1000, 100, 100);
  // Shared global edge x=500. On the LEFT screen it maps to the right edge
  // (cx=100); on the RIGHT screen to the left edge (cx=0). Same global point ->
  // touching canvas edges -> continuous content.
  assert.ok(Math.abs(apply(left, 500, 300)[0] - 100) < 1e-6);
  assert.ok(Math.abs(apply(right, 500, 300)[0] - 0) < 1e-6);
});

test('mmMeshTransform — deterministic for same inputs', () => {
  const a = mmMeshTransform([[0.1,0.2],[0.6,0.15],[0.62,0.9],[0.08,0.85]], 1280, 960, 1024, 768);
  const b = mmMeshTransform([[0.1,0.2],[0.6,0.15],[0.62,0.9],[0.08,0.85]], 1280, 960, 1024, 768);
  assert.deepStrictEqual(a, b);
});

test('mmMeshTransform — degenerate quad (collinear edges) returns null', () => {
  // index-3 corner == index-0 corner -> left edge vector is zero -> det 0.
  assert.equal(mmMeshTransform([[0,0],[1,0],[1,0],[0,0]], 1000, 1000, 100, 100), null);
});
