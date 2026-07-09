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
