import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

// Loads GoTime.js into a sandbox with recording timers + a stub-able Math.random.
function loadGoTime() {
  const code = fs.readFileSync(new URL('../../../js/GoTime.js', import.meta.url), 'utf8');
  let now = 1000000;
  let randVal = 0;
  const timers = [];
  function FakeDate() {}
  FakeDate.now = function () { return now; };
  FakeDate.prototype.getTime = function () { return now; };
  FakeDate.prototype.valueOf = function () { return now; };   // so `date - number` works in _calculateOffset
  const fakeMath = Object.create(Math);          // inherits floor/round/abs/min/max…
  fakeMath.random = function () { return randVal; };
  const sandbox = {
    Date: FakeDate,
    Math: fakeMath,
    setTimeout: function (fn, d) { timers.push({ fn: fn, delay: d }); return timers.length; },
    setInterval: function () { return 0; },
    clearTimeout: function () {},
    XMLHttpRequest: function () { this.open = function () {}; this.send = function () {}; },
    console: { log: function () {} },
    window: {},
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  sandbox.__setNow = function (t) { now = t; };
  sandbox.__setRand = function (v) { randVal = v; };
  sandbox.__timers = timers;
  return sandbox;
}

// _nextSyncDelay returns an object created INSIDE the vm realm, whose prototype
// differs from this realm's Object.prototype — so assert.deepStrictEqual would
// reject it on prototype identity even when structurally equal. Re-create the
// result in this realm so the comparison is by structure/values.
function decide(s, state) {
  const r = s.GoTime._nextSyncDelay(state);
  return { delayMs: r.delayMs, phase: r.phase, streak: r.streak };
}

const OPTS = {
  SyncInterval: 30000, FastSyncInterval: 1000, SyncPrecisionTargetMs: 40,
  SyncPrecisionStreak: 2, FastSyncCapMs: 60000,
};

test('_nextSyncDelay: fast + coarse precision -> stay fast, streak resets to 0', () => {
  const s = loadGoTime();
  assert.deepStrictEqual(
    decide(s, { phase: 'fast', precision: 120, streak: 1, fastElapsedMs: 1000, opts: OPTS }),
    { delayMs: 1000, phase: 'fast', streak: 0 });
});

test('_nextSyncDelay: fast + good precision below streak target -> stay fast, streak++', () => {
  const s = loadGoTime();
  assert.deepStrictEqual(
    decide(s, { phase: 'fast', precision: 30, streak: 0, fastElapsedMs: 1000, opts: OPTS }),
    { delayMs: 1000, phase: 'fast', streak: 1 });
});

test('_nextSyncDelay: fast + good precision reaching streak -> transition to slow', () => {
  const s = loadGoTime();
  assert.deepStrictEqual(
    decide(s, { phase: 'fast', precision: 30, streak: 1, fastElapsedMs: 2000, opts: OPTS }),
    { delayMs: 30000, phase: 'slow', streak: 2 });
});

test('_nextSyncDelay: precision exactly at target counts as good', () => {
  const s = loadGoTime();
  assert.deepStrictEqual(
    decide(s, { phase: 'fast', precision: 40, streak: 1, fastElapsedMs: 2000, opts: OPTS }),
    { delayMs: 30000, phase: 'slow', streak: 2 });
});

test('_nextSyncDelay: fast + coarse but past cap -> transition to slow (cap wins)', () => {
  const s = loadGoTime();
  assert.deepStrictEqual(
    decide(s, { phase: 'fast', precision: 500, streak: 0, fastElapsedMs: 60000, opts: OPTS }),
    { delayMs: 30000, phase: 'slow', streak: 0 });
});

test('_nextSyncDelay: slow phase always stays slow regardless of precision', () => {
  const s = loadGoTime();
  assert.deepStrictEqual(
    decide(s, { phase: 'slow', precision: 5, streak: 2, fastElapsedMs: 999999, opts: OPTS }),
    { delayMs: 30000, phase: 'slow', streak: 2 });
});

// --- scheduler integration (drives the real _sync/_scheduleAdaptiveSync path) ---

// Wire GoTime for a scheduler run: ws sender always "succeeds", knobs set,
// which arms the 500ms kick via _setupSync. Returns the sandbox.
function primeScheduler(s) {
  s.GoTime.wsSend(function () { return true; });   // _sync's wsCall path succeeds
  s.GoTime.setOptions({
    FastSyncInterval: 1000, FastSyncJitterMs: 150, SyncPrecisionTargetMs: 40,
    SyncPrecisionStreak: 2, FastSyncCapMs: 60000, SyncInterval: 30000,
  });
  return s;
}
function lastDelay(s) { return s.__timers[s.__timers.length - 1].delay; }
function fireLast(s) { s.__timers[s.__timers.length - 1].fn(); }
// Feed one good clock sample through the real revise path (RTT 0 -> precision 0).
function goodSample(s) { s.GoTime.wsReceived(String(1000000)); }

test('scheduler: _setupSync arms the 500ms kick', () => {
  const s = primeScheduler(loadGoTime());
  assert.strictEqual(lastDelay(s), 500);
});

test('scheduler: first sync (no sample yet) schedules fast, jitter 0 -> exactly 1000', () => {
  const s = primeScheduler(loadGoTime());
  s.__setRand(0);
  fireLast(s);                       // fire _sync #1
  assert.strictEqual(lastDelay(s), 1000);
});

test('scheduler: fast jitter stays within [1000, 1150)', () => {
  const s = primeScheduler(loadGoTime());
  s.__setRand(0.999);
  fireLast(s);                       // _sync #1
  const d = lastDelay(s);
  assert.ok(d >= 1000 && d < 1150, 'delay ' + d + ' in [1000,1150)');
});

test('scheduler: two consecutive good samples transition to the 30000ms slow cadence', () => {
  const s = primeScheduler(loadGoTime());
  s.__setRand(0);
  fireLast(s);            goodSample(s);   // _sync #1 -> streak 0, then a good sample
  fireLast(s);            goodSample(s);   // _sync #2 -> streak 1, then a good sample
  assert.strictEqual(lastDelay(s), 1000);  // still fast after streak 1
  fireLast(s);                             // _sync #3 -> streak 2 -> slow
  assert.strictEqual(lastDelay(s), 30000);
});

test('scheduler: a dropped sample (no wsReceived) resets the streak, stays fast', () => {
  const s = primeScheduler(loadGoTime());
  s.__setRand(0);
  fireLast(s); goodSample(s);              // _sync #1 -> streak 0, good sample
  fireLast(s);                             // _sync #2 -> streak 1 (no new sample after)
  fireLast(s);                             // _sync #3: sample NOT returned -> sentinel -> streak 0
  assert.strictEqual(lastDelay(s), 1000);  // still fast, not slow
});
