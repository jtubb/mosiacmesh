/**
 * Over a simulated hour with a fixed oscillator drift + realistic sample noise, the disciplined
 * clock keeps now() within a tight bound of true server time, whereas the offset-only baseline
 * (skew forced to 0) drifts roughly linearly. Guards the whole point of the feature.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';
function load() {
  const code = fs.readFileSync(new URL('../../../js/GoTime.js', import.meta.url), 'utf8');
  let now = 0;
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
// deterministic pseudo-noise (no Math.random — reproducible), zero-mean +/-15ms.
// NOTE: a naive `(Math.sin(i*12.9898)*43758.5453) % 1` is NOT zero-mean here — JS `%`
// keeps the sign of the dividend, so `x % 1` in (-1,1) skews the result to
// roughly (-45,+15)ms. Fold through Math.floor to get a true [0,1) uniform first.
function noise(i) {
  var f = Math.sin(i * 12.9898) * 43758.5453;
  f = f - Math.floor(f); // [0,1) uniform
  return (f - 0.5) * 30; // [-15,+15), ~zero-mean
}

test('disciplined now() tracks server time over an hour of fixed drift', () => {
  const { GT, setNow } = load();
  var trueSkew = 35e-6, i, worst = 0;
  for (i = 0; i <= 360; i++) {                          // 360 samples, 10s apart = 1 hour
    var t = i * 10000; setNow(t);
    var off = trueSkew * t + noise(i);
    GT._reviseOffset({ offset: off, precision: 12 }, 'test');
    GT._steerTick();
    if (i > 30) {                                        // after convergence
      var serverNow = t + trueSkew * t;
      var err = Math.abs(GT.now() - serverNow);
      if (err > worst) { worst = err; }
    }
  }
  assert.ok(worst < 25, 'worst tracking error after convergence was ' + worst + 'ms (want <25)');
});
