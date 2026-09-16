"""Aggregation math for the drift probe (audit 0.3).

The per-screen probe answers "is this screen's -125ms real or a stale sensor?".
This aggregates it into the wall answer, and the distinction that matters is:

  spread of pTrue  -> the ACTUAL cross-screen desync (what the eye sees)
  spread of pLoop  -> the spread of what the CONTROLLERS believe

If the sensor is biased those two diverge, and every metric built on the loop's
own view (including the drift `err` this investigation has trusted) is measuring
the controllers rather than the wall. Unit-tested because a measurement tool that
quietly lies has already cost this project several cycles.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

import driftprobe_core as dc


def _row(key, true_ms, loop_ms, verdict="BIAS", step=250, t_ms=1000):
    return {"key": key, "t_ms": t_ms, "pTrue": true_ms, "pLoop": loop_ms,
            "pStale": true_ms - loop_ms, "pV": verdict, "pStep": step,
            "pN": 4000, "pE": 300}


class TestPercentile:
    def test_endpoints(self):
        vals = list(range(0, 101, 10))       # 0..100, n=11
        assert dc.percentile(vals, 0) == 0
        assert dc.percentile(vals, 100) == 100

    def test_interpolates(self):
        vals = list(range(0, 101, 10))
        assert dc.percentile(vals, 10) == 10
        assert dc.percentile(vals, 90) == 90

    def test_single_value(self):
        assert dc.percentile([42], 50) == 42

    def test_empty_is_none(self):
        assert dc.percentile([], 50) is None


class TestLatestPerKey:
    def test_keeps_the_newest_row_per_screen(self):
        rows = [_row("a", -10, -130, t_ms=100), _row("a", -20, -140, t_ms=900),
                _row("b", -30, -150, t_ms=500)]
        latest = dc.latest_per_key(rows)
        assert set(latest) == {"a", "b"}
        assert latest["a"]["pTrue"] == -20
        assert latest["b"]["pTrue"] == -30

    def test_drops_rows_older_than_the_freshness_window(self):
        rows = [_row("stale", -10, -130, t_ms=0), _row("fresh", -20, -140, t_ms=60000)]
        latest = dc.latest_per_key(rows, now_ms=60000, fresh_ms=30000)
        assert set(latest) == {"fresh"}


class TestFleetSummary:
    def test_reports_true_spread_as_the_headline(self):
        # Controllers disagree wildly, but the videos are nearly aligned.
        rows = {f"s{i}": _row(f"s{i}", t, l) for i, (t, l) in enumerate(
            [(0, -20), (5, -120), (-5, -220), (10, -60), (-10, -180)])}
        s = dc.fleet_summary(rows)
        assert s["n"] == 5
        assert s["trueSpreadMs"] < s["loopSpreadMs"], (
            "the wall is tighter than the controllers' own view -- that IS the finding")

    def test_fleet_verdict_is_the_majority(self):
        rows = {"a": _row("a", 0, -125, "BIAS"), "b": _row("b", 2, -120, "BIAS"),
                "c": _row("c", -125, -125, "DROOP")}
        s = dc.fleet_summary(rows)
        assert s["verdict"] == "BIAS"
        assert s["verdictCounts"]["BIAS"] == 2
        assert s["verdictCounts"]["DROOP"] == 1

    def test_no_majority_reports_mixed_fleet(self):
        rows = {"a": _row("a", 0, -125, "BIAS"), "b": _row("b", -125, -125, "DROOP")}
        assert dc.fleet_summary(rows)["verdict"] == "SPLIT"

    def test_medians_are_reported(self):
        rows = {"a": _row("a", -10, -130), "b": _row("b", -20, -140),
                "c": _row("c", -30, -150)}
        s = dc.fleet_summary(rows)
        assert s["medTrue"] == -20
        assert s["medLoop"] == -140
        assert s["medStep"] == 250

    def test_spread_uses_p10_p90_not_max_min(self):
        """One erratic screen must not define the wall number.

        The investigation's own record: synctool's clock-disagreement was
        outlier-dominated and unreliable for exactly this reason.
        """
        vals = [(0, -125)] * 20 + [(900, -125)]     # 20 tight screens + 1 wild outlier
        rows = {f"s{i}": _row(f"s{i}", t, l) for i, (t, l) in enumerate(vals)}
        s = dc.fleet_summary(rows)
        assert s["trueSpreadMs"] < 100, "an outlier hijacked the robust spread"
        assert s["trueSpreadMaxMs"] >= 900, "max-min should still expose it"

    def test_empty_is_handled(self):
        s = dc.fleet_summary({})
        assert s["n"] == 0
        assert s["verdict"] == "NO_DATA"


class TestParseProbeLine:
    def test_parses_a_driftprobe_clientlog_line(self):
        line = ("2026-09-16 07:55:15,123 WARNING CLIENTLOG abc123 "
                "{'tag': 'driftprobe', 'pTrue': -8, 'pLoop': -131, 'pStale': 123, "
                "'pStep': 250, 'pGap': 50, 'pLive': 0, 'pN': 4000, 'pE': 300, "
                "'pRej': 2, 'pV': 'BIAS'}")
        r = dc.parse_probe_line(line)
        assert r is not None
        assert r["key"] == "abc123"
        assert r["pTrue"] == -8 and r["pLoop"] == -131
        assert r["pV"] == "BIAS" and r["pStep"] == 250

    def test_ignores_other_tags(self):
        line = ("2026-09-16 07:55:15,123 WARNING CLIENTLOG abc123 "
                "{'tag': 'drift', 'err': -120}")
        assert dc.parse_probe_line(line) is None

    def test_ignores_non_clientlog_noise(self):
        assert dc.parse_probe_line("2026-09-16 07:55:15,123 INFO something else") is None
