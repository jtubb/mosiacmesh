import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

// js/mmVideoRecovery.js is an ES5 browser-global module (no ESM export; must stay
// ES5 for iPad-1). Run it in a vm context with a stub window + module, mirroring
// tests/unit/js/_mmcache_load.js, and return the attached global.
function loadRecovery() {
  const code = fs.readFileSync(new URL('../../../js/mmVideoRecovery.js', import.meta.url), 'utf8');
  const sandbox = { window: {}, module: { exports: {} } };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox.window.mmVideoRecovery;
}

const A = loadRecovery().mmVideoErrorAction;
const MAX = 3;

test('non-local error -> ignore (regardless of retries)', () => {
  assert.strictEqual(A({ isLocal: false, retries: 0, maxRetries: MAX }), 'ignore');
  assert.strictEqual(A({ isLocal: false, retries: 9, maxRetries: MAX }), 'ignore');
});

test('local, retries below max -> retry', () => {
  assert.strictEqual(A({ isLocal: true, retries: 0, maxRetries: MAX }), 'retry');
  assert.strictEqual(A({ isLocal: true, retries: 2, maxRetries: MAX }), 'retry'); // max-1 boundary
});

test('local, retries at/over max -> downgrade', () => {
  assert.strictEqual(A({ isLocal: true, retries: 3, maxRetries: MAX }), 'downgrade'); // == max boundary
  assert.strictEqual(A({ isLocal: true, retries: 4, maxRetries: MAX }), 'downgrade');
});

test('missing state -> ignore (defensive)', () => {
  assert.strictEqual(A(null), 'ignore');
  assert.strictEqual(A(undefined), 'ignore');
});
