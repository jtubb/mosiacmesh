# Playlist Scheduling — Design

**Date:** 2026-05-26
**Status:** Design approved, pending implementation plan
**Builds on:** named playlists (`settings.playlists`), `ASSIGN_PLAYLIST` / `PLAY` / `STOP`, the async render pipeline (`render_group_async`, `compute_render_token`), and the existing `process()` background loop (runs every 5s).

## Context

The roadmap's last untouched pillar: run playlists on a display group **automatically by calendar time**, unattended. A schedule binds a saved playlist to a group with a full-calendar recurrence (via `python-dateutil` rrule) and a daily time window. A background evaluator in `process()` opens/closes windows, auto-rendering and auto-playing as needed, and stopping the group when nothing is scheduled.

## Goals

- Full-calendar recurrence: daily/weekly/monthly/yearly, intervals, by-weekday, end conditions (never / until date / after N), and per-date exceptions.
- A daily time-of-day window per schedule; server-local time.
- Unattended operation: a scheduled SEGMENT/INDIVIDUAL playlist auto-renders then auto-plays when ready.
- Outside all windows, an optional per-group **default playlist** plays (else STOP); priority resolves overlaps.
- Survives server restart (re-asserts the currently-active schedule).
- An editor panel to author schedules.

## Non-goals (deferred)

- Cross-midnight windows (`endTime` must be after `startTime`, same day).
- Render pre-warm/look-ahead (auto-render happens at window open, so first run has a brief black gap while rendering; cached renders are instant).
- Timezone selection (server-local time only).
- A raw RRULE-string editor (the editor uses structured recurrence controls; the server compiles them).

## Dependency

Add `python-dateutil` to `requirements.txt` (tiny, pure-Python). Used for `dateutil.rrule` recurrence math.

## Data model

New `settings.schedules` dict, keyed by `id`, persisted via jsonpickle (like `settings.playlists`). `migrate_client_objects` backfills `settings.schedules = {}` on an older `settings.dat`.

`Display` (the group) gains **`defaultPlaylistName`** (`str | None`, default `None`) — the fallback playlist played whenever no schedule is active for that group. A migration guard backfills it on older `Display` objects loaded from `settings.dat` (accessed via `getattr(display, "defaultPlaylistName", None)`).

```
class Schedule:
    id:           str            # generated on first save
    name:         str
    playlistName: str            # a key into settings.playlists
    displayID:    str            # target group
    priority:     int  = 0       # higher wins on overlap; id breaks ties
    enabled:      bool = True
    # recurrence (structured; the server compiles a dateutil rrule from these):
    freq:         str            # "DAILY" | "WEEKLY" | "MONTHLY" | "YEARLY"
    interval:     int  = 1       # every N freq units
    byweekday:    list = []      # ints 0=Mon..6=Sun (used for WEEKLY)
    dtstart:      str            # "YYYY-MM-DD" recurrence start date
    end:          dict           # {"type": "never"} | {"type":"until","untilDate":"YYYY-MM-DD"} | {"type":"count","count":N}
    exdates:      list = []      # ["YYYY-MM-DD", ...] skipped occurrence dates
    # daily time-of-day window (server-local), applied to each occurrence day:
    startTime:    str            # "HH:MM"
    endTime:      str            # "HH:MM" ; must be > startTime (same-day)
```

## Recurrence evaluation

A helper `schedule_active_at(schedule, when)` (pure, unit-testable):
1. Compile a `dateutil.rrule.rrule(freq, interval, byweekday, dtstart@00:00, until/count)` wrapped in an `rruleset` with `exdate` for each `exdates` entry.
2. Active iff: `when.date()` is an occurrence of the ruleset **and** `startTime <= when.time() <= endTime`. (`enabled` is checked by the caller.)

`freq`/weekday strings map to dateutil constants. Building the rule per evaluation is cheap at 5s cadence; no caching needed.

## Evaluator (`evaluate_schedules()`, called from `process()`)

For each group targeted by any schedule **or with a `defaultPlaylistName` set**:
1. **Effective target** — compute `(key, playlistName)`:
   - Among `enabled` schedules for the group with `schedule_active_at(s, now)` true, pick the highest `priority` (ties → lowest `id`). If one wins → `(key=schedule.id, schedule.playlistName)`.
   - Else if the group has a `defaultPlaylistName` → `(key="__default__", defaultPlaylistName)`. (A real active schedule always outranks the default fallback.)
   - Else → `None` (nothing to play).
2. **Transition** vs the group's tracked `display.scheduledEntryId`:
   - effective is `None` and group was driven → **STOP**, clear `scheduledEntryId`.
   - effective `key` changed (different schedule, or schedule↔default, or default playlist changed) → **assign** that playlist (reuse the ASSIGN_PLAYLIST logic: rebuild `mediaElements`, reset `renderedToken`, broadcast PRELOAD), set `scheduledEntryId = key`, `scheduledPlaying = False`.
   - unchanged → steady state.
3. **Auto-render → play** (on the transition tick and each tick until playing):
   - Needs render (`has_renderable` and `compute_render_token != renderedToken`) and `renderStatus != "rendering"` → `asyncio.ensure_future(render_group_async(displayID))` **and reset `scheduledPlaying = False`** (so a stale token — e.g. the playlist was edited mid-window — re-plays once the fresh render is ready).
   - Else ready (or no render needed) and not `scheduledPlaying` → **PLAY** (set `playStartEpoch`, broadcast via the existing per-client / group path), set `scheduledPlaying = True`.

**Tracking fields** on `Display`: `scheduledEntryId` (None) and `scheduledPlaying` (False), **transient — reset on startup** so a restart re-asserts the active schedule (re-assign + render + play), recovering reconnecting clients.

**Manual-control interaction:** the evaluator acts only on edges (window open/close, priority switch, render-ready). It does not re-assert every tick, so a manual STOP/PLAY mid-window persists until the next schedule boundary.

**Edge cases:**
- The effective playlist (scheduled or default) was deleted/renamed → assign finds no playlist → log + STOP + clear tracking (treated like nothing to play).
- Playlist edited mid-window (token changes) → `scheduledPlaying` is keyed off the render being current; a stale token makes the evaluator re-render and re-play when ready.
- `endTime <= startTime` → rejected at SAVE.

## Server API (websocket, `msg_response`)

| Request | Payload | Returns |
|---|---|---|
| `LIST_SCHEDULES` | — | `[{id, name, playlistName, displayID, priority, enabled, activeNow}]` (`activeNow` from `schedule_active_at(s, now)`) |
| `GET_SCHEDULE` | `{id}` | full `Schedule` dict, or `{error: "not found"}` |
| `SAVE_SCHEDULE` | full schedule (`id` optional) | `{id}`; validates by compiling the rrule and checking `endTime > startTime` → `{error}` if invalid |
| `DELETE_SCHEDULE` | `{id}` | `"SUCCESS"` |
| `GET_GROUP_DEFAULTS` | — | `[{displayID, defaultPlaylistName}]` for all groups |
| `SET_GROUP_DEFAULT` | `{displayID, playlistName}` (empty/null clears) | `"SUCCESS"` (sets `display.defaultPlaylistName`) |

## Editor UI (`admin.html`, modern JS)

A new **Schedules** panel, sibling to the playlist editor:
- Schedule list from `LIST_SCHEDULES`, each row showing name/group/playlist and an `● active` indicator (`activeNow`); New / Save / Delete.
- Form: name; target **group** (dropdown, same source as the assign bar); **playlist** (dropdown from `LIST_PLAYLISTS`); **recurrence builder** — frequency (Daily/Weekly/Monthly/Yearly), interval, weekday checkboxes (shown only for Weekly), start date, end (Never / Until date / After N); **exceptions** (add/remove skip dates); **time window** (start/end `HH:MM`); **priority** (number); **enabled** (checkbox).
- Save serializes the structured recurrence + window to `SAVE_SCHEDULE`.
- **Default playlist (when idle):** a small section listing each group with a playlist dropdown ("None" + playlists), backed by `GET_GROUP_DEFAULTS` / `SET_GROUP_DEFAULT` — sets the fallback that plays when no schedule is active.
- `index.html` untouched.

## Testing

### pytest
- Model: `Schedule` round-trips jsonpickle; `settings.schedules` persists; migrate backfills `{}`.
- `schedule_active_at`: daily; weekly + specific weekdays; monthly; `interval` (every 2 weeks); `until` and `count` end conditions; an `exdate` skips that day; time-window inclusion (just-inside vs just-outside `[start,end]`); a date before `dtstart` is inactive; disabled handled by caller.
- Winner selection: highest priority wins; `id` tiebreak; disabled excluded; none-active → None.
- CRUD: `SAVE_SCHEDULE` upserts and generates an id when absent; `LIST_SCHEDULES` includes `activeNow`; `GET_SCHEDULE` unknown → error; `DELETE_SCHEDULE`; malformed recurrence or `endTime <= startTime` → error. `SET_GROUP_DEFAULT` sets/clears `display.defaultPlaylistName`; `GET_GROUP_DEFAULTS` reports it.
- Evaluator (`evaluate_schedules` with a frozen `now` and mocked `render_group_async` + `socketmanager`): window-open → assign + (render kick or) PLAY; window-close with no default → STOP; window-close **with a default** → switches to the default playlist (key `__default__`); a real active schedule outranks a set default; priority switch → reassign to the higher entry; render-needed → kicks `render_group_async` once (not every tick) and PLAYs when ready; already-playing → no duplicate PLAY; manual STOP mid-window is not re-asserted; startup (cleared tracking) re-asserts the effective target.

### Playwright (light, `admin.html`)
- The Schedules panel lists / saves / deletes; the recurrence builder writes correct structured fields (Weekly + Mon/Wed → `freq:"WEEKLY", byweekday:[0,2]`); weekday checkboxes appear only when frequency is Weekly; the time-window and priority inputs round-trip into the saved schedule. The default-playlist section sets a group's default via `SET_GROUP_DEFAULT` and reflects it from `GET_GROUP_DEFAULTS`.

## Legacy / ES5

Server-side Python (`schedule_active_at`, `evaluate_schedules`, CRUD, the `python-dateutil` dep) plus a desktop-console (`admin.html`) panel. `index.html` is untouched — scheduled playback drives the same `ASSIGN`/`PLAY`/`STOP` the ES5 client already handles — so the 1st-gen iPad constraint is unaffected.
