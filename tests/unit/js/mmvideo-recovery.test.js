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
const ARMAX = { maxRetries: 3, maxRecaches: 1 };

test('arm: active -> skip (poll watchdog owns active playback)', () => {
  assert.strictEqual(AR({ active: true, isLocal: true, retries: 0, recaches: 0, ...ARMAX }), 'skip');
});

test('arm: non-local src -> skip', () => {
  assert.strictEqual(AR({ active: false, isLocal: false, retries: 0, recaches: 0, ...ARMAX }), 'skip');
});

test('arm: arming (not active) + local + retries below max -> retry', () => {
  assert.strictEqual(AR({ active: false, isLocal: true, retries: 0, recaches: 0, ...ARMAX }), 'retry');
  assert.strictEqual(AR({ active: false, isLocal: true, retries: 2, recaches: 0, ...ARMAX }), 'retry'); // max-1
});

test('arm: retries exhausted, recaches below max -> recache', () => {
  assert.strictEqual(AR({ active: false, isLocal: true, retries: 3, recaches: 0, ...ARMAX }), 'recache'); // ==maxRetries
});

test('arm: retries + recaches exhausted -> skip (NEEDS_ARM, never central)', () => {
  assert.strictEqual(AR({ active: false, isLocal: true, retries: 3, recaches: 1, ...ARMAX }), 'skip'); // ==both
});

test('arm: missing state -> skip (defensive)', () => {
  assert.strictEqual(AR(null), 'skip');
});

// --- arm recache poll decision: poll mmCache.state until the self-pull terminates.
// 'cached' -> re-arm the arming element; 'failed' -> give up (NEEDS_ARM, never central);
// 'pending'/'none'/unknown -> keep waiting (no wall-clock bound -> any seg size). ---
const RP = loadRecovery().mmRecachePollAction;

test('recache-poll: cached -> rearm', () => {
  assert.strictEqual(RP('cached'), 'rearm');
});

test('recache-poll: failed -> giveup', () => {
  assert.strictEqual(RP('failed'), 'giveup');
});

test('recache-poll: pending -> wait (download in progress)', () => {
  assert.strictEqual(RP('pending'), 'wait');
});

test('recache-poll: none -> wait', () => {
  assert.strictEqual(RP('none'), 'wait');
});

test('recache-poll: unknown/undefined -> wait (defensive)', () => {
  assert.strictEqual(RP(undefined), 'wait');
  assert.strictEqual(RP('weird'), 'wait');
});

/* ---- LATCH BREAKER (2026-09-16 on-wall root cause) ------------------------
 * After a fatal MEDIA_ERR_DECODE the <video> element parks at ns=0
 * (NETWORK_EMPTY) and NOTHING re-attempts the load, so the screen stays dark
 * forever. The escalation ladder ended at 'dead' ("stay black"), which IS the
 * latch: 8 screens walked retry -> recache -> dead and parked there.
 *
 * Proven on-wall: re-load() on the PERSISTENT element does not clear it (the
 * watchdog once fired 941 times futilely), but a FRESH element does -- 8/8
 * screens recovered, verr=3 went 8 -> 0 fleet-wide. So 'recreate' belongs
 * between 'recache' and 'dead'.
 */
const WMAX3 = { maxRetries: 2, maxRecaches: 1, maxRecreates: 1 };

test('latch: after retries and recaches are spent, RECREATE before giving up', () => {
  assert.equal(W({ shouldPlay: true, decoding: false, retries: 2, recaches: 1,
                   recreates: 0, ...WMAX3 }), 'recreate');
});

test('latch: recreate budget spent -> dead (never loops forever)', () => {
  assert.equal(W({ shouldPlay: true, decoding: false, retries: 2, recaches: 1,
                   recreates: 1, ...WMAX3 }), 'dead');
});

test('latch: recreate does NOT pre-empt the cheaper retry/recache steps', () => {
  assert.equal(W({ shouldPlay: true, decoding: false, retries: 0, recaches: 0,
                   recreates: 0, ...WMAX3 }), 'retry');
  assert.equal(W({ shouldPlay: true, decoding: false, retries: 2, recaches: 0,
                   recreates: 0, ...WMAX3 }), 'recache');
});

test('latch: a healthy decoding element is never recreated', () => {
  assert.equal(W({ shouldPlay: true, decoding: true, retries: 2, recaches: 1,
                   recreates: 0, ...WMAX3 }), 'ok');
});

test('latch: callers that omit recreate budget keep the OLD ladder (back-compat)', () => {
  // Existing call sites pass no recreates/maxRecreates; they must still get 'dead'
  // rather than an action they cannot perform.
  assert.equal(W({ shouldPlay: true, decoding: false, retries: 2, recaches: 1,
                   maxRetries: 2, maxRecaches: 1 }), 'dead');
});
