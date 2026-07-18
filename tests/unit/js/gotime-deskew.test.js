/**
 * _deskewSamples removes the skew trend from the offset samples so the offset steer chases the
 * RESIDUAL (not the drift the skew term already corrects). Under a pure linear drift, the
 * de-skewed offsets are constant -> the steer's robust-median target is stationary. Pure.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';
function loadGoTime() {
  const code = fs.readFileSync(new URL('../../../js/GoTime.js', import.meta.url), 'utf8');
  function FakeDate() {}
  FakeDate.now = function () { return 1000000; };
  FakeDate.prototype.getTime = function () { return 1000000; };
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
  return sandbox;
}
const GT = loadGoTime().GoTime;
function smp(offset, precision, t) { return { offset: offset, precision: precision, t: t }; }

test('_deskewSamples flattens a pure linear drift to a constant', () => {
  var b = 20e-6, ref = 0, samples = [], i;
  for (i = 0; i < 10; i++) { var t = i * 10000; samples.push(smp(1000 + b * t, 10, t)); }
  var out = GT._deskewSamples(samples, b, ref);
  for (i = 0; i < out.length; i++) { assert.ok(Math.abs(out[i].offset - 1000) < 1e-6, out[i].offset); }
});

test('_deskewSamples preserves precision/t and does not mutate input', () => {
  var samples = [smp(1000, 12, 5000)];
  var out = GT._deskewSamples(samples, 20e-6, 0);
  assert.strictEqual(out[0].precision, 12);
  assert.strictEqual(out[0].t, 5000);
  assert.strictEqual(samples[0].offset, 1000);      // input untouched
  assert.notStrictEqual(out, samples);
});

test('_deskewSamples with skew=0 is an offset-preserving copy', () => {
  var samples = [smp(1000, 10, 5000), smp(1200, 10, 9000)];
  var out = GT._deskewSamples(samples, 0, 3000);
  assert.strictEqual(out[0].offset, 1000);
  assert.strictEqual(out[1].offset, 1200);
});
