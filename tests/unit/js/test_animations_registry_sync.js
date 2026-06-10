/**
 * Guard against mirror/index.html drift: every animation in the test
 * mirror must also be registered in index.html's `animations` object.
 * We don't compare function bodies (indentation differs — tabs vs
 * spaces); the Playwright smoke covers behavior of the real copy.
 * This is the cheap "did you forget to paste it into index.html?"
 * check.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { mirror } from './_animations_mirror.js';
import { ANIMATIONS } from '../../../js/timeline/animations-catalog.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');

// `key: function` as it appears in index.html's `var animations = {...}`.
function registeredInIndexHtml(html, key) {
  return new RegExp('\\b' + key + '\\s*:\\s*function\\s*\\(').test(html);
}

test('every mirror animation is registered in index.html', () => {
  const html = readFileSync(path.join(ROOT, 'index.html'), 'utf8');
  for (const key of Object.keys(mirror)) {
    assert.ok(registeredInIndexHtml(html, key), `index.html is missing animation "${key}"`);
  }
});

test('every catalog animation is registered in index.html', () => {
  // Closes the other direction: a catalog entry whose animation was never
  // added to index.html (and isn't in the mirror — e.g. the pre-existing
  // bouncingBalls) would otherwise offer the operator a SCRIPT dropdown
  // option that silently renders a blank wall (index.html guards
  // `if (animations[name])`). Catch it at test time instead.
  const html = readFileSync(path.join(ROOT, 'index.html'), 'utf8');
  for (const a of ANIMATIONS) {
    assert.ok(registeredInIndexHtml(html, a.key),
      `catalog lists "${a.key}" but index.html has no such animation`);
  }
});
