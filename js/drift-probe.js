/* js/drift-probe.js — ES5, NO module syntax (classic <script> for the iPad-1
 * client AND a side-effect import for node tests). Pure: no DOM, no timers.
 *
 * WHAT IT ANSWERS
 * The wall's steady-state drift error sits at -77..-206ms, always behind. Two
 * mechanisms predict that SAME number and need OPPOSITE fixes:
 *
 *   controller steady state:  err_measured = -deficit/GAIN = -0.025/0.0002 = -125ms
 *   relationship:             err_true     = err_measured + s     (s = sensor staleness)
 *
 *   DROOP (s=0)    -> err_true = -125 : the video really is 125ms behind.
 *                     An integral fixes this.
 *   BIAS  (s=125)  -> err_true =    0 : the video is correctly positioned and the
 *                     controller is chasing a phantom. An integral CANNOT fix
 *                     this -- it drives the MEASURED error to zero, which parks
 *                     the TRUE position at exactly s.
 *
 * Sampling the same signal faster cannot separate them. What separates them is
 * WHERE in the staircase you read.
 *
 * HOW
 * mmvideo publishes currentTime from a 250ms periodic time observer and caches
 * it with no timestamp (MMTransplantEngine.m:160/164/172/287), so v.currentTime
 * is a zero-order hold: frozen between observer fires, stale by s in [0,250).
 * Oversampling at ~20Hz exposes the staircase. At a STEP EDGE the value was just
 * published, so staleness is ~0 and the error read there is err_true. Read at an
 * arbitrary moment -- what the 500ms drift loop does -- it is err_measured.
 * Their difference IS s.
 *
 * The step period is a free bonus: if currentTime really is a 250ms ZOH the
 * measured step period is ~250ms, which confirms or kills the premise outright.
 */
(function (root) {
  var DEFAULTS = {
    STEP_MAX_MS: 400,          // above this a "step" is a seek, not an observer tick
    WINDOW_MS: 30000,          // both readings are medianed over THIS SAME span
    RING_CAP: 2048,            // memory backstop only; WINDOW_MS does the pruning
    MIN_EDGES: 12,             // withhold a verdict until the sample is meaningful
    VERDICT_STALENESS_MS: 40,  // staleness below this -> the sensor is not the problem
    VERDICT_TRUE_ERR_MS: 50,   // |err_true| below this -> the video is actually fine
    LIVE_SENSOR_RATIO: 1.5     // stepPeriod < ratio*pollGap -> sensor updates every sample
  };

  function opt(opts, k) { return (opts && opts[k] !== undefined) ? opts[k] : DEFAULTS[k]; }

  function median(arr) {
    var a = arr.slice().sort(function (x, y) { return x - y; });
    var n = a.length;
    if (n === 0) { return 0; }
    var mid = Math.floor(n / 2);
    return (n % 2) ? a[mid] : (a[mid - 1] + a[mid]) / 2;
  }

  /* Rings are pruned by TIME, not by count.
   *
   * ON-WALL BUG (2026-09-16, first live run): with both rings capped at 512
   * entries the loop ring covered ~26s (samples every ~52ms) while the edge ring
   * covered ~130s (edges every ~255ms). Their medians were taken over different
   * spans, so pStale -- the whole point of the probe -- subtracted a recent
   * median from a stale one and came back NEGATIVE on every screen, which a
   * cached sensor cannot physically produce. The edge median was still carrying
   * the arm transient the loop median had long since forgotten.
   *
   * The two readings are only comparable over the SAME window, so that is what
   * is enforced; the count cap survives purely as a memory backstop. */
  function push(ring, times, v, tMs, cap, windowMs) {
    ring.push(v);
    times.push(tMs);
    while (times.length && (tMs - times[0]) > windowMs) { times.shift(); ring.shift(); }
    while (ring.length > cap) { ring.shift(); times.shift(); }
  }

  function mmProbeState() {
    return {
      lastCt: null, lastT: null, lastEdgeT: null,
      edges: [],  edgeT: [],   // err read AT a step edge  (~ err_true)
      loop: [],   loopT: [],   // err read at every sample (~ err_measured)
      steps: [],  stepT: [],   // interval between step edges (~ observer period)
      gaps: [],   gapT: [],    // sampling interval at an edge (detection-lag scale)
      nSamples: 0, rejected: 0
    };
  }

  /* One observation. errMs is the drift loop's own error definition
   * (currentTime*1000 - intended offset), passed in so this stays free of any
   * playlist/clock dependency. */
  function mmProbeSample(state, ctMs, tMs, errMs, opts) {
    var CAP = opt(opts, 'RING_CAP');
    var WIN = opt(opts, 'WINDOW_MS');
    var STEP_MAX = opt(opts, 'STEP_MAX_MS');
    state.nSamples += 1;
    push(state.loop, state.loopT, errMs, tMs, CAP, WIN);
    if (state.lastCt !== null && ctMs !== state.lastCt) {
      var dct = ctMs - state.lastCt;
      if (dct > 0 && dct < STEP_MAX) {
        push(state.edges, state.edgeT, errMs, tMs, CAP, WIN);
        if (state.lastT !== null) {
          push(state.gaps, state.gapT, tMs - state.lastT, tMs, CAP, WIN);
        }
        if (state.lastEdgeT !== null) {
          push(state.steps, state.stepT, tMs - state.lastEdgeT, tMs, CAP, WIN);
        }
        state.lastEdgeT = tMs;
      } else {
        // A seek or a loop wrap, not an observer tick. Drop it, and forget the
        // previous edge so the next step interval isn't measured across the jump.
        state.rejected += 1;
        state.lastEdgeT = null;
      }
    }
    state.lastCt = ctMs;
    state.lastT = tMs;
    return state;
  }

  /* Which mechanism the two readings imply. Pure so it is testable on its own.
   *
   * The SIGN of err_true matters. Droop can only put a video BEHIND -- it is the
   * standing lag needed to sustain a rate correction against a decode deficit --
   * so a video measured AHEAD of the clock cannot be drooping, however large the
   * error. An overshoot is what a negative measurement bias produces: the
   * controller reads a phantom lag and over-speeds past the target. Only a
   * genuine LAG alongside a lying sensor is really MIXED. (The first live run
   * read +57..+146ms AHEAD on many screens with ~130ms of staleness; calling
   * that MIXED on magnitude alone hid the finding.) */
  function mmProbeVerdict(edgeErrMs, loopErrMs, opts) {
    var stale = edgeErrMs - loopErrMs;
    if (stale < opt(opts, 'VERDICT_STALENESS_MS')) { return 'DROOP'; }
    if (edgeErrMs >= -opt(opts, 'VERDICT_TRUE_ERR_MS')) { return 'BIAS'; }
    return 'MIXED';
  }

  function mmProbeSummary(state, opts) {
    var out = {
      ready: false, nSamples: state.nSamples,
      nEdges: state.edges.length, rejected: state.rejected
    };
    if (state.edges.length < opt(opts, 'MIN_EDGES')) { return out; }

    var pollGap = state.gaps.length ? median(state.gaps) : 0;
    var stepPeriod = state.steps.length ? median(state.steps) : null;

    // Detection lag: an edge is noticed at the first sample AFTER the value was
    // published, so the reading is on average half a polling interval stale.
    // That correction applies only while we are detecting DISCRETE steps -- if
    // currentTime advances on every sample the sensor is live and already current.
    var live = (stepPeriod === null) ||
               (stepPeriod < opt(opts, 'LIVE_SENSOR_RATIO') * pollGap);
    var edgeErr = median(state.edges) + (live ? 0 : pollGap / 2);
    var loopErr = median(state.loop);

    out.ready = true;
    out.edgeErrMs = edgeErr;        // err_true  : where the video REALLY is
    out.loopErrMs = loopErr;        // err_measured : what the controller acts on
    out.stalenessMs = edgeErr - loopErr;   // s : the sensor's lie
    out.stepPeriodMs = stepPeriod;  // ~250 confirms the zero-order hold
    out.pollGapMs = pollGap;
    out.sensorLive = live;
    out.verdict = mmProbeVerdict(edgeErr, loopErr, opts);
    return out;
  }

  root.mmProbeState = mmProbeState;
  root.mmProbeSample = mmProbeSample;
  root.mmProbeSummary = mmProbeSummary;
  root.mmProbeVerdict = mmProbeVerdict;
  root._mmProbeMedian = median;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
