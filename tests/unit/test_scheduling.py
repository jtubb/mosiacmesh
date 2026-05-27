"""Unit tests for playlist scheduling."""
import sys, json, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
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
