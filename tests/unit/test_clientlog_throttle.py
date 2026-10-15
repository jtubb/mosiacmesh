"""Unit tests for mosaicmesh.clientlog_throttle.

WHY THIS EXISTS (measured 2026-10-15):
mm_server.err reached 2.52 GB in ~2.5 days and the server died with it; 16.85 GB
of logs had accumulated across 54 files. The volume is arithmetic, not a leak:
24 screens x ~1 CLIENTLOG/s x 86400 s x ~500 B is about 1 GB/day, and
legacy.py logged every one of them at WARNING unconditionally.

Measured tag breakdown on a freshly rotated log, with playback STOPPED:

    tag                 lines        bytes   % of log
    clock-sync            317      230,821      83.8%
    boot                   44       34,142      12.4%
    clock-sync-first        4        2,963       1.1%
    (non-CLIENTLOG)        84        7,354       2.7%

So one tag is ~84% of the file, and it is emitted even when nothing is playing.

WHAT MUST NOT BE THROTTLED, and why this is a sampler rather than a filter:
synctool fits (t_ms, ct) from `drift` and `hb` lines, and clockspread fits
`elapsed` per screen; both average out 22-214 ms of one-sided uplink jitter by
having MANY samples in a window. Thinning those would directly degrade the clock
and video measurements this project runs on. `clock-sync` is different: synctool
reads it ONLY as `latest_any`, a liveness reference, so one line every few
seconds serves it exactly as well as one per second.

Unknown tags therefore default to FULL RATE. A future diagnostic must not be
silently swallowed by a throttle written before it existed -- that is the same
class of trap as a log whose lifetime counts mislead because nothing records
that suppression happened.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from mosaicmesh.clientlog_throttle import ClientlogThrottle


def test_throttled_tag_passes_the_first_time():
    t = ClientlogThrottle(interval_s=10.0, throttled={"clock-sync"})
    assert t.should_log("screen1", "clock-sync", 1000.0) is True


def test_throttled_tag_is_suppressed_within_the_interval():
    t = ClientlogThrottle(interval_s=10.0, throttled={"clock-sync"})
    assert t.should_log("screen1", "clock-sync", 1000.0) is True
    assert t.should_log("screen1", "clock-sync", 1001.0) is False
    assert t.should_log("screen1", "clock-sync", 1009.9) is False


def test_throttled_tag_passes_again_after_the_interval():
    t = ClientlogThrottle(interval_s=10.0, throttled={"clock-sync"})
    t.should_log("screen1", "clock-sync", 1000.0)
    assert t.should_log("screen1", "clock-sync", 1010.0) is True
    assert t.should_log("screen1", "clock-sync", 1011.0) is False


def test_each_screen_is_throttled_independently():
    # 24 screens share one server; screen1's line must not silence screen2's,
    # or the liveness reference would only ever name one device.
    t = ClientlogThrottle(interval_s=10.0, throttled={"clock-sync"})
    assert t.should_log("screen1", "clock-sync", 1000.0) is True
    assert t.should_log("screen2", "clock-sync", 1000.0) is True
    assert t.should_log("screen1", "clock-sync", 1001.0) is False
    assert t.should_log("screen2", "clock-sync", 1001.0) is False


def test_each_tag_is_throttled_independently():
    t = ClientlogThrottle(interval_s=10.0, throttled={"clock-sync", "noisy"})
    assert t.should_log("screen1", "clock-sync", 1000.0) is True
    assert t.should_log("screen1", "noisy", 1000.0) is True
    assert t.should_log("screen1", "clock-sync", 1001.0) is False


def test_measurement_tags_are_never_throttled():
    # drift/hb feed synctool's ct fits; seekland/loopland are the drift
    # diagnostics. Thinning any of them degrades the measurements directly.
    t = ClientlogThrottle(interval_s=10.0, throttled={"clock-sync"})
    for tag in ("drift", "hb", "seekland", "loopland"):
        for i in range(50):
            assert t.should_log("screen1", tag, 1000.0 + i * 0.1) is True, tag


def test_unknown_tags_default_to_full_rate():
    # A diagnostic added later must not be silently swallowed by a throttle
    # written before it existed.
    t = ClientlogThrottle(interval_s=10.0, throttled={"clock-sync"})
    for i in range(20):
        assert t.should_log("screen1", "some-future-tag", 1000.0 + i * 0.1) is True


def test_a_missing_tag_is_treated_as_full_rate():
    t = ClientlogThrottle(interval_s=10.0, throttled={"clock-sync"})
    assert t.should_log("screen1", None, 1000.0) is True
    assert t.should_log("screen1", None, 1000.1) is True


def test_suppression_is_counted_so_the_log_can_say_it_happened():
    # A reader who sees a thinned clock-sync stream must be able to tell that it
    # was deliberate. Silent suppression is how a log becomes misleading.
    t = ClientlogThrottle(interval_s=10.0, throttled={"clock-sync"})
    t.should_log("s1", "clock-sync", 1000.0)
    for i in range(5):
        t.should_log("s1", "clock-sync", 1001.0 + i)
    assert t.suppressed_total == 5
    assert t.suppressed_since_report == 5
    n = t.take_suppressed()
    assert n == 5
    assert t.suppressed_since_report == 0, "take_suppressed resets the window"
    assert t.suppressed_total == 5, "the lifetime total is NOT reset"


def test_interval_zero_disables_throttling_entirely():
    # An operator must be able to turn it off without a code change.
    t = ClientlogThrottle(interval_s=0.0, throttled={"clock-sync"})
    for i in range(20):
        assert t.should_log("screen1", "clock-sync", 1000.0 + i * 0.01) is True
    assert t.suppressed_total == 0


def test_clock_goes_backwards_does_not_wedge_the_throttle():
    # time.time() can step backwards (NTP, DST on a Windows host). A naive
    # "now - last >= interval" would then suppress forever.
    t = ClientlogThrottle(interval_s=10.0, throttled={"clock-sync"})
    assert t.should_log("s1", "clock-sync", 2000.0) is True
    assert t.should_log("s1", "clock-sync", 1000.0) is True, "backwards step must not wedge"


def test_state_does_not_grow_without_bound():
    # One entry per (screen, tag) is fine -- 24 screens x a handful of tags. But
    # a bogus/spoofed SRC must not be able to grow it forever, since SRC comes
    # straight off the wire.
    t = ClientlogThrottle(interval_s=10.0, throttled={"clock-sync"}, max_keys=50)
    for i in range(500):
        t.should_log("screen%d" % i, "clock-sync", 1000.0 + i)
    assert t.tracked_keys() <= 50
