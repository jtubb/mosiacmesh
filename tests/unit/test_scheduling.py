"""Unit tests for playlist scheduling."""
import sys, json
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


import datetime

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
