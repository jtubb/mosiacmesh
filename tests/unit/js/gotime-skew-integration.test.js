/**
 * Drive the real loop: feed samples carrying a fixed oscillator drift, tick the steer, and assert
 * _steerTick locks a skew close to the true drift and the steered offset stops drifting (stationary
 * de-skewed residual). Uses the exposed _reviseOffset + _steerTick with a controllable clock.
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

test('_steerTick locks a skew near the true drift and stops offset drift', () => {
  const { GT, setNow } = load();
  var trueSkew = 40e-6, trueOffset = 0, i;
  // feed 20 clean samples over 200s, ticking the steer each time
  for (i = 0; i <= 20; i++) {
    var t = i * 10000; setNow(t);
    var off = trueOffset + trueSkew * t;                 // the true offset drifts
    GT._reviseOffset({ offset: off, precision: 10 }, 'test');
    GT._steerTick();
  }
  var st = GT.getSteerState();
  assert.ok(Math.abs(st.skew - trueSkew) < 8e-6, 'locked skew ' + st.skew + ' vs ' + trueSkew);
  // now() should track the true server time (raw + trueOffset + trueSkew*raw) closely
  setNow(210000);
  var serverNow = 210000 + trueOffset + trueSkew * 210000;
  assert.ok(Math.abs(GT.now() - serverNow) < 40, 'now() off by ' + (GT.now() - serverNow) + 'ms');
});

// M1 regression guard: with trueOffset == 0 (the test above), now-space `t` and raw-space
// `skewRef` coincide, so a reference-frame mismatch between them is invisible. Repeat the drive
// with a large, non-zero trueOffset (1hr) so the mismatch (order skew*offset) is large enough to
// be unmissable if the skew fit / de-trend ever mix the two frames again. Before the M1 fix this
// failed with now() off by ~144ms (skew(40e-6) * offset(3600000ms) drift); after the fix the error
// collapses back to the same sub-2ms baseline as the offset==0 case above.
test('_steerTick tracks true server time under a large non-zero offset (M1 reference-frame guard)', () => {
  const { GT, setNow } = load();
  var trueSkew = 40e-6, trueOffset = 3600000, i;
  for (i = 0; i <= 20; i++) {
    var t = i * 10000; setNow(t);
    var off = trueOffset + trueSkew * t;
    GT._reviseOffset({ offset: off, precision: 10 }, 'test');
    GT._steerTick();
  }
  var st = GT.getSteerState();
  assert.ok(Math.abs(st.skew - trueSkew) < 8e-6, 'locked skew ' + st.skew + ' vs ' + trueSkew);
  setNow(210000);
  var serverNow = 210000 + trueOffset + trueSkew * 210000;
  assert.ok(Math.abs(GT.now() - serverNow) < 25,
    'now() off by ' + (GT.now() - serverNow) + 'ms under a ' + trueOffset + 'ms offset');
});
