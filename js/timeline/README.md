# Admin Timeline (`js/timeline/`)

Client-side reactive timeline view for the admin console, loaded into
`admin.html` as ES modules under the `[data-route="timeline"]` section.

## Module map

- **`index.js`** — entry point; bootstraps Alpine, registers store + components.
- **`api.js`** — thin async wrappers around the PR-2 REST endpoints
  (`/api/playlists`, `/api/schedules`, `/api/profiles`, `/api/media`,
  `/api/discovery/devices`). GET-only in PR-4a.
- **`store.js`** — `Alpine.store('mm')`: single source of truth for
  hydrated server state + local UI state (viewMode, viewDate, selection).
- **`util/time.js`** — pure recurrence-expansion functions. Importable
  in Node for tests. Mirrors `mosaicmesh.scheduling.schedule_active_at`.
- **`util/conflicts.js`** — pure conflict-detection helpers.
- **`timeline/`** — render components (grid axis, track header, clip,
  now-line, top-level timeline renderer).
- **`schedule/`** — the responsive Schedule destination's mobile views
  (Section 3). Pure render helpers (`util.js`, `agenda-row.js`,
  `agenda-view.js`, `month-grid.js`, `vertical-timeline.js`) + the
  `mmScheduleMobile` Alpine component. `<760px` (store.isMobile) renders
  this stack; `≥760px` keeps the `timeline/` desktop grid. `month-grid.js`
  is shared by both. Create flow is the unified "+ Schedule" →
  `openScheduleCreator` in `modals/recurrence-editor.js`.
- **`toolbar.js`** — view-mode toggle + date nav (UI state only — no
  server mutations).
- **`bin/`** — left-bin sections (media library + playlist library).

## Data flow

`alpine:init` → register store → store.hydrate() fires five parallel
GETs → store populated → components reactively render. SockJS
broadcasts (`DISCOVERY_HEARTBEAT`, `CLIENTS_WENT_OFFLINE`,
`RENDER_IN_PROGRESS`) route into `store.setStatus()` for live indicator
updates. **PR-4a is read-only.** Drag/drop and click-mutate handlers
land in PR-4b; modals land in PR-4c.

## iPad-1 compatibility

Nothing in this directory runs on the iPad display client. Display
clients load `index.html` which uses ES5 + jQuery 1.x + SockJS. Admin
console runs modern JS — these modules use ES2020 imports, async/await,
optional chaining, etc.
