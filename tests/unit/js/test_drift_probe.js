/* Drift probe: does the -125ms steady-state ferr come from P-DROOP or from a
 * STALE SENSOR? (audit 2026-09-15 section 0.2/0.3)
 *
 * Both hypotheses predict the SAME measured error, so measuring harder at the
 * same sampling rate cannot separate them:
 *
 *   controller steady state:  err_measured = -deficit/GAIN = -0.025/0.0002 = -125ms
 *   and                       err_true     = err_measured + s      (s = sensor staleness)
 *
 *   DROOP (s=0):    err_true = -125  -> the video really is 125ms behind
 *   BIAS  (s=125):  err_true =    0  -> the video is correctly positioned and the
 *                                        controller is chasing a phantom
 *
 * They need OPPOSITE fixes: an integral cures droop but is immune to bias (it
 * drives the MEASURED error to zero, parking the TRUE position at exactly s).
 *
 * The probe separates them by oversampling v.currentTime (~20Hz) and reading the
 * error at STAIRCASE STEP EDGES, where staleness is ~0, versus at arbitrary
 * sample times (what the 500ms drift loop sees). Their difference IS s.
 */
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/drift-probe.js');
const g = globalThis;

const POLL = 50;          // probe sampling period (ms)
const OBSERVER = 250;     // mmvideo's addPeriodicTimeObserver interval

/* Feed the probe a synthetic run.
 *   trueErrMs : the video's REAL offset from the shared clock
 *   zoh       : true  -> currentTime is a zero-order hold (mmvideo today)
 *               false -> currentTime is live/continuous
 * Returns the summary. */
function simulate(trueErrMs, zoh, opts) {
  opts = opts || {};
  const durationMs = opts.durationMs || 60000;
  const phase = opts.phase === undefined ? 13 : opts.phase;   // poll vs observer are unaligned
  const st = g.mmProbeState();
  for (let t = phase; t < durationMs; t += POLL) {
    // The video's TRUE media position tracks the clock, offset by trueErrMs.
    const ctTrue = t + trueErrMs;
    // What currentTime actually reports.
    const ctReported = zoh ? (Math.floor(t / OBSERVER) * OBSERVER) + trueErrMs : ctTrue;
    // What the drift loop computes: reported position minus intended position.
    const errMs = ctReported - t;
    g.mmProbeSample(st, ctReported, t, errMs);
  }
  return g.mmProbeSummary(st);
}

test('state starts empty and summary is not ready', () => {
  const st = g.mmProbeState();
  const s = g.mmProbeSummary(st);
  assert.equal(s.ready, false);
  assert.equal(s.nEdges, 0);
  assert.equal(s.nSamples, 0);
});

test('a held value produces no edge; a change produces one', () => {
  const st = g.mmProbeState();
  g.mmProbeSample(st, 1000, 0, -100);
  g.mmProbeSample(st, 1000, 50, -150);     // held -> no edge
  assert.equal(st.edges.length, 0);
  g.mmProbeSample(st, 1250, 100, -100);    // stepped -> edge
  assert.equal(st.edges.length, 1);
  assert.equal(st.nSamples, 3);
});

test('seeks and loop wraps are rejected, not counted as observer steps', () => {
  const st = g.mmProbeState();
  g.mmProbeSample(st, 1000, 0, -100);
  g.mmProbeSample(st, 9000, 50, -100);     // forward seek: too large
  g.mmProbeSample(st, 0, 100, -100);       // loop wrap: negative
  assert.equal(st.edges.length, 0);
  assert.equal(st.rejected, 2);
});

test('verdict: stale sensor with a correctly-positioned video -> BIAS', () => {
  assert.equal(g.mmProbeVerdict(0, -125), 'BIAS');
});

test('verdict: no staleness and a genuinely-behind video -> DROOP', () => {
  assert.equal(g.mmProbeVerdict(-125, -125), 'DROOP');
});

test('verdict: stale sensor AND a genuinely-behind video -> MIXED', () => {
  assert.equal(g.mmProbeVerdict(-125, -250), 'MIXED');
});

test('verdict: AHEAD of the clock with staleness is BIAS, not MIXED', () => {
  /* Droop can only put a video BEHIND, so an overshoot cannot be droop -- it is
   * what a negative measurement bias produces (the controller over-speeds to
   * chase a phantom lag). The first live run read +57..+146ms ahead on many
   * screens with ~130ms staleness; judging on magnitude alone hid that. */
  assert.equal(g.mmProbeVerdict(146, 20), 'BIAS');
  assert.equal(g.mmProbeVerdict(57, -59), 'BIAS');
});

test('SIMULATION: 250ms zero-order hold over a correct video reads as BIAS', () => {
  const s = simulate(0, true);
  assert.equal(s.ready, true);
  assert.equal(s.verdict, 'BIAS');
  // The loop sees a large negative error that is pure measurement lag...
  assert.ok(Math.abs(s.loopErrMs - -125) < 25, `loopErrMs=${s.loopErrMs}`);
  // ...while the video is actually where it should be.
  assert.ok(Math.abs(s.edgeErrMs) < 30, `edgeErrMs=${s.edgeErrMs}`);
  // And the recovered staleness matches half the observer interval.
  assert.ok(Math.abs(s.stalenessMs - 125) < 30, `stalenessMs=${s.stalenessMs}`);
  // The step period identifies the observer itself -- this alone confirms the ZOH.
  assert.ok(Math.abs(s.stepPeriodMs - OBSERVER) < 60, `stepPeriodMs=${s.stepPeriodMs}`);
});

test('SIMULATION: live sensor over a genuinely-behind video reads as DROOP', () => {
  const s = simulate(-125, false);
  assert.equal(s.ready, true);
  assert.equal(s.verdict, 'DROOP');
  assert.ok(Math.abs(s.loopErrMs - -125) < 20, `loopErrMs=${s.loopErrMs}`);
  // With a live sensor the edge reading agrees with the loop: the error is REAL.
  assert.ok(Math.abs(s.edgeErrMs - -125) < 30, `edgeErrMs=${s.edgeErrMs}`);
  assert.ok(Math.abs(s.stalenessMs) < 40, `stalenessMs=${s.stalenessMs}`);
});

test('SIMULATION: ZOH over a genuinely-behind video reads as MIXED', () => {
  const s = simulate(-120, true);
  assert.equal(s.ready, true);
  assert.equal(s.verdict, 'MIXED');
  // The loop over-reads the error by the staleness: -120 true becomes ~-245.
  assert.ok(Math.abs(s.loopErrMs - -245) < 30, `loopErrMs=${s.loopErrMs}`);
  assert.ok(Math.abs(s.edgeErrMs - -120) < 30, `edgeErrMs=${s.edgeErrMs}`);
});

test('SIMULATION: recovered true error tracks the real one across a sweep', () => {
  [-200, -100, 0, 100].forEach(function (trueErr) {
    const s = simulate(trueErr, true);
    assert.ok(Math.abs(s.edgeErrMs - trueErr) < 35,
      `trueErr=${trueErr} recovered=${s.edgeErrMs}`);
  });
});

test('summary withholds a verdict until enough edges are seen', () => {
  const st = g.mmProbeState();
  for (let t = 0; t < 600; t += POLL) {
    g.mmProbeSample(st, Math.floor(t / OBSERVER) * OBSERVER, t, 0);
  }
  const s = g.mmProbeSummary(st);
  assert.equal(s.ready, false, 'must not judge on a handful of edges');
  assert.ok(s.nEdges < 12);
});

test('the two readings are compared over the SAME time window', () => {
  /* ON-WALL BUG (2026-09-16, first live run): both rings were capped by COUNT at
   * 512, but samples arrive every ~52ms and step edges only every ~255ms -- so the
   * loop ring covered ~26s while the edge ring covered ~130s. pStale subtracted
   * two medians taken over different spans and came back NEGATIVE on every screen
   * (-739, -993, -954...), which is physically impossible: a cached sensor cannot
   * report the future. The edge median was still dragged down by the arm
   * transient the loop median had long since forgotten.
   *
   * Here the run starts badly (a transient at -900) and then settles into a clean
   * 250ms ZOH over a correctly-positioned video. Once the transient is outside the
   * window, staleness must read ~+125 -- and must never be negative. */
  const st = g.mmProbeState();
  let t = 0;
  for (; t < 60000; t += POLL) {              // 60s of ugly transient
    g.mmProbeSample(st, (Math.floor(t / OBSERVER) * OBSERVER) - 900, t, -900);
  }
  const settledStart = t;
  for (; t < settledStart + 40000; t += POLL) {   // then 40s settled
    const ctReported = Math.floor(t / OBSERVER) * OBSERVER;
    g.mmProbeSample(st, ctReported, t, ctReported - t);
  }
  const s = g.mmProbeSummary(st);
  assert.ok(s.stalenessMs > 0, `staleness must be positive, got ${s.stalenessMs}`);
  assert.ok(Math.abs(s.stalenessMs - 125) < 40, `stalenessMs=${s.stalenessMs}`);
  assert.ok(Math.abs(s.edgeErrMs) < 40,
    `transient leaked into the edge median: edgeErrMs=${s.edgeErrMs}`);
});

test('both rings span the same window after a long run', () => {
  const st = g.mmProbeState();
  for (let t = 0; t < 300000; t += POLL) {
    g.mmProbeSample(st, Math.floor(t / OBSERVER) * OBSERVER, t, -50);
  }
  // Edges arrive ~5x slower than samples, so an equal-COUNT cap would make the
  // edge window ~5x longer. Equal-time means ~5x fewer edges than samples.
  const ratio = st.loop.length / st.edges.length;
  assert.ok(ratio > 3 && ratio < 8,
    `rings are not equal-time: ${st.loop.length} samples vs ${st.edges.length} edges`);
});

test('ring buffers stay bounded: a 10min run holds no more than a 1min run', () => {
  function sizesAfter(durationMs) {
    const st = g.mmProbeState();
    for (let t = 0; t < durationMs; t += POLL) {
      g.mmProbeSample(st, Math.floor(t / OBSERVER) * OBSERVER, t, -50);
    }
    return { loop: st.loop.length, edges: st.edges.length };
  }
  const short = sizesAfter(60000);
  const long = sizesAfter(600000);
  assert.equal(long.loop, short.loop, 'loop ring grew with run length');
  assert.equal(long.edges, short.edges, 'edge ring grew with run length');
  // Bounded by the 30s window, not by run length: ~30000/50 samples, ~30000/250 edges.
  assert.ok(long.loop <= 700, `loop ring holds ${long.loop}`);
  assert.ok(long.edges <= 200, `edge ring holds ${long.edges}`);
});
