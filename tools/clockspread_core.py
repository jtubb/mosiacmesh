"""Fit-based cross-screen CLOCK spread -- the trustworthy clock-layer metric.

`elapsed` on a client is `GoTime.now() - startEpoch`, and every screen in a group
shares the SAME server-set startEpoch. So the cross-screen spread of `elapsed` at
a common instant IS the clock disagreement, with no video position mixed in
(unlike `ct`, which carries decode/seek error too).

The complication is that `elapsed` is only observed when the sample REACHES the
server, and the iPad-1 uplink carries 22-214ms of jitter that is essentially
one-sided (delay only ever makes a sample look older). A snapshot therefore
measures the radio, not the clocks -- which is why synctool's clock-disagreement
attribution runs outlier-inflated and the project's own record says to trust a
per-screen linear FIT over a window instead.

So: fit each screen's `elapsed` against server time across the window, evaluate
every fit at ONE common instant, and take the spread of those. The fit averages
the jitter out; p10-p90 keeps a single bad radio from defining the answer.

Pure module -- all I/O lives in tools/clockspread.py.
"""
import ast
import re

_TS_RE = re.compile(r'(\d\d):(\d\d):(\d\d),(\d\d\d)\s+WARNING\s+CLIENTLOG\s+(\S+)\s+(\{.*\})\s*$')

MIN_POINTS = 8          # below this a fit is not meaningfully better than a sample


def parse_elapsed_line(line):
    """Parse a timestamped CLIENTLOG line carrying `elapsed` -> dict, else None.

    An UNtimestamped line is unusable by construction: with no server clock there
    is nothing to fit against. (The server only emits timestamps when its log
    format is configured -- see server.py's basicConfig.)
    """
    if "CLIENTLOG" not in line:
        return None
    m = _TS_RE.search(line)
    if not m:
        return None
    hh, mm, ss, ms, key, payload = m.groups()
    try:
        p = ast.literal_eval(payload)
    except Exception:
        return None
    if not isinstance(p, dict):
        return None
    el = p.get("elapsed")
    if not isinstance(el, (int, float)):
        return None
    return {"key": key,
            "t_ms": ((int(hh) * 3600 + int(mm) * 60 + int(ss)) * 1000 + int(ms)),
            "elapsed": el,
            "rs": p.get("rs")}


def fit_offset(points, min_points=MIN_POINTS):
    """Least-squares fit of (elapsed - t) against t. None if too few points.

    Fitting the RESIDUAL rather than elapsed itself removes the common ramp (both
    advance at ~1ms/ms), which keeps the numbers small and the fit well
    conditioned. `offset` is that residual evaluated at the window midpoint --
    the screen's clock position relative to server time.
    """
    if not points or len(points) < min_points:
        return None
    ts = [float(t) for t, _ in points]
    rel = [float(e) - float(t) for t, e in points]
    n = len(ts)
    tbar = sum(ts) / n
    rbar = sum(rel) / n
    sxx = sum((t - tbar) ** 2 for t in ts)
    if sxx <= 0:
        return None
    sxy = sum((ts[i] - tbar) * (rel[i] - rbar) for i in range(n))
    slope = sxy / sxx                      # ms of drift per ms of wall time
    resid = [rel[i] - (rbar + slope * (ts[i] - tbar)) for i in range(n)]
    rms = (sum(r * r for r in resid) / n) ** 0.5
    return {"slope": slope, "offset": rbar, "tbar": tbar, "n": n, "rmsMs": rms}


def percentile(values, pct):
    if not values:
        return None
    v = sorted(values)
    if len(v) == 1:
        return v[0]
    pos = (pct / 100.0) * (len(v) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def clock_spread(per_screen, min_points=MIN_POINTS):
    """Cross-screen clock disagreement from per-screen (t, elapsed) samples.

    Every screen's fit is evaluated at the SAME instant (the mean of their window
    midpoints) so the comparison is apples-to-apples even when screens reported on
    slightly different cadences.
    """
    fits, dropped = {}, []
    for key, pts in (per_screen or {}).items():
        f = fit_offset(pts, min_points=min_points)
        if f is None:
            dropped.append(key)
        else:
            fits[key] = f
    out = {"n": len(fits), "dropped": dropped, "p10p90Ms": None,
           "maxMinMs": None, "medianOffsetMs": None, "perScreen": {}}
    if not fits:
        return out

    common_t = sum(f["tbar"] for f in fits.values()) / len(fits)
    offsets = {}
    for key, f in fits.items():
        offsets[key] = f["offset"] + f["slope"] * (common_t - f["tbar"])

    vals = list(offsets.values())
    med = percentile(vals, 50)
    out["medianOffsetMs"] = med
    # Report each screen RELATIVE to the fleet median: a common offset is
    # invisible on the wall; only disagreement matters.
    out["perScreen"] = dict((k, {"relMs": v - med, "rmsMs": fits[k]["rmsMs"],
                                 "slope": fits[k]["slope"], "n": fits[k]["n"]})
                            for k, v in offsets.items())
    out["p10p90Ms"] = percentile(vals, 90) - percentile(vals, 10)
    out["maxMinMs"] = max(vals) - min(vals)
    return out
