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
