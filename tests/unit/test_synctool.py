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


def test_parse_clientlog_line():
    line = ("2026-07-30 15:39:21,220 WARNING CLIENTLOG ei49puuugjznz5mi "
            "{'tag': 'drift', 'ct': 12043, 'err': -18, 'off': -112725, 'rateGot': 1.02}")
    r = sc.parse_clientlog_line(line)
    assert r['key'] == 'ei49puuugjznz5mi'
    assert r['tag'] == 'drift' and r['ct'] == 12043 and r['err'] == -18
    assert abs(r['rate'] - 1.02) < 1e-9
    # 15:39:21.220 -> ms of day
    assert r['t_ms'] == ((15 * 3600 + 39 * 60 + 21) * 1000 + 220)


def test_parse_ignores_non_drift_and_garbage():
    assert sc.parse_clientlog_line("2026-07-30 15:39:21,220 INFO something else") is None
    assert sc.parse_clientlog_line("garbage") is None
    # clock-sync (idle) has no ct -> still parses but ct is None (caller filters)
    cs = sc.parse_clientlog_line("2026-07-30 15:39:21,220 WARNING CLIENTLOG k {'tag': 'clock-sync'}")
    assert cs is not None and cs['ct'] is None
