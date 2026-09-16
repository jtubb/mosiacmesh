"""Pure aggregation for the drift probe (audit 2026-09-15 section 0.3).

Each screen reports, from js/drift-probe.js:
    pTrue  -- error read AT a currentTime step edge  (~ where the video REALLY is)
    pLoop  -- error read at arbitrary moments        (what the drift loop acts on)
    pStale -- pTrue - pLoop, the sensor's staleness
    pStep  -- measured step period (~250 confirms the zero-order hold)
    pV     -- BIAS | DROOP | MIXED

The headline the wall cares about is the cross-screen spread of pTrue: the actual
visible desync. The spread of pLoop is the spread of what the CONTROLLERS believe,
and if the sensor is biased those two diverge -- which would mean every metric
derived from the loop's own `err` has been measuring the controllers, not the wall.

All I/O lives in tools/driftprobe.py; this module is pure so it can be tested.
"""
import ast
import re

# Same CLIENTLOG shape synctool_core parses, but we keep the whole payload.
_LINE_RE = re.compile(r'(\d\d):(\d\d):(\d\d),(\d\d\d) WARNING CLIENTLOG (\S+) (\{.*\})\s*$')

_FRESH_MS_DEFAULT = 120000       # a summary older than this is not "now"


def percentile(values, pct):
    """Linear-interpolated percentile of an unsorted list. None if empty."""
    if not values:
        return None
    vals = sorted(values)
    if len(vals) == 1:
        return vals[0]
    pos = (pct / 100.0) * (len(vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    frac = pos - lo
    return vals[lo] + (vals[hi] - vals[lo]) * frac


def median(values):
    return percentile(values, 50)


def parse_probe_line(line):
    """Parse one 'driftprobe' CLIENTLOG line -> dict, or None if it isn't one."""
    if "CLIENTLOG" not in line:
        return None
    m = _LINE_RE.search(line)
    if not m:
        return None
    hh, mm, ss, ms, key, payload = m.groups()
    try:
        p = ast.literal_eval(payload)
    except Exception:
        return None
    if not isinstance(p, dict) or p.get("tag") != "driftprobe":
        return None
    out = {"key": key,
           "t_ms": ((int(hh) * 3600 + int(mm) * 60 + int(ss)) * 1000 + int(ms))}
    for f in ("pTrue", "pLoop", "pStale", "pStep", "pGap", "pLive", "pN", "pE",
              "pRej", "pV"):
        out[f] = p.get(f)
    return out


def latest_per_key(rows, now_ms=None, fresh_ms=_FRESH_MS_DEFAULT):
    """Newest summary per screen, dropping screens whose newest is stale.

    The probe emits a ROLLING summary, so only the most recent one per screen is
    meaningful; and a screen that stopped reporting must not contribute a frozen
    number to the fleet spread (the stale-window trap this project has hit before).
    """
    if not rows:
        return {}
    if now_ms is None:
        now_ms = max(r["t_ms"] for r in rows)
    latest = {}
    for r in rows:
        prev = latest.get(r["key"])
        if prev is None or r["t_ms"] > prev["t_ms"]:
            latest[r["key"]] = r
    return dict((k, v) for k, v in latest.items()
                if (now_ms - v["t_ms"]) <= fresh_ms)


def fleet_summary(latest):
    """Roll per-screen summaries into the wall answer."""
    rows = list(latest.values())
    out = {"n": len(rows), "verdict": "NO_DATA"}
    if not rows:
        return out

    def col(name):
        return [r[name] for r in rows if isinstance(r.get(name), (int, float))]

    true_vals, loop_vals = col("pTrue"), col("pLoop")

    def spread(vals):
        if not vals:
            return None, None
        p10, p90 = percentile(vals, 10), percentile(vals, 90)
        return (p90 - p10), (max(vals) - min(vals))

    # p10-p90 is the headline: a couple of erratic screens must not define the
    # wall number (synctool's clock-disagreement was outlier-dominated for
    # exactly this reason). max-min is kept so an outlier is still visible.
    out["trueSpreadMs"], out["trueSpreadMaxMs"] = spread(true_vals)
    out["loopSpreadMs"], out["loopSpreadMaxMs"] = spread(loop_vals)
    out["medTrue"] = median(true_vals)
    out["medLoop"] = median(loop_vals)
    out["medStale"] = median(col("pStale"))
    out["medStep"] = median(col("pStep"))

    counts = {}
    for r in rows:
        v = r.get("pV")
        if v:
            counts[v] = counts.get(v, 0) + 1
    out["verdictCounts"] = counts
    if counts:
        top, n_top = max(counts.items(), key=lambda kv: kv[1])
        out["verdict"] = top if n_top > len(rows) / 2.0 else "SPLIT"
    return out
