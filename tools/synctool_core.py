"""Pure functions for cross-screen sync measurement (no I/O). Unit-tested in
tests/unit/test_synctool.py. See docs/superpowers/specs/2026-07-30-synctool-*-design.md."""

import re
import ast
import datetime as _dt

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


def playback_stale(newest_ct_ms, newest_any_ms, fresh_ms=FRESH_MS):
    """Guard against measuring a frozen pre-stop snapshot. During live playback the newest
    video-ct line and the newest telemetry line of any kind are seconds apart; when playback
    stops, the group keeps emitting clock-sync (ct:None) lines that pull newest_any ahead of
    the frozen newest_ct. Returns (is_stale, age_ms) where age = newest_any - newest_ct. Stale
    means no video is playing now, so any ct-based spread would be an old snapshot.

    Note: catches playback-stopped-while-group-still-reporting (the common case). It does NOT
    catch a fully-frozen log/server (newest_any == newest_ct, both old) — that needs a
    wall-clock reference, tracked separately."""
    age = newest_any_ms - newest_ct_ms
    return age > fresh_ms, age


# The DATE IS REQUIRED. t_ms used to be ms-of-day, which inverts the moment a log
# spans midnight: on 2026-10-13 the wall was demonstrably playing (89,076 CLIENTLOG
# lines with a live ct, newest at 11:04:40) and synctool reported
#   "NO LIVE PLAYBACK - newest video ct is 46883s old"
# because the largest ms-of-day in the file belonged to 2026-10-12 23:59:55:
#   max t_ms over all telemetry : 86,395,138  (23:59:55, from 10-12)
#   max t_ms with a video ct    : 39,880,843  (11:04:40, from 10-13)
# `latest_any = max(t_ms)` landed on a pre-midnight line and the liveness guard saw
# the newest ct lagging it by 46,514,295 ms.
#
# It was never only the guard: `now = max(t_ms)`, the FRESH_MS staleness test and
# every WINDOW_MS fit read the same field, so suppressing the guard would have
# mixed two days of samples with inverted deltas and produced a confident wrong
# number instead of a refusal. The server always timestamps with a date
# (commit e53e6be), so a bare HH:MM:SS line cannot be placed on a timeline at all
# -- refuse it rather than guess a day for it.
_LINE_RE = re.compile(
    r'(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d),(\d\d\d) WARNING CLIENTLOG (\S+) (\{.*\})\s*$')

_EPOCH = _dt.date(1970, 1, 1)


def parse_clientlog_line(line):
    """Parse one CLIENTLOG line -> dict (or None).

    t_ms is ABSOLUTE milliseconds (UTC-naive, from the log's own clock), so it
    orders and subtracts correctly across midnight and across multi-day logs.
    `t_ms % 86400000` still recovers the ms-of-day if a caller wants it."""
    m = _LINE_RE.search(line)
    if not m:
        return None
    yyyy, mon, dd, hh, mm, ss, ms, key, payload = m.groups()
    try:
        p = ast.literal_eval(payload)
    except Exception:
        return None
    if not isinstance(p, dict):
        return None
    try:
        days = (_dt.date(int(yyyy), int(mon), int(dd)) - _EPOCH).days
    except ValueError:                       # impossible date in a mangled line
        return None
    t_ms = (days * 86400000
            + ((int(hh) * 3600 + int(mm) * 60 + int(ss)) * 1000 + int(ms)))
    return {'t_ms': t_ms, 'key': key, 'tag': p.get('tag'),
            'ct': p.get('ct'), 'err': p.get('err'), 'off': p.get('off'),
            'rate': p.get('rateGot')}
