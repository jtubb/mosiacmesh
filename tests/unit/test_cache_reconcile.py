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

def _mk_client(did, mode="lighttpd-localhost", online=True, ip="1.2.3.4", cached=None):
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
