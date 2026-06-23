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

function smp(offset, precision, t) { return { offset: offset, precision: precision, t: t }; }

const O = { deadbandMs: 33, snapMs: 500, capMsPerSec: 15 };

test('_steerStep: error within deadband -> unchanged', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime._steerStep(1000, 1020, 1000, O), 1000); // err 20 <= 33
});

test('_steerStep: slews toward target, capped by rate*dt', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime._steerStep(1000, 1200, 1000, O), 1015); // +err 200, cap 15ms/1s
  assert.equal(s.GoTime._steerStep(1000, 800, 1000, O), 985);   // -err 200, cap 15ms/1s
  assert.equal(s.GoTime._steerStep(1000, 1200, 2000, O), 1030); // dt 2s -> cap 30ms
});

test('_steerStep: snaps on large error', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime._steerStep(1000, 1600, 1000, O), 1600); // err 600 >= 500
});

test('_steerStep: slew can never reverse now() (|move| < dt)', () => {
  const s = loadGoTime();
  const dt = 1000;
  const out = s.GoTime._steerStep(1000, 1400, dt, O); // err 400 < snap -> slew
  assert.ok(Math.abs(out - 1000) < dt);               // 15 < 1000
});

const RT = { windowMs: 120000, minSamples: 3, precisionFloorMs: 60 };

test('_robustTarget: median of in-window gated samples', () => {
  const s = loadGoTime(); const now = 200000;
  const samples = [smp(100, 10, now - 1000), smp(110, 12, now - 2000), smp(105, 11, now - 3000)];
  assert.equal(s.GoTime._robustTarget(samples, now, RT), 105); // median[100,105,110]
});

test('_robustTarget: excludes high-RTT (gated) outliers', () => {
  const s = loadGoTime(); const now = 200000;
  // best precision 10 -> gate = max(20, 60) = 60; the 200-precision sample is dropped
  const samples = [smp(100, 10, now - 1000), smp(110, 12, now - 2000),
                   smp(106, 11, now - 3000), smp(9999, 200, now - 1500)];
  assert.equal(s.GoTime._robustTarget(samples, now, RT), 106); // median[100,106,110]
});

test('_robustTarget: excludes out-of-window samples', () => {
  const s = loadGoTime(); const now = 500000;
  const samples = [smp(100, 10, now - 1000), smp(110, 12, now - 2000), smp(105, 11, now - 999999)];
  assert.equal(s.GoTime._robustTarget(samples, now, RT), null); // only 2 in window < 3
});

test('_robustTarget: below minSamples -> null', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime._robustTarget([smp(100, 10, 99000), smp(110, 12, 98000)], 100000, RT), null);
});

test('_robustTarget: empty -> null', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime._robustTarget([], 100000, RT), null);
});

test('_robustTarget: even count -> average of the two middle offsets', () => {
  const s = loadGoTime(); const now = 200000;
  const samples = [smp(100, 10, now - 1000), smp(110, 10, now - 2000),
                   smp(106, 10, now - 3000), smp(104, 10, now - 4000)];
  // sorted offsets [100,104,106,110] -> median = (104+106)/2
  assert.equal(s.GoTime._robustTarget(samples, now, RT), 105);
});

test('_robustTarget: gate uses 2*best above the floor (admits 70 when best=40, drops 90)', () => {
  const s = loadGoTime(); const now = 200000;
  // best=40 -> gate=max(80,60)=80; precision 70 passes (proves the 2*best arm), 90 dropped
  const samples = [smp(100, 40, now - 1000), smp(110, 50, now - 2000),
                   smp(120, 70, now - 3000), smp(999, 90, now - 1500)];
  assert.equal(s.GoTime._robustTarget(samples, now, RT), 110); // median[100,110,120]
});

test('getSteerState: initial state', () => {
  const s = loadGoTime();
  const st = s.GoTime.getSteerState();
  assert.equal(st.steering, false);
  assert.equal(st.samples, 0);
  assert.equal(st.offset, 0);
});

test('setOptions: steer knobs are accepted', () => {
  const s = loadGoTime();
  s.GoTime.setOptions({ SteerCapMsPerSec: 25, SteerMinSamples: 2 });
  assert.equal(s.GoTime.getSteerState().samples, 0);
});

test('_reviseOffset records samples into the ring', () => {
  const s = loadGoTime();
  s._reviseOffset({ offset: 100, precision: 10 }, 't');
  s._reviseOffset({ offset: 110, precision: 12 }, 't');
  assert.equal(s.GoTime.getSteerState().samples, 2);
});

test('ring is bounded (does not grow without limit)', () => {
  const s = loadGoTime();
  for (var i = 0; i < 200; i++) { s._reviseOffset({ offset: 100, precision: 10 }, 't'); }
  assert.ok(s.GoTime.getSteerState().samples <= 64);
});
