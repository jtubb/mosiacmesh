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
