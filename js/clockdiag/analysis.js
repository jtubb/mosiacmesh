/* js/clockdiag/analysis.js — ES5, NO module syntax (classic <script> for the
 * iPad-1 Safari 5.1 diagnostic page AND a side-effect import for node tests).
 * PURE analysis over a stream of NTP round-trip samples — NO DOM, NO network,
 * NO timers, NO Date/Math.random. Every number the diagnostic reports is
 * computed here, so this file is the unit-tested source of truth (the recvTs
 * lesson: never trust a measurement whose math isn't tested).
 *
 * Sample shape: { t1, t2, t3, t4 }  (epoch ms)
 *   t1 = client send, t2 = server receive, t3 = server send, t4 = client receive.
 * Standard NTP:  rtt = (t4-t1) - (t3-t2);  offset = ((t2-t1) + (t3-t4)) / 2.
 */
(function (root) {

  function median(arr) {
    if (!arr.length) { return 0; }
    var a = arr.slice().sort(function (x, y) { return x - y; });
    var m = Math.floor(a.length / 2);
    return (a.length % 2) ? a[m] : (a[m - 1] + a[m]) / 2;
  }
  function percentile(arr, p) {          // p in [0,1], nearest-rank
    if (!arr.length) { return 0; }
    var a = arr.slice().sort(function (x, y) { return x - y; });
    var idx = Math.ceil(p * a.length) - 1;
    if (idx < 0) { idx = 0; }
    if (idx >= a.length) { idx = a.length - 1; }
    return a[idx];
  }
  function mean(arr) {
    if (!arr.length) { return 0; }
    var s = 0, i;
    for (i = 0; i < arr.length; i++) { s += arr[i]; }
    return s / arr.length;
  }
  function pstdev(arr) {
    if (arr.length < 2) { return 0; }
    var m = mean(arr), s = 0, i, d;
    for (i = 0; i < arr.length; i++) { d = arr[i] - m; s += d * d; }
    return Math.sqrt(s / arr.length);
  }
  // least-squares slope of ys vs xs (dimensionless: dy per dx)
  function slope(xs, ys) {
    var n = xs.length;
    if (n < 2) { return 0; }
    var mx = mean(xs), my = mean(ys), num = 0, den = 0, i, dx;
    for (i = 0; i < n; i++) { dx = xs[i] - mx; num += dx * (ys[i] - my); den += dx * dx; }
    return den === 0 ? 0 : num / den;
  }

  function rttOf(s)    { return (s.t4 - s.t1) - (s.t3 - s.t2); }
  function offsetOf(s) { return ((s.t2 - s.t1) + (s.t3 - s.t4)) / 2; }

  // Trustworthy oscillator-skew fit. Naive least-squares over 60s is pure noise
  // (a 50ppm crystal drifts 3ms in 60s, far below the ~100ms offset jitter). So:
  //  (1) keep only MIN-RTT samples (within rttMargin of the min) -> cleanest offsets;
  //  (2) Theil-Sen slope (median of pairwise slopes) over pairs >= minSpanMs apart
  //      -> robust to remaining outliers and needs a real time baseline;
  //  (3) a CONFIDENCE gate: report skew only when the predicted drift over the
  //      window exceeds ~2x the detrended residual noise AND the window is long
  //      enough. Otherwise confident=false ("insufficient window") -- never a
  //      garbage number.
  // opts: { rttMargin=15, minSpanMs=30000, minWindowMs=180000, minClean=8 }
  function mmSkewFit(samples, opts) {
    opts = opts || {};
    var rttMargin  = opts.rttMargin  != null ? opts.rttMargin  : 15;
    var minSpanMs  = opts.minSpanMs  != null ? opts.minSpanMs  : 30000;
    var minWindowMs= opts.minWindowMs!= null ? opts.minWindowMs: 180000;
    var minClean   = opts.minClean   != null ? opts.minClean   : 8;
    var n = samples.length, i, j;
    var out = { ppm: 0, cleanN: 0, windowMs: 0, residMs: 0, driftMs: 0, confident: false };
    if (n < minClean) { return out; }

    var minRtt = Infinity;
    for (i = 0; i < n; i++) { var r = rttOf(samples[i]); if (r < minRtt) { minRtt = r; } }
    var clean = [];
    for (i = 0; i < n; i++) {
      if (rttOf(samples[i]) <= minRtt + rttMargin) {
        clean.push({ t: samples[i].t1, off: offsetOf(samples[i]) });
      }
    }
    out.cleanN = clean.length;
    if (clean.length < minClean) { return out; }
    out.windowMs = clean[clean.length - 1].t - clean[0].t;

    var slopes = [], dt;
    for (i = 0; i < clean.length; i++) {
      for (j = i + 1; j < clean.length; j++) {
        dt = clean[j].t - clean[i].t;
        if (dt >= minSpanMs) { slopes.push((clean[j].off - clean[i].off) / dt); }
      }
    }
    if (slopes.length < 3) { return out; }             // not enough long-baseline pairs
    var mSlope = median(slopes);                        // ms-offset per ms-time
    out.ppm = mSlope * 1e6;

    // detrended residual noise of the clean offsets, and the drift the slope predicts
    var t0 = clean[0].t, resid = [];
    for (i = 0; i < clean.length; i++) { resid.push(clean[i].off - mSlope * (clean[i].t - t0)); }
    out.residMs = pstdev(resid);
    out.driftMs = Math.abs(mSlope) * out.windowMs;
    out.confident = (out.windowMs >= minWindowMs) && (clean.length >= minClean) &&
                    (out.driftMs >= 2 * out.residMs);
    return out;
  }

  // Main analysis. `samples` = array of {t1,t2,t3,t4}. Returns a stats object.
  function mmClockStats(samples, opts) {
    var n = samples.length, i;
    if (n === 0) { return { n: 0 }; }

    var rtts = [], offs = [];
    var minRttIdx = 0, minRtt = Infinity, r;
    for (i = 0; i < n; i++) {
      r = rttOf(samples[i]);
      rtts.push(r); offs.push(offsetOf(samples[i]));
      if (r < minRtt) { minRtt = r; minRttIdx = i; }
    }

    // Best offset = the offset of the lowest-RTT probe (least queueing noise).
    var bestOffset = offs[minRttIdx];

    // Transit JITTER per direction. up_raw = t2-t1 (carries +offset + up-transit),
    // down_raw = t4-t3 (carries -offset + down-transit). The offset is a CONSTANT,
    // so it drops out of each direction's std -> upJitter/downJitter are clock-
    // independent and measure how variable each leg's delay is. An imbalance flags
    // an asymmetrically-noisy path (e.g. a congested/PSM-throttled uplink).
    // NOTE (fundamental): the CONSTANT part of transit asymmetry is unobservable
    // from round-trips -- it is absorbed 1:1 into the offset estimate ((Du-Dd)/2),
    // so a device cannot measure (nor any round-trip sync correct) its own steady
    // one-way asymmetry. We deliberately do NOT report a mean up-down "asymmetry"
    // (it would just echo 2*offset). If the per-screen clock bias is this steady
    // asymmetry, it is irreducible without an out-of-band one-way reference.
    var upRaw = [], downRaw = [];
    for (i = 0; i < n; i++) {
      upRaw.push(samples[i].t2 - samples[i].t1);
      downRaw.push(samples[i].t4 - samples[i].t3);
    }
    var upJitter = pstdev(upRaw), downJitter = pstdev(downRaw);

    // Skew: robust min-RTT + Theil-Sen fit with a confidence gate (naive
    // least-squares over a short window is noise -- see mmSkewFit).
    var skew = mmSkewFit(samples, opts && opts.skew);

    return {
      n: n,
      rtt: {
        min: minRtt, median: median(rtts), p90: percentile(rtts, 0.9),
        max: percentile(rtts, 1), std: pstdev(rtts)
      },
      offset: bestOffset,
      offsetJitter: pstdev(offs),
      skew: skew,
      jitter: { up: upJitter, down: downJitter, imbalance: Math.abs(upJitter - downJitter) }
    };
  }

  // Threshold verdicts (initial; recalibrate from real device data). Returns
  // { metric: {value, verdict, unit} } with verdict in {'PASS','WARN','FAIL'}.
  function band(value, passLt, warnLt) {
    var v = Math.abs(value);
    return v < passLt ? 'PASS' : (v < warnLt ? 'WARN' : 'FAIL');
  }
  function mmClockVerdicts(stats) {
    if (!stats || !stats.n) { return {}; }
    return {
      rttJitter:     { value: stats.rtt.std,            verdict: band(stats.rtt.std, 30, 80),        unit: 'ms std' },
      offsetJitter:  { value: stats.offsetJitter,       verdict: band(stats.offsetJitter, 20, 60),   unit: 'ms std' },
      skew:          stats.skew.confident
                       ? { value: stats.skew.ppm, verdict: band(stats.skew.ppm, 30, 80), unit: 'ppm' }
                       : { value: stats.skew.ppm, verdict: 'N/A', unit: 'ppm (insufficient window — run longer)' },
      jitterImbalance: { value: stats.jitter.imbalance, verdict: band(stats.jitter.imbalance, 20, 60), unit: 'ms up-vs-down' }
    };
  }

  // v3 (GoTime rung): how much error GoTime's steered clock adds ON TOP of the
  // path floor. rows: [{ gto, off, rtt }] where gto = GoTime's server-local
  // estimate (GoTime.now() - localNow), off = the parallel probe's raw NTP
  // offset, rtt = that probe's RTT. Ground truth = the offset of the MIN-RTT
  // probe (best instantaneous estimate). GoTime error = gto - groundTruth per
  // row; report its bias (mean) + wander (std). ~0/small => GoTime is optimal
  // (path-bound); large => the algorithm (droop/ratchet-staleness/steer) adds error.
  function mmGoTimeError(rows) {
    var n = rows.length, i;
    if (n < 3) { return { n: n }; }
    var minRtt = Infinity, gt = 0;
    for (i = 0; i < n; i++) { if (rows[i].rtt < minRtt) { minRtt = rows[i].rtt; gt = rows[i].off; } }
    var errs = [];
    for (i = 0; i < n; i++) { errs.push(rows[i].gto - gt); }
    return { n: n, groundTruth: gt, errMean: mean(errs), errStd: pstdev(errs),
             errP90: percentile(errs.map(Math.abs), 0.9) };
  }

  root.mmClockStats = mmClockStats;
  root.mmClockVerdicts = mmClockVerdicts;
  root.mmSkewFit = mmSkewFit;
  root.mmGoTimeError = mmGoTimeError;
  root._mmClockHelpers = { median: median, percentile: percentile, mean: mean, pstdev: pstdev, slope: slope, rttOf: rttOf, offsetOf: offsetOf };

})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
