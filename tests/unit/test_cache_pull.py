from mosaicmesh.cache_pull import PrecacheWindow

def test_window_grants_bounded_then_advances():
    w = PrecacheWindow(["a", "b", "c", "d"], n=2)
    assert set(w.start()) == {"a", "b"}          # only 2 concurrent
    assert w.advance("a") == "c"                  # a done -> grant c
    assert w.advance("b") == "d"                  # b done -> grant d
    assert w.advance("c") is None                 # nothing left to grant
    assert w.advance("d") is None
    assert w.drained() is True

def test_window_sweep_timeouts_advances_past_non_ackers():
    w = PrecacheWindow(["a", "b", "c"], n=1)
    assert w.start(now=100.0) == ["a"]                       # a granted at t=100
    assert w.sweep_timeouts(now=105.0, timeout_s=20.0) == ([], [])   # 5s -> not stale
    stale, granted = w.sweep_timeouts(now=125.0, timeout_s=20.0)     # 25s -> a stale
    assert stale == ["a"]
    assert granted == ["b"]                                  # advanced past a -> b granted
    assert w.sweep_timeouts(now=126.0, timeout_s=20.0) == ([], [])   # b fresh (granted @125)
    # a real ack for b advances to c normally
    assert w.advance("b", now=126.0) == "c"


# ---------------------------------------------------------------------------
# Per-grant, size-aware ack timeouts.
#
# PRECACHE_ACK_TIMEOUT was a flat 20s, sized when segments were ~11MB. A 93MB
# segment (PullTest / big_buck_bunny, 2026-09-20) cannot transfer over iPad-1
# WiFi in 20s, so EVERY grant timed out, the slot was freed, the client was
# marked failed, and the next reconcile cycle re-issued -- restarting the
# download from scratch. Observed on-wall: 26 PRECACHE instructions in 5
# minutes to the same clients, a .part file frozen at exactly 7 x 1MB chunks,
# another at 0 bytes, and 0 CACHED acks ever for that token. The transfer was
# structurally incapable of completing.
#
# A client that genuinely fails acks CACHE_FAILED and advances the window
# immediately, so the timeout only guards a client that says NOTHING at all.
# Being generous there costs throughput on a dead slot; being too strict makes
# large segments impossible. Scale it with the bytes actually being moved.

def test_window_honours_a_per_grant_timeout():
    """A big segment must not be swept at the small-segment deadline."""
    slow = {"big": 600.0, "small": 30.0}
    w = PrecacheWindow(["big", "small"], n=2, timeout_for=lambda k: slow[k])
    assert set(w.start(now=0.0)) == {"big", "small"}
    stale, _ = w.sweep_timeouts(now=60.0)      # past small's 30s, well inside big's 600s
    assert stale == ["small"]
    stale, _ = w.sweep_timeouts(now=700.0)
    assert stale == ["big"]


def test_window_per_grant_timeout_applies_to_later_grants_too():
    """The deadline must follow the client granted into the freed slot, not the
    one that vacated it."""
    w = PrecacheWindow(["a", "b"], n=1, timeout_for=lambda k: 10.0 if k == "a" else 500.0)
    assert w.start(now=0.0) == ["a"]
    stale, granted = w.sweep_timeouts(now=20.0)
    assert (stale, granted) == (["a"], ["b"])
    assert w.sweep_timeouts(now=100.0) == ([], []), "b inherited a's short deadline"
    stale, _ = w.sweep_timeouts(now=600.0)
    assert stale == ["b"]


def test_window_without_timeout_for_keeps_the_flat_fallback():
    """Existing callers pass timeout_s and supply no per-grant map."""
    w = PrecacheWindow(["a"], n=1)
    assert w.start(now=0.0) == ["a"]
    assert w.sweep_timeouts(now=10.0, timeout_s=20.0) == ([], [])
    stale, _ = w.sweep_timeouts(now=30.0, timeout_s=20.0)
    assert stale == ["a"]


def test_precache_ack_timeout_scales_with_segment_size():
    import server
    small = server.precache_ack_timeout(11 * 1024 * 1024)    # the size that worked
    big = server.precache_ack_timeout(93 * 1024 * 1024)      # the size that never could
    assert big > small
    assert small >= 20.0, "must not be stricter than the old flat timeout"
    assert big >= 300.0, f"93MB needs a realistic window, got {big:.0f}s"
    assert server.precache_ack_timeout(0) >= 20.0            # degenerate input is safe
    assert server.precache_ack_timeout(None) >= 20.0         # unknown size -> base


def test_precache_ack_timeout_is_capped():
    import server
    assert server.precache_ack_timeout(100 * 1024**3) <= server.PRECACHE_ACK_MAX


# ---------------------------------------------------------------------------
# Progress-driven liveness.
#
# The size-aware deadline (de70b7b) let large segments finish, but it traded one
# problem for another: the server still cannot tell "transferring" from "died",
# so a grant that goes silent holds one of n slots for its FULL deadline. At 93MB
# that is ~16 minutes, and with 11 screens needing the segment a full rotation
# went from ~1 minute to ~50. Measured on-wall 2026-09-20: 24 minutes of watching
# with zero grants reaching the queued canaries.
#
# A progress report resets the grant's clock AND shortens its deadline to a stall
# timeout, so death is detected in seconds. A client that never reports is simply
# never touched and keeps the size-aware deadline -- absence of progress is the
# backward-compatible fallback, so no capability flag is needed.

def test_touch_resets_the_deadline_clock():
    w = PrecacheWindow(["a"], n=1, timeout_for=lambda k: 600.0)
    assert w.start(now=0.0) == ["a"]
    assert w.sweep_timeouts(now=500.0) == ([], []), "inside the 600s deadline"
    assert w.touch("a", now=500.0) is True
    # without the touch this would expire at 600; the clock restarted at 500
    assert w.sweep_timeouts(now=900.0) == ([], []), "progress must postpone expiry"


def test_touch_shortens_the_deadline_to_a_stall_timeout():
    """Once a client proves it is alive and reporting, a silence of stall_s is
    enough to call it dead -- no need to wait out the whole-file deadline."""
    w = PrecacheWindow(["a", "b"], n=1, timeout_for=lambda k: 600.0)
    assert w.start(now=0.0) == ["a"]
    assert w.touch("a", now=100.0, stall_s=90.0) is True
    assert w.sweep_timeouts(now=180.0) == ([], []), "80s of silence is not a stall yet"
    stale, granted = w.sweep_timeouts(now=200.0)     # 100s since last progress
    assert stale == ["a"], "a reporting client that goes quiet must be dropped fast"
    assert granted == ["b"]


def test_touch_on_an_unknown_or_finished_grant_is_a_noop():
    w = PrecacheWindow(["a"], n=1)
    w.start(now=0.0)
    assert w.touch("nobody", now=1.0) is False
    w.advance("a", now=1.0)
    assert w.touch("a", now=2.0) is False, "a completed grant is not active"


def test_a_client_that_never_reports_keeps_the_size_aware_deadline():
    """Old client JS sends no progress. It must behave exactly as before."""
    w = PrecacheWindow(["a", "b"], n=1, timeout_for=lambda k: 600.0)
    assert w.start(now=0.0) == ["a"]
    assert w.sweep_timeouts(now=300.0) == ([], []), "must not be dropped early"
    stale, _ = w.sweep_timeouts(now=700.0)
    assert stale == ["a"]


def test_progress_then_completion_advances_normally():
    w = PrecacheWindow(["a", "b"], n=1, timeout_for=lambda k: 600.0)
    w.start(now=0.0)
    w.touch("a", now=10.0, stall_s=90.0)
    w.touch("a", now=20.0, stall_s=90.0)
    assert w.advance("a", now=25.0) == "b"
    assert w.drained() is False
    assert w.advance("b", now=26.0) is None
    assert w.drained() is True
