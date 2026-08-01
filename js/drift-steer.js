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
    RATE_GAIN: 0.0002,      // rate delta per ms of filtered err
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
    return { ring: [], inertCount: 0, rateLive: true, lastRateCmd: 1, probeCountdown: 0 };
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

    // fine regime: deadband, then damped rate on FILTERED err
    if (Math.abs(fErr) < DEAD) {
      state.lastRateCmd = 1;
      return { action: 'hold', rate: 1, filteredErr: fErr, rateLive: state.rateLive };
    }
    var rate = 1 - fErr * GAIN;   // err<0 (behind) => rate>1 (speed up)
    if (rate < 1 - CLAMP) { rate = 1 - CLAMP; }
    if (rate > 1 + CLAMP) { rate = 1 + CLAMP; }
    state.lastRateCmd = rate;
    return { action: 'rate', rate: rate, filteredErr: fErr, rateLive: state.rateLive };
  }

  root.mmDriftState = mmDriftState;
  root.mmDriftCommand = mmDriftCommand;
  root._mmDriftMedian = median;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
