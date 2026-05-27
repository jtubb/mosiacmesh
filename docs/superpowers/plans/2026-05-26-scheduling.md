# Playlist Scheduling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run playlists on display groups automatically by full-calendar schedule (dateutil rrule + daily time window), auto-rendering and auto-playing unattended, with a per-group default playlist when nothing is scheduled.

**Architecture:** `settings.schedules` holds `Schedule` objects with structured recurrence; `schedule_active_at()` compiles a `dateutil.rrule` and tests "is active now". `evaluate_schedules()` runs each `process()` tick (5s): per group it picks the highest-priority active schedule (else the group's `defaultPlaylistName`, else nothing), and drives assign → auto-render → play / stop via extracted playback helpers. CRUD + group-default websocket requests and an editor Schedules panel round it out. `index.html` is untouched.

**Tech Stack:** Python 3 / aiohttp / `python-dateutil` (new) / jsonpickle; jQuery 1.x (admin console); pytest (`tests/pytest.ini`, `asyncio_mode=auto`).

---

## Conventions for every task

- **Run tests:** `python -m pytest <path> -c tests/pytest.ini -v`. Full suite: `python pytest_runner.py --unit`.
- **Branch:** stay on `feature/discovery-completion-legacy-compat` (NOT main).
- Commit trailer: `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`.
- **Regression rule:** after each task, the existing suites (esp. `test_mosaic.py`, `test_playlists.py`, `test_playback.py`) stay green — Task 4 refactors the PLAY/STOP/ASSIGN bodies into helpers, so their tests are the guard.
- New server tests go in `tests/unit/test_scheduling.py` (create in Task 1). Use the same patterns as `test_playlists.py`: `import server`, `mock_settings` fixture, `_make_session()`, `MagicMock`, `jsonpickle`.

### Reference (current code)
- `Settings.__init__` (server.py ~611): `self.displays/scripts/clients/playlists = {}`.
- `Display.__init__` (~605): `boundingBox, boundingBoxCenter, mediaElements, loop, currentFrame, action, playStartEpoch, pauseOffset, renderedToken, renderStatus`.
- `migrate_client_objects` (~ near end): backfills client fields; iterates `settings.clients`. (We add a `settings.schedules` guard + a displays loop.)
- `_build_media_elements(items)`, `_media_item_payload(me)`, `_is_renderable(me)`, `compute_render_token(display_id)`, `broadcast_to_display_group`, `_broadcast_per_client_play(display_id, display)`, `render_group_async(display_id)` all exist.
- `ASSIGN_PLAYLIST` branch (~1042): builds `mediaElements` from a saved playlist, sets loop, resets token, broadcasts PRELOAD, classifies status.
- `PLAY` branch (~945) else-body sets `playStartEpoch`, `action=PlayState.PLAY`, then per-client or group broadcast. `STOP` branch (~ after PLAY) sets `action=STOP`, `currentFrame=0`, broadcasts STOP.
- `process()` (~1640) runs every 5s (the `__main__` loop `await process(); await asyncio.sleep(5)`).

---

## Task 1: Data model + python-dateutil dependency

**Files:**
- Modify: `server.py` — `Schedule` class, `Settings.__init__`, `Display.__init__`, `migrate_client_objects`; `requirements.txt`.
- Test: `tests/unit/test_scheduling.py` (create)

- [ ] **Step 1: Install the dependency**

Run: `pip install python-dateutil` and add a line `python-dateutil>=2.8` to `requirements.txt` (after the `numpy` line). Verify: `python -c "from dateutil import rrule; print('ok')"` → `ok`.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_scheduling.py`:

```python
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
```

- [ ] **Step 3: Run, expect FAIL** — `python -m pytest tests/unit/test_scheduling.py::TestScheduleModel -c tests/pytest.ini -v`.

- [ ] **Step 4: Implement in `server.py`**

Add `self.schedules = {}` to `Settings.__init__` (next to `self.playlists = {}`).

Add to `Display.__init__` (after `self.renderStatus = ""`):
```python
        self.defaultPlaylistName = None   # fallback playlist when no schedule is active
        self.scheduledEntryId = None      # transient: which schedule/"__default__" currently drives this group
        self.scheduledPlaying = False     # transient: have we issued PLAY for the current effective target
```

Add a `Schedule` class next to `Playlist`:
```python
class Schedule():
    def __init__(self):
        self.id = ""
        self.name = ""
        self.playlistName = ""
        self.displayID = ""
        self.priority = 0
        self.enabled = True
        self.freq = "DAILY"          # DAILY | WEEKLY | MONTHLY | YEARLY
        self.interval = 1
        self.byweekday = []          # ints 0=Mon..6=Sun (WEEKLY)
        self.dtstart = ""            # "YYYY-MM-DD"
        self.end = {"type": "never"} # or {"type":"until","untilDate":...} / {"type":"count","count":N}
        self.exdates = []            # ["YYYY-MM-DD", ...]
        self.startTime = "00:00"
        self.endTime = "23:59"
```

Extend `migrate_client_objects` — add at the top (after the existing `settings.playlists` guard if present, else near the top):
```python
    if not hasattr(settings, 'schedules'):
        settings.schedules = {}
    for _disp in settings.displays.values():
        if not hasattr(_disp, 'defaultPlaylistName'):
            _disp.defaultPlaylistName = None
        _disp.scheduledEntryId = None      # transient — reset on startup
        _disp.scheduledPlaying = False
```

- [ ] **Step 5: Run, expect PASS** — `python -m pytest tests/unit/test_scheduling.py::TestScheduleModel -c tests/pytest.ini -v`; then `python -c "import server"`.

- [ ] **Step 6: Commit**
```bash
git add server.py requirements.txt tests/unit/test_scheduling.py
git commit -m "feat(scheduling): Schedule model, settings.schedules, group default field, dateutil dep"
```

---

## Task 2: Recurrence evaluation `schedule_active_at`

**Files:**
- Modify: `server.py` — add `import datetime` and `from dateutil import rrule as _rrule` near the top imports; add `schedule_active_at`.
- Test: `tests/unit/test_scheduling.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
import datetime

class TestScheduleActiveAt:
    def _at(self, **kw):
        # build a datetime "now" for tests
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
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/test_scheduling.py::TestScheduleActiveAt -c tests/pytest.ini -v`.

- [ ] **Step 3: Implement in `server.py`**

Add near the top imports (with the other stdlib/third-party imports):
```python
import datetime
from dateutil import rrule as _rrule
```

Add the function (place it near `compute_render_token` / the other module-level helpers):
```python
_FREQ_MAP = {"DAILY": _rrule.DAILY, "WEEKLY": _rrule.WEEKLY,
             "MONTHLY": _rrule.MONTHLY, "YEARLY": _rrule.YEARLY}


def _parse_date(s):
    y, m, d = [int(x) for x in str(s).split("-")]
    return datetime.datetime(y, m, d)


def _hhmm_to_min(s):
    hh, mm = [int(x) for x in str(s).split(":")]
    return hh * 60 + mm


def schedule_active_at(schedule, when):
    """True if `schedule` is active at datetime `when` (server-local): `when`'s
    date is an rrule occurrence (minus exdates) and the time is within the
    [startTime, endTime] window. Pure; ignores `enabled` (caller checks that)."""
    freq = _FREQ_MAP.get(getattr(schedule, "freq", None))
    if freq is None:
        return False
    try:
        dtstart = _parse_date(schedule.dtstart)
    except Exception:
        return False
    kw = {"dtstart": dtstart, "interval": max(1, int(getattr(schedule, "interval", 1) or 1))}
    end = getattr(schedule, "end", None) or {"type": "never"}
    if end.get("type") == "until" and end.get("untilDate"):
        try:
            u = _parse_date(end["untilDate"])
            kw["until"] = u.replace(hour=23, minute=59, second=59)
        except Exception:
            pass
    elif end.get("type") == "count" and end.get("count"):
        kw["count"] = int(end["count"])
    if getattr(schedule, "freq", None) == "WEEKLY" and getattr(schedule, "byweekday", None):
        kw["byweekday"] = [int(x) for x in schedule.byweekday]
    rset = _rrule.rruleset()
    rset.rrule(_rrule.rrule(freq, **kw))
    for ex in (getattr(schedule, "exdates", None) or []):
        try:
            rset.exdate(_parse_date(ex))
        except Exception:
            pass
    day_start = datetime.datetime(when.year, when.month, when.day)
    if not rset.between(day_start, day_start, inc=True):   # occurrences sit at midnight of each day
        return False
    now_min = when.hour * 60 + when.minute
    try:
        return _hhmm_to_min(schedule.startTime) <= now_min <= _hhmm_to_min(schedule.endTime)
    except Exception:
        return False
```

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/unit/test_scheduling.py::TestScheduleActiveAt -c tests/pytest.ini -v` (8 pass). Confirm `python -c "import server"`.

- [ ] **Step 5: Commit**
```bash
git add server.py tests/unit/test_scheduling.py
git commit -m "feat(scheduling): schedule_active_at recurrence evaluation (dateutil rrule)"
```

---

## Task 3: CRUD + group-default requests

**Files:**
- Modify: `server.py` — six `elif` branches in `msg_response` (before the final `else`).
- Test: `tests/unit/test_scheduling.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
class TestScheduleCRUD:
    def _save(self, name="S", sid=None, **kw):
        payload = {"name": name, "playlistName": kw.get("playlistName", "P"),
                   "displayID": kw.get("displayID", "Default"), "priority": kw.get("priority", 0),
                   "enabled": kw.get("enabled", True), "freq": kw.get("freq", "DAILY"),
                   "interval": 1, "byweekday": kw.get("byweekday", []), "dtstart": "2026-01-01",
                   "end": {"type": "never"}, "exdates": [], "startTime": "09:00", "endTime": "17:00"}
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
        bad = self._save(name="X")  # baseline ok
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

    def test_group_default_set_and_get(self, mock_settings):
        server.settings = mock_settings
        server.msg_response({"SRC": "a", "DEST": "SRV", "REQUEST": "SET_GROUP_DEFAULT",
                             "PAYLOAD": {"displayID": "Default", "playlistName": "P"}}, _make_session())
        assert mock_settings.displays["Default"].defaultPlaylistName == "P"
        rows = jsonpickle.decode(server.msg_response(
            {"SRC": "a", "DEST": "SRV", "REQUEST": "GET_GROUP_DEFAULTS", "PAYLOAD": {}}, _make_session()))["PAYLOAD"]
        assert {"displayID": "Default", "defaultPlaylistName": "P"} in rows
        # clearing
        server.msg_response({"SRC": "a", "DEST": "SRV", "REQUEST": "SET_GROUP_DEFAULT",
                             "PAYLOAD": {"displayID": "Default", "playlistName": ""}}, _make_session())
        assert mock_settings.displays["Default"].defaultPlaylistName is None
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/test_scheduling.py::TestScheduleCRUD -c tests/pytest.ini -v`.

- [ ] **Step 3: Implement in `server.py`**

Add these branches before the final `else:` in `msg_response`. (`Schedule` is a module-level class; `uuid` — add `import uuid` to the top imports if not present.)

```python
    elif(msg["REQUEST"] == "LIST_SCHEDULES"):
        now = datetime.datetime.now()
        rows = []
        for sid, s in settings.schedules.items():
            rows.append({"id": s.id, "name": s.name, "playlistName": s.playlistName,
                         "displayID": s.displayID, "priority": s.priority, "enabled": s.enabled,
                         "activeNow": bool(getattr(s, "enabled", True)) and schedule_active_at(s, now)})
        response["PAYLOAD"] = rows

    elif(msg["REQUEST"] == "GET_SCHEDULE"):
        s = settings.schedules.get(msg["PAYLOAD"].get("id"))
        if s is None:
            response["PAYLOAD"] = {"error": "not found"}
        else:
            response["PAYLOAD"] = {"id": s.id, "name": s.name, "playlistName": s.playlistName,
                                   "displayID": s.displayID, "priority": s.priority, "enabled": s.enabled,
                                   "freq": s.freq, "interval": s.interval, "byweekday": s.byweekday,
                                   "dtstart": s.dtstart, "end": s.end, "exdates": s.exdates,
                                   "startTime": s.startTime, "endTime": s.endTime}

    elif(msg["REQUEST"] == "SAVE_SCHEDULE"):
        p = msg["PAYLOAD"]
        # validate time window and recurrence compile
        ok = True; err = None
        try:
            if _hhmm_to_min(p.get("startTime", "00:00")) >= _hhmm_to_min(p.get("endTime", "23:59")):
                ok = False; err = "endTime must be after startTime"
        except Exception:
            ok = False; err = "bad time"
        if ok:
            probe = Schedule()
            for k in ("freq", "interval", "byweekday", "dtstart", "end"):
                setattr(probe, k, p.get(k, getattr(probe, k)))
            probe.startTime = p.get("startTime", "00:00"); probe.endTime = p.get("endTime", "23:59")
            try:
                schedule_active_at(probe, datetime.datetime.now())  # compiles the rrule
            except Exception as e:
                ok = False; err = "bad recurrence: " + str(e)
        if not ok:
            response["PAYLOAD"] = {"error": err}
        else:
            sid = p.get("id") or ("sch_" + uuid.uuid4().hex[:10])
            s = settings.schedules.setdefault(sid, Schedule())
            s.id = sid
            for k in ("name", "playlistName", "displayID", "priority", "enabled",
                      "freq", "interval", "byweekday", "dtstart", "end", "exdates",
                      "startTime", "endTime"):
                if k in p:
                    setattr(s, k, p[k])
            response["PAYLOAD"] = {"id": sid}

    elif(msg["REQUEST"] == "DELETE_SCHEDULE"):
        settings.schedules.pop(msg["PAYLOAD"].get("id"), None)
        response["PAYLOAD"] = "SUCCESS"

    elif(msg["REQUEST"] == "GET_GROUP_DEFAULTS"):
        response["PAYLOAD"] = [{"displayID": did, "defaultPlaylistName": getattr(d, "defaultPlaylistName", None)}
                               for did, d in settings.displays.items()]

    elif(msg["REQUEST"] == "SET_GROUP_DEFAULT"):
        p = msg["PAYLOAD"]
        display = settings.displays.setdefault(p.get("displayID"), Display())
        display.defaultPlaylistName = (p.get("playlistName") or "").strip() or None
        response["PAYLOAD"] = "SUCCESS"
```

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/unit/test_scheduling.py::TestScheduleCRUD -c tests/pytest.ini -v`; then full file green.

- [ ] **Step 5: Commit**
```bash
git add server.py tests/unit/test_scheduling.py
git commit -m "feat(scheduling): schedule CRUD + group-default websocket requests"
```

---

## Task 4: Playback helpers + the evaluator

**Files:**
- Modify: `server.py` — extract `_apply_playlist`, `_start_group_playback`, `_stop_group_playback` (refactor ASSIGN/PLAY/STOP to use them); add `evaluate_schedules`; call it from `process()`.
- Test: `tests/unit/test_scheduling.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
class TestEvaluator:
    def _setup(self, mock_settings, monkeypatch, default=None):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        # a simple FULL playlist (no render needed)
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

    def _renders(self, monkeypatch):
        kicked = []
        monkeypatch.setattr(server.asyncio, "ensure_future", lambda coro: (kicked.append(coro), coro.close()))
        return kicked

    def test_window_open_assigns_and_plays(self, mock_settings, monkeypatch):
        disp = self._setup(mock_settings, monkeypatch)
        self._renders(monkeypatch)
        mock_settings.schedules = {"s1": _schedule(id="s1", playlistName="P", displayID="Default",
                                                   startTime="00:00", endTime="23:59")}
        server.evaluate_schedules(datetime.datetime(2026, 6, 1, 12, 0))
        assert disp.scheduledEntryId == "s1"
        assert disp.action == server.PlayState.PLAY
        assert disp.scheduledPlaying is True

    def test_window_closed_no_default_stops(self, mock_settings, monkeypatch):
        disp = self._setup(mock_settings, monkeypatch)
        self._renders(monkeypatch)
        disp.scheduledEntryId = "s1"; disp.scheduledPlaying = True; disp.action = server.PlayState.PLAY
        mock_settings.schedules = {"s1": _schedule(id="s1", playlistName="P", displayID="Default",
                                                   startTime="09:00", endTime="17:00")}
        server.evaluate_schedules(datetime.datetime(2026, 6, 1, 20, 0))  # after window
        assert disp.scheduledEntryId is None
        assert disp.action == server.PlayState.STOP

    def test_window_closed_with_default_plays_default(self, mock_settings, monkeypatch):
        disp = self._setup(mock_settings, monkeypatch, default="P")
        self._renders(monkeypatch)
        mock_settings.schedules = {"s1": _schedule(id="s1", playlistName="P", displayID="Default",
                                                   startTime="09:00", endTime="17:00")}
        server.evaluate_schedules(datetime.datetime(2026, 6, 1, 20, 0))
        assert disp.scheduledEntryId == "__default__"
        assert disp.action == server.PlayState.PLAY

    def test_active_schedule_outranks_default(self, mock_settings, monkeypatch):
        disp = self._setup(mock_settings, monkeypatch, default="P")
        self._renders(monkeypatch)
        mock_settings.schedules = {"s1": _schedule(id="s1", playlistName="P", displayID="Default",
                                                   startTime="00:00", endTime="23:59")}
        server.evaluate_schedules(datetime.datetime(2026, 6, 1, 12, 0))
        assert disp.scheduledEntryId == "s1"

    def test_priority_winner(self, mock_settings, monkeypatch):
        disp = self._setup(mock_settings, monkeypatch)
        self._renders(monkeypatch)
        mock_settings.schedules = {
            "lo": _schedule(id="lo", priority=1, playlistName="P", displayID="Default", startTime="00:00", endTime="23:59"),
            "hi": _schedule(id="hi", priority=5, playlistName="P", displayID="Default", startTime="00:00", endTime="23:59")}
        server.evaluate_schedules(datetime.datetime(2026, 6, 1, 12, 0))
        assert disp.scheduledEntryId == "hi"

    def test_idempotent_no_double_play(self, mock_settings, monkeypatch):
        disp = self._setup(mock_settings, monkeypatch)
        self._renders(monkeypatch)
        mock_settings.schedules = {"s1": _schedule(id="s1", playlistName="P", displayID="Default",
                                                   startTime="00:00", endTime="23:59")}
        server.evaluate_schedules(datetime.datetime(2026, 6, 1, 12, 0))
        server.socketmanager.broadcast.reset_mock()
        server.evaluate_schedules(datetime.datetime(2026, 6, 1, 12, 0, 5))  # same window, 5s later
        assert server.socketmanager.broadcast.call_count == 0   # no re-assign / re-play
```

- [ ] **Step 2: Run, expect FAIL** — `python -m pytest tests/unit/test_scheduling.py::TestEvaluator -c tests/pytest.ini -v`.

- [ ] **Step 3: Implement in `server.py`**

Add three helpers near the other playback helpers (above `msg_response`):
```python
def _apply_playlist(display_id, pl):
    """Copy a saved Playlist onto a group (mediaElements, loop, reset token, PRELOAD)."""
    display = settings.displays.setdefault(display_id, Display())
    display.mediaElements = _build_media_elements(pl.items)
    display.loop = bool(pl.loop)
    display.renderedToken = ""
    broadcast_to_display_group(display_id, {
        "REQUEST": "PRELOAD",
        "PAYLOAD": {"items": [_media_item_payload(me) for me in display.mediaElements]}})


def _start_group_playback(display_id, resume_epoch=None):
    """Set the group playing now and broadcast PLAY (per-client for renderable items,
    else group-wide). No render gating here — callers ensure render readiness."""
    display = settings.displays.get(display_id)
    if not display or not display.mediaElements:
        return
    now_ms = int(time.time() * 1000)
    if resume_epoch is None:
        resume_epoch = now_ms - display.pauseOffset if display.action == PlayState.PAUSE else now_ms
    display.playStartEpoch = resume_epoch
    display.action = PlayState.PLAY
    if any(_is_renderable(me) for me in display.mediaElements):
        _broadcast_per_client_play(display_id, display)
    else:
        items = [_media_item_payload(me) for me in display.mediaElements]
        broadcast_to_display_group(display_id, {
            "REQUEST": "PLAY",
            "PAYLOAD": {"startEpoch": display.playStartEpoch, "items": items, "loop": display.loop}})


def _stop_group_playback(display_id):
    display = settings.displays.get(display_id)
    if display:
        display.action = PlayState.STOP
        display.currentFrame = 0
    broadcast_to_display_group(display_id, {"REQUEST": "STOP", "PAYLOAD": {"displayID": display_id}})
```

Refactor the existing handlers to use them (keeps behavior identical; existing tests guard it):
- In `ASSIGN_PLAYLIST`, replace the inline `display.mediaElements = _build_media_elements(pl.items); display.loop = ...; display.renderedToken=""; broadcast_to_display_group(... PRELOAD ...)` block with `_apply_playlist(display_id, pl)` (then keep the existing `has_renderable`/status classification using `settings.displays[display_id]`).
- In the `PLAY` handler's final `else:` branch, replace the `display.playStartEpoch = resume_epoch ... broadcast` block with `_start_group_playback(display_id, resume_epoch)` then `response["PAYLOAD"] = "SUCCESS"`.
- In the `STOP` handler, replace its body with `_stop_group_playback(display_id); response["PAYLOAD"] = "SUCCESS"`.

Add the evaluator (near `process()`):
```python
def evaluate_schedules(now=None):
    """Per group with a schedule or a default playlist: pick the effective target
    (highest-priority active schedule, else the group default, else nothing) and
    drive assign -> auto-render -> play / stop. Called every process() tick."""
    if now is None:
        now = datetime.datetime.now()
    group_ids = set(s.displayID for s in settings.schedules.values())
    for did, d in settings.displays.items():
        if getattr(d, "defaultPlaylistName", None):
            group_ids.add(did)
    for display_id in group_ids:
        display = settings.displays.get(display_id)
        if display is None:
            continue
        winner = None
        for s in settings.schedules.values():
            if s.displayID != display_id or not getattr(s, "enabled", True):
                continue
            if schedule_active_at(s, now):
                if (winner is None or s.priority > winner.priority
                        or (s.priority == winner.priority and s.id < winner.id)):
                    winner = s
        if winner is not None:
            key, playlist_name = winner.id, winner.playlistName
        elif getattr(display, "defaultPlaylistName", None):
            key, playlist_name = "__default__", display.defaultPlaylistName
        else:
            key, playlist_name = None, None

        prev = getattr(display, "scheduledEntryId", None)
        if key is None:
            if prev is not None:
                _stop_group_playback(display_id)
                display.scheduledEntryId = None
                display.scheduledPlaying = False
            continue
        if key != prev:
            pl = settings.playlists.get(playlist_name)
            if pl is None:
                if prev is not None:
                    _stop_group_playback(display_id)
                display.scheduledEntryId = None
                display.scheduledPlaying = False
                continue
            _apply_playlist(display_id, pl)
            display.scheduledEntryId = key
            display.scheduledPlaying = False
        # render-gate then play
        has_renderable = any(_is_renderable(me) for me in display.mediaElements)
        if has_renderable and compute_render_token(display_id) != display.renderedToken:
            if display.renderStatus != "rendering":
                asyncio.ensure_future(render_group_async(display_id))
                display.scheduledPlaying = False
        elif not getattr(display, "scheduledPlaying", False):
            _start_group_playback(display_id)
            display.scheduledPlaying = True
```

Call it from `process()` — add near the top of the function body (after `current_time = time.time()`):
```python
    try:
        evaluate_schedules()
    except Exception as e:
        logging.error("schedule evaluation failed: %s", e)
```

- [ ] **Step 4: Run, expect PASS** — `python -m pytest tests/unit/test_scheduling.py::TestEvaluator -c tests/pytest.ini -v`; then **full** `python pytest_runner.py --unit` (the ASSIGN/PLAY/STOP refactor must not break `test_playback.py`/`test_mosaic.py`/`test_playlists.py`). All green.

- [ ] **Step 5: Commit**
```bash
git add server.py tests/unit/test_scheduling.py
git commit -m "feat(scheduling): evaluator in process() + extracted playback helpers"
```

---

## Task 5: Editor Schedules panel

**Files:**
- Modify: `admin.html` — a new `#scheduleEditor` section + `<script>` with the recurrence builder, default-playlist controls, and CRUD wiring; response handlers in `mosiacMeshCallback`.
- Verify: Playwright (controller-run).

`admin.html` is a desktop console (modern JS fine). `index.html` untouched.

- [ ] **Step 1: Add the HTML section** — insert before the `<div id="log" ...>` block:

```html
<div id="scheduleEditor" style="border:1px solid black; padding:8px; width:62em; margin-bottom:1em;">
  <div style="margin-bottom:6px;">
    <b>Schedules:</b>
    <select id="schSelect"></select>
    <button id="schNew" type="button">+ New</button>
    <button id="schSave" type="button">Save</button>
    <button id="schDelete" type="button">Delete</button>
    <span id="schStatus" style="margin-left:8px; color:#555;"></span>
  </div>
  <div id="schForm" style="display:flex; flex-wrap:wrap; gap:10px;"></div>
  <div style="margin-top:8px; border-top:1px solid #ccc; padding-top:6px;">
    <b>Default playlist (when idle)</b>
    <div id="schDefaults"></div>
  </div>
</div>
```

- [ ] **Step 2: Add the editor script** — new `<script>` before `</body>`:

```html
<script>
var schEditor = null;   // current schedule being edited (object) or null
var WEEKDAYS = [["0","Mon"],["1","Tue"],["2","Wed"],["3","Thu"],["4","Fri"],["5","Sat"],["6","Sun"]];

function schGroups() { return (typeof plGroupOptions === 'function') ? plGroupOptions() : ["Default"]; }

function schBlank() {
  return { id:"", name:"", playlistName:"", displayID:(schGroups()[0]||"Default"), priority:0,
           enabled:true, freq:"WEEKLY", interval:1, byweekday:[], dtstart:"2026-01-01",
           end:{type:"never"}, exdates:[], startTime:"09:00", endTime:"17:00" };
}

function schRenderForm() {
  var e = schEditor, $f = $('#schForm').empty();
  if (!e) { $f.html('<span class="size" style="color:#888;">+ New to create a schedule</span>'); return; }
  function row(label, $inp) { $('<div>').append('<div class="size">'+label+'</div>').append($inp).appendTo($f); }
  row("Name", $('<input>').val(e.name).on('change', function(){ e.name=this.value; }));
  var $grp = $('<select>'); $.each(schGroups(), function(_,g){ $('<option>').val(g).text(g).appendTo($grp); });
  $grp.val(e.displayID).on('change', function(){ e.displayID=this.value; }); row("Group", $grp);
  var $pl = $('<select>'); $('<option>').val("").text("-- playlist --").appendTo($pl);
  $.each((window._schPlaylists||[]), function(_,n){ $('<option>').val(n).text(n).appendTo($pl); });
  $pl.val(e.playlistName).on('change', function(){ e.playlistName=this.value; }); row("Playlist", $pl);
  var $freq = $('<select>'); $.each(["DAILY","WEEKLY","MONTHLY","YEARLY"], function(_,f){ $('<option>').val(f).text(f).appendTo($freq); });
  $freq.val(e.freq).on('change', function(){ e.freq=this.value; schRenderForm(); }); row("Frequency", $freq);
  row("Every N", $('<input type="number">').val(e.interval).css('width','4em').on('change', function(){ e.interval=parseInt(this.value,10)||1; }));
  if (e.freq === "WEEKLY") {
    var $days = $('<div>');
    $.each(WEEKDAYS, function(_,d){
      var on = e.byweekday.indexOf(parseInt(d[0],10))>=0 || e.byweekday.indexOf(d[0])>=0;
      var $cb = $('<input type="checkbox">').prop('checked', on)
        .on('change', function(){ var v=parseInt(d[0],10); e.byweekday = e.byweekday.filter(function(x){return parseInt(x,10)!==v;});
          if (this.checked) e.byweekday.push(v); });
      $days.append($cb).append('<span class="size" style="margin-right:6px;">'+d[1]+'</span>');
    });
    row("Weekdays", $days);
  }
  row("Start date", $('<input type="date">').val(e.dtstart).on('change', function(){ e.dtstart=this.value; }));
  var $end = $('<select>'); $.each([["never","Never"],["until","Until date"],["count","After N"]], function(_,o){ $('<option>').val(o[0]).text(o[1]).appendTo($end); });
  $end.val(e.end.type).on('change', function(){ e.end = {type:this.value}; schRenderForm(); }); row("End", $end);
  if (e.end.type === "until") row("Until", $('<input type="date">').val(e.end.untilDate||"").on('change', function(){ e.end.untilDate=this.value; }));
  if (e.end.type === "count") row("Count", $('<input type="number">').val(e.end.count||1).css('width','4em').on('change', function(){ e.end.count=parseInt(this.value,10)||1; }));
  row("Start time", $('<input type="time">').val(e.startTime).on('change', function(){ e.startTime=this.value; }));
  row("End time", $('<input type="time">').val(e.endTime).on('change', function(){ e.endTime=this.value; }));
  row("Priority", $('<input type="number">').val(e.priority).css('width','4em').on('change', function(){ e.priority=parseInt(this.value,10)||0; }));
  row("Enabled", $('<input type="checkbox">').prop('checked', e.enabled!==false).on('change', function(){ e.enabled=this.checked; }));
  // exceptions (skip dates)
  var $ex = $('<div>');
  $.each(e.exdates, function(i,d){ $('<span class="size" style="margin-right:4px;">'+d+' </span>')
    .append($('<a href="#">✕</a>').on('click', function(ev){ ev.preventDefault(); e.exdates.splice(i,1); schRenderForm(); })).appendTo($ex); });
  var $exAdd = $('<input type="date">');
  $ex.append($exAdd).append($('<button type="button">+ skip</button>').on('click', function(){ if ($exAdd.val()){ e.exdates.push($exAdd.val()); schRenderForm(); } }));
  row("Exceptions", $ex);
}

function schRefreshList() { sock.send(generateMessage('SRV','LIST_SCHEDULES',{})); sock.send(generateMessage('SRV','LIST_PLAYLISTS',{})); }
function schLoad(id) { if (id) sock.send(generateMessage('SRV','GET_SCHEDULE',{id:id})); }
function schNew() { schEditor = schBlank(); $('#schStatus').text("(new)"); schRenderForm(); }
function schSave() {
  if (!schEditor) return;
  sock.send(generateMessage('SRV','SAVE_SCHEDULE', schEditor));
  setTimeout(schRefreshList, 150);
}
function schDelete() {
  if (schEditor && schEditor.id) { sock.send(generateMessage('SRV','DELETE_SCHEDULE',{id:schEditor.id})); }
  schNew(); setTimeout(schRefreshList, 150);
}
function schRenderDefaults(rows) {
  var $d = $('#schDefaults').empty();
  $.each(schGroups(), function(_, g){
    var cur = ""; $.each(rows||[], function(_, r){ if (r.displayID===g) cur = r.defaultPlaylistName||""; });
    var $sel = $('<select>'); $('<option>').val("").text("None").appendTo($sel);
    $.each((window._schPlaylists||[]), function(_,n){ $('<option>').val(n).text(n).appendTo($sel); });
    $sel.val(cur).on('change', function(){ sock.send(generateMessage('SRV','SET_GROUP_DEFAULT',{displayID:g, playlistName:this.value})); });
    $('<div>').append('<span class="size" style="margin-right:6px;">'+g+'</span>').append($sel).appendTo($d);
  });
}

$(function(){
  $('#schNew').on('click', schNew);
  $('#schSave').on('click', schSave);
  $('#schDelete').on('click', schDelete);
  $('#schSelect').on('change', function(){ schLoad($(this).val()); });
  setTimeout(function(){ schRefreshList(); sock.send(generateMessage('SRV','GET_GROUP_DEFAULTS',{})); }, 700);
});
</script>
```

- [ ] **Step 3: Wire responses into `mosiacMeshCallback`** — add `else if` branches:

```javascript
		else if(data_obj.REQUEST == "LIST_SCHEDULES")
		{
			var $s = $('#schSelect').empty();
			$('<option>').val("").text("-- select --").appendTo($s);
			$.each(data_obj.PAYLOAD, function(_, r){
				$('<option>').val(r.id).text((r.activeNow?"● ":"") + r.name + " ["+r.displayID+"]").appendTo($s); });
			if (schEditor && schEditor.id) { $s.val(schEditor.id); }
		}
		else if(data_obj.REQUEST == "GET_SCHEDULE")
		{
			if (data_obj.PAYLOAD && !data_obj.PAYLOAD.error) { schEditor = data_obj.PAYLOAD; $('#schStatus').text(schEditor.name); schRenderForm(); }
		}
		else if(data_obj.REQUEST == "SAVE_SCHEDULE")
		{
			if (data_obj.PAYLOAD && data_obj.PAYLOAD.id && schEditor) { schEditor.id = data_obj.PAYLOAD.id; $('#schStatus').text("saved"); }
			else if (data_obj.PAYLOAD && data_obj.PAYLOAD.error) { $('#schStatus').text("⚠ " + data_obj.PAYLOAD.error); }
		}
		else if(data_obj.REQUEST == "LIST_PLAYLISTS")
		{
			window._schPlaylists = $.map(data_obj.PAYLOAD, function(r){ return r.name; });
		}
		else if(data_obj.REQUEST == "GET_GROUP_DEFAULTS")
		{
			schRenderDefaults(data_obj.PAYLOAD);
		}
```
NOTE: the playlist editor already has its own `LIST_PLAYLISTS` handler (it populates `#plSelect`). Do NOT remove it — append the `window._schPlaylists` capture as an additional statement inside the EXISTING `LIST_PLAYLISTS` branch rather than adding a second branch (two `else if` for the same REQUEST would make the second unreachable). Read `mosiacMeshCallback` and merge accordingly.

- [ ] **Step 4: Verify (controller, Playwright)** — start `python server.py -p 3000` (background), navigate to `http://localhost:3000/admin.html`, then:
```javascript
() => {
  return new Promise(function(resolve){
    schRefreshList(); sock.send(generateMessage('SRV','GET_GROUP_DEFAULTS',{}));
    setTimeout(function(){
      schNew();
      schEditor.name = "PW"; schEditor.freq = "WEEKLY"; schRenderForm();
      // tick Mon + Wed
      var cbs = $('#schForm input[type=checkbox]');
      // weekday checkboxes are the ones before the Enabled checkbox; tick first (Mon) and third (Wed)
      $(cbs[0]).prop('checked', true).trigger('change');
      $(cbs[2]).prop('checked', true).trigger('change');
      resolve({ freq: schEditor.freq, byweekday: schEditor.byweekday.slice().sort(),
                weekdayRowShown: cbs.length >= 7 });
    }, 800);
  });
}
```
Expected: `freq:"WEEKLY"`, `byweekday:[0,2]`, `weekdayRowShown:true`. Then switch `schEditor.freq="DAILY"; schRenderForm();` and confirm the weekday checkboxes disappear (`$('#schForm input[type=checkbox]').length` drops — only Enabled remains).

- [ ] **Step 5: Commit**
```bash
git add admin.html
git commit -m "feat(scheduling): editor Schedules panel (recurrence builder + group defaults)"
```

---

## Final verification (after all tasks)

- [ ] `python pytest_runner.py --unit` → all green (incl. the PLAY/STOP/ASSIGN refactor regression).
- [ ] Playwright: Schedules panel CRUD; recurrence builder writes correct structured fields; weekday row only for Weekly; default-playlist dropdowns set/reflect group defaults.
- [ ] Push branch and update PR #1.

## Notes for the implementer

- **DRY:** `_apply_playlist`/`_start_group_playback`/`_stop_group_playback` are the single sources — ASSIGN/PLAY/STOP and the evaluator all go through them. After extracting, the existing `test_playback.py`/`test_mosaic.py`/`test_playlists.py` are your regression guard; if any breaks, the extracted helper changed behavior — fix it.
- **YAGNI:** server-local time only; same-day windows (validated `endTime > startTime`); no pre-warm. Don't add timezone handling.
- **No ES5 concern** — `index.html` untouched; `admin.html` is the desktop console.
- The evaluator acts on edges and is idempotent within a window (the `scheduledPlaying` flag) — don't re-assert every tick.
- If `process()`, the PLAY/STOP/ASSIGN branch bodies, or `mosiacMeshCallback`'s `LIST_PLAYLISTS` handler differ materially from the reference, STOP and report NEEDS_CONTEXT.
```
