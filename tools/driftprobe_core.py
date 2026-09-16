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

# Two CLIENTLOG shapes exist in the wild:
#   timestamped   "HH:MM:SS,mmm WARNING CLIENTLOG <key> {...}"   (what synctool assumes)
#   bare          "WARNING:root:CLIENTLOG <key> {...}"           (what the live server writes)
# The live mm_server.err carries NO timestamps, so a timestamp-only regex silently
# matches nothing and the tool reports "no telemetry" while the data is right there.
_TS_RE = re.compile(r'(\d\d):(\d\d):(\d\d),(\d\d\d)\s+WARNING\s+CLIENTLOG\s+(\S+)\s+(\{.*\})\s*$')
_BARE_RE = re.compile(r'CLIENTLOG\s+(\S+)\s+(\{.*\})\s*$')

_FRESH_MS_DEFAULT = 120000       # a summary older than this is not "now"
_FRESH_ROUNDS_DEFAULT = 4        # ...or, with no clock, this many emission rounds


_VERDICT_STALENESS_MS = 40      # below this the sensor is honest enough
_VERDICT_TRUE_ERR_MS = 50       # |err_true| below this: the video is where it should be


def verdict(true_ms, loop_ms, staleness_ms=_VERDICT_STALENESS_MS,
            true_err_ms=_VERDICT_TRUE_ERR_MS):
    """Which mechanism the two readings imply, honouring the SIGN of err_true.

    Droop can only put a video BEHIND -- it is the standing lag required to
    sustain a rate correction against a decode deficit. So a video measured AHEAD
    of the clock cannot be drooping, however large the error: an overshoot is
    what a negative measurement bias produces, since the controller reads a
    phantom lag and over-speeds past the target. Only a genuine LAG alongside a
    lying sensor is really MIXED.
    """
    stale = true_ms - loop_ms
    if stale < staleness_ms:
        return "DROOP"                      # sensor is honest; the error is real
    if true_ms >= -true_err_ms:
        return "BIAS"                       # at target or overshooting past it
    return "MIXED"                          # lying sensor AND a genuine lag


def row_verdict(r):
    """Verdict for one screen, recomputed from its readings (falls back to the
    client-reported pV only if the readings are missing)."""
    t, l = r.get("pTrue"), r.get("pLoop")
    if isinstance(t, (int, float)) and isinstance(l, (int, float)):
        return verdict(t, l)
    return r.get("pV")


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


def parse_probe_line(line, seq=None):
    """Parse one 'driftprobe' CLIENTLOG line -> dict, or None if it isn't one.

    Handles both log shapes. t_ms is None when the line carries no timestamp, in
    which case callers order by `seq` (file position) instead.
    """
    if "CLIENTLOG" not in line:
        return None
    t_ms = None
    m = _TS_RE.search(line)
    if m:
        hh, mm, ss, ms, key, payload = m.groups()
        t_ms = ((int(hh) * 3600 + int(mm) * 60 + int(ss)) * 1000 + int(ms))
    else:
        m = _BARE_RE.search(line)
        if not m:
            return None
        key, payload = m.groups()
    try:
        p = ast.literal_eval(payload)
    except Exception:
        return None
    if not isinstance(p, dict) or p.get("tag") != "driftprobe":
        return None
    out = {"key": key, "t_ms": t_ms, "seq": seq}
    for f in ("pTrue", "pLoop", "pStale", "pStep", "pGap", "pLive", "pN", "pE",
              "pRej", "pV"):
        out[f] = p.get(f)
    return out


def latest_per_key(rows, now_ms=None, fresh_ms=_FRESH_MS_DEFAULT,
                   fresh_rounds=_FRESH_ROUNDS_DEFAULT):
    """Newest summary per screen, dropping screens whose newest one is stale.

    The probe emits a ROLLING summary, so only the most recent per screen is
    meaningful; and a screen that stopped reporting must not contribute a frozen
    number to the fleet spread (the stale-window trap this project has hit before).

    Orders by timestamp when the log has one, otherwise by file position. With no
    clock, "stale" is expressed in emission ROUNDS: every screen reports on the
    same interval, so one round is roughly one record per screen, and a screen
    that has missed several rounds has gone quiet.
    """
    if not rows:
        return {}
    timed = all(r.get("t_ms") is not None for r in rows)
    field = "t_ms" if timed else "seq"
    if any(r.get(field) is None for r in rows):   # nothing usable to order by
        return {}

    latest = {}
    for r in rows:
        prev = latest.get(r["key"])
        if prev is None or r[field] > prev[field]:
            latest[r["key"]] = r

    newest = now_ms if (timed and now_ms is not None) else max(r[field] for r in rows)
    window = fresh_ms if timed else max(1, fresh_rounds * max(1, len(latest)))
    return dict((k, v) for k, v in latest.items() if (newest - v[field]) <= window)


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

    # Recompute the verdict here rather than trusting the client's pV: the tool
    # owns interpretation, so a refinement applies to telemetry already captured
    # instead of needing a fleet reload to take effect.
    counts = {}
    for r in rows:
        v = row_verdict(r)
        if v:
            counts[v] = counts.get(v, 0) + 1
    out["verdictCounts"] = counts
    if counts:
        top, n_top = max(counts.items(), key=lambda kv: kv[1])
        out["verdict"] = top if n_top > len(rows) / 2.0 else "SPLIT"
    return out
