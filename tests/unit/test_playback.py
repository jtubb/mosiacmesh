"""Unit tests for synchronized playback (playlist_index math + WS handlers)."""
import sys
from pathlib import Path
import argparse

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Import server cleanly (arg parsing is under __main__, so no patch needed)
import server


class TestPlaylistIndex:
    def test_empty_playlist_returns_none(self):
        assert server.playlist_index(0, [], False) is None

    def test_zero_total_duration_returns_none(self):
        assert server.playlist_index(100, [0, 0], False) is None

    def test_first_item(self):
        assert server.playlist_index(0, [1000, 2000], False) == {"index": 0, "offsetMs": 0}

    def test_within_second_item(self):
        assert server.playlist_index(1000, [1000, 2000], False) == {"index": 1, "offsetMs": 0}
        assert server.playlist_index(2500, [1000, 2000], False) == {"index": 1, "offsetMs": 1500}

    def test_non_loop_past_end_returns_none(self):
        assert server.playlist_index(3000, [1000, 2000], False) is None

    def test_loop_wraps(self):
        assert server.playlist_index(3000, [1000, 2000], True) == {"index": 0, "offsetMs": 0}
        assert server.playlist_index(4200, [1000, 2000], True) == {"index": 1, "offsetMs": 200}

    def test_negative_elapsed_clamps_to_start(self):
        assert server.playlist_index(-50, [1000, 2000], False) == {"index": 0, "offsetMs": 0}


import pytest
from unittest.mock import MagicMock


def _make_session(session_id="sess1"):
    s = MagicMock()
    s.id = session_id
    s.request = MagicMock()
    s.request.remote = "127.0.0.1"
    s.request.headers = {"User-Agent": "Test Browser"}
    return s


class TestSetPlaylist:
    def test_setplaylist_stores_items_and_broadcasts_preload(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        client = server.Client()
        client.displayID = "Default"
        mock_settings.clients["c1"] = client

        msg = {
            "SRC": "admin", "DEST": "SRV", "REQUEST": "SETPLAYLIST",
            "PAYLOAD": {
                "displayID": "Default",
                "loop": True,
                "items": [
                    {"id": "a", "file": "/media/server/a.jpg", "duration": 1000},
                    {"id": "b", "file": "/media/server/b.jpg", "duration": 2000},
                ],
            },
        }
        server.msg_response(msg, _make_session())

        disp = mock_settings.displays["Default"]
        assert disp.loop is True
        assert len(disp.mediaElements) == 2
        assert disp.mediaElements[0].file == "/media/server/a.jpg"
        assert disp.mediaElements[1].duration == 2000
        assert server.socketmanager.broadcast.call_count == 1


from unittest.mock import patch


class TestPlayStop:
    def _group_with_items(self, mock_settings):
        disp = mock_settings.displays["Default"]
        disp.mediaElements = []
        for f, d in [("/media/server/a.jpg", 1000), ("/media/server/b.jpg", 2000)]:
            me = server.MediaElement(); me.file = f; me.duration = d
            disp.mediaElements.append(me)
        disp.loop = True
        client = server.Client(); client.displayID = "Default"
        mock_settings.clients["c1"] = client
        return disp

    def test_play_sets_state_and_broadcasts(self, mock_settings):
        # Fresh start: PLAY now enters coordinated prepare (PREPARING state, PREPARE broadcast)
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = self._group_with_items(mock_settings)

        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "PLAY",
               "PAYLOAD": {"displayID": "Default"}}
        with patch("time.time", return_value=1000.0):
            server.msg_response(msg, _make_session())

        assert disp.action == server.PlayState.PREPARING
        assert disp.prepareId  # set by _begin_prepare
        assert server.socketmanager.broadcast.call_count == 1  # PREPARE broadcast

    def test_stop_resets_state_and_broadcasts(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = self._group_with_items(mock_settings)
        disp.action = server.PlayState.PLAY
        disp.currentFrame = 5

        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "STOP",
               "PAYLOAD": {"displayID": "Default"}}
        server.msg_response(msg, _make_session())

        assert disp.action == server.PlayState.STOP
        assert disp.currentFrame == 0
        # STOP broadcast to the group + the PLAYBACK_CHANGED admin broadcast (Task 5)
        assert server.socketmanager.broadcast.call_count == 2


class TestMidJoinSync:
    def test_new_client_in_playing_group_receives_preload_and_play(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.file = "/media/server/a.jpg"; me.duration = 1000
        disp.mediaElements = [me]
        disp.loop = True
        disp.action = server.PlayState.PLAY
        disp.playStartEpoch = 1000000

        client = server.Client(); client.displayID = "Default"
        mock_settings.clients["newc"] = client

        server.sync_new_client_to_group("newc", client)
        assert server.socketmanager.broadcast.call_count == 2

    def test_new_client_in_idle_group_receives_nothing(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        disp.action = server.PlayState.STOP

        client = server.Client(); client.displayID = "Default"
        server.sync_new_client_to_group("idlec", client)
        assert server.socketmanager.broadcast.call_count == 0


class TestPause:
    def _playing_group(self, mock_settings):
        disp = mock_settings.displays["Default"]
        disp.mediaElements = []
        for f, d in [("/media/server/a.jpg", 1000), ("/media/server/b.jpg", 2000)]:
            me = server.MediaElement(); me.file = f; me.duration = d
            disp.mediaElements.append(me)
        disp.loop = True
        disp.action = server.PlayState.PLAY
        disp.playStartEpoch = 1000000
        client = server.Client(); client.displayID = "Default"
        mock_settings.clients["c1"] = client
        return disp

    def test_pause_sets_state_offset_and_broadcasts(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = self._playing_group(mock_settings)
        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "PAUSE",
               "PAYLOAD": {"displayID": "Default"}}
        with patch("time.time", return_value=1002.5):  # 1002500 ms
            server.msg_response(msg, _make_session())
        assert disp.action == server.PlayState.PAUSE
        assert disp.pauseOffset == 2500  # 1002500 - 1000000
        # PAUSE broadcast to the group + the PLAYBACK_CHANGED admin broadcast (Task 5)
        assert server.socketmanager.broadcast.call_count == 2


class TestResume:
    def _group(self, mock_settings, action, pause_offset=0):
        disp = mock_settings.displays["Default"]
        disp.mediaElements = []
        for f, d in [("/media/server/a.jpg", 1000), ("/media/server/b.jpg", 2000)]:
            me = server.MediaElement(); me.file = f; me.duration = d
            disp.mediaElements.append(me)
        disp.loop = True
        disp.action = action
        disp.pauseOffset = pause_offset
        client = server.Client(); client.displayID = "Default"
        mock_settings.clients["c1"] = client
        return disp

    def test_play_resumes_from_pause(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = self._group(mock_settings, server.PlayState.PAUSE, pause_offset=2500)
        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "PLAY",
               "PAYLOAD": {"displayID": "Default"}}
        with patch("time.time", return_value=5000.0):  # 5000000 ms
            server.msg_response(msg, _make_session())
        assert disp.action == server.PlayState.PLAY
        assert disp.playStartEpoch == 4997500  # 5000000 - 2500 (resume)

    def test_play_from_stopped_starts_fresh(self, mock_settings):
        # Fresh start from STOP: enters coordinated prepare (PREPARING state, PREPARE broadcast)
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = self._group(mock_settings, server.PlayState.STOP, pause_offset=2500)
        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "PLAY",
               "PAYLOAD": {"displayID": "Default"}}
        with patch("time.time", return_value=5000.0):
            server.msg_response(msg, _make_session())
        assert disp.action == server.PlayState.PREPARING
        assert disp.prepareId  # set by _begin_prepare; clock not started yet


class TestScriptPlayback:
    def _script_group(self, mock_settings):
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "anim"; me.file = "bouncingBalls"
        me.duration = 10000; me.playmode = server.PlayMode.SCRIPT
        disp.mediaElements = [me]; disp.loop = True; disp.action = server.PlayState.STOP
        client = server.Client(); client.displayID = "Default"
        client.synced = True  # synced so _begin_prepare sends PREPARE to this client
        mock_settings.clients["c1"] = client
        return disp

    def test_setplaylist_maps_script_playmode(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "SETPLAYLIST", "PAYLOAD": {
            "displayID": "Default", "loop": False,
            "items": [{"id": "a", "file": "bouncingBalls", "duration": 10000, "playmode": "SCRIPT"}]}}
        server.msg_response(msg, _make_session())
        assert mock_settings.displays["Default"].mediaElements[0].playmode == server.PlayMode.SCRIPT

    def test_play_script_broadcasts_with_playmode_and_no_render_gate(self, mock_settings):
        import jsonpickle
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        self._script_group(mock_settings)
        ret = server.msg_response({"SRC": "a", "DEST": "SRV", "REQUEST": "PLAY",
                                   "PAYLOAD": {"displayID": "Default"}}, _make_session())
        assert jsonpickle.decode(ret)["PAYLOAD"] == "SUCCESS"        # not RENDER_REQUIRED
        # 2 broadcasts: PREPARE (per-client) + PLAYBACK_CHANGED (state update)
        assert server.socketmanager.broadcast.call_count == 2
        sent = jsonpickle.decode(server.socketmanager.broadcast.call_args_list[0].args[0])
        assert sent["PAYLOAD"]["items"][0]["playmode"] == "SCRIPT"
        assert sent["PAYLOAD"]["items"][0]["file"] == "bouncingBalls"


class TestSilentPlayMiss:
    """A client can sit online, clock-ready, calibrated and cache-complete while
    holding NO content, and nothing notices.

    Seen on-wall 2026-09-19 (sign1screen15): its PLAY landed ~300-500ms before a
    page boot and was discarded, and after a clean REGISTER no further PLAY ever
    arrived -- despite display.action == PLAY, mediaElements populated, and the
    client being in the group. `_deliver` reported no miss (the targeted send
    succeeded into a session the client was no longer reading), so there was no
    warning, no retry and no recovery path. The screen stayed dark for hours.

    Recording what the server SENT cannot detect this -- the send "succeeded".
    The violated invariant is that the client has ACKNOWLEDGED the group's
    current playback epoch, so that is what is checked.
    """

    def _playing(self, mock_settings, epoch=1000000):
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.file = "/media/server/a.mp4"; me.duration = 1000
        disp.mediaElements = [me]
        disp.action = server.PlayState.PLAY
        disp.playStartEpoch = epoch
        c = server.Client(); c.displayID = "Default"; c.isOnline = True
        c.supportsPlayAck = True        # modern client JS; the False case is its own test
        mock_settings.clients["c1"] = c
        return disp, c

    def test_client_that_never_acked_is_healed_after_the_grace(self, mock_settings):
        server.settings = mock_settings
        disp, c = self._playing(mock_settings)
        assert server.needs_play_reissue(c, disp, 1000000 + 60000) is True

    def test_client_that_acked_the_current_epoch_is_left_alone(self, mock_settings):
        server.settings = mock_settings
        disp, c = self._playing(mock_settings)
        c.lastPlayAck = disp.playStartEpoch
        assert server.needs_play_reissue(c, disp, 1000000 + 60000) is False

    def test_a_stale_ack_from_a_previous_playback_does_not_count(self, mock_settings):
        server.settings = mock_settings
        disp, c = self._playing(mock_settings)
        c.lastPlayAck = 999999           # acked an EARLIER epoch
        assert server.needs_play_reissue(c, disp, 1000000 + 60000) is True

    def test_grace_period_protects_a_normal_start(self, mock_settings):
        """Right after PLAY every client is legitimately un-acked."""
        server.settings = mock_settings
        disp, c = self._playing(mock_settings)
        assert server.needs_play_reissue(c, disp, 1000000 + 500) is False

    def test_throttled_so_one_client_cannot_be_spammed(self, mock_settings):
        server.settings = mock_settings
        disp, c = self._playing(mock_settings)
        now = 1000000 + 60000
        assert server.needs_play_reissue(c, disp, now) is True
        c.lastPlayHealMs = now                       # just healed
        assert server.needs_play_reissue(c, disp, now + 1000) is False
        assert server.needs_play_reissue(c, disp, now + 120000) is True

    def test_offline_client_is_not_healed(self, mock_settings):
        server.settings = mock_settings
        disp, c = self._playing(mock_settings)
        c.isOnline = False
        assert server.needs_play_reissue(c, disp, 1000000 + 60000) is False

    def test_idle_group_is_not_healed(self, mock_settings):
        server.settings = mock_settings
        disp, c = self._playing(mock_settings)
        disp.action = server.PlayState.STOP
        assert server.needs_play_reissue(c, disp, 1000000 + 60000) is False

    def test_group_with_no_media_is_not_healed(self, mock_settings):
        server.settings = mock_settings
        disp, c = self._playing(mock_settings)
        disp.mediaElements = []
        assert server.needs_play_reissue(c, disp, 1000000 + 60000) is False

    def test_missing_epoch_is_not_healed(self, mock_settings):
        server.settings = mock_settings
        disp, c = self._playing(mock_settings)
        disp.playStartEpoch = 0
        assert server.needs_play_reissue(c, disp, 1000000 + 60000) is False

    def test_playack_handler_records_the_epoch(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp, c = self._playing(mock_settings)
        session = MagicMock()
        session.request.headers = {"User-Agent": "x"}
        session.request.remote = "1.1.1.1"
        server.msg_response({"SRC": "c1", "DEST": "SRV", "REQUEST": "PLAYACK",
                             "PAYLOAD": {"startEpoch": disp.playStartEpoch}}, session)
        assert c.lastPlayAck == disp.playStartEpoch
        assert server.needs_play_reissue(c, disp, 1000000 + 60000) is False

    def test_a_client_that_cannot_ack_is_NEVER_healed(self, mock_settings):
        """Old client JS does not send PLAYACK, so it can never satisfy the ack
        check. Without this guard the sweep would re-issue PLAY to every healthy
        pre-upgrade screen every throttle period -- and PLAY sets shownIndex=-1
        to force a fresh mount, so that is a visible re-mount on the whole wall.
        Capability is declared at REGISTER, which happens regardless of PLAY, so
        it does not have the bootstrap problem an ack-based probe would."""
        server.settings = mock_settings
        disp, c = self._playing(mock_settings)
        c.supportsPlayAck = False
        assert server.needs_play_reissue(c, disp, 1000000 + 60000) is False

    def test_register_records_the_playack_capability(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        session = MagicMock()
        session.request.headers = {"User-Agent": "x"}
        session.request.remote = "1.1.1.1"
        server.msg_response({"SRC": "newk", "DEST": "SRV", "REQUEST": "REGISTER",
                             "PAYLOAD": {"width": 768, "height": 1024, "playAck": True}}, session)
        assert server.settings.clients["newk"].supportsPlayAck is True

    def test_register_without_the_flag_marks_the_client_unable(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        session = MagicMock()
        session.request.headers = {"User-Agent": "x"}
        session.request.remote = "1.1.1.1"
        server.msg_response({"SRC": "oldk", "DEST": "SRV", "REQUEST": "REGISTER",
                             "PAYLOAD": {"width": 768, "height": 1024}}, session)
        assert server.settings.clients["oldk"].supportsPlayAck is False
