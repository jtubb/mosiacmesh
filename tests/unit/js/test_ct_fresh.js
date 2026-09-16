/* Staleness corrector for v.currentTime (audit 0.4 lever 1).
 *
 * Measured on-wall 2026-09-16 (18/18 screens): currentTime is a ~256ms
 * zero-order hold, median staleness 142ms. The drift controller reads it raw at
 * index.html:1084, so it acts on a ~142ms phantom lag and over-speeds; the videos
 * end up ~40ms AHEAD of the clock and the per-screen staleness differences show
 * up as cross-screen spread.
 *
 * The fix needs no dylib: oversample currentTime and keep the MAXIMUM of
 * (ct_i - t_i) over a short window. Writing ct_i = t_i + R - s_i (R = the video's
 * true offset from the clock, s_i = staleness in [0, observerPeriod)), that max is
 * R - min(s_i), and a window spanning more than one observer period is guaranteed
 * to contain a just-published sample where s ~ 0. So the max recovers R.
 */
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/ct-fresh.js');
const g = globalThis;

const POLL = 50;          // sampler period
const OBSERVER = 256;     // measured on-wall

/* Drive the estimator over a synthetic zero-order-hold run.
 *   trueOffsetMs : the video's REAL offset from the clock (R)
 *   driftPerSec  : how fast R changes (rate steering), ms per second
 * Returns { rawErr, estErr } = how wrong each reading is at the end. */
function run(trueOffsetMs, driftPerSec, opts) {
  opts = opts || {};
  const durationMs = opts.durationMs || 20000;
  const phase = opts.phase === undefined ? 13 : opts.phase;
  const st = g.mmCtFreshState();
  let raw = 0, est = null, tLast = 0, ctTrueLast = 0;
  for (let t = phase; t < durationMs; t += POLL) {
    const R = trueOffsetMs + driftPerSec * (t / 1000);
    const ctTrue = t + R;
    // Reported currentTime is frozen at the last observer fire.
    const fireT = Math.floor(t / OBSERVER) * OBSERVER;
    const ctReported = fireT + trueOffsetMs + driftPerSec * (fireT / 1000);
    g.mmCtFreshSample(st, ctReported, t);
    raw = ctReported;
    est = g.mmCtFreshEstimate(st, t);
    tLast = t; ctTrueLast = ctTrue;
  }
  return {
    rawErr: raw - ctTrueLast,
    estErr: est === null ? null : est - ctTrueLast,
    state: st
  };
}

test('state starts empty and refuses to estimate', () => {
  const st = g.mmCtFreshState();
  assert.equal(g.mmCtFreshEstimate(st, 0), null);
});

test('refuses until it has seen enough samples to catch a fresh one', () => {
  const st = g.mmCtFreshState();
  g.mmCtFreshSample(st, 0, 0);
  g.mmCtFreshSample(st, 0, 50);
  assert.equal(g.mmCtFreshEstimate(st, 50), null, 'must not guess from two samples');
});

test('THE POINT: corrects the ~128ms zero-order-hold bias', () => {
  const r = run(0, 0);
  // Raw is stale by roughly half the observer period...
  assert.ok(r.rawErr < -60, `raw should read well behind truth, got ${r.rawErr}`);
  // ...the estimate lands close to it.
  assert.ok(Math.abs(r.estErr) < 35, `estErr=${r.estErr}`);
  assert.ok(Math.abs(r.estErr) < Math.abs(r.rawErr) / 3,
    `correction must be a large improvement: raw=${r.rawErr} est=${r.estErr}`);
});

test('works regardless of the video offset it is correcting', () => {
  [-300, -100, 0, 100, 300].forEach(function (R) {
    const r = run(R, 0);
    assert.ok(Math.abs(r.estErr) < 35, `R=${R} estErr=${r.estErr}`);
  });
});

test('tracks a drifting offset while the controller steers', () => {
  // 2% rate error = 20ms of offset change per second; the window must be short
  // enough that the max does not latch a stale peak.
  [-20, 20].forEach(function (driftPerSec) {
    const r = run(0, driftPerSec);
    assert.ok(Math.abs(r.estErr) < 40, `drift=${driftPerSec} estErr=${r.estErr}`);
  });
});

test('a seek discards the window instead of latching a stale maximum', () => {
  const st = g.mmCtFreshState();
  // Steady state around ct = t.
  for (let t = 0; t < 1000; t += POLL) {
    g.mmCtFreshSample(st, Math.floor(t / OBSERVER) * OBSERVER, t);
  }
  // A seek jumps the media timeline far forward.
  g.mmCtFreshSample(st, 30000, 1000);
  assert.equal(g.mmCtFreshEstimate(st, 1000), null,
    'the pre-seek window must be discarded, not reused');
});

test('a loop wrap discards the window', () => {
  const st = g.mmCtFreshState();
  for (let t = 0; t < 1000; t += POLL) {
    g.mmCtFreshSample(st, 29000 + t, t);
  }
  g.mmCtFreshSample(st, 0, 1000);            // wrapped to the start
  assert.equal(g.mmCtFreshEstimate(st, 1000), null);
});

test('refuses to estimate from a window that has gone stale', () => {
  const st = g.mmCtFreshState();
  for (let t = 0; t < 1000; t += POLL) {
    g.mmCtFreshSample(st, Math.floor(t / OBSERVER) * OBSERVER, t);
  }
  assert.ok(g.mmCtFreshEstimate(st, 1000) !== null, 'fresh window should estimate');
  assert.equal(g.mmCtFreshEstimate(st, 20000), null, 'sampler stopped: must refuse');
});

test('window stays bounded over a long run', () => {
  const st = g.mmCtFreshState();
  for (let t = 0; t < 300000; t += POLL) {
    g.mmCtFreshSample(st, Math.floor(t / OBSERVER) * OBSERVER, t);
  }
  assert.ok(st.rel.length <= 25, `window holds ${st.rel.length} samples`);
  assert.equal(st.rel.length, st.t.length, 'parallel arrays drifted apart');
});

test('residual is COMMON-MODE, so it barely moves cross-screen spread', () => {
  /* Screens differ in observer PHASE (and in main-queue delay), which is exactly
   * why raw staleness varies per screen and shows up as wall desync. The
   * corrector's own residual is set by the sampling interval, which is identical
   * everywhere -- so what is left should be nearly the same on every screen. */
  const residuals = [0, 37, 73, 111, 149, 191].map(function (phase) {
    return run(0, 0, { phase: phase }).estErr;
  });
  const spread = Math.max.apply(null, residuals) - Math.min.apply(null, residuals);
  assert.ok(spread < 40, `residual spread across observer phases = ${spread}ms`);
  // Context: the per-screen staleness it REPLACES ranged 45-245ms on the wall.
});
