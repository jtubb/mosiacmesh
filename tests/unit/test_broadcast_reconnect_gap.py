"""Reconnect-gap delivery without fleet amplification (audit 1.2).

_deliver fell back to socketmanager.broadcast() on a targeted-send miss, sending
ONE client's per-client message to EVERY session. The docstring argues that is
bounded ("only for the affected client"), but its own field note records the
storm case: "19 of 22 reconnected iPads missed PREPARE" -- 19 clients in the gap
at once means 19 full-fleet broadcasts, so every session absorbs ~19x the payload
in a burst. That is the mmws rx-reassembly overflow -> ws teardown -> reconnect
loop path, and it feeds itself: the overflow causes more reconnects.

Fix: a miss means the iPad is mid-reconnect and its clientID is about to be
updated by REGISTER. Retry the TARGETED send across that window (re-reading
clientID each time) instead of blasting the fleet, and keep the broadcast only
as a last resort if the client never comes back.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import server
from mosaicmesh import broadcast as B


class _Session:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


class _SM:
    """socketmanager stand-in tracking targeted sends vs fleet broadcasts."""

    def __init__(self, live=None):
        self.sessions = dict(live or {})
        self.broadcasts = []

    def get(self, session_id, default=None):
        return self.sessions.get(session_id, default)

    def broadcast(self, msg):
        self.broadcasts.append(msg)


def _client(mock_settings, key, session_id):
    c = server.Client()
    c.clientID = session_id
    mock_settings.clients[key] = c
    return c


@pytest.fixture
def wired(mock_settings, monkeypatch):
    monkeypatch.setattr(server, "settings", mock_settings, raising=False)
    sm = _SM()
    monkeypatch.setattr(server, "socketmanager", sm, raising=False)
    # Keep the retry window short so tests stay fast.
    monkeypatch.setattr(B, "_GAP_RETRY_ATTEMPTS", 4, raising=False)
    monkeypatch.setattr(B, "_GAP_RETRY_DELAY", 0.02, raising=False)
    return sm, mock_settings


def test_live_session_gets_targeted_send_and_no_broadcast(wired):
    sm, settings = wired
    sess = _Session()
    sm.sessions["s-live"] = sess
    c = _client(settings, "ipad1", "s-live")

    B._deliver("ipad1", "MSG", c)

    assert sess.sent == ["MSG"]
    assert sm.broadcasts == []


def test_miss_without_a_running_loop_falls_back_immediately(wired):
    """Sync callers (and existing tests) keep the original behavior."""
    sm, settings = wired
    c = _client(settings, "ipad1", "s-dead")

    B._deliver("ipad1", "MSG", c)

    assert sm.broadcasts == ["MSG"], "no loop -> must still reach the client"


async def test_miss_retries_targeted_and_never_broadcasts_when_client_returns(wired):
    """The reconnect gap: REGISTER swaps clientID, retry then lands targeted."""
    sm, settings = wired
    c = _client(settings, "ipad1", "s-old")

    B._deliver("ipad1", "MSG", c)
    assert sm.broadcasts == [], "must not blast the fleet during the gap"

    # The iPad re-registers on a NEW session partway through the window.
    await asyncio.sleep(0.03)
    new_sess = _Session()
    sm.sessions["s-new"] = new_sess
    c.clientID = "s-new"

    await asyncio.sleep(0.2)
    assert new_sess.sent == ["MSG"], "retry did not follow the new session"
    assert sm.broadcasts == [], "fell back to broadcast despite a successful retry"


async def test_client_that_never_returns_falls_back_exactly_once(wired):
    sm, settings = wired
    _client(settings, "ipad1", "s-dead")

    B._deliver("ipad1", "MSG", settings.clients["ipad1"])
    await asyncio.sleep(0.3)

    assert sm.broadcasts == ["MSG"], "last-resort fallback must fire exactly once"


async def test_storm_does_not_multiply_fleet_broadcasts(wired):
    """20 clients in the gap must not produce 20 fleet broadcasts."""
    sm, settings = wired
    for i in range(20):
        _client(settings, f"ipad{i}", f"s-old-{i}")

    for i in range(20):
        B._deliver(f"ipad{i}", f"MSG{i}", settings.clients[f"ipad{i}"])

    # All of them come back, as happens after a mass reload.
    await asyncio.sleep(0.03)
    for i in range(20):
        sess = _Session()
        sm.sessions[f"s-new-{i}"] = sess
        settings.clients[f"ipad{i}"].clientID = f"s-new-{i}"

    await asyncio.sleep(0.25)
    assert sm.broadcasts == [], "reconnect storm amplified into fleet broadcasts"
    for i in range(20):
        assert sm.sessions[f"s-new-{i}"].sent == [f"MSG{i}"]
