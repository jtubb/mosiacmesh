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

const W = loadRecovery().mmWatchdogAction;
const WMAX = { maxRetries: 2, maxRecaches: 1 };

test('watchdog: not should-play -> ok', () => {
  assert.strictEqual(W({ shouldPlay: false, decoding: false, retries: 5, recaches: 5, ...WMAX }), 'ok');
});

test('watchdog: decoding -> ok (regardless of counters)', () => {
  assert.strictEqual(W({ shouldPlay: true, decoding: true, retries: 5, recaches: 5, ...WMAX }), 'ok');
});

test('watchdog: stalled, retries below max -> retry', () => {
  assert.strictEqual(W({ shouldPlay: true, decoding: false, retries: 0, recaches: 0, ...WMAX }), 'retry');
  assert.strictEqual(W({ shouldPlay: true, decoding: false, retries: 1, recaches: 0, ...WMAX }), 'retry'); // max-1
});

test('watchdog: retries exhausted, recaches below max -> recache', () => {
  assert.strictEqual(W({ shouldPlay: true, decoding: false, retries: 2, recaches: 0, ...WMAX }), 'recache'); // ==maxRetries
});

test('watchdog: retries + recaches exhausted -> dead', () => {
  assert.strictEqual(W({ shouldPlay: true, decoding: false, retries: 2, recaches: 1, ...WMAX }), 'dead'); // ==both
});

test('watchdog: missing state -> ok (defensive)', () => {
  assert.strictEqual(W(null), 'ok');
});

// --- arm-phase error recovery (the poll watchdog is gated on playback.active, so it
// does NOT cover the pre-playback ARM phase; an arm-time <video> error is handled here) ---
const AR = loadRecovery().mmArmRetryAction;
const ARMAX = { maxRetries: 3 };

test('arm: active -> skip (poll watchdog owns active playback)', () => {
  assert.strictEqual(AR({ active: true, isLocal: true, retries: 0, ...ARMAX }), 'skip');
});

test('arm: non-local src -> skip', () => {
  assert.strictEqual(AR({ active: false, isLocal: false, retries: 0, ...ARMAX }), 'skip');
});

test('arm: arming (not active) + local + retries below max -> retry', () => {
  assert.strictEqual(AR({ active: false, isLocal: true, retries: 0, ...ARMAX }), 'retry');
  assert.strictEqual(AR({ active: false, isLocal: true, retries: 2, ...ARMAX }), 'retry'); // max-1
});

test('arm: retries exhausted -> skip (NEEDS_ARM/black takes over, never central)', () => {
  assert.strictEqual(AR({ active: false, isLocal: true, retries: 3, ...ARMAX }), 'skip'); // ==max
});

test('arm: missing state -> skip (defensive)', () => {
  assert.strictEqual(AR(null), 'skip');
});
