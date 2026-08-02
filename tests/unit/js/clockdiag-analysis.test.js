import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/clockdiag/analysis.js');
const g = globalThis;

// Build one NTP sample: client sends at t1, server clock is OFF ahead of client,
// up-transit Du, down-transit Dd.  Frame math (see analysis.js):
//   t2=t3 = t1 + OFF + Du ;  t4 = t1 + Du + Dd
function mk(t1, OFF, Du, Dd) {
  return { t1: t1, t2: t1 + OFF + Du, t3: t1 + OFF + Du, t4: t1 + Du + Dd };
}
function close(a, b, tol) { assert.ok(Math.abs(a - b) <= (tol || 0.001), a + ' ~= ' + b); }

test('symmetric constant path: offset exact, rtt = Du+Dd, zero jitter/skew', () => {
  const s = [];
  for (let i = 0; i < 30; i++) { s.push(mk(i * 1000, 100, 20, 20)); }
  const st = g.mmClockStats(s);
  close(st.offset, 100);
  close(st.rtt.min, 40); close(st.rtt.std, 0);
  close(st.jitter.up, 0); close(st.jitter.down, 0);
  close(st.skewPpm, 0);
});

test('CONSTANT asymmetry is absorbed into offset (the NTP limit) and invisible to jitter', () => {
  // OFF=0 but Du=50, Dd=10 -> estimated offset must be (Du-Dd)/2 = 20, NOT 0.
  const s = [];
  for (let i = 0; i < 30; i++) { s.push(mk(i * 1000, 0, 50, 10)); }
  const st = g.mmClockStats(s);
  close(st.offset, 20);              // false offset created purely by path asymmetry
  close(st.rtt.min, 60);
  close(st.jitter.up, 0);            // constant asymmetry adds NO jitter -> unmeasurable
  close(st.jitter.down, 0);
});

test('per-direction jitter: a noisy uplink shows in upJitter, not downJitter', () => {
  const ups = [10, 40, 12, 38, 11, 39, 13, 37, 10, 40];
  const s = ups.map((du, i) => mk(i * 1000, 0, du, 15));
  const st = g.mmClockStats(s);
  assert.ok(st.jitter.up > 10, 'upJitter should be large: ' + st.jitter.up);
  close(st.jitter.down, 0, 0.001);   // downlink constant
  assert.ok(st.jitter.imbalance > 10);
});

test('skew: a linearly drifting offset yields the right ppm', () => {
  // offset grows 2ms every 1000ms sample => 2ms/1000ms = 2000 ppm
  const s = [];
  for (let i = 0; i < 30; i++) { s.push(mk(i * 1000, 100 + i * 2, 20, 20)); }
  const st = g.mmClockStats(s);
  close(st.skewPpm, 2000, 1);
});

test('best offset comes from the minimum-RTT sample', () => {
  // most samples heavily queued (huge symmetric delay -> offset still ~OFF but noisy),
  // one clean low-RTT sample carries the true OFF exactly.
  const s = [];
  for (let i = 0; i < 20; i++) { s.push(mk(i * 1000, 100, 300, 100)); } // asym+queued
  s.push(mk(20000, 100, 5, 5));                                          // clean min-RTT
  const st = g.mmClockStats(s);
  close(st.rtt.min, 10);
  close(st.offset, 100);             // the clean sample's offset, not the queued ones'
});

test('verdicts band correctly (PASS/WARN/FAIL)', () => {
  const stats = { n: 10, rtt: { std: 5 }, offsetJitter: 10, skewPpm: 15,
                  jitter: { up: 5, down: 5, imbalance: 5 } };
  let v = g.mmClockVerdicts(stats);
  assert.equal(v.rttJitter.verdict, 'PASS');
  assert.equal(v.skew.verdict, 'PASS');
  assert.equal(v.jitterImbalance.verdict, 'PASS');

  stats.rtt.std = 50; stats.skewPpm = 90; stats.jitter.imbalance = 45;
  v = g.mmClockVerdicts(stats);
  assert.equal(v.rttJitter.verdict, 'WARN');   // 30<=50<80
  assert.equal(v.skew.verdict, 'FAIL');        // >=80
  assert.equal(v.jitterImbalance.verdict, 'WARN'); // 20<=45<60
});

test('empty input is safe', () => {
  assert.equal(g.mmClockStats([]).n, 0);
  assert.deepEqual(g.mmClockVerdicts({ n: 0 }), {});
});

test('helpers: median, percentile, pstdev, slope', () => {
  const h = g._mmClockHelpers;
  assert.equal(h.median([3, 1, 2]), 2);
  assert.equal(h.median([4, 1, 3, 2]), 2.5);
  assert.equal(h.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.9), 9);
  close(h.pstdev([2, 2, 2]), 0);
  close(h.slope([0, 1, 2, 3], [0, 2, 4, 6]), 2);   // dy/dx = 2
});
