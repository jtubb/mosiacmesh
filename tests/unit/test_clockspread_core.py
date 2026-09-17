"""Fit-based cross-screen CLOCK spread (read-only measurement).

Why this metric rather than synctool's attribution: `elapsed` is
`GoTime.now() - startEpoch`, and every screen in a group shares the SAME
server-set startEpoch. So cross-screen differences in `elapsed` are purely
CLOCK disagreement -- no video position involved, unlike `ct`.

The catch is that we only observe `elapsed` at the SERVER-RECEIVE instant, and
the iPad-1 uplink carries 22-214ms of jitter. A single sample is therefore
useless. Fitting each screen's `elapsed` against server time over a window and
evaluating all fits at ONE common instant averages that jitter out -- which is
why the project's own record says to trust this over a synctool snapshot
("outlier + uplink-jitter dominated ... trust the per-screen elapsed LINEAR-FIT
cross-screen spread").
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

import clockspread_core as cs


def _line(t0, n, step, offset, slope=0.0, jitter=None):
    """Samples of (t_server, elapsed) for a screen whose clock sits `offset` ms
    away, optionally drifting at `slope` and with per-sample uplink jitter."""
    pts = []
    for i in range(n):
        t = t0 + i * step
        elapsed = t + offset + slope * (t - t0)
        if jitter:
            # Uplink delay only ever makes the server SEE a sample later, which
            # reads as a smaller elapsed -- one-sided, like the real thing.
            elapsed -= jitter[i % len(jitter)]
        pts.append((t, elapsed))
    return pts


class TestFit:
    def test_recovers_a_clean_offset(self):
        f = cs.fit_offset(_line(0, 30, 1000, 250))
        assert abs(f["offset"] - 250) < 1.0
        assert f["n"] == 30

    def test_recovers_offset_despite_heavy_one_sided_jitter(self):
        """The whole point: 200ms of uplink jitter must not move the answer much."""
        jit = [0, 40, 200, 15, 90, 5, 130, 60]
        f = cs.fit_offset(_line(0, 60, 1000, 250, jitter=jit))
        # A single sample could be off by up to 200ms; the fit must do far better.
        assert abs(f["offset"] - 250) < 80, f["offset"]

    def test_reports_slope_for_a_drifting_clock(self):
        f = cs.fit_offset(_line(0, 40, 1000, 0, slope=0.001))   # 1ms per second
        assert abs(f["slope"] - 0.001) < 2e-4

    def test_too_few_points_is_rejected(self):
        assert cs.fit_offset(_line(0, 3, 1000, 250)) is None

    def test_empty_is_rejected(self):
        assert cs.fit_offset([]) is None


class TestSpread:
    def test_recovers_known_cross_screen_offsets(self):
        per = {"a": _line(0, 40, 1000, 0),
               "b": _line(0, 40, 1000, 100),
               "c": _line(0, 40, 1000, -100)}
        s = cs.clock_spread(per)
        assert s["n"] == 3
        assert abs(s["maxMinMs"] - 200) < 5

    def test_jitter_does_not_inflate_the_spread(self):
        """Screens are perfectly synced; only the uplink is noisy.

        A snapshot metric would report a large spread here -- that is exactly the
        failure this measurement exists to avoid.
        """
        jit = [0, 150, 30, 200, 10, 90]
        per = {k: _line(0, 60, 1000, 0, jitter=jit[i:] + jit[:i])
               for i, k in enumerate(["a", "b", "c", "d", "e", "f"])}
        s = cs.clock_spread(per)
        assert s["p10p90Ms"] < 60, f"jitter leaked into the spread: {s['p10p90Ms']}"

    def test_p10_p90_resists_a_single_wild_screen(self):
        per = {k: _line(0, 40, 1000, 0) for k in "abcdefghij"}
        per["wild"] = _line(0, 40, 1000, 900)
        s = cs.clock_spread(per)
        assert s["p10p90Ms"] < 200, "one bad radio defined the number"
        assert s["maxMinMs"] >= 850, "max-min should still expose it"

    def test_screens_with_too_few_samples_are_dropped(self):
        per = {"good": _line(0, 40, 1000, 0), "thin": _line(0, 2, 1000, 500)}
        s = cs.clock_spread(per)
        assert s["n"] == 1
        assert "thin" in s["dropped"]

    def test_empty_input(self):
        s = cs.clock_spread({})
        assert s["n"] == 0 and s["p10p90Ms"] is None


class TestParse:
    def test_extracts_elapsed_with_a_server_timestamp(self):
        line = ("2026-09-17 11:20:05,250 WARNING CLIENTLOG abc123 "
                "{'tag': 'hb', 'elapsed': 61234, 'rs': 4}")
        r = cs.parse_elapsed_line(line)
        assert r is not None
        assert r["key"] == "abc123" and r["elapsed"] == 61234
        assert r["t_ms"] == ((11 * 3600 + 20 * 60 + 5) * 1000 + 250)

    def test_untimestamped_line_is_unusable(self):
        """Without a server clock there is nothing to fit against."""
        line = "WARNING:root:CLIENTLOG abc123 {'tag': 'hb', 'elapsed': 61234}"
        assert cs.parse_elapsed_line(line) is None

    def test_line_without_elapsed_is_skipped(self):
        line = "2026-09-17 11:20:05,250 WARNING CLIENTLOG abc {'tag': 'boot'}"
        assert cs.parse_elapsed_line(line) is None
