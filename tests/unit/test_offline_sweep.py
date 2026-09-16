"""Offline detection sweep (audit 1.1).

The old process() loop did this:

    for client in clients:
        if client.isOnline:
            client.lastSeen = current_time        # refresh EVERY online client
    for client in clients:
        if (current_time - client.lastSeen) > 60 and client.isOnline:   # always 0.0

...so the staleness delta was tautologically zero and the silence path to
CLIENTS_WENT_OFFLINE was unreachable. A client only went offline on a clean TCP
FIN, so an iPad that dropped WiFi without one stayed isOnline=True forever --
the "discovery API `online` field lies" trap.

The fix cannot simply delete the refresh: when the clock beat is enabled,
GoTime._sync returns early WITHOUT calling _wsCall, so a converged idle client
sends the server nothing at all (the 1s 'hb' only fires while playback.active).
Message recency alone would mark the whole idle fleet offline.

So liveness is keyed on the SockJS session instead: a client with a live session
is genuinely connected (refresh it), and one whose session is gone goes offline
after a grace period that covers the reconnect gap.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import server


class _SM:
    """Minimal socketmanager stand-in: .get() resolves only live session ids."""

    def __init__(self, live_ids):
        self._live = set(live_ids)
        self.broadcasts = []

    def get(self, session_id, default=None):
        return object() if session_id in self._live else default

    def broadcast(self, msg):
        self.broadcasts.append(msg)


def _client(mock_settings, key, session_id, last_seen, online=True):
    c = server.Client()
    c.clientID = session_id
    c.friendlyName = key
    c.displayID = "Default"
    c.isOnline = online
    c.lastSeen = last_seen
    mock_settings.clients[key] = c
    return c


def test_dead_session_past_grace_goes_offline(mock_settings, monkeypatch):
    now = 10_000.0
    c = _client(mock_settings, "ghost", "sess-dead", last_seen=now - 120)
    monkeypatch.setattr(server, "settings", mock_settings, raising=False)
    monkeypatch.setattr(server, "socketmanager", _SM(live_ids=[]), raising=False)

    stale = server.sweep_offline_clients(now)

    assert c.isOnline is False
    assert [d["clientKey"] for d in stale] == ["ghost"]


def test_live_session_stays_online_even_when_silent(mock_settings, monkeypatch):
    """Beat-mode regression guard: an idle client sends NOTHING for minutes.

    GoTime._sync short-circuits before _wsCall while beats are fresh, and the 1s
    'hb' only fires during playback -- so lastSeen legitimately goes stale on a
    perfectly healthy idle screen. Its live session must keep it online.
    """
    now = 10_000.0
    c = _client(mock_settings, "idle", "sess-live", last_seen=now - 600)
    monkeypatch.setattr(server, "settings", mock_settings, raising=False)
    monkeypatch.setattr(server, "socketmanager", _SM(live_ids=["sess-live"]), raising=False)

    stale = server.sweep_offline_clients(now)

    assert c.isOnline is True
    assert stale == []
    assert c.lastSeen == now, "a live session is real liveness evidence; refresh it"


def test_dead_session_within_grace_is_not_dropped(mock_settings, monkeypatch):
    """The reconnect gap: a reloading iPad has no session for ~100ms-2s."""
    now = 10_000.0
    c = _client(mock_settings, "reconnecting", "sess-old", last_seen=now - 5)
    monkeypatch.setattr(server, "settings", mock_settings, raising=False)
    monkeypatch.setattr(server, "socketmanager", _SM(live_ids=[]), raising=False)

    stale = server.sweep_offline_clients(now)

    assert c.isOnline is True
    assert stale == []


def test_already_offline_client_is_not_re_reported(mock_settings, monkeypatch):
    now = 10_000.0
    _client(mock_settings, "long-gone", "sess-x", last_seen=now - 9999, online=False)
    monkeypatch.setattr(server, "settings", mock_settings, raising=False)
    monkeypatch.setattr(server, "socketmanager", _SM(live_ids=[]), raising=False)

    assert server.sweep_offline_clients(now) == []


def test_no_socketmanager_falls_back_to_message_recency(mock_settings, monkeypatch):
    """Before the sockjs manager exists (early boot / tests), don't mass-offline."""
    now = 10_000.0
    fresh = _client(mock_settings, "fresh", "s1", last_seen=now - 5)
    stale_c = _client(mock_settings, "stale", "s2", last_seen=now - 120)
    monkeypatch.setattr(server, "settings", mock_settings, raising=False)
    monkeypatch.setattr(server, "socketmanager", None, raising=False)

    stale = server.sweep_offline_clients(now)

    assert fresh.isOnline is True
    assert stale_c.isOnline is False
    assert [d["clientKey"] for d in stale] == ["stale"]
