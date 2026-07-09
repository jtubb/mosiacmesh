import types
from mosaicmesh.websocket.legacy import apply_cache_capability


def test_cacheCapable_true_upgrades_default_none():
    c = types.SimpleNamespace(cacheMode="none")
    apply_cache_capability(c, {"cacheCapable": True})
    assert c.cacheMode == "lighttpd-localhost"


def test_cacheCapable_false_leaves_none():
    c = types.SimpleNamespace(cacheMode="none")
    apply_cache_capability(c, {"cacheCapable": False})
    assert c.cacheMode == "none"


def test_does_not_override_a_non_default_mode():
    c = types.SimpleNamespace(cacheMode="none")   # simulate an already-decided value
    c.cacheMode = "something-else"
    apply_cache_capability(c, {"cacheCapable": True})
    assert c.cacheMode == "something-else"        # only upgrades from the default 'none'
