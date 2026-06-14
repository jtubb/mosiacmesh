import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

// GoTime.js is a classic ES5 script (sets globals, no exports). Load it into a
// sandbox so we can exercise the new public members without a browser.
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

test('readyVerdict: all within thresholds -> true', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime.readyVerdict(20, 5000, 5, 3, {}), true);
});
test('readyVerdict: stale offset -> false', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime.readyVerdict(20, 999999, 5, 3, {}), false);
});
test('readyVerdict: imprecise offset -> false', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime.readyVerdict(200, 5000, 5, 3, {}), false);
});
test('readyVerdict: jittery beat -> false', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime.readyVerdict(20, 5000, 50, 3, {}), false);
  assert.equal(s.GoTime.readyVerdict(20, 5000, 5, 99, {}), false);
});
test('readyVerdict: custom thresholds honored', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime.readyVerdict(80, 5000, 5, 3, { maxPrecisionMs: 100 }), true);
});
test('msSinceAccept: Infinity before any accepted sample', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime.msSinceAccept(), Infinity);
});
test('resync: schedules n samples', () => {
  let calls = 0;
  const s = loadGoTime({ setTimeout: function () { calls++; return 0; } });
  s.GoTime.resync(4, 100);
  assert.equal(calls, 4);
});
