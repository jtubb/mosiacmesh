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
