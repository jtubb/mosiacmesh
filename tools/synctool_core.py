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
