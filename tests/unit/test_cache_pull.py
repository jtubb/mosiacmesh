from mosaicmesh.cache_pull import PrecacheWindow

def test_window_grants_bounded_then_advances():
    w = PrecacheWindow(["a", "b", "c", "d"], n=2)
    assert set(w.start()) == {"a", "b"}          # only 2 concurrent
    assert w.advance("a") == "c"                  # a done -> grant c
    assert w.advance("b") == "d"                  # b done -> grant d
    assert w.advance("c") is None                 # nothing left to grant
    assert w.advance("d") is None
    assert w.drained() is True
