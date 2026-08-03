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
  close(st.skew.ppm, 0);   // constant offset -> zero slope
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

test('mmSkewFit: clean linear drift -> correct ppm + CONFIDENT (long-enough window)', () => {
  // offset = 100 + 50ppm*t1 ; 200 samples 2s apart = 398s window, clean (Du=Dd=10)
  const ppm = 50, s = [];
  for (let i = 0; i < 200; i++) { const t1 = i * 2000; s.push(mk(t1, 100 + ppm * 1e-6 * t1, 10, 10)); }
  const f = g.mmSkewFit(s);
  close(f.ppm, 50, 0.5);
  assert.equal(f.confident, true);
  assert.ok(f.driftMs >= 2 * f.residMs, 'drift beats residual noise');
});

test('mmSkewFit: too-short window -> NOT confident (never a garbage number)', () => {
  const s = [];
  for (let i = 0; i < 30; i++) { s.push(mk(i * 1000, 100 + 50 * 1e-6 * (i * 1000), 10, 10)); }
  const f = g.mmSkewFit(s);            // 29s window < 180s min
  assert.equal(f.confident, false);
});

test('mmSkewFit: min-RTT filter drops high-RTT noisy samples from the fit', () => {
  // half the samples are clean (RTT=20), half heavily/variably queued (RTT ~200-400)
  const s = [];
  for (let i = 0; i < 200; i++) {
    const t1 = i * 2000, off = 100 + 50 * 1e-6 * t1;
    if (i % 2 === 0) { s.push(mk(t1, off, 10, 10)); }                       // clean
    else { const q = 100 + (i % 7) * 40; s.push(mk(t1, off, q, q + (i % 5) * 30)); } // queued/noisy
  }
  const f = g.mmSkewFit(s);
  assert.ok(f.cleanN <= 105 && f.cleanN >= 95, 'kept ~the 100 clean samples: ' + f.cleanN);
  close(f.ppm, 50, 1);               // still recovers the true skew despite the noisy half
  assert.equal(f.confident, true);
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

test('verdicts band correctly (PASS/WARN/FAIL), skew gated on confidence', () => {
  const stats = { n: 10, rtt: { std: 5 }, offsetJitter: 10,
                  skew: { ppm: 15, confident: true },
                  jitter: { up: 5, down: 5, imbalance: 5 } };
  let v = g.mmClockVerdicts(stats);
  assert.equal(v.rttJitter.verdict, 'PASS');
  assert.equal(v.skew.verdict, 'PASS');
  assert.equal(v.jitterImbalance.verdict, 'PASS');

  stats.rtt.std = 50; stats.skew = { ppm: 90, confident: true }; stats.jitter.imbalance = 45;
  v = g.mmClockVerdicts(stats);
  assert.equal(v.rttJitter.verdict, 'WARN');   // 30<=50<80
  assert.equal(v.skew.verdict, 'FAIL');        // >=80
  assert.equal(v.jitterImbalance.verdict, 'WARN'); // 20<=45<60

  // low confidence -> N/A regardless of the ppm value
  stats.skew = { ppm: 5, confident: false };
  v = g.mmClockVerdicts(stats);
  assert.equal(v.skew.verdict, 'N/A');
});

test('mmGoTimeError: ground truth = min-RTT offset; error = GoTime vs truth', () => {
  // min-RTT row has rtt=10, off=100 -> ground truth 100.
  // GoTime is a steady 40ms high (gto = 140) -> errMean 40, errStd 0.
  const rows = [
    { gto: 140, off: 180, rtt: 90 },   // high-RTT probe (noisy off), ignored for GT
    { gto: 140, off: 100, rtt: 10 },   // min-RTT -> GT = 100
    { gto: 140, off: 60,  rtt: 85 }
  ];
  const e = g.mmGoTimeError(rows);
  assert.equal(e.groundTruth, 100);
  close(e.errMean, 40);
  close(e.errStd, 0);
});

test('mmGoTimeError: zero error when GoTime tracks the min-RTT truth', () => {
  const rows = [
    { gto: 100, off: 100, rtt: 12 },
    { gto: 101, off: 250, rtt: 120 },
    { gto:  99, off: -40, rtt: 140 }
  ];
  const e = g.mmGoTimeError(rows);
  assert.equal(e.groundTruth, 100);   // min-RTT row
  assert.ok(Math.abs(e.errMean) <= 1);
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
