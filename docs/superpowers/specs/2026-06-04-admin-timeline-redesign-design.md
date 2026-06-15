# Admin UI Timeline Redesign — Design Spec

**Date:** 2026-06-04
**Status:** Draft — awaiting user review
**Scope:** Consolidate the admin console's Displays / Media / Playlists / Schedules pages into a single timeline-editor view; extract device-type-specific scripts and constants from `server.py` into editable Scripting Profiles; split `server.py` into focused modules; add REST endpoints for the new client.

---

## 1. Goal & rationale

The MosaicMesh admin console currently splits four related concerns across four pages:

- **Displays** — calibration + group/screen tree
- **Media** — file upload + thumbnail grid
- **Playlists** — named ordered lists of media items
- **Schedules** — assigns `(playlist, display)` pairs to recurring time windows

Operators frequently traverse all four to perform a single workflow ("show this video on the Lobby Wall every weekday from 2pm to 8pm"). This redesign consolidates them into one **timeline-editor-style view** modeled on traditional non-linear video editors (Premiere/Avid): horizontal display tracks, time on the x-axis, clips for scheduled playlists.

Concurrently — and as a closely-related cleanup the operator surfaced during brainstorming — all device-type-specific scripts and constants (today hardcoded in `server.py` as `DEFAULT_DEVICE_SCRIPTS`, `WEBCLIP_BUNDLE_ID`, `WEBAPP_ICON_FBX/FBY`, the `_launch_webapp_via_vnc` function) move into editable **Scripting Profiles** stored as data. Profiles auto-match to clients by `deviceType` and use template variables (`{ip}`, `{webclipBundleId}`, `{displayUrl}`) for any per-client variation. No per-device script overrides exist after migration.

Server-side, `server.py` (~6000 lines) splits into focused modules along clean responsibility boundaries. New REST endpoints (`/api/playlists`, `/api/schedules`, `/api/profiles`) back the new client UI. The legacy SockJS `REQUEST` protocol that display clients (24 iPad-1s on iOS 5.1.1) use is **untouched** — only relocated.

**Out of scope** (explicitly deferred to separate specs):

- Consolidating the dual websocket protocols (legacy `msg_response` vs newer async `handle_websocket_message`). The 24 iPads depend on legacy; consolidation needs its own shim period and design.
- Normalizing the global `settings` object into an explicit state container. Touches every function + every test; load-bearing for the test suite.
- Undo/redo in the timeline UI. Substantial store complexity; not required to ship.

---

## 2. Architecture

A single-page Alpine.js view embedded inside `admin.html`, replacing the existing `[data-route="displays"]`, `[data-route="media"]`, `[data-route="playlists"]`, and `[data-route="schedules"]` sections with one new `[data-route="timeline"]`. The Overview, Console, and Discovery routes remain unchanged.

The timeline is a **client-side reactive view over existing server state**: it hydrates from REST endpoints, holds an Alpine store as the local source of truth, and persists changes through the same endpoints. Live updates arrive via the existing SockJS broadcast channel (`DISCOVERY_HEARTBEAT`, `CLIENTS_WENT_OFFLINE`, `RENDER_IN_PROGRESS`, `PLAY`).

**Why Alpine + no build step:** matches the project's "no build step" convention. jQuery 1.x stays — Alpine sits alongside. New client code lives in `js/timeline/` as plain ES modules loaded via `<script type="module">`. Display clients (iPads) are untouched and remain ES5/jQuery 1.x.

---

## 3. Layout

The page splits into four regions, all visible at once:

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Toolbar:  [Day][Week][Month]   ◀ Thu Jun 4 ▶   …   Fleet: 🔓▶⏹⟲  ⚙Cal  │
├─────────────┬────────────────────────────────────────────────────────────┤
│ Left bin    │ Timeline canvas                                            │
│  ─────────  │  ─────────────────────────────────────────────────────     │
│ ▾ Media (12)│  Track header column │ Time grid (Day=24h, Week=7d, Mo=cal)│
│   • news.mp4│  ──────────────────  │ ──────────────────────────────────  │
│   • logo.png│  Lobby Wall  6/6 ●   │  [Morning News] [Lunch] [Marketing] │
│   • brand…  │  Conf Room A 1/1 ●   │         [Meeting Agenda]            │
│  + Upload   │  Cafe        2/2 ●   │           [Lunch Menu]              │
│             │  Default ▾ idle      │  ─ defaultPlaylist row when idle    │
│ ▾ Playlists │                      │                                     │
│   • Morn... │                      │   ↑ red "now" line at current time  │
│   + New     │                      │                                     │
└─────────────┴──────────────────────┴─────────────────────────────────────┘
```

**Toolbar** (40px): view-mode toggle (Day/Week/Month), date nav, today shortcut, fleet-wide action buttons (Login/Start/Stop/Reboot/Start-Testing all — kept from existing Overview), Calibrate button (opens modal), ⚙ Profiles button (opens profile-editor modal — see Section 7), Discovery link (separate route).

**Left bin** (180px, scrollable): two collapsible sections.

- *Media library*: file thumbnails, search input, `+ Upload`. Drag source onto timeline (when drilled into a clip) or for ad-hoc playlist creation.
- *Playlist library*: named playlists, `+ New`. Drag source onto a track to create a schedule.

**Track header column** (110px): per display group — name, online/total screen count, status dot, optional render-progress badge (shown when a `RENDER_IN_PROGRESS` broadcast is active for the group), and an "idle" sub-row below each track showing the default playlist. Clicking the header opens a small popover with `profileName` dropdown for overriding the auto-assigned profile (see Section 9).

**Time grid**: CSS Grid with `(N+1)` columns (label column + time slots). Resolutions:

- *Day view*: 24 hourly columns; multiple display tracks stacked vertically (default view)
- *Week view*: 7 day columns × hour rows; one display selected at a time (dropdown in toolbar)
- *Month view*: calendar cells with dot summaries (per-day playlist colors)

Each clip is positioned via `grid-column: <start> / span <N>` (Day view) or grid-row+column (Week view).

**Red now-line**: vertical 2px line at the current clock position; auto-scrolls into view on first load; advances every second from a single `setInterval`.

**Conflict overlay**: when two schedules overlap on the same track with different priorities, the lower-priority clip renders with a diagonal-stripe overlay over the overlap region; hover shows a tooltip explaining which priority wins.

---

## 4. Client-side components

New files under `js/timeline/`, all ES modules:

```
js/timeline/
  index.js              -- entry; bootstraps Alpine, wires the store
  store.js              -- Alpine.store('mm') -- single source of truth
  api.js                -- thin wrappers over REST endpoints
  toolbar.js            -- view-mode toggle, date nav, fleet actions
  bin/
    media-bin.js        -- file list + search + upload + drag source
    playlist-bin.js     -- playlist list + "+ New" + drag source
  timeline/
    timeline.js         -- top-level grid renderer
    track-header.js     -- left column: name, status dot, default-playlist row
    clip.js             -- single clip block; drag body, drag edges, double-click
    clip-inspector.js   -- inline drilled-in sub-track of playlist items
    now-line.js         -- vertical now-indicator with autoscroll
    grid-axis.js        -- header row (hours/days), grid layout helpers
  modals/
    calibration.js      -- existing ArUco flow extracted into a modal
    playlist-editor.js  -- modal for per-item properties (playmode, backgroundColor,
                           startEffect, endEffect) that don't fit the inline sub-track.
                           Opened from an item's right-click → "Item details". The
                           inline drilled-in sub-track handles add/remove/reorder
                           directly; this modal is for the deeper per-item form.
    profile-editor.js   -- new: 3-pane ScriptingProfile editor
    recurrence-editor.js-- popover/form for editing schedule.freq/byweekday/etc.
  util/
    time.js             -- iCal recurrence expansion → visible clip windows
    template-vars.js    -- {var} substitution helpers (for profile preview)
```

**Alpine store** (`Alpine.store('mm')`) holds the single source of truth:

```js
{
  displays: [{key, displayID, friendlyName, screens, onlineCount, totalCount, defaultPlaylistName, _serverVersion}, ...],
  playlists: {name: {name, items, loop, _serverVersion}, ...},
  schedules: [{id, playlistName, displayID, freq, byweekday, dtstart, end, exdates, startTime, endTime, priority, enabled, _serverVersion}, ...],
  media: [{file, mtime, kind, durationMs, ...}, ...],
  profiles: {name: ScriptingProfile, ...},
  // UI state
  viewMode: "day"|"week"|"month",
  viewDate: ISO date,
  selection: clipId | null,
  dragState: {kind, source, target, ...} | null,
}
```

Components reference state via `$store.mm.…`. Mutations go through store methods (`createSchedule`, `updateSchedule`, etc.), which call `api.js` and update local state on success, rollback on failure.

**`util/time.js` recurrence expander** is the one piece of non-trivial new client logic. Given a `Schedule` and a visible window (e.g. "Thursday 2026-06-04 06:00 → next-day 05:59"), it returns concrete clip placements (`{startMs, endMs, playlistName}`) for that window. Client-side mirror of the server's `schedule_active_at()`. Pure function, no DOM access, importable in Node for tests.

---

## 5. Server module split

`server.py` (~6000 lines) splits along responsibility boundaries. **Pure relocation — no behavior change.** Target layout:

```
server.py                        -- entry: route table, startup, process() loop, __main__
mosaicmesh/
  state.py                       -- Settings, Client, Playlist, Schedule, ScriptingProfile;
                                    migrate_client_objects()
  persistence.py                 -- save_settings_incremental(), jsonpickle setup,
                                    settings.dat IO
  device_scripts.py              -- launch dispatcher: _exec_ssh, _vnc_tap_sequence,
                                    _ssh_then_vnc; template-var assembly
  render.py                      -- ffmpeg orchestration: mosaic + segment + video
  calibration.py                 -- generateAruco(), calibrate(), find_squares(), angle_cos()
  cache.py                       -- get_cached_file(), file-handle pool, /debug/cache
  websocket/
    legacy.py                    -- msg_response() (iPad-facing REQUEST switch — unchanged)
    typed.py                     -- handle_websocket_message() (async; unchanged)
    dispatch.py                  -- ws_handler() (connection lifecycle, dispatch)
  api/
    discovery.py                 -- existing /api/discovery/{devices,stats,configure}
    playlists.py                 -- NEW: GET/POST/PUT/DELETE /api/playlists
    schedules.py                 -- NEW: GET/POST/PUT/DELETE /api/schedules
    profiles.py                  -- NEW: GET/POST/PUT/DELETE /api/profiles
    media.py                     -- NEW: GET /api/media, POST /api/upload
```

**Constraints preserved** (per `CLAUDE.md`):

- Global `settings` object stays. Every module imports it from `mosaicmesh.state`. No state-container refactor.
- `msg_response()` semantics stay byte-identical. The 24 iPads cannot notice.
- `parse_args()` stays under `if __name__ == '__main__'`. `import server` remains side-effect-free.
- Tests' `server.settings = mock_settings` pattern still works — `server.py` re-exports `settings` from `mosaicmesh.state` for backward compatibility.

---

## 6. New REST endpoints

All endpoints return JSON. Success: `{success: true, …}`. Error: `{success: false, error: "<message>"}` with appropriate HTTP status (400 validation, 404 not found, 409 conflict, 412 stale version, 500 server). All mutating endpoints accept `If-Match: <_serverVersion>` for optimistic concurrency.

### Playlists (`mosaicmesh/api/playlists.py`)

- `GET /api/playlists` → `{playlists: [{name, items, loop, _serverVersion}, ...]}`
- `POST /api/playlists` (body: `{name, items?, loop?}`) → `201 + {playlist}`
- `PUT /api/playlists/{name}` (body: `{items?, loop?}`, header: `If-Match`) → `200 + {playlist}` or `412`
- `DELETE /api/playlists/{name}` → `204` or `409` if referenced by any schedule (`{error, refs: [scheduleId, ...]}`)

### Schedules (`mosaicmesh/api/schedules.py`)

- `GET /api/schedules` → `{schedules: [Schedule, ...]}`
- `POST /api/schedules` (body: `{playlistName, displayID, freq?, interval?, byweekday?, dtstart?, end?, exdates?, startTime?, endTime?, priority?, enabled?}`) → `201 + {schedule}`. Validates: `playlistName` exists, `displayID` exists, `freq ∈ {DAILY,WEEKLY,MONTHLY,YEARLY}`, `endTime > startTime` (or wraps next-day cleanly).
- `PUT /api/schedules/{id}` (body: any subset of fields, header: `If-Match`) → `200 + {schedule}` or `412`
- `DELETE /api/schedules/{id}` → `204`

### Profiles (`mosaicmesh/api/profiles.py`)

- `GET /api/profiles` → `{profiles: [ScriptingProfile, ...]}`
- `POST /api/profiles` (body: full profile) → `201 + {profile}`
- `PUT /api/profiles/{name}` (header: `If-Match`) → `200 + {profile}` or `412`
- `DELETE /api/profiles/{name}` → `204` or `409` if any `Client.profileName` references it (`{error, refs: [clientKey, ...]}`)
- `POST /api/clients/{clientKey}/profile` (body: `{profileName}`) → `200`

### Media (`mosaicmesh/api/media.py`)

- `GET /api/media` → `{media: [{file, mtime, kind, durationMs, size, ...}, ...]}` — extracted from existing upload/listing handlers
- `POST /api/upload` — existing endpoint relocated; unchanged

The `_serverVersion` field is a monotonic integer attached to each `Playlist`, `Schedule`, `Profile`, and (optionally) `Client.displayID/defaultPlaylistName`-bearing fields. Bumped server-side on each mutation. Invisible to legacy SockJS clients (they ignore unknown fields).

---

## 7. Scripting Profiles

### Data model

New `ScriptingProfile` entity in `settings.profiles{name → ScriptingProfile}`:

```python
class ScriptingProfile:
    name: str                  # unique key (e.g. "ipad1-ios5")
    label: str                 # human label ("iPad 1 — iOS 5.1.1")
    matchDeviceType: str       # auto-assign on REGISTER (e.g. "Tablet"); "" = manual only
    scripts: dict              # {"login": "...", "start": "...", "stop": "...",
                               #  "test": "...", "reboot": "..."}
    launch: dict               # {"method": "shell" | "vnc-tap" | "ssh-then-vnc",
                               #  ...method-specific keys (vncPassword, taps, wakeScript)}
    webclip: dict              # {"bundleId": "com.apple.webapp-...", "title": "MosaicMesh"}
    ssh: dict                  # {"legacyCrypto": true, "user": "root", "keyPath": "..."}
    _serverVersion: int        # concurrency token
```

### Changes to `Client`

- **Add** `profileName: str | None` (assigned at REGISTER from first profile whose `matchDeviceType` matches `client.deviceType`; admin can override). **If no profile matches**, `profileName` stays `None`; the client appears in the UI with a *"⚠ Needs profile"* badge on its track header and lifecycle script invocations no-op with a server log warning until a profile is assigned (manually via the track-header dropdown or by editing `matchDeviceType` on an existing profile so the auto-match catches it).
- **Remove** `loginScript`, `startScript`, `stopScript`, `testScript`, `rebootScript`. On loading an existing `settings.dat`, these fields are discarded silently.

### Template variables

When a script (or launch step) runs, the server builds a substitution dict from:

| Source | Variables |
|---|---|
| Client object | `{clientID}`, `{ip}`, `{friendlyName}`, `{displayId}`, `{cacheMode}` |
| Server config | `{displayUrl}` (today's `DISPLAY_URL` constant) |
| Profile.webclip | `{webclipBundleId}`, `{webclipTitle}` |
| Profile.launch | `{vncPassword}`, `{fbX}`, `{fbY}` (when relevant to context) |

Substitution uses `str.format_map(SafeDict)` — unknown `{tokens}` are left literal, no error raised. Operators control shell quoting inside their templates; the substitution layer doesn't try to be clever.

### Launch dispatcher

`mosaicmesh/device_scripts.py` owns the *how* of running profile actions. It's the only Python that knows about SSH, VNC, or shell execution. The dispatcher table:

```python
LAUNCH_METHODS = {
    "shell":         lambda client, profile, vars_: _exec_ssh(client, profile.scripts["start"], vars_),
    "vnc-tap":       lambda client, profile, vars_: _vnc_tap_sequence(client, profile.launch, vars_),
    "ssh-then-vnc":  lambda client, profile, vars_: _ssh_then_vnc(client, profile, vars_),
}
```

`_exec_ssh(client, script_template, vars_)`, `_vnc_tap_sequence(client, launch_cfg, vars_)`, `_ssh_then_vnc(client, profile, vars_)` are the only Python bits with action mechanics. Everything else — the script content, the tap coordinates, the bundle id, the VNC password — lives in profile data.

### Profile editor UI

3-pane modal opened from the toolbar (gear icon next to Calibrate):

1. **Profile list** (left): scroll list of profiles + `+ New` + `Delete` buttons
2. **Profile form** (center): fields for `name`, `label`, `matchDeviceType`, five script textareas (login/start/stop/test/reboot), launch config (method-aware: tap coordinates appear only for `vnc-tap`/`ssh-then-vnc`), webclip config, ssh config
3. **Preview** (right): live-resolved version of the currently-edited script against a selected sample client; unresolved `{tokens}` highlighted in red

### Bootstrap & migration

On first server start with no profiles in `settings.dat`, seed `ipad1-ios5` from the existing hardcoded defaults:

```python
DEFAULT_PROFILE = ScriptingProfile(
    name="ipad1-ios5",
    label="iPad 1 — iOS 5.1.1",
    matchDeviceType="Tablet",
    scripts={
      "login":  "activator send libactivator.lockscreen.dismiss; sleep 1; "
                "activator send switch-off.com.a3tweaks.switch.autolock; "
                "su mobile -c 'defaults write com.apple.springboard SBOrientationLockedActive -bool YES' 2>/dev/null; "
                "su mobile -c 'defaults write com.apple.springboard SBOrientationLockedOrientation -int 1' 2>/dev/null; "
                "echo LOGIN_OK",
      "start":  "sbdidlaunch '{webclipBundleId}' 2>/dev/null || uiopen '{displayUrl}'; echo START_OK",
      "stop":   "killall Web 2>/dev/null; killall MobileSafari 2>/dev/null; "
                "activator send switch-on.com.a3tweaks.switch.autolock; "
                "activator send libactivator.system.sleepbutton; echo STOP_OK",
      "test":   "killall MobileSafari 2>/dev/null; sleep 1; uiopen '{displayUrl}?tdbg'; echo TEST_OK",
      "reboot": "echo REBOOTING; reboot",
    },
    launch={"method": "ssh-then-vnc", "vncPassword": "mosaicmesh",
            "wakeScript": "activator send libactivator.lockscreen.dismiss",
            "taps": [{"fbX": 945, "fbY": 671}]},
    webclip={"bundleId": "com.apple.webapp-4D6F736169634D6573684B696F736B31",
             "title": "MosaicMesh"},
    ssh={"legacyCrypto": True, "user": "root", "keyPath": "~/.ssh/mosaic_ipad"},
)
```

For every existing Client, the migration sets `profileName="ipad1-ios5"` and discards the old per-Client script fields. After bootstrap, `DEFAULT_DEVICE_SCRIPTS`, `WEBCLIP_BUNDLE_ID`, `WEBAPP_ICON_FBX`, `WEBAPP_ICON_FBY`, and `_launch_webapp_via_vnc` are **deleted from `server.py`**.

---

## 8. Data flow

### Initial load

1. Page navigates to `#timeline` → Alpine boots → `store.hydrate()` fires.
2. Parallel `fetch`s: `/api/discovery/devices`, `/api/playlists`, `/api/schedules`, `/api/media`, `/api/profiles`.
3. Store populates `displays[]`, `playlists{}`, `schedules[]`, `media[]`, `profiles{}`.
4. `util/time.js` expands schedules into visible clip placements for the current `(viewMode, viewDate)` window.
5. Alpine renders tracks + clips reactively.

### Edits — optimistic-local + server-confirm

| User action | Store mutation | Server call | Rollback on error |
|---|---|---|---|
| Drag playlist onto track at time T | append ephemeral schedule + expand + render clip | `POST /api/schedules` | remove ephemeral; toast |
| Drag clip edge (resize) | mutate `startTime`/`endTime` locally | `PUT /api/schedules/{id}` (+ If-Match) | revert to last server state; toast |
| Drag clip body (move) | mutate `startTime` (+ `displayID` if cross-track) | `PUT /api/schedules/{id}` | revert; toast |
| Delete clip | remove from `schedules[]` | `DELETE /api/schedules/{id}` | re-insert; toast |
| Double-click clip → reorder items inside | mutate `playlists[name].items` | `PUT /api/playlists/{name}` | revert items; toast |
| Drag media file from bin onto a drilled-in clip | append item to playlist | `PUT /api/playlists/{name}` | pop item; toast |
| Upload media | append optimistic placeholder to `media[]` | `POST /api/upload` | remove placeholder; toast |
| Edit display group name / default playlist | mutate `displays[i]` | `POST /api/discovery/configure` (existing) | revert; toast |
| Edit / create / delete profile | mutate `profiles{}` | `POST/PUT/DELETE /api/profiles[/{name}]` | revert; toast |

### Live updates (SockJS)

The timeline subscribes to the existing `socketmanager`. Broadcasts route into store mutations:

- `DISCOVERY_HEARTBEAT` / `CLIENTS_WENT_OFFLINE` → update `displays[i].onlineCount` and status dot
- `RENDER_IN_PROGRESS` → set per-track render badge on the affected display
- `PLAY` → highlight the active clip on the affected track

Now-line auto-advances every second from a single `setInterval`.

---

## 9. Interaction model

**Primary actions:**

| Action | Trigger | Result |
|---|---|---|
| Create schedule | Drag playlist from bin onto track + time | New `Schedule(playlistName, displayID, freq=DAILY, dtstart=today, startTime=drop, endTime=drop+1h)`. Recurrence editable in inspector. |
| Move clip | Drag clip body | Updates `startTime` (+ `displayID` if cross-track). 15-min snap; Shift = free placement. |
| Resize clip | Drag clip's left/right edge | Updates `startTime` or `endTime`. 15-min snap. |
| Delete clip | Select + `Del`, or right-click → Delete | Confirmation modal only if the schedule has multiple recurring instances visible. |
| Drill into clip | Double-click | Row expands to show playlist items as sub-clips. Double-click again to collapse. |
| Edit playlist items | While drilled in: drag media from bin onto sub-track; drag sub-clips to reorder; `Del` on a sub-clip | Mutates `playlist.items` via `PUT /api/playlists/{name}`. |
| Edit schedule recurrence | Click clip → recurrence popover, or right-click → Edit schedule | Form: freq, byweekday checkboxes (Weekly), dtstart, end (Never/Until/N), startTime, endTime, priority. |
| Set default playlist | Drag playlist from bin onto track's "idle" sub-row | Updates `displayGroup.defaultPlaylistName`. |
| Reload all displays in a group | Right-click track header → Reload | Existing `RELOAD` broadcast. |
| Override profile for a display | Click track header → popover with `profileName` dropdown | Calls `POST /api/clients/{key}/profile`. Auto-match still wins for any device the operator hasn't explicitly overridden. |
| Fleet-wide action | Toolbar buttons | Runs profile action on every device. Confirmation modal if >3 devices affected. |

**Selection model.** Single-select by clicking a clip; click empty area to deselect. Shift-click for multi-select (bulk delete). Selected clip gets a 2px outline.

**Conflict display.** Two schedules overlap on same track → higher-priority wins (matches server `schedule_active_at()`). Lower-priority clip renders with a diagonal-stripe overlay over the overlap region; hover tooltip shows: *"Lunch Menu (priority 10) overrides Morning News here."*

**Keyboard shortcuts:**

- `Del` — delete selection
- `1` / `2` / `3` — Day / Week / Month view
- `T` — go to Today
- `←` / `→` — previous / next day (week / month per view mode)

**Out of scope for v1:** undo/redo. Adds material store complexity; confirmation modals on destructive ops suffice. Add later if usage demands.

---

## 10. Error handling

| Class | Behavior |
|---|---|
| Client-side validation (`endTime < startTime`, empty name, etc.) | Caught at submit time; inline message on the form field. No API call. |
| Server 4xx (e.g. `{success:false, error:"Profile assigned to 24 devices"}`) | Rollback optimistic update. Toast shows the server's `error` string. |
| Server 5xx / network error | Rollback. Toast: *"Couldn't save — network issue. Retrying…"* Auto-retry 3× at 2/5/10s. After 3rd failure: persistent toast with manual Retry / Dismiss. |
| Concurrent edit conflict (`412 If-Match` failed) | Refetch the entity from server; store updates to new version; toast: *"'<name>' updated by another admin."* Last-write-wins for v1. |
| Offline device target | Allow the action. Track-header banner: *"Conf A — 0/1 online; changes apply when device returns."* No blocking error. |
| Profile template unresolved `{var}` | `SafeDict` leaves literal — operator may know it's environmental. Profile editor preview highlights unresolved tokens in red; warns (but doesn't block) on Save. |
| Profile deletion with references | `409` with `{refs:[clientKey,...]}`. UI offers bulk-reassign link. |
| Schedule overlap | Not rejected — overlap is legal, priority resolver picks. UI shows the diagonal-stripe overlay. |
| Calibration failure (0 markers detected) | Modal stays open with photo + result: *"Found 0 markers. Check lighting and that markers are visible. [Retry / Cancel]"* No state mutated. |
| Upload failure | Server returns specific error; toast shows it. Optimistic media placeholder removes itself. |

**Concurrency** uses `_serverVersion` + `If-Match` header (Section 6). New attribute added to `Playlist`, `Schedule`, `ScriptingProfile`. Bumped server-side on each successful mutation. Legacy SockJS clients ignore the field.

---

## 11. Testing

### Server-side (pytest, `python pytest_runner.py --unit`)

| What | Tests |
|---|---|
| Module split | None new — pure relocation. All existing `tests/unit/test_*.py` pass with updated imports. |
| `api/playlists.py` | `test_api_playlists.py`: GET list, POST create, PUT update, DELETE (incl. 404 when referenced by schedule), If-Match handling. |
| `api/schedules.py` | `test_api_schedules.py`: GET, POST (incl. validation), PUT, DELETE; If-Match handling. |
| `api/profiles.py` | `test_api_profiles.py`: GET, POST, PUT, DELETE (incl. 409 + refs list), If-Match handling. |
| Bootstrap | `test_profile_bootstrap.py`: empty `settings.dat` seeds default profile; existing `settings.dat` with `Client.loginScript` fields migrates them to profile reference. |
| Template substitution | `test_template_vars.py`: known `{var}` substituted; unknown left literal; nested braces handled; client + profile + global vars all reachable. |
| Launch dispatcher | `test_launch_dispatcher.py`: mock SSH + mock vnc; assert `method: shell` calls `_exec_ssh`, `method: vnc-tap` calls `_vnc_tap_sequence`, `method: ssh-then-vnc` calls both in order. |
| Client migration | `test_client_migration.py`: load fixture `settings.dat` with old per-Client script fields → after `migrate_client_objects()`, `profileName` set, old fields gone. |
| `_serverVersion` | `test_version_concurrency.py`: PUT without If-Match → 412; PUT with stale If-Match → 412; PUT with current → 200 + version bumps. |

### Client-side (new — pure-function Node tests)

```
tests/unit/js/
  test_time_recurrence.js   -- util/time.js: expand Schedule into visible clips
                              for various (viewMode, viewDate) windows.
                              Coverage: DAILY, WEEKLY+byweekday, MONTHLY, YEARLY,
                              exdates, dtstart in future, until-date, count-based
                              end, overnight schedules (startTime > endTime).
  test_clip_conflicts.js    -- conflict detector: given clips on a track,
                              return overlap regions + which priority wins.
```

Run with `node --test tests/unit/js/`. Modules in `js/timeline/util/` written as plain ES modules importable in both browser and Node — no DOM access. The recurrence expander is the highest-value unit-test target (pure logic, easy to get subtly wrong on timezones / overnight / exdates).

### Integration / E2E

No headless browser tests in v1 — matches the rest of the project's convention. Manual smoke checklist:

1. Load page → existing displays + schedules + playlists render correctly
2. Drag a playlist onto a track at 2pm → creates schedule → reload page → still there
3. Double-click clip → drill-in opens with playlist items
4. Drag a media file from bin onto sub-track → playlist gains an item → reload → persists
5. Switch Day → Week → Month → same data renders correctly each time
6. Delete a clip → confirm → reload → gone
7. Toolbar fleet actions (Start all / Stop all / Login all / Reboot all / Start Testing all) → verify on real devices
8. Open profile editor modal → edit a script → save → run start on a device → new script content fires
9. Calibration modal → upload photo → ArUco detected → measurements stored

**Coverage target:** ≥80% on `mosaicmesh/api/` and `mosaicmesh/device_scripts.py` via `python pytest_runner.py --unit --coverage`.

---

## 12. Rollout

Six independently revertable PRs. Each passes the full test suite + manual smoke before the next merges. The fleet runs untouched through PRs 1-3; the legacy SockJS protocol stays bit-identical throughout.

| # | PR | What changes | Fleet impact |
|---|---|---|---|
| 1 | **Server module split** | Move code from `server.py` into `mosaicmesh/*.py`. Existing tests pass unchanged (only imports update). | None — pure relocation. |
| 2 | **New REST endpoints** | Add `api/playlists.py`, `api/schedules.py`, `api/profiles.py` with full CRUD + `_serverVersion` + If-Match. Existing admin pages keep working. | None — additive. |
| 3 | **ScriptingProfile + migration** | Add `ScriptingProfile` to `state.py`; bootstrap `ipad1-ios5`; migrate Client script fields → `profileName`. Move `_run_device_script` (today in `server.py`) into `device_scripts.py` and rewrite it to: (a) look up `client.profileName` → profile, (b) build template vars dict, (c) format the script, (d) dispatch to the appropriate `LAUNCH_METHODS` entry. The legacy SockJS REQUEST callers (`RUNSCRIPT`, etc.) still call `_run_device_script` with the same signature — only the function body changes. Delete `DEFAULT_DEVICE_SCRIPTS`, `WEBCLIP_BUNDLE_ID`, `WEBAPP_ICON_FBX/FBY`, `_launch_webapp_via_vnc` from `server.py`. | New execution path; resulting shell is byte-identical to today's defaults. Smoke on screen1 before merging. **Backup `settings.dat` at this boundary.** |
| 4 | **Build timeline UI (additive)** | Add `js/timeline/` + new `<section data-route="timeline">` to `admin.html`. Add nav entry. Old four pages still navigable. Both UIs read the same server state. | None — additive. |
| 5 | **Switch admin home to timeline** | Flip default route from `displays` → `timeline`. Old pages still reachable via nav. | None — operator-facing only. |
| 6 | **Remove old sections** | Delete the four old `[data-route="…"]` blocks from `admin.html` + their inline JS. Remove nav entries. Delete dead code. | None. |

### `settings.dat` compatibility

jsonpickle tolerates added / removed attributes:

- A **new server loading an old `settings.dat`**: missing `settings.profiles` → bootstrap creates default; Client fields without `profileName` → migration sets it.
- An **old server loading a new `settings.dat`** (rollback PR-3 → PR-2): unknown `profileName` on Client is silently ignored; unknown top-level `profiles` dict ignored. Operator loses profile-editing ability but everything else functions.

PR-3 rollback caveat: lifecycle-script changes made via the profile editor after PR-3 deployed are lost on rollback (OLD server reads the per-Client scripts that the migration cleared). Hence: keep a `settings.dat` backup at the PR-3 boundary.

### No protocol changes for displays

The 24 iPads run ES5 client code that uses `msg_response()`'s REQUEST-based protocol. None of these PRs touch that surface. Per `CLAUDE.md`: the legacy protocol stays in `mosaicmesh/websocket/legacy.py`, behaviorally identical, just relocated.

### Implementation plans

This spec covers six PRs of work. Each PR is independently revertable and has clear boundaries — they can either share a single implementation plan with six well-bounded task groups, or be split into separate plans (e.g. PRs 1-2 as one plan, PR-3 as another, PRs 4-6 as a third). The writing-plans step (after spec approval) will decide based on plan-size constraints.

### Pacing guidance

- **PR-1** (module split) — riskiest for test-suite breakage but easy to validate (the diff is wide but mechanical).
- **PR-3** (profile migration) — riskiest for fleet behavior. Needs real-device smoke before merge.
- Recommended cadence: PRs 1-2 in one session, sit for a day of normal operation, then PR-3 with explicit *"smoke on screen1 + re-run onboarding to confirm"* checklist. PRs 4-6 can move quickly once 3 is stable.

---

## 13. Open questions

None at spec time. All design decisions captured above.

(Items that came up during brainstorming and were explicitly chosen:)

- Track axis = display group, not playlist (location-first)
- Clip granularity = scheduled playlist, with double-click drill into items (hybrid)
- Time-axis scopes = Day (default) + Week + Month toggles
- Page layout = Left bin (Premiere/Avid style)
- Migration = replace the four pages (Overview/Console/Discovery stay)
- Tech stack = Alpine.js + vanilla JS, no build step
- Server scope = module split + new endpoints + profiles in this spec; protocol & state normalization deferred
- Per-device script overrides removed entirely; all variation via profile + template variables
- Concurrency = `_serverVersion` + `If-Match`; last-write-wins on conflict with brief toast
- Undo/redo deferred

---

## Appendix A — File-by-file change summary

### New files

```
mosaicmesh/__init__.py
mosaicmesh/state.py
mosaicmesh/persistence.py
mosaicmesh/device_scripts.py
mosaicmesh/render.py
mosaicmesh/calibration.py
mosaicmesh/cache.py
mosaicmesh/websocket/__init__.py
mosaicmesh/websocket/legacy.py
mosaicmesh/websocket/typed.py
mosaicmesh/websocket/dispatch.py
mosaicmesh/api/__init__.py
mosaicmesh/api/discovery.py
mosaicmesh/api/playlists.py
mosaicmesh/api/schedules.py
mosaicmesh/api/profiles.py
mosaicmesh/api/media.py

js/timeline/index.js
js/timeline/store.js
js/timeline/api.js
js/timeline/toolbar.js
js/timeline/bin/media-bin.js
js/timeline/bin/playlist-bin.js
js/timeline/timeline/timeline.js
js/timeline/timeline/track-header.js
js/timeline/timeline/clip.js
js/timeline/timeline/clip-inspector.js
js/timeline/timeline/now-line.js
js/timeline/timeline/grid-axis.js
js/timeline/modals/calibration.js
js/timeline/modals/playlist-editor.js
js/timeline/modals/profile-editor.js
js/timeline/modals/recurrence-editor.js
js/timeline/util/time.js
js/timeline/util/template-vars.js

tests/unit/test_api_playlists.py
tests/unit/test_api_schedules.py
tests/unit/test_api_profiles.py
tests/unit/test_profile_bootstrap.py
tests/unit/test_template_vars.py
tests/unit/test_launch_dispatcher.py
tests/unit/test_client_migration.py
tests/unit/test_version_concurrency.py
tests/unit/js/test_time_recurrence.js
tests/unit/js/test_clip_conflicts.js
```

### Modified files

```
server.py                    -- shrinks to entry + route table; re-exports settings
                                from mosaicmesh.state for test compat
admin.html                   -- adds [data-route="timeline"] section; later removes
                                the four old sections (PR-6)
tests/unit/test_*.py         -- import updates after the module split
CLAUDE.md                    -- update Architecture + Layout sections to reflect
                                the module split + new entities
```

### Deleted from `server.py` (PR-3)

- `DEFAULT_DEVICE_SCRIPTS` dict
- `WEBCLIP_BUNDLE_ID` constant
- `WEBAPP_ICON_FBX`, `WEBAPP_ICON_FBY` constants
- `_launch_webapp_via_vnc()` function (replaced by `device_scripts._vnc_tap_sequence`)
- `_apply_default_scripts()` (replaced by `device_scripts.resolve_profile_for_client`)
- Per-Client `loginScript` / `startScript` / `stopScript` / `testScript` / `rebootScript` attributes (with one-shot migration)
