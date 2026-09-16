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
    RING_CAP: 512,             // bounded history (a long run must not grow memory)
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

  function push(ring, v, cap) {
    ring.push(v);
    while (ring.length > cap) { ring.shift(); }
  }

  function mmProbeState() {
    return {
      lastCt: null, lastT: null, lastEdgeT: null,
      edges: [],    // err read AT a step edge  (~ err_true)
      loop: [],     // err read at every sample (~ err_measured, the loop's view)
      steps: [],    // interval between step edges (~ the observer period)
      gaps: [],     // sampling interval at an edge (the detection-lag scale)
      nSamples: 0, rejected: 0
    };
  }

  /* One observation. errMs is the drift loop's own error definition
   * (currentTime*1000 - intended offset), passed in so this stays free of any
   * playlist/clock dependency. */
  function mmProbeSample(state, ctMs, tMs, errMs, opts) {
    var CAP = opt(opts, 'RING_CAP');
    var STEP_MAX = opt(opts, 'STEP_MAX_MS');
    state.nSamples += 1;
    push(state.loop, errMs, CAP);
    if (state.lastCt !== null && ctMs !== state.lastCt) {
      var dct = ctMs - state.lastCt;
      if (dct > 0 && dct < STEP_MAX) {
        push(state.edges, errMs, CAP);
        if (state.lastT !== null) { push(state.gaps, tMs - state.lastT, CAP); }
        if (state.lastEdgeT !== null) { push(state.steps, tMs - state.lastEdgeT, CAP); }
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

  /* Which mechanism the two readings imply. Pure so it is testable on its own. */
  function mmProbeVerdict(edgeErrMs, loopErrMs, opts) {
    var stale = edgeErrMs - loopErrMs;
    if (stale < opt(opts, 'VERDICT_STALENESS_MS')) { return 'DROOP'; }
    if (Math.abs(edgeErrMs) <= opt(opts, 'VERDICT_TRUE_ERR_MS')) { return 'BIAS'; }
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
