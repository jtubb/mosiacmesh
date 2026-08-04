/* js/ratefit.js — ES5, PURE. Estimate a video's actual advance rate from
 * (wallMs, ctSec) samples. currentTime LOOPS (wraps) and mmvideo's readback is
 * noisy (~200ms bimodal). Strategy: UNWRAP the loop (add the period on each ct-drop)
 * to make ct monotonic, then least-squares fit the whole series (a fit over ~30
 * samples averages the readback noise to <1.5%). If a real period is supplied
 * (video.duration, which mmvideo mirrors) the unwrap is exact even for short loops;
 * otherwise it's estimated from the max observed ct. Returns { rate, method, n, period }
 * — rate is dimensionless (ctSec per wallSec). */
(function (root) {
  function median(a) {
    if (!a.length) { return 0; }
    var s = a.slice().sort(function (x, y) { return x - y; });
    var m = Math.floor(s.length / 2);
    return (s.length % 2) ? s[m] : (s[m - 1] + s[m]) / 2;
  }
  function slope(xs, ys) {   // dct(sec) / dwall(ms) * 1000 -> sec/sec
    var n = xs.length, i, mx = 0, my = 0;
    if (n < 2) { return null; }
    for (i = 0; i < n; i++) { mx += xs[i]; my += ys[i]; }
    mx /= n; my /= n;
    var num = 0, den = 0, dx;
    for (i = 0; i < n; i++) { dx = xs[i] - mx; num += dx * (ys[i] - my); den += dx * dx; }
    return den === 0 ? null : (num / den) * 1000;
  }
  // samples: [{ w: wallMs, ct: ctSec }] (time-ordered). opts.period = real loop length (sec).
  function mmRateSlope(samples, opts) {
    opts = opts || {};
    var i, period = (opts.period && opts.period > 0) ? opts.period : null, exact = period !== null;
    if (period === null) {   // estimate the loop length from the highest ct seen
      period = 0;
      for (i = 0; i < samples.length; i++) { if (samples[i].ct > period) { period = samples[i].ct; } }
    }
    var xs = [], ys = [], off = 0, prev = null, wraps = 0;
    for (i = 0; i < samples.length; i++) {
      if (prev !== null && samples[i].ct < prev - 0.05 && period > 0) { off += period; wraps++; }
      xs.push(samples[i].w); ys.push(samples[i].ct + off);
      prev = samples[i].ct;
    }
    var sl = slope(xs, ys);
    if (sl !== null) {
      return { rate: sl, method: (exact ? 'unwrap' : 'unwrap-est'), n: xs.length, period: period, wraps: wraps };
    }
    // degenerate fallback: median of per-interval non-wrap rates
    var rates = [];
    for (i = 1; i < samples.length; i++) {
      var dw = samples[i].w - samples[i - 1].w, dct = samples[i].ct - samples[i - 1].ct;
      if (dw > 0 && dct >= 0) { rates.push(dct * 1000 / dw); }
    }
    return { rate: rates.length ? median(rates) : null, method: 'median', n: rates.length, period: period, wraps: wraps };
  }
  root.mmRateSlope = mmRateSlope;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
