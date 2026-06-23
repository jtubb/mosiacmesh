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
