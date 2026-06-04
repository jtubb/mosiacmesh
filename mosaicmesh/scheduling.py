"""Schedule recurrence evaluation: given a Schedule and a datetime, is the
schedule active? Plus playlist-index calculation for in-progress catch-up.

Pure functions — they take Schedule/Playlist as parameters, no
server.settings dependency. Uses python-dateutil's rrule for iCal-style
recurrence (DAILY/WEEKLY/MONTHLY/YEARLY with interval, byweekday, dtstart,
end, exdates, startTime, endTime).
"""
import datetime
from dateutil import rrule as _rrule

__all__ = [
    "playlist_index",
    "schedule_active_at",
    "_FREQ_MAP",
    "_parse_date",
    "_hhmm_to_min",
]

_FREQ_MAP = {"DAILY": _rrule.DAILY, "WEEKLY": _rrule.WEEKLY,
             "MONTHLY": _rrule.MONTHLY, "YEARLY": _rrule.YEARLY}


def playlist_index(elapsed_ms, durations, loop):
    """Given elapsed playback time and per-item durations (ms), return the
    current {'index', 'offsetMs'} or None when the playlist is empty/ended.

    This is the synchronization core: clients call the JS mirror of this with
    elapsed = GoTime.now() - startEpoch, so every display lands on the same
    item at the same instant.
    """
    total = 0
    for d in durations:
        total += d
    if total <= 0:
        return None
    if loop:
        elapsed_ms = elapsed_ms % total
    elif elapsed_ms >= total:
        return None
    if elapsed_ms < 0:
        elapsed_ms = 0
    cum = 0
    for i in range(len(durations)):
        if elapsed_ms < cum + durations[i]:
            return {"index": i, "offsetMs": elapsed_ms - cum}
        cum += durations[i]
    return {"index": len(durations) - 1, "offsetMs": durations[-1]}


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
    if not isinstance(end, dict):
        end = {"type": "never"}
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
