"""Unit tests for playlist scheduling."""
import sys, json, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import pytest
import server
import jsonpickle


def _make_session(session_id="sess1"):
    s = MagicMock()
    s.id = session_id
    s.request = MagicMock()
    s.request.remote = "127.0.0.1"
    s.request.headers = {"User-Agent": "Test Browser"}
    return s


def _schedule(**kw):
    s = server.Schedule()
    s.id = kw.get("id", "s1"); s.name = kw.get("name", "S")
    s.playlistName = kw.get("playlistName", "P"); s.displayID = kw.get("displayID", "Default")
    s.priority = kw.get("priority", 0); s.enabled = kw.get("enabled", True)
    s.freq = kw.get("freq", "DAILY"); s.interval = kw.get("interval", 1)
    s.byweekday = kw.get("byweekday", []); s.dtstart = kw.get("dtstart", "2026-01-01")
    s.end = kw.get("end", {"type": "never"}); s.exdates = kw.get("exdates", [])
    s.startTime = kw.get("startTime", "09:00"); s.endTime = kw.get("endTime", "17:00")
    return s


class TestScheduleModel:
    def test_settings_has_schedules(self):
        assert isinstance(server.Settings().schedules, dict)

    def test_display_has_default_playlist(self):
        d = server.Display()
        assert d.defaultPlaylistName is None
        assert d.scheduledEntryId is None
        assert d.scheduledPlaying is False

    def test_schedule_round_trips(self):
        s = _schedule(byweekday=[0, 2], end={"type": "count", "count": 5})
        dec = jsonpickle.decode(jsonpickle.encode(s))
        assert dec.freq == "DAILY" and dec.byweekday == [0, 2]
        assert dec.end["count"] == 5

    def test_migrate_backfills_schedules_and_display_fields(self, mock_settings):
        del mock_settings.schedules
        for d in mock_settings.displays.values():
            if hasattr(d, "defaultPlaylistName"):
                del d.defaultPlaylistName
        server.settings = mock_settings
        server.migrate_client_objects()
        assert mock_settings.schedules == {}
        for d in mock_settings.displays.values():
            assert d.defaultPlaylistName is None
            assert d.scheduledEntryId is None and d.scheduledPlaying is False


class TestScheduleActiveAt:
    def _at(self, **kw):
        return datetime.datetime(kw["y"], kw["mo"], kw["d"], kw.get("h", 12), kw.get("mi", 0))

    def test_daily_inside_window(self):
        s = _schedule(freq="DAILY", dtstart="2026-01-01", startTime="09:00", endTime="17:00")
        assert server.schedule_active_at(s, self._at(y=2026, mo=6, d=1, h=12)) is True

    def test_daily_outside_window(self):
        s = _schedule(freq="DAILY", startTime="09:00", endTime="17:00")
        assert server.schedule_active_at(s, self._at(y=2026, mo=6, d=1, h=8)) is False
        assert server.schedule_active_at(s, self._at(y=2026, mo=6, d=1, h=18)) is False

    def test_before_dtstart(self):
        s = _schedule(freq="DAILY", dtstart="2026-06-15")
        assert server.schedule_active_at(s, self._at(y=2026, mo=6, d=1, h=12)) is False

    def test_weekly_only_selected_days(self):
        # 2026-06-01 is a Monday(0); 2026-06-02 Tuesday(1)
        s = _schedule(freq="WEEKLY", byweekday=[0], dtstart="2026-01-01")
        assert server.schedule_active_at(s, self._at(y=2026, mo=6, d=1, h=12)) is True
        assert server.schedule_active_at(s, self._at(y=2026, mo=6, d=2, h=12)) is False

    def test_interval_every_two_days(self):
        s = _schedule(freq="DAILY", interval=2, dtstart="2026-06-01")
        assert server.schedule_active_at(s, self._at(y=2026, mo=6, d=1, h=12)) is True   # day 0
        assert server.schedule_active_at(s, self._at(y=2026, mo=6, d=2, h=12)) is False  # day 1
        assert server.schedule_active_at(s, self._at(y=2026, mo=6, d=3, h=12)) is True   # day 2

    def test_until_end(self):
        s = _schedule(freq="DAILY", dtstart="2026-06-01", end={"type": "until", "untilDate": "2026-06-02"})
        assert server.schedule_active_at(s, self._at(y=2026, mo=6, d=2, h=12)) is True
        assert server.schedule_active_at(s, self._at(y=2026, mo=6, d=5, h=12)) is False

    def test_count_end(self):
        s = _schedule(freq="DAILY", dtstart="2026-06-01", end={"type": "count", "count": 2})
        assert server.schedule_active_at(s, self._at(y=2026, mo=6, d=2, h=12)) is True   # 2nd occ
        assert server.schedule_active_at(s, self._at(y=2026, mo=6, d=3, h=12)) is False  # 3rd

    def test_exdate_skips_day(self):
        s = _schedule(freq="DAILY", dtstart="2026-06-01", exdates=["2026-06-02"])
        assert server.schedule_active_at(s, self._at(y=2026, mo=6, d=2, h=12)) is False
        assert server.schedule_active_at(s, self._at(y=2026, mo=6, d=3, h=12)) is True

    def test_tolerates_non_dict_end(self):
        s = _schedule(freq="DAILY", dtstart="2026-06-01", startTime="00:00", endTime="23:59")
        s.end = "garbage"   # malformed -> must not raise, treated as never-ending
        assert server.schedule_active_at(s, self._at(y=2026, mo=6, d=5, h=12)) is True


class TestScheduleCRUD:
    def _save(self, name="S", sid=None, **kw):
        payload = {"name": name, "playlistName": kw.get("playlistName", "P"),
                   "displayID": kw.get("displayID", "Default"), "priority": kw.get("priority", 0),
                   "enabled": kw.get("enabled", True), "freq": kw.get("freq", "DAILY"),
                   "interval": 1, "byweekday": kw.get("byweekday", []), "dtstart": "2026-01-01",
                   "end": {"type": "never"}, "exdates": [], "startTime": kw.get("startTime", "09:00"),
                   "endTime": kw.get("endTime", "17:00")}
        if sid is not None:
            payload["id"] = sid
        return jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "SAVE_SCHEDULE", "PAYLOAD": payload}, _make_session()))

    def test_save_generates_id_and_get(self, mock_settings):
        server.settings = mock_settings
        resp = self._save(name="Morning")
        sid = resp["PAYLOAD"]["id"]
        assert sid and sid in mock_settings.schedules
        got = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "GET_SCHEDULE", "PAYLOAD": {"id": sid}}, _make_session()))
        assert got["PAYLOAD"]["name"] == "Morning" and got["PAYLOAD"]["startTime"] == "09:00"

    def test_save_upserts_by_id(self, mock_settings):
        server.settings = mock_settings
        sid = self._save(name="A")["PAYLOAD"]["id"]
        self._save(name="B", sid=sid)
        assert len(mock_settings.schedules) == 1 and mock_settings.schedules[sid].name == "B"

    def test_save_rejects_bad_window(self, mock_settings):
        server.settings = mock_settings
        resp = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "SAVE_SCHEDULE",
             "PAYLOAD": {"name": "Y", "playlistName": "P", "displayID": "Default", "freq": "DAILY",
                         "interval": 1, "byweekday": [], "dtstart": "2026-01-01", "end": {"type": "never"},
                         "exdates": [], "startTime": "17:00", "endTime": "09:00"}}, _make_session()))
        assert "error" in resp["PAYLOAD"]

    def test_list_includes_activenow(self, mock_settings):
        server.settings = mock_settings
        self._save(name="AllDay", startTime="00:00", endTime="23:59")
        rows = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "LIST_SCHEDULES", "PAYLOAD": {}}, _make_session()))["PAYLOAD"]
        assert len(rows) == 1 and "activeNow" in rows[0]

    def test_delete(self, mock_settings):
        server.settings = mock_settings
        sid = self._save()["PAYLOAD"]["id"]
        server.msg_response({"SRC": "a", "DEST": "SRV", "REQUEST": "DELETE_SCHEDULE",
                             "PAYLOAD": {"id": sid}}, _make_session())
        assert sid not in mock_settings.schedules

    def test_get_unknown_error(self, mock_settings):
        server.settings = mock_settings
        resp = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "GET_SCHEDULE", "PAYLOAD": {"id": "nope"}}, _make_session()))
        assert resp["PAYLOAD"] == {"error": "not found"}

    def test_group_default_set_and_get(self, mock_settings):
        server.settings = mock_settings
        server.msg_response({"SRC": "a", "DEST": "SRV", "REQUEST": "SET_GROUP_DEFAULT",
                             "PAYLOAD": {"displayID": "Default", "playlistName": "P"}}, _make_session())
        assert mock_settings.displays["Default"].defaultPlaylistName == "P"
        rows = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "GET_GROUP_DEFAULTS", "PAYLOAD": {}}, _make_session()))["PAYLOAD"]
        assert {"displayID": "Default", "defaultPlaylistName": "P"} in rows
        server.msg_response({"SRC": "a", "DEST": "SRV", "REQUEST": "SET_GROUP_DEFAULT",
                             "PAYLOAD": {"displayID": "Default", "playlistName": ""}}, _make_session())
        assert mock_settings.displays["Default"].defaultPlaylistName is None

    def test_save_rejects_bad_dtstart(self, mock_settings):
        server.settings = mock_settings
        resp = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "SAVE_SCHEDULE",
             "PAYLOAD": {"name": "Z", "playlistName": "P", "displayID": "Default", "freq": "DAILY",
                         "interval": 1, "byweekday": [], "dtstart": "not-a-date", "end": {"type": "never"},
                         "exdates": [], "startTime": "09:00", "endTime": "17:00"}}, _make_session()))
        assert "error" in resp["PAYLOAD"]
        assert mock_settings.schedules == {}   # not saved

    def test_save_rejects_bad_freq(self, mock_settings):
        server.settings = mock_settings
        resp = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "SAVE_SCHEDULE",
             "PAYLOAD": {"name": "Z", "playlistName": "P", "displayID": "Default", "freq": "NOPE",
                         "interval": 1, "byweekday": [], "dtstart": "2026-01-01", "end": {"type": "never"},
                         "exdates": [], "startTime": "09:00", "endTime": "17:00"}}, _make_session()))
        assert "error" in resp["PAYLOAD"]


class TestEvaluator:
    def _setup(self, mock_settings, monkeypatch, default=None):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        pl = server.Playlist(); pl.name = "P"; pl.loop = True
        pl.items = [{"id": "a", "file": "/media/server/images/x.jpg", "duration": 5,
                     "playmode": "FULL", "backgroundColor": "#000000",
                     "startEffect": None, "endEffect": None}]
        mock_settings.playlists = {"P": pl}
        c = server.Client(); c.displayID = "Default"
        mock_settings.clients = {"c1": c}
        disp = mock_settings.displays["Default"]
        disp.scheduledEntryId = None; disp.scheduledPlaying = False
        disp.defaultPlaylistName = default
        return disp

    def _no_real_render(self, monkeypatch):
        monkeypatch.setattr(server.asyncio, "ensure_future",
                            lambda coro: (coro.close() if hasattr(coro, "close") else None))

    def test_window_open_assigns_and_plays(self, mock_settings, monkeypatch):
        # PT-T5: FULL is renderable + render-gated. Seed a READY registry entry so
        # evaluate_schedules sees is_playlist_ready=True and proceeds to PLAY.
        from mosaicmesh import render as R
        disp = self._setup(mock_settings, monkeypatch); self._no_real_render(monkeypatch)
        # Seed READY entry for playlist "P" on "Default" so the render gate passes.
        elements = R._build_media_elements(mock_settings.playlists["P"].items)
        tok = R.render_token(elements, "Default")
        R._set_render_state(disp, "P", R.RENDER_READY, token=tok)
        mock_settings.schedules = {"s1": _schedule(id="s1", playlistName="P", displayID="Default",
                                                   startTime="00:00", endTime="23:59")}
        server.evaluate_schedules(datetime.datetime(2026, 6, 1, 12, 0))
        assert disp.scheduledEntryId == "s1"
        assert disp.action == server.PlayState.PLAY
        assert disp.scheduledPlaying is True

    def test_window_closed_no_default_stops(self, mock_settings, monkeypatch):
        disp = self._setup(mock_settings, monkeypatch); self._no_real_render(monkeypatch)
        disp.scheduledEntryId = "s1"; disp.scheduledPlaying = True; disp.action = server.PlayState.PLAY
        mock_settings.schedules = {"s1": _schedule(id="s1", playlistName="P", displayID="Default",
                                                   startTime="09:00", endTime="17:00")}
        server.evaluate_schedules(datetime.datetime(2026, 6, 1, 20, 0))
        assert disp.scheduledEntryId is None
        assert disp.action == server.PlayState.STOP

    def test_window_closed_with_default_plays_default(self, mock_settings, monkeypatch):
        # PT-T5: FULL is renderable + render-gated. Seed a READY registry entry for the
        # default playlist "P" so evaluate_schedules proceeds to PLAY when the schedule
        # window is closed and the default playlist takes over.
        from mosaicmesh import render as R
        disp = self._setup(mock_settings, monkeypatch, default="P"); self._no_real_render(monkeypatch)
        # Seed READY entry for playlist "P" on "Default" so the render gate passes.
        elements = R._build_media_elements(mock_settings.playlists["P"].items)
        tok = R.render_token(elements, "Default")
        R._set_render_state(disp, "P", R.RENDER_READY, token=tok)
        mock_settings.schedules = {"s1": _schedule(id="s1", playlistName="P", displayID="Default",
                                                   startTime="09:00", endTime="17:00")}
        server.evaluate_schedules(datetime.datetime(2026, 6, 1, 20, 0))
        assert disp.scheduledEntryId == "__default__:P"
        assert disp.action == server.PlayState.PLAY

    def test_active_schedule_outranks_default(self, mock_settings, monkeypatch):
        disp = self._setup(mock_settings, monkeypatch, default="P"); self._no_real_render(monkeypatch)
        mock_settings.schedules = {"s1": _schedule(id="s1", playlistName="P", displayID="Default",
                                                   startTime="00:00", endTime="23:59")}
        server.evaluate_schedules(datetime.datetime(2026, 6, 1, 12, 0))
        assert disp.scheduledEntryId == "s1"

    def test_priority_winner(self, mock_settings, monkeypatch):
        disp = self._setup(mock_settings, monkeypatch); self._no_real_render(monkeypatch)
        mock_settings.schedules = {
            "lo": _schedule(id="lo", priority=1, playlistName="P", displayID="Default", startTime="00:00", endTime="23:59"),
            "hi": _schedule(id="hi", priority=5, playlistName="P", displayID="Default", startTime="00:00", endTime="23:59")}
        server.evaluate_schedules(datetime.datetime(2026, 6, 1, 12, 0))
        assert disp.scheduledEntryId == "hi"

    def test_idempotent_no_double_play(self, mock_settings, monkeypatch):
        disp = self._setup(mock_settings, monkeypatch); self._no_real_render(monkeypatch)
        mock_settings.schedules = {"s1": _schedule(id="s1", playlistName="P", displayID="Default",
                                                   startTime="00:00", endTime="23:59")}
        server.evaluate_schedules(datetime.datetime(2026, 6, 1, 12, 0))
        server.socketmanager.broadcast.reset_mock()
        server.evaluate_schedules(datetime.datetime(2026, 6, 1, 12, 0, 5))
        assert server.socketmanager.broadcast.call_count == 0

    def test_disabled_schedule_is_ignored(self, mock_settings, monkeypatch):
        disp = self._setup(mock_settings, monkeypatch); self._no_real_render(monkeypatch)
        mock_settings.schedules = {"s1": _schedule(id="s1", playlistName="P", displayID="Default",
                                                   enabled=False, startTime="00:00", endTime="23:59")}
        server.evaluate_schedules(datetime.datetime(2026, 6, 1, 12, 0))
        assert disp.scheduledEntryId is None        # disabled -> not active
        assert disp.action != server.PlayState.PLAY

    def test_one_bad_group_does_not_block_others(self, mock_settings, monkeypatch):
        # PT-T5: FULL is renderable + render-gated. Seed a READY registry entry for the
        # good group's playlist "P" so evaluate_schedules proceeds to PLAY for "Default"
        # independently of Mobile's broken schedule ("MISSING" playlist).
        from mosaicmesh import render as R
        disp = self._setup(mock_settings, monkeypatch); self._no_real_render(monkeypatch)
        # Seed READY entry for playlist "P" on "Default" so the render gate passes.
        elements = R._build_media_elements(mock_settings.playlists["P"].items)
        tok = R.render_token(elements, "Default")
        R._set_render_state(disp, "P", R.RENDER_READY, token=tok)
        # second group (bad: references a missing playlist — must not block the good group)
        mock_settings.displays["Mobile"] = server.Display()
        mock_settings.displays["Mobile"].scheduledEntryId = None
        mock_settings.displays["Mobile"].scheduledPlaying = False
        mock_settings.schedules = {
            "ok": _schedule(id="ok", playlistName="P", displayID="Default", startTime="00:00", endTime="23:59"),
            "bad": _schedule(id="bad", playlistName="MISSING", displayID="Mobile", startTime="00:00", endTime="23:59")}
        server.evaluate_schedules(datetime.datetime(2026, 6, 1, 12, 0))
        assert disp.scheduledEntryId == "ok"
        assert disp.action == server.PlayState.PLAY

    def test_default_playlist_change_reassigns(self, mock_settings, monkeypatch):
        disp = self._setup(mock_settings, monkeypatch, default="P"); self._no_real_render(monkeypatch)
        # second playlist Q
        q = server.Playlist(); q.name = "Q"; q.loop = True
        q.items = [{"id":"b","file":"/media/server/images/y.jpg","duration":5,"playmode":"FULL",
                    "backgroundColor":"#000000","startEffect":None,"endEffect":None}]
        mock_settings.playlists["Q"] = q
        mock_settings.schedules = {}   # no schedules -> default drives it
        server.evaluate_schedules(datetime.datetime(2026,6,1,12,0))
        assert disp.scheduledEntryId == "__default__:P"
        # change the default
        disp.defaultPlaylistName = "Q"
        server.evaluate_schedules(datetime.datetime(2026,6,1,12,0,5))
        assert disp.scheduledEntryId == "__default__:Q"
        assert disp.mediaElements[0].file == "/media/server/images/y.jpg"  # re-assigned to Q


class TestScheduleCRUDExtra:
    def test_save_coerces_bad_priority(self, mock_settings):
        server.settings = mock_settings
        sid = jsonpickle.decode(server.msg_response(
            {"SRC":"a","DEST":"SRV","REQUEST":"SAVE_SCHEDULE",
             "PAYLOAD":{"name":"X","playlistName":"P","displayID":"Default","freq":"DAILY","interval":1,
                        "byweekday":[],"dtstart":"2026-01-01","end":{"type":"never"},"exdates":[],
                        "startTime":"09:00","endTime":"17:00","priority":"high"}}, _make_session()))["PAYLOAD"]["id"]
        assert mock_settings.schedules[sid].priority == 0

    def test_save_rejects_weekly_no_days(self, mock_settings):
        server.settings = mock_settings
        resp = jsonpickle.decode(server.msg_response(
            {"SRC":"a","DEST":"SRV","REQUEST":"SAVE_SCHEDULE",
             "PAYLOAD":{"name":"X","playlistName":"P","displayID":"Default","freq":"WEEKLY","interval":1,
                        "byweekday":[],"dtstart":"2026-01-01","end":{"type":"never"},"exdates":[],
                        "startTime":"09:00","endTime":"17:00"}}, _make_session()))
        assert "error" in resp["PAYLOAD"]
