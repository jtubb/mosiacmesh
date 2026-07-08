from mosaicmesh.cache_pull import PrecacheWindow, CacheState

def test_window_grants_bounded_then_advances():
    w = PrecacheWindow(["a", "b", "c", "d"], n=2)
    assert set(w.start()) == {"a", "b"}          # only 2 concurrent
    assert w.advance("a") == "c"                  # a done -> grant c
    assert w.advance("b") == "d"                  # b done -> grant d
    assert w.advance("c") is None                 # nothing left to grant
    assert w.advance("d") is None
    assert w.drained() is True

def test_cache_state_tracks_and_gates():
    s = CacheState()
    s.record_cached("a", "T1")
    s.record_cached("b", "T1")
    s.record_failed("c", "T1")
    assert s.is_cached("a", "T1") is True
    assert s.is_cached("c", "T1") is False
    assert set(s.cached_clients(["a", "b", "c", "d"], "T1")) == {"a", "b"}
    # a stale token is not "cached"
    assert s.is_cached("a", "T2") is False
