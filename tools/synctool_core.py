"""Pure functions for cross-screen sync measurement (no I/O). Unit-tested in
tests/unit/test_synctool.py. See docs/superpowers/specs/2026-07-30-synctool-*-design.md."""

WINDOW_MS = 18000
FRESH_MS = 20000
MIN_SAMPLES = 5
OUTLIER_MS = 120
SETTLE_TOL_MS = 25
SLOPE_TOL = 0.05


def signed_circ_dist(a, b, loop_len):
    """Signed shortest distance to move a -> b on a circle of circumference loop_len.
    Result in (-loop_len/2, loop_len/2]. Positive = forward (increasing)."""
    d = (b - a) % loop_len
    if d > loop_len / 2.0:
        d -= loop_len
    return d


def circular_spread(positions, loop_len):
    """Minimal-arc spread of positions on the circle: circumference minus the largest empty
    gap. Aligned -> 0; a wrap-straddling pair reads as its short-way distance."""
    if len(positions) < 2:
        return 0.0
    xs = sorted(p % loop_len for p in positions)
    n = len(xs)
    gaps = [xs[i + 1] - xs[i] for i in range(n - 1)]
    gaps.append(xs[0] + loop_len - xs[-1])   # wrap gap
    return loop_len - max(gaps)


def circular_center(positions, loop_len):
    """Midpoint of the minimal enclosing arc (the arc opposite the largest empty gap)."""
    if not positions:
        return 0.0
    if len(positions) == 1:
        return positions[0] % loop_len
    xs = sorted(p % loop_len for p in positions)
    n = len(xs)
    gaps = [(xs[(i + 1) % n] - xs[i]) % loop_len for i in range(n)]
    k = max(range(n), key=lambda i: gaps[i])   # largest gap is AFTER xs[k]
    start = xs[(k + 1) % n]                     # arc begins at the point after the gap
    spread = loop_len - gaps[k]
    return (start + spread / 2.0) % loop_len


def corrections(positions, loop_len):
    """Returns (center, [signed distance from each position to center])."""
    c = circular_center(positions, loop_len)
    return c, [signed_circ_dist(p % loop_len, c, loop_len) for p in positions]


def _linfit(pts):
    """Least-squares (slope, intercept) for [(x, y), ...]. slope 0 if degenerate."""
    n = len(pts)
    sx = sum(x for x, _ in pts); sy = sum(y for _, y in pts)
    sxx = sum(x * x for x, _ in pts); sxy = sum(x * y for x, y in pts)
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0.0, (sy / n if n else 0.0)
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


def fit_ct(samples, outlier_ms=OUTLIER_MS, min_samples=MIN_SAMPLES):
    """Fit ct(t)=ct0+slope*(t-t0) over the longest wrap-free run of samples, rejecting
    outliers. samples=[(t_ms, ct)]. Returns a fit dict or None if too few kept."""
    if len(samples) < 2:
        return None
    s = sorted(samples)
    runs = [[s[0]]]
    for i in range(1, len(s)):
        if s[i][1] < runs[-1][-1][1] - 1000:   # ct dropped >1s => loop wrap
            runs.append([s[i]])
        else:
            runs[-1].append(s[i])
    run = max(runs, key=len)
    if len(run) < min_samples:
        return None
    t0 = run[0][0]
    pts = [(t - t0, ct) for t, ct in run]
    slope, ct0 = _linfit(pts)
    kept = [(x, y) for x, y in pts if abs(y - (ct0 + slope * x)) <= outlier_ms]
    rejected = len(pts) - len(kept)
    if len(kept) < min_samples:
        kept = pts; rejected = 0        # don't over-reject on a noisy screen
    slope, ct0 = _linfit(kept)
    return {'ct0': ct0, 'slope': slope, 't0': t0, 'n': len(kept), 'rejected': rejected}


def eval_fit(fit, T):
    return fit['ct0'] + fit['slope'] * (T - fit['t0'])


def settled_verdict(early_spread, late_spread, slopes, settle_tol=SETTLE_TOL_MS, slope_tol=SLOPE_TOL):
    """SETTLED only if the spread is stable across the two sub-windows AND every screen is
    playing at ~1x (not seeking/arming). Otherwise CONVERGING with the reason."""
    if abs(early_spread - late_spread) > settle_tol:
        return 'CONVERGING', 'spread moved %.0f->%.0f ms' % (early_spread, late_spread)
    off = [s for s in slopes if abs(s - 1.0) > slope_tol]
    if off:
        return 'CONVERGING', '%d screen(s) not at 1x (seeking/arming)' % len(off)
    return 'SETTLED', 'stable'
