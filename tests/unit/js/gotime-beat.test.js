import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

// GoTime.js is a classic ES5 script (sets globals, no exports). Load it into a
// sandbox so we can exercise the new public members without a browser. Same
// pattern as the sibling gotime-*.test.js files (a plain `await import()` of
// this file fails: package.json has "type":"module" so Node loads .js as ESM,
// which is always-strict -- and GoTime.js's `getAccurateTimestamp = ...`
// undeclared-global assignment throws ReferenceError under strict mode).
//
// vm.runInContext runs the source as a classic (non-strict) script, so every
// undeclared assignment inside the GoTime IIFE (e.g. `_sync = function() {...}`,
// `_beatSuppressesPoll = function() {...}`) becomes a property directly on the
// sandbox context object -- not just the `GoTime` return value. That lets the
// wiring tests below call the sandbox's `_sync()` directly, recording whether
// it actually polled (ws call) vs. suppressed + re-armed.
function loadGoTime(opts) {
  opts = opts || {};
  const code = fs.readFileSync(new URL('../../../js/GoTime.js', import.meta.url), 'utf8');
  let now = opts.startNow || 1000000;
  const timers = [];
  function FakeDate() {}
  FakeDate.now = function () { return now; };
  FakeDate.prototype.getTime = function () { return now; };
  FakeDate.prototype.valueOf = function () { return now; };   // so `date - number` works in _calculateOffset
  const sandbox = {
    Date: FakeDate,
    setTimeout: opts.setTimeout || function (fn, d) { timers.push({ fn: fn, delay: d }); return timers.length; },
    setInterval: function () { return 0; },
    clearTimeout: function () {},
    XMLHttpRequest: function () { this.open = function () {}; this.send = function () {}; },
    console: { log: function () {} },
    window: opts.window || {},
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  sandbox.__setNow = function (t) { now = t; };
  sandbox.__timers = timers;
  return sandbox;
}
function lastDelay(s) { return s.__timers[s.__timers.length - 1].delay; }

test('_beatSample: naive offset = serverSendMs - localRecv, fixed precision', () => {
  const G = loadGoTime().GoTime;
  const s = G._beatSample(1000000, 999880, 12);
  assert.equal(s.offset, 120);       // 1000000 - 999880, NO ToF correction
  assert.equal(s.precision, 12);
});

test('_beatSuppressesPoll: fresh beat + enabled -> suppress /time', () => {
  const G = loadGoTime().GoTime;
  assert.equal(G._beatSuppressesPoll(true, 10000, 12000, 6000), true);   // age 2000 < 6000
});
test('_beatSuppressesPoll: stale beat -> poll (failsafe)', () => {
  const G = loadGoTime().GoTime;
  assert.equal(G._beatSuppressesPoll(true, 10000, 20000, 6000), false);  // age 10000 > 6000
});
test('_beatSuppressesPoll: disabled -> never suppress', () => {
  const G = loadGoTime().GoTime;
  assert.equal(G._beatSuppressesPoll(false, 10000, 12000, 6000), false);
});
test('_beatSuppressesPoll: no beat yet (null) -> poll', () => {
  const G = loadGoTime().GoTime;
  assert.equal(G._beatSuppressesPoll(true, null, 12000, 6000), false);
});
test('_beatSuppressesPoll: boundary -- age === staleMs -> poll (not suppressed), age just under -> suppressed', () => {
  const G = loadGoTime().GoTime;
  assert.equal(G._beatSuppressesPoll(true, 10000, 16000, 6000), false);       // age 6000 === staleMs
  assert.equal(G._beatSuppressesPoll(true, 10000, 15999, 6000), true);        // age 5999 < staleMs
});

// --- _sync wiring: the gate must actually skip the poll, not just retime the
// timer (that was the Task-2-review defect: the original fix only changed the
// setTimeout delay in _scheduleAdaptiveSync, but _sync() unconditionally polled
// /time or ws every time it fired regardless of delay -- so a fresh beat made
// polling MORE frequent (every _beatStaleMs) instead of suppressing it). ---

test('_sync: beat mode ON + fresh beat -> skips the ws/ajax poll entirely, re-arms at _beatStaleMs', () => {
  const s = loadGoTime({ window: { _mmClockBeat: true } });
  let wsCalls = 0;
  s.GoTime.wsSend(function () { wsCalls++; return true; });
  s.GoTime.beatReceived(1000000);   // stamps _lastBeatMs at the sandbox's current "now" (1000000)
  s.__setNow(1000000 + 1000);       // age 1000ms, well under the 6000ms default staleMs
  s._sync();
  assert.equal(wsCalls, 0, 'poll must be skipped while the beat is fresh');
  assert.equal(lastDelay(s), 6000, 're-arm delay must be the staleness re-check (_beatStaleMs), not the adaptive cadence');
});

test('_sync: beat mode ON but beat gone stale -> falls through and polls normally', () => {
  const s = loadGoTime({ window: { _mmClockBeat: true } });
  let wsCalls = 0;
  s.GoTime.wsSend(function () { wsCalls++; return true; });
  s.GoTime.beatReceived(1000000);
  s.__setNow(1000000 + 6000);       // age exactly staleMs -> _beatSuppressesPoll's `<` fails -> not suppressed
  s._sync();
  assert.equal(wsCalls, 1, 'a stale beat must fall back to the normal /time-equivalent poll (failsafe)');
});

test('_sync: beat mode ON but no beat ever received -> polls normally', () => {
  const s = loadGoTime({ window: { _mmClockBeat: true } });
  let wsCalls = 0;
  s.GoTime.wsSend(function () { wsCalls++; return true; });
  s._sync();
  assert.equal(wsCalls, 1);
});

test('_sync: beat mode OFF (window._mmClockBeat falsy) -> always polls even with a fresh beat', () => {
  const s = loadGoTime({ window: {} });
  let wsCalls = 0;
  s.GoTime.wsSend(function () { wsCalls++; return true; });
  s.GoTime.beatReceived(1000000);
  s.__setNow(1000000 + 1000);       // would be "fresh" if beat mode were on
  s._sync();
  assert.equal(wsCalls, 1, 'beat mode must be opt-in via window._mmClockBeat');
});

// --- accAge (?tdbg) freshness: a beat sample's fixed 12ms precision can never
// beat an already-tighter /time lock's precision ratchet, so the ratchet branch
// in _reviseOffset (which normally stamps _lastAcceptTime) gets skipped for
// every subsequent beat -- freezing msSinceAccept()/accAge at minutes-old even
// though beats keep arriving every ~2s. beatReceived must refresh
// _lastAcceptTime unconditionally, WITHOUT touching the precision ratchet
// itself (that still governs _robustTarget weighting + readyVerdict). ---

test('beatReceived: refreshes accAge (_lastAcceptTime) even though beat precision cannot beat a tighter /time lock', () => {
  const s = loadGoTime();
  s.GoTime.wsSend(function () { return true; });
  s._sync();                              // stamps options._wsRequestTime at "now" (1000000)
  s.GoTime.wsReceived(String(1000000));   // 0-RTT sample -> precision 0, locks the ratchet + _lastAcceptTime
  assert.equal(s.GoTime.getPrecision(), 0);

  s.__setNow(1000000 + 5000);             // 5s pass with no further /time samples
  assert.ok(s.GoTime.msSinceAccept() >= 4900, 'sanity: accAge should read stale before any beat arrives');

  s.GoTime.beatReceived(1005000);         // beat's fixed 12ms precision can never beat the 0ms /time lock
  assert.equal(s.GoTime.getPrecision(), 0, 'precision ratchet must stay at the tighter /time lock, untouched by the beat');
  assert.ok(s.GoTime.msSinceAccept() < 1000, 'accAge must refresh on beat acceptance regardless of the precision ratchet');
});
