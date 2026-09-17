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
  const o = { RATE_CLAMP: 0.06, RATE_IGAIN: 0.000002 };
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
