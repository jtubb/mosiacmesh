import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import argparse
_orig = argparse.ArgumentParser.parse_args
class _MockArgs:
    Port = 3000
    Verbose = False
argparse.ArgumentParser.parse_args = lambda self, a=None, n=None: _MockArgs()
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig
import pytest
from mosaicmesh import render as R
from mosaicmesh.state import Settings, Display, Client


def test_pull_url_for_seg_key_segment():
    assert R.pull_url_for_seg_key("ck1", "abc123_0") == "/media/ck1/videos/seg_abc123_0.mp4"

def test_pull_url_for_seg_key_full():
    assert R.pull_url_for_seg_key("ck1", "full_abc123_2") == "/media/server/videos/full_abc123_2.mp4"

def test_pull_url_for_seg_key_reexported_on_server():
    assert server.pull_url_for_seg_key("ck1", "abc123_0") == "/media/ck1/videos/seg_abc123_0.mp4"


@pytest.fixture
def fresh_settings():
    prev = getattr(server, "settings", None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev

def _mk_client(did, mode="mmvideo", online=True, ip="1.2.3.4", cached=None):
    c = Client(); c.displayID = did; c.cacheMode = mode; c.isOnline = online
    c.ip = ip; c.cachedSegments = set(cached or [])
    return c

def test_needing_selects_only_missing_eligible(fresh_settings, monkeypatch):
    monkeypatch.setattr(server, "_expected_seg_keys_for_display",
                        lambda d: {"tok_0", "tok_1"})
    fresh_settings.displays["G1"] = Display()
    fresh_settings.clients["miss"] = _mk_client("G1", cached=["tok_0"])          # missing tok_1
    fresh_settings.clients["full"] = _mk_client("G1", cached=["tok_0", "tok_1"]) # up to date
    fresh_settings.clients["off"]  = _mk_client("G1", online=False, cached=[])   # offline
    fresh_settings.clients["none"] = _mk_client("G1", mode="none", cached=[])    # not cache-capable
    fresh_settings.clients["other"]= _mk_client("G2", cached=[])                 # other group
    out = server.clients_needing_precache("G1")
    assert out == {"miss": ["tok_1"]}

def test_needing_empty_when_no_expected(fresh_settings, monkeypatch):
    monkeypatch.setattr(server, "_expected_seg_keys_for_display", lambda d: set())
    fresh_settings.displays["G1"] = Display()
    fresh_settings.clients["c"] = _mk_client("G1", cached=[])
    assert server.clients_needing_precache("G1") == {}

def test_needing_sorts_multiple_missing(fresh_settings, monkeypatch):
    monkeypatch.setattr(server, "_expected_seg_keys_for_display",
                        lambda d: {"tok_1", "tok_0"})
    fresh_settings.displays["G1"] = Display()
    fresh_settings.clients["c"] = _mk_client("G1", cached=[])
    assert server.clients_needing_precache("G1") == {"c": ["tok_0", "tok_1"]}

def test_reconcile_skips_when_window_active(fresh_settings, monkeypatch):
    monkeypatch.setattr(server, "precache_windows", {"G1": object()})
    called = []
    monkeypatch.setattr(server, "start_precache", lambda *a, **k: called.append(a))
    server.reconcile_group_cache("G1")
    assert called == []

def test_reconcile_skips_when_not_ready(fresh_settings, monkeypatch):
    monkeypatch.setattr(server, "precache_windows", {})
    d = Display(); d.renderedToken = "tok"; d.currentPlaylistName = "P"
    d.renders = {"P": {"state": "RENDERING"}}
    fresh_settings.displays["G1"] = d
    called = []
    monkeypatch.setattr(server, "start_precache", lambda *a, **k: called.append(a))
    server.reconcile_group_cache("G1")
    assert called == []

def test_reconcile_starts_precache_one_seg_per_client(fresh_settings, monkeypatch):
    # The MM_CACHE_RECONCILE gate now lives in reconcile_decision (which
    # reconcile_group_cache obeys) rather than only in the process()-loop
    # wrapper, so a test asserting a grant has to switch the feature on.
    monkeypatch.setenv("MM_CACHE_RECONCILE", "1")
    monkeypatch.setattr(server, "precache_windows", {})
    d = Display(); d.renderedToken = "tok"; d.currentPlaylistName = "P"
    d.renders = {"P": {"state": "READY"}}
    fresh_settings.displays["G1"] = d
    monkeypatch.setattr(server, "clients_needing_precache",
                        lambda did: {"ck1": ["tok_0", "tok_1"], "ck2": ["tok_0"]})
    calls = []
    monkeypatch.setattr(server, "start_precache",
                        lambda group, token, urls, **k: calls.append((group, token, urls)))
    server.reconcile_group_cache("G1")
    assert len(calls) == 1
    group, token, urls = calls[0]
    assert group == "G1" and token == "tok"
    # one seg per client (the first, sorted) -> URL via pull_url_for_seg_key
    assert urls == {"ck1": "/media/ck1/videos/seg_tok_0.mp4",
                    "ck2": "/media/ck2/videos/seg_tok_0.mp4"}

def test_reconcile_noop_when_nothing_missing(fresh_settings, monkeypatch):
    monkeypatch.setattr(server, "precache_windows", {})
    d = Display(); d.renderedToken = "tok"; d.currentPlaylistName = "P"
    d.renders = {"P": {"state": "READY"}}
    fresh_settings.displays["G1"] = d
    monkeypatch.setattr(server, "clients_needing_precache", lambda did: {})
    called = []
    monkeypatch.setattr(server, "start_precache", lambda *a, **k: called.append(a))
    server.reconcile_group_cache("G1")
    assert called == []

def test_reconcile_all_calls_each_group_and_isolates_errors(fresh_settings, monkeypatch):
    monkeypatch.setenv("MM_CACHE_RECONCILE", "1")   # enable the gated reconcile
    fresh_settings.displays["G1"] = Display()
    fresh_settings.displays["G2"] = Display()
    seen = []
    def _fake(did):
        seen.append(did)
        if did == "G1":
            raise RuntimeError("boom")   # one group failing must not stop the others
    monkeypatch.setattr(server, "reconcile_group_cache", _fake)
    server._reconcile_all_group_caches()   # must not raise
    assert set(seen) == {"G1", "G2"}


def test_reconcile_all_is_off_by_default(fresh_settings, monkeypatch):
    monkeypatch.delenv("MM_CACHE_RECONCILE", raising=False)   # unset -> paused
    fresh_settings.displays["G1"] = Display()
    called = []
    monkeypatch.setattr(server, "reconcile_group_cache", lambda did: called.append(did))
    server._reconcile_all_group_caches()
    assert called == []   # gated off; no reconcile runs


def test_reconcile_works_after_stop_clears_the_playlist_name(fresh_settings, monkeypatch):
    """The reconcile located the render entry by currentPlaylistName, but a STOP
    clears that field while leaving renderedToken set -- so from that moment it
    returned immediately, every cycle, silently, and caches could never self-heal
    while a group was stopped. Which is exactly when filling them is safest.

    Observed on-wall 2026-09-20: renderedToken=4298b787efa0 with a READY entry for
    'PullTest', currentPlaylistName=None, 11 screens missing the segment, and zero
    precache grants for 24 minutes with nothing logged. Resolve the entry by TOKEN,
    which is the thing the clients are actually being asked to pull."""
    # The MM_CACHE_RECONCILE gate now lives in reconcile_decision (which
    # reconcile_group_cache obeys) rather than only in the process()-loop
    # wrapper, so a test asserting a grant has to switch the feature on.
    monkeypatch.setenv("MM_CACHE_RECONCILE", "1")
    monkeypatch.setattr(server, "precache_windows", {})
    d = Display()
    d.renderedToken = "tok"
    d.currentPlaylistName = None                    # STOP cleared it
    d.renders = {"PullTest": {"state": "READY", "token": "tok"}}
    fresh_settings.displays["G1"] = d
    monkeypatch.setattr(server, "clients_needing_precache", lambda did: {"ck1": ["tok_0"]})
    calls = []
    monkeypatch.setattr(server, "start_precache",
                        lambda group, token, urls, **k: calls.append((group, token, urls)))
    server.reconcile_group_cache("G1")
    assert len(calls) == 1, "a stopped group with a READY render must still self-heal"
    assert calls[0][1] == "tok"


def test_reconcile_still_requires_the_tokens_entry_to_be_ready(fresh_settings, monkeypatch):
    """Resolving by token must not weaken the readiness gate: a token whose entry
    is mid-render must not be pushed."""
    monkeypatch.setattr(server, "precache_windows", {})
    d = Display()
    d.renderedToken = "tok"
    d.currentPlaylistName = None
    d.renders = {"PullTest": {"state": "RENDERING", "token": "tok"}}
    fresh_settings.displays["G1"] = d
    monkeypatch.setattr(server, "clients_needing_precache", lambda did: {"ck1": ["tok_0"]})
    called = []
    monkeypatch.setattr(server, "start_precache", lambda *a, **k: called.append(a))
    server.reconcile_group_cache("G1")
    assert called == []


def test_reconcile_ignores_a_ready_entry_for_a_DIFFERENT_token(fresh_settings, monkeypatch):
    """Only the entry matching renderedToken counts -- another playlist being READY
    says nothing about the segments these clients are being asked to fetch."""
    monkeypatch.setattr(server, "precache_windows", {})
    d = Display()
    d.renderedToken = "tok"
    d.currentPlaylistName = None
    d.renders = {"Other": {"state": "READY", "token": "SOMETHING_ELSE"}}
    fresh_settings.displays["G1"] = d
    monkeypatch.setattr(server, "clients_needing_precache", lambda did: {"ck1": ["tok_0"]})
    called = []
    monkeypatch.setattr(server, "start_precache", lambda *a, **k: called.append(a))
    server.reconcile_group_cache("G1")
    assert called == []


# ---------------------------------------------------------------------------
# reconcile_decision(): why the reconcile did or did not grant.
#
# WHY THIS EXISTS (2026-09-26): two canaries sat with an empty cache and
# propagationPercent 0.0 while not one PRECACHE reached them for 14 minutes.
# reconcile_group_cache() has five separate early returns and logs on none of
# them, so from outside the server every candidate explanation fitted equally
# well -- MM_CACHE_RECONCILE unset, a PrecacheWindow wedged by a lost ack, a
# render entry not READY, nothing actually missing, or a grant delivered into a
# stale SockJS session. Each was argued and none could be checked.
#
# The decision is therefore factored out so that ONE function answers it and
# reconcile_group_cache() obeys that same answer. A debug endpoint that recomputed
# the reasons separately could drift from the behaviour it claims to explain,
# which would be worse than no endpoint at all.

def _ready_display(did, token, playlist="P"):
    d = Display()
    d.renderedToken = token
    d.renders = {playlist: {"token": token, "state": R.RENDER_READY}}
    return d


def test_decision_grant_when_ready_and_clients_missing(fresh_settings, monkeypatch):
    monkeypatch.setattr(server, "_expected_seg_keys_for_display", lambda d: {"tok_0"})
    monkeypatch.setenv("MM_CACHE_RECONCILE", "1")
    fresh_settings.displays["G1"] = _ready_display("G1", "tok")
    fresh_settings.clients["miss"] = _mk_client("G1", cached=[])
    dec = server.reconcile_decision("G1")
    assert dec["willGrant"] is True
    assert dec["reason"] == "grant"
    assert dec["needing"] == {"miss": ["tok_0"]}
    assert dec["token"] == "tok"


def test_decision_reports_the_env_gate_being_off(fresh_settings, monkeypatch):
    # The question that cost the most time: is the reconcile even running? It was
    # unanswerable from outside because the gate lives in _reconcile_all_group_caches
    # and nothing reports it.
    monkeypatch.setattr(server, "_expected_seg_keys_for_display", lambda d: {"tok_0"})
    monkeypatch.delenv("MM_CACHE_RECONCILE", raising=False)
    fresh_settings.displays["G1"] = _ready_display("G1", "tok")
    fresh_settings.clients["miss"] = _mk_client("G1", cached=[])
    dec = server.reconcile_decision("G1")
    assert dec["willGrant"] is False
    assert dec["reason"] == "disabled"
    # ...and it still reports WHAT WOULD have been granted, so an operator can see
    # the cost of leaving it off.
    assert dec["needing"] == {"miss": ["tok_0"]}


def test_decision_reports_a_window_still_draining(fresh_settings, monkeypatch):
    monkeypatch.setattr(server, "_expected_seg_keys_for_display", lambda d: {"tok_0"})
    monkeypatch.setenv("MM_CACHE_RECONCILE", "1")
    fresh_settings.displays["G1"] = _ready_display("G1", "tok")
    fresh_settings.clients["miss"] = _mk_client("G1", cached=[])
    from mosaicmesh.cache_pull import PrecacheWindow
    server.precache_windows["G1"] = PrecacheWindow(["other"], n=1)
    try:
        dec = server.reconcile_decision("G1")
        assert dec["willGrant"] is False
        assert dec["reason"] == "window-active"
    finally:
        server.precache_windows.pop("G1", None)


def test_decision_reports_no_rendered_token(fresh_settings, monkeypatch):
    monkeypatch.setenv("MM_CACHE_RECONCILE", "1")
    d = Display()
    d.renderedToken = None
    fresh_settings.displays["G1"] = d
    dec = server.reconcile_decision("G1")
    assert dec["willGrant"] is False
    assert dec["reason"] == "no-rendered-token"


def test_decision_reports_render_not_ready(fresh_settings, monkeypatch):
    monkeypatch.setattr(server, "_expected_seg_keys_for_display", lambda d: {"tok_0"})
    monkeypatch.setenv("MM_CACHE_RECONCILE", "1")
    d = Display()
    d.renderedToken = "tok"
    d.renders = {"P": {"token": "tok", "state": "STALE"}}
    fresh_settings.displays["G1"] = d
    dec = server.reconcile_decision("G1")
    assert dec["willGrant"] is False
    assert dec["reason"] == "render-not-ready"
    assert dec["renderState"] == "STALE"


def test_decision_reports_nothing_missing(fresh_settings, monkeypatch):
    monkeypatch.setattr(server, "_expected_seg_keys_for_display", lambda d: {"tok_0"})
    monkeypatch.setenv("MM_CACHE_RECONCILE", "1")
    fresh_settings.displays["G1"] = _ready_display("G1", "tok")
    fresh_settings.clients["ok"] = _mk_client("G1", cached=["tok_0"])
    dec = server.reconcile_decision("G1")
    assert dec["willGrant"] is False
    assert dec["reason"] == "nothing-missing"
    assert dec["needing"] == {}


def test_decision_reports_unknown_group(fresh_settings, monkeypatch):
    monkeypatch.setenv("MM_CACHE_RECONCILE", "1")
    dec = server.reconcile_decision("nope")
    assert dec["willGrant"] is False
    assert dec["reason"] == "no-display"


def test_decision_resolves_the_render_entry_by_token_not_playlist_name(fresh_settings, monkeypatch):
    # A STOP clears currentPlaylistName but leaves renderedToken set. Resolving by
    # name therefore returned None from that moment on and the reconcile silently
    # did nothing for as long as the group stayed stopped -- precisely when filling
    # caches is safest. The decision must key off the token.
    monkeypatch.setattr(server, "_expected_seg_keys_for_display", lambda d: {"tok_0"})
    monkeypatch.setenv("MM_CACHE_RECONCILE", "1")
    d = _ready_display("G1", "tok", playlist="PullTest")
    d.currentPlaylistName = None
    fresh_settings.displays["G1"] = d
    fresh_settings.clients["miss"] = _mk_client("G1", cached=[])
    dec = server.reconcile_decision("G1")
    assert dec["reason"] == "grant"
    assert dec["playlist"] == "PullTest"


def test_reconcile_group_cache_obeys_the_decision(fresh_settings, monkeypatch):
    # The whole point of factoring this out: the endpoint's explanation and the
    # actual behaviour cannot diverge.
    monkeypatch.setattr(server, "_expected_seg_keys_for_display", lambda d: {"tok_0"})
    monkeypatch.setenv("MM_CACHE_RECONCILE", "1")
    fresh_settings.displays["G1"] = _ready_display("G1", "tok")
    fresh_settings.clients["miss"] = _mk_client("G1", cached=[])
    calls = []
    monkeypatch.setattr(server, "start_precache",
                        lambda g, t, urls, **kw: calls.append((g, t, dict(urls))))
    server.reconcile_group_cache("G1")
    assert len(calls) == 1
    assert calls[0][0] == "G1" and calls[0][1] == "tok"
    assert list(calls[0][2]) == ["miss"]

    # ...and a declining reason grants nothing.
    calls.clear()
    fresh_settings.clients["miss"].cachedSegments = {"tok_0"}
    server.reconcile_group_cache("G1")
    assert calls == []
