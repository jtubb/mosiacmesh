/* js/drift-steer.js — ES5, NO module syntax (classic <script> for the iPad-1
 * client AND a side-effect import for node tests). Pure video drift controller:
 * a median-filtered, gently-damped rate PLL (mmvideo has LIVE playbackRate) with
 * a coarse boundary-seek regime for big errors. Rate-liveness auto-detect is
 * layered in Task 2. No DOM, no timers. */
(function (root) {
  var DEFAULTS = {
    COARSE_SEEK_MS: 350,    // coarse/fine regime boundary
    FINE_DEADBAND_MS: 20,   // below this filtered err, hold rate=1
    RATE_CLAMP: 0.035,      // max fractional rate delta (±3.5%)
    RATE_GAIN: 0.0002,      // PROPORTIONAL: rate delta per ms of filtered err
    // INTEGRAL: rate delta per ms-tick of accumulated err. DEFAULT 0 = pure P,
    // byte-identical for callers that don't opt in.
    //
    // Why it matters: a P-only law parks each screen at droop = deficit/GAIN, and
    // screens have DIFFERENT decode deficits (2-4.4% measured on the fleet), so
    // they settle at DIFFERENT offsets -- and that spread IS the visible desync.
    // After the sensor fix + clamp raise the residual video-offset was 79ms, very
    // close to (spread of deficits)/GAIN. An integral converges each screen to its
    // OWN deficit, so all of them reach err~0 however much they individually lag.
    //
    // This could not work before: with the stale sensor the integral would drive
    // the MEASURED error to zero and park the TRUE position at the staleness, and
    // with the old 3.5% clamp there was no authority left to apply the correction.
    RATE_IGAIN: 0,
    ERR_FILTER_N: 5,        // median filter window
    LIVENESS_EPS: 0.02,     // rateGot-vs-cmd tolerance (Task 2)
    LIVENESS_K: 4,          // consecutive inert ticks -> seek-primary (Task 2)
    PROBE_TICKS: 8,         // inert re-probe cadence (Task 2)
    PROBE_DELTA: 0.03       // probe rate delta (Task 2)
  };

  function opt(opts, k) { return (opts && opts[k] !== undefined) ? opts[k] : DEFAULTS[k]; }

  function median(arr) {
    var a = arr.slice().sort(function (x, y) { return x - y; });
    var n = a.length;
    if (n === 0) { return 0; }
    var mid = Math.floor(n / 2);
    return (n % 2) ? a[mid] : (a[mid - 1] + a[mid]) / 2;
  }

  function mmDriftState() {
    return { ring: [], inertCount: 0, rateLive: true, lastRateCmd: 1, probeCountdown: 0,
             integral: 0 };
  }

  function mmDriftCommand(state, err, obs, opts) {
    var COARSE = opt(opts, 'COARSE_SEEK_MS');
    var DEAD = opt(opts, 'FINE_DEADBAND_MS');
    var CLAMP = opt(opts, 'RATE_CLAMP');
    var GAIN = opt(opts, 'RATE_GAIN');
    var N = opt(opts, 'ERR_FILTER_N');
    var canSeek = !!(obs && obs.canSeek);

    var EPS = opt(opts, 'LIVENESS_EPS');
    var K = opt(opts, 'LIVENESS_K');
    var rateGot = (obs && typeof obs.rateGot === 'number') ? obs.rateGot : 1;

    // liveness: did LAST tick's non-unity rate command take effect this tick?
    if (state.lastRateCmd !== 1) {
      if (Math.abs(rateGot - state.lastRateCmd) > EPS) {
        state.inertCount += 1;
        if (state.inertCount >= K) { state.rateLive = false; }
      } else {
        state.inertCount = 0;
        state.rateLive = true;
      }
    }

    // median filter
    state.ring.push(err);
    while (state.ring.length > N) { state.ring.shift(); }
    var fErr = median(state.ring);

    // coarse regime keys on FILTERED err (median rejects the single-tick bimodal
    // currentTime readback spike that otherwise fires a spurious seek; a genuine
    // persistent jump still crosses COARSE within a few ticks, and a fresh ring's
    // median equals the lone sample so real startup jumps still react fast).
    if (Math.abs(fErr) > COARSE && canSeek) {
      state.lastRateCmd = 1;   // rate resets to 1 after a seek
      return { action: 'seek', rate: 1, filteredErr: fErr, rateLive: state.rateLive };
    }

    // rate inert (stock Safari pins playbackRate to 1): can't fine-steer via rate.
    // Hold, but emit a periodic probe so recovery is observable.
    if (!state.rateLive) {
      state.probeCountdown -= 1;
      if (state.probeCountdown <= 0) {
        state.probeCountdown = opt(opts, 'PROBE_TICKS');
        var probe = 1 + opt(opts, 'PROBE_DELTA');
        state.lastRateCmd = probe;
        return { action: 'rate', rate: probe, filteredErr: fErr, rateLive: false };
      }
      state.lastRateCmd = 1;
      return { action: 'hold', rate: 1, filteredErr: fErr, rateLive: false };
    }

    // Fine regime: PI. P reacts to the error; I accumulates it to cancel this
    // screen's steady decode deficit, so screens with DIFFERENT deficits all
    // converge to err~0 instead of parking at different P-droop offsets.
    var IGAIN = opt(opts, 'RATE_IGAIN');
    var iMax = (IGAIN > 0) ? (CLAMP / IGAIN) : 0;   // bound the integral's authority to +/-CLAMP
    // The DEADBAND applies to the PROPORTIONAL term only. Inside it the integral
    // HOLDS its accumulated value rather than snapping the rate back to 1 -- that
    // held rate IS the deficit cancellation, and dropping it would make the screen
    // immediately start drifting again.
    var pErr = (Math.abs(fErr) < DEAD) ? 0 : fErr;
    // CLAMPING ANTI-WINDUP: accumulate only while the output is NOT saturated. A
    // big transient pins the P term at the clamp; integrating there would wind up
    // and overshoot. So the integral only trims the STEADY residual once P has
    // pulled the error into the linear range.
    var unclamped = 1 - pErr * GAIN - state.integral * IGAIN;
    var saturated = (unclamped > 1 + CLAMP) || (unclamped < 1 - CLAMP);
    if (IGAIN > 0 && Math.abs(fErr) >= DEAD && !saturated) {
      state.integral += fErr;
      if (state.integral > iMax) { state.integral = iMax; }
      if (state.integral < -iMax) { state.integral = -iMax; }
    }
    var rate = 1 - pErr * GAIN - state.integral * IGAIN;   // err<0 (behind) => rate>1
    if (rate < 1 - CLAMP) { rate = 1 - CLAMP; }
    if (rate > 1 + CLAMP) { rate = 1 + CLAMP; }
    state.lastRateCmd = rate;
    var act = (Math.abs(rate - 1) < 1e-9) ? 'hold' : 'rate';
    return { action: act, rate: rate, filteredErr: fErr, rateLive: state.rateLive };
  }

  root.mmDriftState = mmDriftState;
  root.mmDriftCommand = mmDriftCommand;
  root._mmDriftMedian = median;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
