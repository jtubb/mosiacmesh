/**
 * _applySkew updates the clock model {offset, skew, skewRef} when a new skew estimate lands,
 * WITHOUT jumping now(): now(raw) = raw + offset + skew*(raw - skewRef) must be equal at the
 * update instant before and after. Also clamps skew to +/-300ppm. Pure.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';
function loadGoTime() {
  const code = fs.readFileSync(new URL('../../../js/GoTime.js', import.meta.url), 'utf8');
  let now = 1000000;
  function FakeDate() {}
  FakeDate.now = function () { return now; };
  FakeDate.prototype.getTime = function () { return now; };
  const sandbox = {
    Date: FakeDate,
    setTimeout: function () { return 0; },
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
const GT = loadGoTime().GoTime;
function nowOf(m, raw) { return raw + m.offset + m.skew * (raw - m.skewRef); }

test('_applySkew keeps now() continuous at the update instant', () => {
  var m0 = { offset: 500, skew: 10e-6, skewRef: 1000 };
  var R = 100000;
  var before = nowOf(m0, R);
  var m1 = GT._applySkew(m0, 25e-6, R);
  var after = nowOf(m1, R);
  assert.ok(Math.abs(before - after) < 1e-9, 'now() jumped: ' + before + ' -> ' + after);
  assert.strictEqual(m1.skewRef, R);
  assert.strictEqual(m1.skew, 25e-6);
});

test('_applySkew then advances at the new rate', () => {
  var m0 = { offset: 0, skew: 0, skewRef: 0 };
  var R = 50000;
  var m1 = GT._applySkew(m0, 30e-6, R);
  // 10s later, now() should have advanced 10000 + 30e-6*10000 = 10000.3
  var adv = nowOf(m1, R + 10000) - nowOf(m1, R);
  assert.ok(Math.abs(adv - (10000 + 30e-6 * 10000)) < 1e-6, 'advance ' + adv);
});

test('_applySkew clamps skew to +/-300ppm', () => {
  var m = GT._applySkew({ offset: 0, skew: 0, skewRef: 0 }, 999e-6, 0);
  assert.strictEqual(m.skew, 300e-6);
  var n = GT._applySkew({ offset: 0, skew: 0, skewRef: 0 }, -999e-6, 0);
  assert.strictEqual(n.skew, -300e-6);
});

test('_applySkew from zero-model is a clean no-op transition', () => {
  var m = GT._applySkew({ offset: 500, skew: 0, skewRef: 0 }, 20e-6, 100000);
  assert.strictEqual(m.offset, 500);          // fold of 0*... adds nothing
  assert.strictEqual(m.skewRef, 100000);
});
