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

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');

test('every mirror animation is registered in index.html', () => {
  const html = readFileSync(path.join(ROOT, 'index.html'), 'utf8');
  for (const key of Object.keys(mirror)) {
    // Match `key: function` as it appears in `var animations = {...}`.
    const re = new RegExp('\\b' + key + '\\s*:\\s*function\\s*\\(');
    assert.ok(re.test(html), `index.html is missing animation "${key}"`);
  }
});
