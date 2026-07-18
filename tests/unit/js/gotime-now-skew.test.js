/**
 * now() = raw + _offset + _skew*(raw - _skewRef). With skew=0 (default) it equals raw+offset
 * (today). After _applySkew sets a skew, now() advances at the corrected rate. Driven via the
 * exposed internals with a controllable FakeDate.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';
function load() {
  const code = fs.readFileSync(new URL('../../../js/GoTime.js', import.meta.url), 'utf8');
  let now = 1000000;
  function FakeDate() {}
  FakeDate.now = function () { return now; };
  FakeDate.prototype.getTime = function () { return now; };
  const sandbox = { Date: FakeDate, setTimeout: function () { return 0; },
    setInterval: function () { return 0; }, clearTimeout: function () {},
    XMLHttpRequest: function () { this.open = function () {}; this.send = function () {}; },
    console: { log: function () {} }, window: {} };
  vm.createContext(sandbox); vm.runInContext(code, sandbox);
  return { GT: sandbox.GoTime, setNow: function (t) { now = t; } };
}

test('now() with default skew=0 equals raw + offset (today behavior)', () => {
  const { GT, setNow } = load();
  setNow(2000000);
  // no offset applied -> now() == raw
  assert.strictEqual(GT.now(), 2000000);
});

test('now() advances at the corrected rate after a skew is applied', () => {
  const { GT, setNow } = load();
  setNow(1000000);
  GT._applyModelForTest(0, 50e-6, 1000000);      // helper set below in Step 3
  var t0 = GT.now();
  setNow(1000000 + 10000);
  var t1 = GT.now();
  assert.ok(Math.abs((t1 - t0) - (10000 + 50e-6 * 10000)) < 1e-6, 'advance ' + (t1 - t0));
});
