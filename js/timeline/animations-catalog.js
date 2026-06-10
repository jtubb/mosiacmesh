/**
 * Catalog of built-in SCRIPT animations, mirroring the `animations`
 * registry in index.html. The playlist editor reads this to render an
 * animation <select> when an item's play mode is SCRIPT, so the
 * operator picks from a list instead of memorizing registry keys.
 *
 * HAND-MAINTAINED: when an animation lands in index.html, add its
 * {key, label, description} here. `tests/unit/js/test_animations_catalog.js`
 * asserts this stays in sync with the test mirror (a proxy for
 * index.html).
 */
export const ANIMATIONS = [
  { key: 'bouncingBalls', label: 'Bouncing balls', description: 'Four balls drifting around the screen (the original).' },
  { key: 'lissajous',     label: 'Lissajous curve', description: 'A single morphing parametric curve that breathes over time.' },
  { key: 'phyllotaxis',   label: 'Phyllotaxis spiral', description: 'A rotating golden-angle sunflower-seed spiral.' },
  { key: 'wireframeCube', label: 'Wireframe cube', description: 'A spinning 3D wireframe cube.' },
];
