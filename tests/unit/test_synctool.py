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
