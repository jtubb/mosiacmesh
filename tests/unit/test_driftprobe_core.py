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


class TestLiveness:
    """A frozen log must not read as valid data.

    Burned 2026-09-16: four consecutive reads came back BYTE-IDENTICAL because the
    wall had stopped and the log was no longer growing. The round-based freshness
    guard cannot catch this -- it only compares screens against EACH OTHER, so when
    every screen freezes together they all still look equally recent. Liveness has
    to be established against something outside the data: whether NEW records are
    still arriving.
    """

    def test_new_records_arriving_is_live(self):
        assert dc.liveness_verdict(100, 108) == "LIVE"

    def test_no_new_records_is_frozen(self):
        assert dc.liveness_verdict(100, 100) == "FROZEN"

    def test_a_single_new_record_still_counts_as_live(self):
        assert dc.liveness_verdict(100, 101) == "LIVE"

    def test_shrinking_log_is_treated_as_rotated_not_live(self):
        assert dc.liveness_verdict(100, 20) == "FROZEN"


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


class TestVerdictSign:
    """Droop can only ever put a video BEHIND, so the sign of err_true matters.

    First live run: many screens read err_true of +57..+146 (AHEAD of the clock)
    with ~130ms of staleness, and the original logic called that MIXED purely
    because |err_true| > 50. But a video running AHEAD cannot be drooping -- droop
    is the standing lag needed to sustain a rate correction against a deficit. An
    overshoot is what a NEGATIVE measurement bias produces: the controller reads a
    phantom lag and over-speeds past the target. That is the bias mechanism, not
    evidence of droop, and lumping it into MIXED hid the finding.
    """

    def test_ahead_of_clock_with_staleness_is_bias_not_mixed(self):
        assert dc_verdict(146, 20) == "BIAS"
        assert dc_verdict(57, -59) == "BIAS"

    def test_genuinely_behind_with_staleness_is_mixed(self):
        assert dc_verdict(-125, -250) == "MIXED"

    def test_no_staleness_is_still_droop(self):
        assert dc_verdict(-125, -125) == "DROOP"

    def test_near_zero_is_bias(self):
        assert dc_verdict(4, -96) == "BIAS"


def dc_verdict(true_ms, loop_ms):
    return dc.verdict(true_ms, loop_ms)


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

    def test_parses_the_UNTIMESTAMPED_format_the_server_actually_writes(self):
        """The live mm_server.err carries no timestamps at all.

        Found on the first real run: every line is `WARNING:root:CLIENTLOG ...`
        with no time prefix, so the regex inherited from synctool_core matched
        nothing and the tool reported "no telemetry" while the data was sitting
        right there. (synctool has the same blind spot against this log.)
        """
        line = ("WARNING:root:CLIENTLOG 6c14aqrn5du6jemi "
                "{'tag': 'driftprobe', 'pTrue': -8, 'pLoop': -131, 'pStale': 123, "
                "'pStep': 255, 'pGap': 52, 'pLive': 0, 'pN': 1052, 'pE': 221, "
                "'pRej': 4, 'pV': 'BIAS'}")
        r = dc.parse_probe_line(line)
        assert r is not None
        assert r["key"] == "6c14aqrn5du6jemi"
        assert r["pTrue"] == -8 and r["pStep"] == 255
        assert r["t_ms"] is None, "no timestamp available in this format"

    def test_parses_a_line_with_extra_payload_fields(self):
        """Real lines carry the whole dbg payload, not just the probe fields."""
        line = ("WARNING:root:CLIENTLOG abc {'tag': 'driftprobe', 'activated': True, "
                "'rs': 4, 'ct': 339, 'src': 'seg_x_0.mp4', 'settled': False, "
                "'steer': {'n': 37, 'kept': 4}, 'pTrue': -9, 'pLoop': -134, "
                "'pStale': 125, 'pStep': 255, 'pV': 'BIAS'}")
        r = dc.parse_probe_line(line)
        assert r is not None and r["pStale"] == 125


class TestOrderingWithoutTimestamps:
    def _rows(self):
        """Two rounds of three screens, newest last, no timestamps."""
        rows = []
        for rnd in range(2):
            for key in ("a", "b", "c"):
                rows.append({"key": key, "t_ms": None, "seq": len(rows),
                             "pTrue": -10 * (rnd + 1), "pLoop": -130, "pStale": 120,
                             "pV": "BIAS", "pStep": 255})
        return rows

    def test_falls_back_to_file_order(self):
        latest = dc.latest_per_key(self._rows())
        assert set(latest) == {"a", "b", "c"}
        assert all(r["pTrue"] == -20 for r in latest.values()), "took an older round"

    def test_drops_a_screen_that_stopped_reporting(self):
        rows = self._rows()
        # 'dead' reported once at the very start and never again; many rounds pass.
        rows.insert(0, {"key": "dead", "t_ms": None, "seq": -1, "pTrue": -999,
                        "pLoop": -130, "pStale": 120, "pV": "BIAS", "pStep": 255})
        for i, r in enumerate(rows):
            r["seq"] = i
        for rnd in range(8):
            for key in ("a", "b", "c"):
                rows.append({"key": key, "t_ms": None, "seq": len(rows), "pTrue": -30,
                             "pLoop": -130, "pStale": 120, "pV": "BIAS", "pStep": 255})
        latest = dc.latest_per_key(rows)
        assert "dead" not in latest, "a screen that went silent still counted"
