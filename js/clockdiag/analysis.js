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

  // Main analysis. `samples` = array of {t1,t2,t3,t4}. Returns a stats object.
  function mmClockStats(samples) {
    var n = samples.length, i;
    if (n === 0) { return { n: 0 }; }

    var rtts = [], offs = [], xs = [];
    var minRttIdx = 0, minRtt = Infinity, r;
    for (i = 0; i < n; i++) {
      r = rttOf(samples[i]);
      rtts.push(r); offs.push(offsetOf(samples[i])); xs.push(samples[i].t1);
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

    // Skew: slope of offset vs local time, in ppm. Anchor xs to the first t1
    // so the magnitudes are small; slope is ms-offset per ms-elapsed => *1e6 ppm.
    var x0 = xs[0], xr = [];
    for (i = 0; i < n; i++) { xr.push(xs[i] - x0); }
    var skewPpm = slope(xr, offs) * 1e6;

    return {
      n: n,
      rtt: {
        min: minRtt, median: median(rtts), p90: percentile(rtts, 0.9),
        max: percentile(rtts, 1), std: pstdev(rtts)
      },
      offset: bestOffset,
      offsetJitter: pstdev(offs),
      skewPpm: skewPpm,
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
      skew:          { value: stats.skewPpm,            verdict: band(stats.skewPpm, 30, 80),         unit: 'ppm' },
      jitterImbalance: { value: stats.jitter.imbalance, verdict: band(stats.jitter.imbalance, 20, 60), unit: 'ms up-vs-down' }
    };
  }

  root.mmClockStats = mmClockStats;
  root.mmClockVerdicts = mmClockVerdicts;
  root._mmClockHelpers = { median: median, percentile: percentile, mean: mean, pstdev: pstdev, slope: slope, rttOf: rttOf, offsetOf: offsetOf };

})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
