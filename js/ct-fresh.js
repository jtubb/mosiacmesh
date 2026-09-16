/* js/ct-fresh.js — ES5, NO module syntax (classic <script> for the iPad-1 client
 * AND a side-effect import for node tests). Pure: no DOM, no timers.
 *
 * WHAT IT FIXES (measured on-wall 2026-09-16, 18/18 screens, verdict BIAS)
 * mmvideo publishes currentTime from a ~256ms periodic time observer and caches
 * it with no timestamp, so v.currentTime is a zero-order hold: frozen between
 * fires and stale by s in [0, observerPeriod). Measured fleet median staleness
 * was 142ms. The drift controller reads it raw (index.html:1084), so it acts on
 * a ~142ms PHANTOM lag, over-speeds to chase it, and parks the videos ~40ms
 * AHEAD of the clock. Because each screen's observer phase and main-queue delay
 * differ, so does its staleness -- and that difference IS cross-screen desync.
 *
 * THE ESTIMATOR
 * Write a sample as       ct_i = t_i + R - s_i
 * where R is the video's true offset from the shared clock and s_i is staleness.
 * Then                    max_i(ct_i - t_i) = R - min_i(s_i)
 * and a window spanning more than one observer period is guaranteed to contain a
 * just-published sample, where s ~ 0. So the maximum recovers R, and
 * ct_true(now) ~ now + R. Staleness only ever makes a reading too SMALL, which is
 * what makes the maximum -- rather than an average or a median -- the right
 * filter here.
 *
 * WHY THE LEFTOVER RESIDUAL DOES NOT MATTER
 * What remains is min(s) over the window, bounded by the SAMPLING interval
 * (~15-25ms at 50ms sampling) rather than by the observer period. Crucially it is
 * COMMON-MODE: every screen samples on the same interval, so the residual is
 * nearly identical fleet-wide and a common offset does not desync anything. The
 * 142ms it replaces was per-screen, which is precisely why it did.
 *
 * Needs no dylib change; the deeper fix is to timestamp the observation inside
 * mmvideo and extrapolate (robust to main-queue starvation, which sampling faster
 * is not). This gets most of the win with a fleet RELOAD.
 */
(function (root) {
  // WINDOW_MS is ~3.5 observer periods rather than the bare 1+ the algebra needs.
  // Measured trade-off (256ms observer, 50ms sampling): widening 500 -> 900 tightens
  // the cross-phase residual spread 33 -> 31ms while costing only ~3ms of tracking
  // error at the +/-3.5% rate clamp. The real reason for the margin is that the
  // observer is on the MAIN QUEUE and stretches under load -- the starvation this
  // whole investigation is about -- so a window that only just spans one nominal
  // period could contain no freshly-published sample exactly when the fleet is busy.
  var DEFAULTS = {
    WINDOW_MS: 900,      // >> one observer period, with headroom for a stretched one
    MIN_SAMPLES: 8,      // ~400ms of coverage before trusting a maximum
    JUMP_MS: 400,        // a bigger ct step is a seek or loop wrap, not an observer tick
    MAX_AGE_MS: 400      // sampler stalled -> refuse rather than serve a stale window
  };

  function opt(opts, k) { return (opts && opts[k] !== undefined) ? opts[k] : DEFAULTS[k]; }

  function mmCtFreshState() {
    return { rel: [], t: [], lastCt: null };
  }

  /* One observation of the raw (possibly stale) currentTime. */
  function mmCtFreshSample(state, ctMs, tMs, opts) {
    var WIN = opt(opts, 'WINDOW_MS');
    var JUMP = opt(opts, 'JUMP_MS');
    if (state.lastCt !== null) {
      var dct = ctMs - state.lastCt;
      // A seek or a loop wrap moves the media timeline itself, so every rel in
      // the window now refers to a different mapping. Keeping them would let the
      // max latch a pre-jump peak and report a confidently wrong position.
      if (dct < -JUMP || dct > JUMP) { state.rel = []; state.t = []; }
    }
    state.lastCt = ctMs;
    state.rel.push(ctMs - tMs);
    state.t.push(tMs);
    while (state.t.length && (tMs - state.t[0]) > WIN) {
      state.t.shift();
      state.rel.shift();
    }
    return state;
  }

  /* Best estimate of the TRUE currentTime (ms) at tMs, or null if it cannot be
   * trusted yet -- callers fall back to the raw read. */
  function mmCtFreshEstimate(state, tMs, opts) {
    if (state.rel.length < opt(opts, 'MIN_SAMPLES')) { return null; }
    var last = state.t[state.t.length - 1];
    if ((tMs - last) > opt(opts, 'MAX_AGE_MS')) { return null; }
    var best = state.rel[0], i;
    for (i = 1; i < state.rel.length; i++) {
      if (state.rel[i] > best) { best = state.rel[i]; }
    }
    return tMs + best;
  }

  root.mmCtFreshState = mmCtFreshState;
  root.mmCtFreshSample = mmCtFreshSample;
  root.mmCtFreshEstimate = mmCtFreshEstimate;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
