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
function close(a, b, tol) { assert.ok(Math.abs(a - b) <= (tol || 0.001), a + ' ~= ' + b); }
// ring entry at time `t` carrying offset = trueOffset - Dd
function r(offset, t) { return { offset: offset, t: t }; }

test('_downlinkTarget: additive queueing -> p90 recovers offset - Dd_floor', () => {
  const s = loadGoTime(), now = 1000000;
  // offset=500; Dd in {10x6,25,40,60,80,100,100} -> ring offset = 500 - Dd
  const dds = [10,10,10,10,10,10,25,40,60,80,100,100];
  const ring = dds.map(function (dd, i) { return r(500 - dd, now - i * 2000); });
  close(s.GoTime._downlinkTarget(ring, now, {}), 490, 1); // 500 - Dd_floor(10)
});

test('_downlinkTarget: out-of-window samples excluded', () => {
  const s = loadGoTime(), now = 1000000;
  const ring = [r(9999, now - 200000)]; // stale, older than dlWindowMs -> dropped
  for (let i = 0; i < 12; i++) { ring.push(r(490, now - i * 1000)); } // fresh floor
  close(s.GoTime._downlinkTarget(ring, now, {}), 490, 1); // stale 9999 not counted
});

test('_downlinkTarget: p90 (not literal max) rejects a single glitch', () => {
  const s = loadGoTime(), now = 1000000;
  const ring = [];
  for (let i = 0; i < 40; i++) { ring.push(r(488, now - i * 1000)); } // clean floor
  ring.push(r(9999, now - 40000));                                    // one glitch
  close(s.GoTime._downlinkTarget(ring, now, {}), 488, 1);
});

test('_downlinkTarget: below dlMinSamples -> null', () => {
  const s = loadGoTime(), now = 1000000;
  const ring = [];
  for (let i = 0; i < 8; i++) { ring.push(r(490, now - i * 1000)); } // 8 < 12
  assert.equal(s.GoTime._downlinkTarget(ring, now, {}), null);
});

test('_downlinkTarget: empty ring -> null', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime._downlinkTarget([], 1000000, {}), null);
});
