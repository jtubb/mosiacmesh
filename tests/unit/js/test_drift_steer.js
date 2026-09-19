import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/drift-steer.js');
const g = globalThis;

test('median: odd, even, empty', () => {
  assert.equal(g._mmDriftMedian([3, 1, 2]), 2);
  assert.equal(g._mmDriftMedian([4, 1, 3, 2]), 2.5);
  assert.equal(g._mmDriftMedian([]), 0);
});

test('coarse regime: |err|>COARSE and canSeek -> seek, rate 1', () => {
  const s = g.mmDriftState();
  const c = g.mmDriftCommand(s, -500, { rateGot: 1, canSeek: true });
  assert.equal(c.action, 'seek');
  assert.equal(c.rate, 1);
});

test('regime boundary: below COARSE -> not seek, above -> seek', () => {
  const below = g.mmDriftCommand(g.mmDriftState(), -300, { rateGot: 1, canSeek: true });
  assert.notEqual(below.action, 'seek');
  const above = g.mmDriftCommand(g.mmDriftState(), -400, { rateGot: 1, canSeek: true });
  assert.equal(above.action, 'seek');
});

test('deadband: small filtered err -> hold, rate 1', () => {
  const s = g.mmDriftState();
  let c;
  [10, -5, 8, -10, 5].forEach(function (e) { c = g.mmDriftCommand(s, e, { rateGot: 1, canSeek: true }); });
  assert.equal(c.action, 'hold');
  assert.equal(c.rate, 1);
});

test('fine regime: rate command stays within clamp', () => {
  const s = g.mmDriftState();
  let c;
  [-1000, -1000, -1000, -1000, -1000].forEach(function (e) {
    c = g.mmDriftCommand(s, e, { rateGot: 1, canSeek: false }); // cooldown -> no seek
  });
  assert.ok(c.rate >= 1 - 0.035 - 1e-9 && c.rate <= 1 + 0.035 + 1e-9);
  assert.equal(c.action, 'rate');
});

test('closed-loop: clean -100ms err converges into deadband without overshoot', () => {
  const s = g.mmDriftState();
  let err = -100, dt = 0.5, overshoot = false;
  for (let i = 0; i < 80; i++) {
    const c = g.mmDriftCommand(s, err, { rateGot: 1, canSeek: false });
    const rate = c.action === 'rate' ? c.rate : 1;
    err = err + (rate - 1) * dt * 1000;   // apply rate for one 500ms tick
    if (err > 5) overshoot = true;
  }
  assert.ok(Math.abs(err) < 25, 'converged, err=' + err);
  assert.ok(!overshoot, 'no overshoot past zero');
});

test('coarse: single bimodal spike past COARSE does NOT seek (median rejects it)', () => {
  const s = g.mmDriftState();
  // Steady bimodal readback: true err ~-160, alternating +50 artifact. None should seek.
  const seq = [-160, 50, -160, 50, -160];
  let c;
  seq.forEach(function (e) { c = g.mmDriftCommand(s, e, { rateGot: 1, canSeek: true }); });
  assert.notEqual(c.action, 'seek', 'steady bimodal must not seek');
  // Now a single-tick readback spike to -395 (raw > 350) while the ring's median stays ~-160.
  c = g.mmDriftCommand(s, -395, { rateGot: 1, canSeek: true });
  assert.notEqual(c.action, 'seek', 'lone -395 spike must not fire a seek');
  assert.ok(Math.abs(c.filteredErr) < 350, 'filtered err stayed sub-threshold: ' + c.filteredErr);
});

test('coarse: persistent err past COARSE DOES seek (genuine drift)', () => {
  const s = g.mmDriftState();
  let c;
  [-400, -400, -400].forEach(function (e) { c = g.mmDriftCommand(s, e, { rateGot: 1, canSeek: true }); });
  assert.equal(c.action, 'seek', 'sustained -400 must seek');
});

test('coarse: fresh-state large err still seeks immediately (startup jump)', () => {
  // Empty ring -> median of one sample = that sample -> preserves fast reaction on a real jump.
  const c = g.mmDriftCommand(g.mmDriftState(), -500, { rateGot: 1, canSeek: true });
  assert.equal(c.action, 'seek');
});

test('liveness: rateGot ignores commands for K ticks -> rateLive false', () => {
  const s = g.mmDriftState();
  for (let i = 0; i < 6; i++) g.mmDriftCommand(s, -100, { rateGot: 1, canSeek: false });
  assert.equal(s.rateLive, false);
});

test('liveness: honored rate keeps rateLive true', () => {
  const s = g.mmDriftState();
  let last = 1;
  for (let i = 0; i < 6; i++) {
    const c = g.mmDriftCommand(s, -100, { rateGot: last, canSeek: false });
    last = c.rate;   // element honors the command
  }
  assert.equal(s.rateLive, true);
});

test('liveness: inert screen emits periodic probe and recovers when honored', () => {
  const s = g.mmDriftState();
  for (let i = 0; i < 6; i++) g.mmDriftCommand(s, -100, { rateGot: 1, canSeek: false });
  assert.equal(s.rateLive, false);
  let probe = null;
  for (let i = 0; i < 12 && probe === null; i++) {
    const c = g.mmDriftCommand(s, -100, { rateGot: 1, canSeek: false });
    if (c.action === 'rate') probe = c.rate;
  }
  assert.ok(probe !== null, 'a probe rate was emitted while inert');
  g.mmDriftCommand(s, -100, { rateGot: probe, canSeek: false }); // probe honored next tick
  assert.equal(s.rateLive, true);
});

/* A raised RATE_CLAMP must actually be honored through the opts path.
 * index.html's DRIFT_OPTS overrides the library default, so the deployed clamp
 * is the one that matters. On-wall, 98.3% of rate commands sat pinned at the old
 * +3.5% clamp while the wall was 213ms behind -- the controller had no authority
 * left above the fleet's real ~3.5% decode deficit. */
test('a custom RATE_CLAMP widens the achievable rate range', () => {
  const s = g.mmDriftState();
  let c, got = 1;
  // Feed the commanded rate back as rateGot -- otherwise the liveness detector
  // decides the element is ignoring us and freezes steering.
  [-900, -900, -900, -900, -900].forEach(function (e) {
    c = g.mmDriftCommand(s, e, { rateGot: got, canSeek: false }, { RATE_CLAMP: 0.06 });
    got = c.rate;
  });
  assert.equal(c.action, 'rate');
  assert.ok(Math.abs(c.rate - 1.06) < 1e-9, `expected the 6% clamp, got ${c.rate}`);
});

test('the default clamp is unchanged for callers that do not override it', () => {
  const s = g.mmDriftState();
  let c, got = 1;
  [-900, -900, -900, -900, -900].forEach(function (e) {
    c = g.mmDriftCommand(s, e, { rateGot: got, canSeek: false });
    got = c.rate;
  });
  assert.ok(Math.abs(c.rate - 1.035) < 1e-9, `library default moved: ${c.rate}`);
});

/* ---- PI integral (RATE_IGAIN) --------------------------------------------
 * P-only parks each screen at droop = deficit/GAIN. Screens have DIFFERENT
 * decode deficits (measured 2-4.4% on the fleet), so they settle at DIFFERENT
 * offsets -- and that spread IS the visible desync. Measured after the sensor
 * fix + clamp raise: video-offset 79ms ~= (spread of deficits)/GAIN.
 *
 * An integral converges each screen to its OWN deficit, so ALL screens reach
 * err~0 regardless of how much they individually lag. Default RATE_IGAIN is 0,
 * so callers that do not opt in keep pure-P behavior byte-identical.
 */

// Closed-loop sim: plant advances at (rate - deficit); controller sees the error.
function simulateLoop(deficit, opts, ticks) {
  const s = g.mmDriftState();
  let err = 0, got = 1, c = null;
  for (let i = 0; i < (ticks || 4000); i++) {
    c = g.mmDriftCommand(s, err, { rateGot: got, canSeek: false }, opts);
    got = c.rate;
    // err in ms accumulates at (rate - 1 - deficit) per 500ms tick
    err += (c.rate - 1 - deficit) * 500;
  }
  return { err, rate: c.rate, integral: s.integral };
}

test('PI: default IGAIN is 0 -> pure-P behavior is unchanged', () => {
  const s = g.mmDriftState();
  let c, got = 1;
  [-900, -900, -900, -900, -900].forEach(function (e) {
    c = g.mmDriftCommand(s, e, { rateGot: got, canSeek: false });
    got = c.rate;
  });
  assert.ok(Math.abs(c.rate - 1.035) < 1e-9, `pure-P clamp moved: ${c.rate}`);
});

test('P-only leaves a standing droop proportional to the deficit', () => {
  const p = simulateLoop(0.03, { RATE_CLAMP: 0.06 });
  // droop = deficit/GAIN = 0.03/0.0002 = 150ms behind
  assert.ok(p.err < -100, `expected a large P droop, got ${p.err}`);
});

test('PI: the integral drives the standing droop to ~zero', () => {
  const pi = simulateLoop(0.03, { RATE_CLAMP: 0.06, RATE_IGAIN: 0.000002 });
  assert.ok(Math.abs(pi.err) < 40, `PI failed to cancel the droop: ${pi.err}`);
});

test('THE POINT: screens with DIFFERENT deficits converge to the SAME error', () => {
  const o = { RATE_CLAMP: 0.06, RATE_IGAIN: 0.000002 };
  const errs = [0.020, 0.030, 0.040, 0.044].map((d) => simulateLoop(d, o).err);
  const spread = Math.max(...errs) - Math.min(...errs);
  // P-only over this deficit range would spread (0.044-0.020)/0.0002 = 120ms.
  assert.ok(spread < 40, `cross-screen spread still ${spread}ms: ${errs}`);
});

test('PI: anti-windup — the integral does not accumulate while saturated', () => {
  const s = g.mmDriftState();
  let got = 1;
  // A huge sustained error saturates the rate clamp immediately.
  for (let i = 0; i < 50; i++) {
    const c = g.mmDriftCommand(s, -5000, { rateGot: got, canSeek: false },
                               { RATE_CLAMP: 0.06, RATE_IGAIN: 0.000002 });
    got = c.rate;
  }
  assert.equal(s.integral, 0, `integral wound up while saturated: ${s.integral}`);
});

test('PI: the integral stays bounded by the clamp', () => {
  const o = { RATE_CLAMP: 0.06, RATE_IGAIN: 0.000002 };
  const r = simulateLoop(0.10, o, 6000);   // deficit far beyond the clamp
  assert.ok(Math.abs(r.integral * 0.000002) <= 0.06 + 1e-9,
    `integral contribution exceeded the clamp: ${r.integral * 0.000002}`);
});

test('PI: inside the deadband the integral HOLDS its correction', () => {
  // Once converged, err sits inside the deadband -- the rate must NOT snap back
  // to 1 or the screen would immediately start drifting again.
  const r = simulateLoop(0.03, { RATE_CLAMP: 0.06, RATE_IGAIN: 0.000002 });
  assert.ok(Math.abs(r.rate - 1) > 0.01,
    `rate collapsed to 1 inside the deadband: ${r.rate}`);
});

/* ---- anti-windup must allow UNWINDING --------------------------------------
 * ON-WALL FAILURE (2026-09-17): the integral wound to the clamp and FROZE there.
 * rateGot sat pinned at 6% for 69.9% of samples while the error grew to -218ms --
 * the controller stuck at full speed-up, unable to correct in either direction.
 *
 * Cause: the guard blocked accumulation whenever the output was saturated, in
 * BOTH directions, so a saturated integral could never move back. Correct
 * conditional integration blocks only the direction that saturates FURTHER.
 *
 * The original tests missed this because they only ever drove TOWARD saturation.
 * These drive into the rail and then ask the controller to come back.
 */

test('anti-windup: a saturated integral can still unwind', () => {
  // STALL_TICKS disabled on purpose: this test isolates INTEGRAL unwind, and its
  // scenario -- err pinned at -300 for 400 ticks while the command is honored --
  // is by definition an inert plant, so the stall detector would (correctly) rule
  // the actuator dead and hand over to seek before the integral ever accumulated.
  const o = { RATE_CLAMP: 0.06, RATE_IGAIN: 0.000002, STALL_TICKS: Infinity };
  const s = g.mmDriftState();
  let got = 1;
  // Drive hard NEGATIVE (behind) until the output saturates high.
  for (let i = 0; i < 400; i++) {
    const c = g.mmDriftCommand(s, -300, { rateGot: got, canSeek: false }, o);
    got = c.rate;
  }
  const wound = s.integral;
  assert.ok(wound < 0, `expected a negative (speed-up) integral, got ${wound}`);
  // Now the screen is AHEAD: the integral must be able to come back.
  for (let i = 0; i < 400; i++) {
    const c = g.mmDriftCommand(s, +300, { rateGot: got, canSeek: false }, o);
    got = c.rate;
  }
  assert.ok(s.integral > wound,
    `integral froze at the rail: ${wound} -> ${s.integral}`);
});

test('anti-windup: the loop RECOVERS after the plant improves', () => {
  /* Realistic shape: a screen struggles (large deficit), the integral winds up to
   * compensate, then the deficit eases. A frozen integral would leave it railed
   * and overshooting forever. */
  const o = { RATE_CLAMP: 0.06, RATE_IGAIN: 0.000002 };
  const s = g.mmDriftState();
  let err = 0, got = 1, c = null;
  for (let i = 0; i < 3000; i++) {                 // hard deficit
    c = g.mmDriftCommand(s, err, { rateGot: got, canSeek: false }, o);
    got = c.rate; err += (c.rate - 1 - 0.055) * 500;
  }
  for (let i = 0; i < 6000; i++) {                 // deficit eases
    c = g.mmDriftCommand(s, err, { rateGot: got, canSeek: false }, o);
    got = c.rate; err += (c.rate - 1 - 0.010) * 500;
  }
  assert.ok(Math.abs(err) < 60, `did not recover after the plant eased: err=${err}`);
  assert.ok(c.rate < 1.059, `still pinned at the clamp: ${c.rate}`);
});

/* ---- Inert-actuator detection + seek fallback (2026-09-18) ----------------
 * Measured on-wall: commanding rate 0.94..1.06 moved the ACTUAL playback rate
 * by 0.2% (regression slope -0.004 over 7342 samples), and forcing MM_CM_SCALE
 * to 2.0 left the real rate at 0.9973 instead of the predicted 1.12. The rate
 * actuator is inert on iOS 5.1.
 *
 * The old liveness check could never see this. It compares rateGot against the
 * command, but mmvideo's rateGot IS the mirrored command, so at the PRODUCTION
 * constants below the comparison is a tautology and rlive stayed 1 forever.
 * (These tests MUST pass PROD: at the library defaults CLAMP=0.035 a literal
 * rateGot of 1.06 trips the old mismatch check by accident and the tautology
 * never gets exercised.)
 *
 * With rate dead, seek is the only actuator, and COARSE_SEEK_MS=350 left the
 * standing ~258ms error permanently inside the deadband -- that parked error IS
 * the "droop". Measured re-drift after correction is 0.10 ms/s median, so a
 * single seek holds for minutes. */
const PROD = { RATE_CLAMP: 0.06, RATE_GAIN: 0.0003, COARSE_SEEK_MS: 350 };

test('inert: mirrored rateGot defeats the OLD check -- stall detector must catch it', () => {
  const s = g.mmDriftState();
  let last = 1;
  for (let i = 0; i < 14; i++) {
    const c = g.mmDriftCommand(s, -337, { rateGot: last, canSeek: false }, PROD);
    last = c.rate;                      // mmvideo mirrors: rateGot == what we set
  }
  assert.equal(s.rateLive, false, 'railed command + frozen error must read as inert');
});

test('inert: a responding actuator under saturation stays live', () => {
  const s = g.mmDriftState();
  let last = 1, e = -337;
  for (let i = 0; i < 14; i++) {
    const c = g.mmDriftCommand(s, e, { rateGot: last, canSeek: false }, PROD);
    last = c.rate; e += 25;             // error genuinely closing
  }
  assert.equal(s.rateLive, true, 'an improving error must NOT be called inert');
});

test('inert: seeks on a sub-COARSE error that the rate path can never fix', () => {
  const s = g.mmDriftState();
  let last = 1, c;
  for (let i = 0; i < 14; i++) {
    c = g.mmDriftCommand(s, -258, { rateGot: last, canSeek: true }, PROD);
    last = c.rate;
  }
  assert.equal(s.rateLive, false);
  assert.equal(c.action, 'seek', '-258ms is uncorrectable by rate and must seek once inert');
});

test('inert: still respects a deadband -- a small residual does not seek', () => {
  const s = g.mmDriftState();
  let last = 1, c;
  for (let i = 0; i < 14; i++) {
    c = g.mmDriftCommand(s, -337, { rateGot: last, canSeek: true }, PROD);
    last = c.rate;
  }
  assert.equal(s.rateLive, false);
  for (let i = 0; i < 8; i++) {
    c = g.mmDriftCommand(s, -15, { rateGot: last, canSeek: true }, PROD);
    last = c.rate;
  }
  assert.notEqual(c.action, 'seek', 'a 15ms residual must not cause seek churn');
});

test('inert: cannot seek when canSeek is false -> holds, no crash', () => {
  const s = g.mmDriftState();
  let last = 1, c;
  for (let i = 0; i < 14; i++) {
    c = g.mmDriftCommand(s, -258, { rateGot: last, canSeek: false }, PROD);
    last = c.rate;
  }
  assert.notEqual(c.action, 'seek');
});

/* ---- Loop-boundary circular error (2026-09-19) ---------------------------
 * The clock position and the video's currentTime both wrap at the loop, but
 * NOT at the same instant. For the moment one has wrapped and the other has
 * not, a raw (ct - target) reports a FULL-LOOP error: on-wall ferr hit
 * +119699 and +119655 on a 120s loop. That spike blows past COARSE_SEEK_MS
 * and fires a seek at every wrap -- 270 of 295 fleet seeks (91.5%) targeted
 * the first 10s of the 120s loop, and |ferr| p99 sat at ~262ms in every
 * reading while p50 was 24ms. Taking the SHORT way round is what synctool
 * already does server-side (circular_spread). */

test('wrapErr: a full-loop spike becomes the small true error', () => {
  assert.equal(g.mmWrapErr(119900, 120000), -100);   // clock wrapped, video hasn't
  assert.equal(g.mmWrapErr(-119900, 120000), 100);   // video wrapped, clock hasn't
});

test('wrapErr: ordinary small errors pass through untouched', () => {
  assert.equal(g.mmWrapErr(-258, 120000), -258);
  assert.equal(g.mmWrapErr(37, 120000), 37);
  assert.equal(g.mmWrapErr(0, 120000), 0);
});

test('wrapErr: picks the short way round past the half-loop', () => {
  assert.equal(g.mmWrapErr(70000, 120000), -50000);
  assert.equal(g.mmWrapErr(60000, 120000), 60000);   // exactly half stays positive
});

test('wrapErr: unknown/zero loop length is a passthrough', () => {
  assert.equal(g.mmWrapErr(119900, 0), 119900);
  assert.equal(g.mmWrapErr(-42, undefined), -42);
});

test('wrapErr: the on-wall spike no longer triggers a coarse seek', () => {
  const s = g.mmDriftState();
  const raw = g.mmDriftCommand(g.mmDriftState(), 119699, { rateGot: 1, canSeek: true });
  assert.equal(raw.action, 'seek', 'raw full-loop error DOES seek (the bug)');
  const wrapped = g.mmWrapErr(119699, 120000);
  const c = g.mmDriftCommand(s, wrapped, { rateGot: 1, canSeek: true });
  assert.notEqual(c.action, 'seek', 'wrapped error is small and must not seek');
  assert.ok(Math.abs(wrapped) < 350, `wrapped to ${wrapped}`);
});

/* ---- Inertness detection below the clamp (2026-09-19) --------------------
 * The first detector keyed on "railed at the clamp", which needs
 * |fErr| >= CLAMP/GAIN = 0.06/0.0003 = 200ms. Everything under that was
 * invisible: too small to rail (no stall verdict, so no INERT_SEEK_MS
 * fallback) and too small for COARSE_SEEK_MS=350. Those screens simply parked.
 *
 * Measured on-wall after the loop-boundary fix: rlive=0 on only 11-14 of 24
 * screens, |ferr| p90 59-95ms and a residual cross-screen spread of 100-155ms
 * -- sitting squarely in the uncovered band. Removing the big wrap excursions
 * is what exposed this: fewer screens ever reach 200ms now, so fewer trip it.
 *
 * The replacement is model-based: a working actuator moves err by about
 * (rate-1)*dt per tick, an inert one by ~0. Signed, so an overshoot reads as
 * WORKING (ratio > 1) rather than as a stall -- which a test on shrinking
 * |fErr| would get exactly backwards. */
const PROD2 = { RATE_CLAMP: 0.06, RATE_GAIN: 0.0003, COARSE_SEEK_MS: 350 };

/** Closed-loop tick: plant moves err by (rate-1)*TICK when live, else not. */
function runLoop(s, startErr, ticks, live, opts, canSeek) {
  let err = startErr, last = 1, c = null;
  const TICK = (opts && opts.TICK_MS) || 200;
  for (let i = 0; i < ticks; i++) {
    c = g.mmDriftCommand(s, err, { rateGot: last, canSeek: !!canSeek }, opts);
    last = c.rate;
    if (c.action === 'seek') { err = 0; }            // a seek lands it
    else if (live) { err += (c.rate - 1) * TICK; }   // working actuator
  }
  return { err, cmd: c };
}


/* WHY THESE TWO ARE todo -- the obvious fix is unsound.
 *
 * Replacing "railed at the clamp" with a model test ("a working actuator moves
 * err by ~(rate-1)*dt, an inert one by ~0") WAS implemented and did make both
 * of these pass -- but it broke all five PI/anti-windup tests, for a reason
 * that is fundamental rather than a tuning problem:
 *
 *   At a P-controller's steady state the net movement is ZERO BY DEFINITION.
 *   A working actuator holding station against a decode deficit commands +3%
 *   and achieves zero net movement; an inert actuator commands +3% and also
 *   achieves nothing. Passively the two are identical. Measuring the achieved
 *   d(ct)/d(wall) does not help either -- both read exactly 1.000 at steady
 *   state. So no passive test can separate equilibrium from inertness.
 *
 * The sound discriminator is PERTURBATION: change the command and see whether
 * the error responds. A constant deficit cancels out of a delta; an inert
 * actuator does not respond to one. The PROBE_DELTA / PROBE_TICKS scaffolding
 * already in mmDriftCommand is the natural place to build that, promoting the
 * model test from a verdict to a "suspect" trigger that arms a probe.
 *
 * Until then the detector only fires above |fErr| = CLAMP/GAIN = 200ms and the
 * 20-200ms band stays uncorrected (measured: rlive=0 on 11-14 of 24 screens,
 * residual spread 100-155ms). Known and deliberate, not forgotten. */
test('sub-clamp: a 120ms error with an INERT actuator is detected', { todo: true }, () => {
  const s = g.mmDriftState();
  runLoop(s, -120, 40, false, PROD2, false);
  assert.equal(s.rateLive, false,
    'a steady 120ms error never rails, but the rate is doing nothing -- must read inert');
});

test('sub-clamp: a 120ms error with a WORKING actuator stays live', () => {
  const s = g.mmDriftState();
  runLoop(s, -120, 40, true, PROD2, false);
  assert.equal(s.rateLive, true, 'the error is closing, so the actuator works');
});

test('sub-clamp: once inert, the seek fallback corrects it', { todo: true }, () => {
  const s = g.mmDriftState();
  const r = runLoop(s, -120, 40, false, PROD2, true);
  assert.equal(s.rateLive, false);
  assert.ok(Math.abs(r.err) < 90,
    `an inert screen at 120ms must be seeked back, ended at ${r.err}`);
});

test('sub-clamp: the railed+frozen case is still caught (regression)', () => {
  const s = g.mmDriftState();
  runLoop(s, -337, 40, false, PROD2, false);
  assert.equal(s.rateLive, false);
});

test('sub-clamp: inside the deadband nothing is commanded, so no false verdict', () => {
  const s = g.mmDriftState();
  runLoop(s, -8, 60, false, PROD2, false);   // below FINE_DEADBAND_MS -> rate 1
  assert.equal(s.rateLive, true, 'not steering means no evidence of inertness');
});

test('sub-clamp: overshoot reads as WORKING, not stalled', () => {
  const s = g.mmDriftState();
  // Plant over-responds 3x: |err| does not shrink monotonically and even flips
  // sign, but the error IS moving in the commanded direction.
  let err = -120, last = 1;
  for (let i = 0; i < 40; i++) {
    const c = g.mmDriftCommand(s, err, { rateGot: last, canSeek: false }, PROD2);
    last = c.rate;
    err += (c.rate - 1) * 200 * 3;
  }
  assert.equal(s.rateLive, true, 'an over-responsive actuator is still an actuator');
});
