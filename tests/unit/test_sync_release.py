"""Sync-gated release: a client that armed but is withholding READY (clock not
yet converged) is NOT released early by _maybe_release, but IS released best-
effort by _release_expired_prepares after the (extended) PREPARE timeout; a
client still awaiting a human tap (armPending) keeps holding the GO."""
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

import time
import pytest
from mosaicmesh.state import Settings, Display, Client, PlayState


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev


def _preparing_group(settings):
    d = Display(); d.action = PlayState.PREPARING
    d.readyClients = set(); d.armPending = set(); d.prepareDeadline = 0
    settings.displays["G1"] = d
    c = Client(); c.displayID = "G1"; c.isOnline = True; c.synced = True
    settings.clients["c1"] = c
    return d


def test_maybe_release_holds_until_ready(fresh_settings, monkeypatch):
    d = _preparing_group(fresh_settings)
    released = []
    monkeypatch.setattr(server, "_release_group", lambda did: released.append(did))
    server._maybe_release("G1")            # c1 online, not in readyClients
    assert released == []                  # not all-ready -> no GO
    d.readyClients.add("c1")
    server._maybe_release("G1")
    assert released == ["G1"]             # now all-ready -> GO


def test_expired_prepare_releases_best_effort(fresh_settings, monkeypatch):
    d = _preparing_group(fresh_settings)
    released = []
    monkeypatch.setattr(server, "_release_group", lambda did: released.append(did))
    d.prepareDeadline = int(time.time() * 1000) - 1   # past
    server._release_expired_prepares()
    assert released == ["G1"]             # armed-but-not-ready -> best-effort release


def test_expired_prepare_waits_for_arm_tap(fresh_settings, monkeypatch):
    d = _preparing_group(fresh_settings)
    released = []
    monkeypatch.setattr(server, "_release_group", lambda did: released.append(did))
    d.armPending.add("c1")                # still needs a human tap
    d.prepareDeadline = int(time.time() * 1000) - 1
    server._release_expired_prepares()
    assert released == []                  # NEEDS_ARM hold preserved
