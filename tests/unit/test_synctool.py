import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from tools import synctool_core as sc


def test_circular_spread_aligned_is_zero():
    assert sc.circular_spread([500, 500, 500], 30000) == 0


def test_circular_spread_simple():
    assert sc.circular_spread([0, 100, 200], 30000) == 200


def test_circular_spread_wrap_straddle():
    # 29900 and 100 are 200ms apart across the loop boundary, NOT 29800
    assert sc.circular_spread([29900, 100], 30000) == 200


def test_signed_circ_dist_wraps_short_way():
    assert sc.signed_circ_dist(29900, 100, 30000) == 200      # forward across wrap
    assert sc.signed_circ_dist(100, 29900, 30000) == -200     # backward across wrap
    assert sc.signed_circ_dist(100, 300, 30000) == 200


def test_corrections_center_and_offsets():
    # points at -50, 0, +50 around 10000 -> center ~10000, corrections pull to center
    center, corr = sc.corrections([9950, 10000, 10050], 30000)
    assert abs(center - 10000) < 1
    assert abs(corr[0] - 50) < 1 and abs(corr[1]) < 1 and abs(corr[2] + 50) < 1


def test_circular_center_single_and_empty():
    assert sc.circular_center([500], 30000) == 500
    assert sc.circular_center([], 30000) == 0.0
    center, corr = sc.corrections([500], 30000)
    assert center == 500 and corr == [0.0]


def test_fit_ct_recovers_clean_line():
    # ct advances 1ms per ms starting at 5000 at t=1000
    samples = [(1000 + 500 * i, 5000 + 500 * i) for i in range(10)]
    f = sc.fit_ct(samples)
    assert abs(f['slope'] - 1.0) < 1e-6
    assert abs(sc.eval_fit(f, 1000) - 5000) < 1e-6
    assert abs(sc.eval_fit(f, 6000) - 10000) < 1e-6


def test_fit_ct_drops_loop_wrap_keeps_longest_run():
    # 3 samples pre-wrap (ct ~29500->29900) then 7 post-wrap (ct 100->3100): keep the 7
    pre = [(1000 + 500 * i, 29500 + 200 * i) for i in range(3)]
    post = [(2500 + 500 * i, 100 + 500 * i) for i in range(7)]
    f = sc.fit_ct(pre + post)
    assert abs(f['slope'] - 1.0) < 0.02          # fit the post-wrap 1x run
    assert abs(sc.eval_fit(f, 2500) - 100) < 60


def test_fit_ct_rejects_outlier_spike():
    samples = [(1000 + 500 * i, 5000 + 500 * i) for i in range(10)]
    samples[5] = (samples[5][0], samples[5][1] + 400)   # a +400ms readback spike
    f = sc.fit_ct(samples)
    assert f['rejected'] >= 1
    assert abs(f['slope'] - 1.0) < 0.05


def test_fit_ct_insufficient_returns_none():
    assert sc.fit_ct([(1000, 5000), (1500, 5500)]) is None


def test_settled_when_stable_and_1x():
    st, _ = sc.settled_verdict(40, 42, [1.0, 1.01, 0.99])
    assert st == 'SETTLED'


def test_converging_when_spread_moves():
    st, _ = sc.settled_verdict(120, 40, [1.0, 1.0])
    assert st == 'CONVERGING'


def test_converging_when_slope_off_1x():
    st, _ = sc.settled_verdict(40, 42, [1.0, 1.2])   # a screen still seeking/arming
    assert st == 'CONVERGING'


def test_playback_stale_when_ct_lags_telemetry():
    # newest video ct is far behind the newest (clock-sync) telemetry -> stale snapshot
    stale, age = sc.playback_stale(1000, 1000 + sc.FRESH_MS + 1)
    assert stale is True
    assert age == sc.FRESH_MS + 1


def test_playback_live_when_ct_current():
    # newest line IS a ct line (gap 0), and a small gap within the window -> both live
    assert sc.playback_stale(5000, 5000) == (False, 0)
    stale, _ = sc.playback_stale(5000, 5000 + sc.FRESH_MS - 1)
    assert stale is False


def test_playback_stale_respects_threshold_boundary():
    # exactly FRESH_MS is still live; one past it is stale
    assert sc.playback_stale(0, sc.FRESH_MS)[0] is False
    assert sc.playback_stale(0, sc.FRESH_MS + 1)[0] is True


def test_parse_clientlog_line():
    line = ("2026-07-30 15:39:21,220 WARNING CLIENTLOG ei49puuugjznz5mi "
            "{'tag': 'drift', 'ct': 12043, 'err': -18, 'off': -112725, 'rateGot': 1.02}")
    r = sc.parse_clientlog_line(line)
    assert r['key'] == 'ei49puuugjznz5mi'
    assert r['tag'] == 'drift' and r['ct'] == 12043 and r['err'] == -18
    assert abs(r['rate'] - 1.02) < 1e-9
    # t_ms is ABSOLUTE (date included), so the ms-of-day component must still be
    # recoverable but the value itself carries the day.
    assert r['t_ms'] % 86400000 == ((15 * 3600 + 39 * 60 + 21) * 1000 + 220)


# ---------------------------------------------------------------------------
# t_ms must be ABSOLUTE, not ms-of-day.
#
# WHY (2026-10-13): synctool reported "NO LIVE PLAYBACK - newest video ct is
# 46883s old" against a wall that was demonstrably playing -- 89,076 CLIENTLOG
# lines carried a live ct, the newest at 11:04:40 on seg_4abfa73aa6cf_0.mp4.
#
# _LINE_RE captured only HH:MM:SS,mmm and t_ms was ms-of-day, so for a log
# spanning midnight (2026-10-12 13:18 -> 2026-10-13 11:04) yesterday's evening
# lines carry a LARGER number than today's morning ones:
#
#     max t_ms over all telemetry : 86,395,138  (23:59:55, from 10-12)
#     max t_ms with a video ct    : 39,880,843  (11:04:40, from 10-13)
#
# `latest_any = max(t_ms)` therefore landed on a pre-midnight line, the liveness
# guard saw the newest ct "lagging" it by 46,514,295 ms, and refused to report.
#
# This is not only the guard: `now = max(t_ms)`, the FRESH_MS staleness test and
# every WINDOW_MS fit are computed off the same field, so bypassing the guard
# would have silently mixed yesterday's samples with today's using inverted
# deltas -- a wrong number instead of a refusal, which is worse.
#
# The bug was on file with "slice the log to today" as the workaround; it had
# never bitten because the log had always been rotated inside a single day.

def _mk(ts, key='k', payload="{'tag': 'drift', 'ct': 1000}"):
    return "%s WARNING CLIENTLOG %s %s" % (ts, key, payload)


def test_t_ms_is_monotonic_across_midnight():
    # THE BUG: ms-of-day makes 23:59 "later" than the next morning.
    late  = sc.parse_clientlog_line(_mk("2026-10-12 23:59:55,138"))
    early = sc.parse_clientlog_line(_mk("2026-10-13 11:04:40,843"))
    assert late['t_ms'] < early['t_ms'], (
        "a line from the next morning must sort AFTER one from the previous night")


def test_t_ms_delta_across_midnight_is_the_real_elapsed_time():
    a = sc.parse_clientlog_line(_mk("2026-10-12 23:59:00,000"))
    b = sc.parse_clientlog_line(_mk("2026-10-13 00:01:00,000"))
    assert b['t_ms'] - a['t_ms'] == 120000, "two minutes apart, not 23h58m"


def test_liveness_guard_does_not_fire_across_midnight():
    # The exact 2026-10-13 shape: newest telemetry of ANY tag is from last night,
    # newest ct is from this morning and only a beat behind the newest line.
    lines = [
        _mk("2026-10-12 23:59:55,138", payload="{'tag': 'clock-sync'}"),
        _mk("2026-10-13 11:04:40,843", payload="{'tag': 'drift', 'ct': 86766}"),
        _mk("2026-10-13 11:04:41,000", payload="{'tag': 'hb', 'ct': 86920}"),
    ]
    rows = [sc.parse_clientlog_line(l) for l in lines]
    newest_any = max(r['t_ms'] for r in rows)
    newest_ct = max(r['t_ms'] for r in rows if r['ct'] is not None)
    stale, age = sc.playback_stale(newest_ct, newest_any)
    assert stale is False, "playback is live; age was %d ms" % age
    assert age < 1000


def test_a_genuinely_stale_snapshot_is_still_caught():
    # The guard must keep working: video stopped last night, clock-sync kept
    # beating through this morning. That IS stale and must still be refused.
    lines = [
        _mk("2026-10-12 22:00:00,000", payload="{'tag': 'drift', 'ct': 50000}"),
        _mk("2026-10-13 11:00:00,000", payload="{'tag': 'clock-sync'}"),
    ]
    rows = [sc.parse_clientlog_line(l) for l in lines]
    newest_any = max(r['t_ms'] for r in rows)
    newest_ct = max(r['t_ms'] for r in rows if r['ct'] is not None)
    stale, age = sc.playback_stale(newest_ct, newest_any)
    assert stale is True
    assert age == 13 * 3600 * 1000, "13 hours, computed across the date boundary"


def test_t_ms_ordering_holds_over_a_multi_day_log():
    stamps = ["2026-10-11 08:00:00,000", "2026-10-11 23:59:59,999",
              "2026-10-12 00:00:00,000", "2026-10-12 13:18:46,527",
              "2026-10-13 11:04:40,843"]
    vals = [sc.parse_clientlog_line(_mk(s))['t_ms'] for s in stamps]
    assert vals == sorted(vals), "t_ms must increase with wall-clock order"
    assert len(set(vals)) == len(vals)


def test_parse_requires_the_date_prefix():
    # The server always timestamps with a date (commit e53e6be); a bare
    # HH:MM:SS line cannot be placed on a timeline, and silently treating it as
    # ms-of-day is what produced the inversion. Refuse it instead of guessing.
    assert sc.parse_clientlog_line(
        _mk("15:39:21,220")) is None


def test_parse_ignores_non_drift_and_garbage():
    assert sc.parse_clientlog_line("2026-07-30 15:39:21,220 INFO something else") is None
    assert sc.parse_clientlog_line("garbage") is None
    # clock-sync (idle) has no ct -> still parses but ct is None (caller filters)
    cs = sc.parse_clientlog_line("2026-07-30 15:39:21,220 WARNING CLIENTLOG k {'tag': 'clock-sync'}")
    assert cs is not None and cs['ct'] is None
