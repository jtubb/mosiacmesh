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
function loadGoTime(opts) {
  opts = opts || {};
  const code = fs.readFileSync(new URL('../../../js/GoTime.js', import.meta.url), 'utf8');
  let now = opts.startNow || 1000000;
  function FakeDate() {}
  FakeDate.now = function () { return now; };
  FakeDate.prototype.getTime = function () { return now; };
  const sandbox = {
    Date: FakeDate,
    setTimeout: opts.setTimeout || function () { return 0; },
    setInterval: function () { return 0; },
    clearTimeout: function () {},
    XMLHttpRequest: function () { this.open = function () {}; this.send = function () {}; },
    console: { log: function () {} },
    window: {},
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  sandbox.__setNow = function (t) { now = t; };
  return sandbox;
}

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
