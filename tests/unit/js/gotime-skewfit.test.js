/**
 * _skewFit: robust Theil-Sen slope of offset-vs-time (the oscillator skew), precision-gated,
 * null unless enough gated samples span enough time baseline. Loaded via the GoTime vm sandbox
 * (same pattern as gotime-steer.test.js), reading the exported _skewFit off the return object.
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
function smp(offset, precision, t) { return { offset: offset, precision: precision, t: t }; }
const OPTS = { windowMs: 120000, skewMinSamples: 6, skewMinSpanMs: 60000, precisionFloorMs: 60 };

test('_skewFit recovers a known slope (20ppm) from clean samples', () => {
  var b = 20e-6, samples = [], i;
  for (i = 0; i < 12; i++) { var t = i * 10000; samples.push(smp(1000 + b * t, 10, t)); } // 0..110s
  var s = GT._skewFit(samples, 110000, OPTS);
  assert.ok(s !== null);
  assert.ok(Math.abs(s - b) < 1e-6, 'slope ' + s + ' should be ~' + b);
});

test('_skewFit resists an outlier (Theil-Sen)', () => {
  var b = 20e-6, samples = [], i;
  for (i = 0; i < 12; i++) { var t = i * 10000; samples.push(smp(1000 + b * t, 10, t)); }
  samples[6] = smp(9999, 10, 60000);                    // one wild offset
  var s = GT._skewFit(samples, 110000, OPTS);
  assert.ok(s !== null && Math.abs(s - b) < 3e-6, 'outlier moved slope too much: ' + s);
});

test('_skewFit returns null on too-few gated samples', () => {
  var samples = [smp(1000, 10, 0), smp(1000, 10, 70000)];
  assert.strictEqual(GT._skewFit(samples, 70000, OPTS), null);
});

test('_skewFit returns null on too-short baseline', () => {
  var samples = [], i;                                  // 8 samples but only 8s span
  for (i = 0; i < 8; i++) { samples.push(smp(1000, 10, i * 1000)); }
  assert.strictEqual(GT._skewFit(samples, 7000, OPTS), null);
});

test('_skewFit precision gate drops high-RTT samples', () => {
  var b = 20e-6, samples = [], i;
  for (i = 0; i < 8; i++) { var t = i * 10000; samples.push(smp(1000 + b * t, 8, t)); }   // clean
  for (i = 0; i < 6; i++) { samples.push(smp(1000 + 400 * Math.sin(i), 300, i * 10000)); } // jittery, gated out
  var s = GT._skewFit(samples, 80000, OPTS);
  assert.ok(s !== null && Math.abs(s - b) < 2e-6, 'gate should exclude the 300ms-precision noise: ' + s);
});
