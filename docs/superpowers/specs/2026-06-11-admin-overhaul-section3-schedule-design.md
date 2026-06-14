# Admin Overhaul — Section 3 (Schedule)

**Date:** 2026-06-11
**Status:** Draft — approved in conversation
**Parent:** [Admin UI Overhaul IA](./2026-06-09-admin-ui-overhaul-design.md) → "Destination: Schedule". Third of four build sections (Shell+Now ✓, Content ✓, **Schedule**, Fleet).

**Goal:** Make the Schedule destination fully responsive and give it a create path that works without a mouse. The desktop tracks×hours grid already exists and is good; this section adds the mobile views (agenda + vertical timeline + day-sectioned week + month calendar) and a unified **"+ Schedule"** create flow, reusing the existing store, recurrence math, and recurrence-editor modal.

## Why

Today the Schedule destination renders **only** the desktop grid (`mmTimeline`: Day/Week/Month tracks×hours, drag/drop, drill-in, context menu, conflict stripes, now-line). It has two problems on a phone:

1. **No mobile layout.** The grid is CSS-grid columns with no responsive fallback; it's unusable at phone width.
2. **No touch create path.** A schedule can *only* be created by dragging a playlist from the left bin onto a track — pure HTML5 drag, impossible on touch. The recurrence editor only *edits* existing schedules (playlist + group are read-only in it).

The IA already prescribes the shape: agenda (mobile default) + vertical day-timeline (toggle) + the desktop grid. This section builds that, plus the missing create path.

## What already exists (reused, not rebuilt)

- **Desktop grid** — `js/timeline/timeline/*` (`timeline.js` ~346 lines + `grid-axis.js`, `track-header.js`, `clip.js`, `conflict-stripes.js`, `now-line.js`). Survives **unchanged**.
- **Recurrence math** — `js/timeline/util/time.js` `expandSchedule(s, startMs, endMs)` (pure, mirrors server `schedule_active_at`); `util/conflicts.js` `detectConflicts(placements)`.
- **Store schedule slice** — `js/timeline/store.js`: `schedules`, `displayGroups`, `viewMode`, `viewDate`, `selectedDisplay`, `selection`, `drilledIn`; `createSchedule(partial)`, `updateSchedule(id, patch)`, `deleteSchedule(id)`, `nextOccurrences(id, n, fromIso)`. All optimistic + `If-Match` + rollback already.
- **Recurrence-editor modal** — `js/timeline/modals/recurrence-editor.js`: full fields (dtstart, start/end time, freq, interval, byweekday, end-type, priority) + next-N preview. Already becomes a full-screen **sheet on mobile** via Section 1's `modal-shell.js`.
- **Drag interactions** — `js/timeline/drag/*`, `select.js`, `context-menu.js`, `drill-in.js`, `toolbar.js`. Desktop-only; untouched.

**Server:** no changes. `/api/schedules` GET/POST/PUT/DELETE already supports everything. This section is admin-only (`admin.html` + `js/timeline/`); the iPad-1 display clients are not touched.

## Decisions (from the brainstorm)

1. **Unified create path: "+ Schedule" → full editor.** A prominent button (desktop toolbar + mobile FAB) opens the recurrence editor in *create* mode with playlist + group pickers. Same editor edits and creates. Desktop drag-to-create stays as a convenience shortcut.
2. **Full scope parity on mobile** — Day, Week, and Month are all reachable on a phone (not desktop-only).
3. **Mobile Week = day-sectioned agenda** — seven day headers, each an agenda list (reuses the agenda row). *Not* a shrunken 7-column grid.
4. **Mobile Month = the existing calendar-with-dots renderer**, tap a day → Day-agenda for that date.
5. **Mobile Day = agenda (default) ↔ vertical day-timeline (toggle).**
6. **Agenda row richness = Rich** — type-color left border, time range, playlist name, now-highlight, content-type icon, proportional duration bar, recurrence hint, playlist-item sparkline, conflict badge.
7. **Responsive switch by width** — `<760px` renders the mobile component, `≥760px` renders the existing grid. Matches the shell's existing breakpoint.

## Architecture

### Responsive switch

A new reactive flag `store.isMobile`, set from `matchMedia('(max-width: 759px)')` and updated on its `change` event. The flag and its `matchMedia` listener are initialized once in `js/timeline/index.js` bootstrap (alongside the other listeners registered there) and written into the store, so `store.isMobile` is the single reactive source the markup binds to. The Schedule section in `admin.html` becomes:

```html
<section data-route="schedule" x-show="$store.mm.activeTab==='schedule'">
  <div x-show="!$store.mm.isMobile"> <!-- existing desktop: bin + toolbar + mmTimeline grid --> </div>
  <div x-show="$store.mm.isMobile"  x-data="mmScheduleMobile"> <!-- new mobile stack --> </div>
</section>
```

The desktop subtree gains one new control: a **"+ Schedule"** button in `mmToolbar`. Otherwise unchanged. The left playlist bin stays in the desktop subtree only (it's a drag source); it is absent on mobile.

### New modules — `js/timeline/schedule/`

- **`agenda-row.js`** — `agendaRowHtml(placement, playlist, opts)` → Rich row HTML string. `opts` carries `{ isNow, conflict, recurrenceText }`. The shared atom across Day-agenda and Week. Pure (string in, string out) for node testing.
- **`agenda-view.js`** — Day-agenda render: take the day's placements, group by display group (`groupPlacementsByGroup`), render a section per group of `agendaRowHtml`s. Empty groups show "nothing scheduled". The same module renders **Week** by iterating the 7 days of the week (`groupPlacementsByDay`), each day a header + its grouped agenda.
- **`vertical-timeline.js`** — Day, one group: hours 00–23 top-to-bottom, scheduled blocks as absolutely-positioned cards sized by duration, a now-line, and a group `<select>` (defaults to the first group). Tap a block → recurrence editor.
- **`schedule-mobile.js`** — the `mmScheduleMobile` Alpine component: owns the scope toggle (Day/Week/Month — reuses `store.viewMode`/`viewDate`), the Day density sub-toggle (agenda ↔ vertical, local UI state), the date stepper, and the "+ Schedule" FAB. Renders: Day → agenda or vertical; Week → day-sectioned agenda; Month → existing calendar-dots renderer (extracted from `timeline.js`'s `renderMonth` into a shared helper so both desktop and mobile call it), with tap-a-day switching to Day-agenda for that `viewDate`.

### Pure helpers (the testable core)

Co-located with the views (or in `js/timeline/schedule/util.js`):

- `groupPlacementsByGroup(placements)` → `{ [displayID]: placement[] }`, time-sorted.
- `groupPlacementsByDay(placements, dayIsoList)` → `{ [iso]: placement[] }`.
- `formatRecurrence(schedule)` → `"Daily"` / `"Mon–Fri"` / `"Every 2 weeks"` / `"Once"` / etc. (reads freq/interval/byweekday/end).
- `sparklineSegments(playlist)` → `[{ kind: 'image'|'video'|'animation', frac }]` from `playlist.items` + resolved durations (Auto items use the same 20s/probed default as the player; kind from `playmode==='SCRIPT'` ? animation : file extension).
- `isNowPlacement(placement, nowMs)` → bool (drives the now-highlight; reused by agenda + vertical).

`agendaRowHtml` composes these. Conflict info comes from the existing `detectConflicts`.

### Editor: create + edit from one form

`recurrence-editor.js` is extended:

- Existing `openRecurrenceEditor(store, scheduleId)` / `open(store, id)` → **edit** mode, unchanged: playlist + group read-only, Save → `store.updateSchedule`.
- New `openScheduleCreator(store, prefill = {})` → **create** mode: the same form, but the Playlist and Display rows are editable `<select>`s populated from `store.playlists` (names) and `store.displayGroups` (displayIDs). Defaults: `dtstart=today`, `startTime` from `prefill.startTime` or `"09:00"`, 1-hour duration, `freq='DAILY'`, `priority=0`. Display preselects `prefill.displayID` when given. Save → `store.createSchedule(draft)` (draft includes `playlistName` + `displayID` from the pickers). Validation: require both a playlist and a group before Save; reuse the existing "until needs a date" check.
- Shared form-building refactored so create and edit don't duplicate the field markup; the only difference is whether playlist/group are `<input disabled>` (edit) or `<select>` (create).

"+ Schedule" entry points both call `openScheduleCreator`: the desktop toolbar button (no prefill) and the mobile FAB (no prefill). Drag-to-create on desktop is unchanged (still calls `store.createSchedule` directly).

## Data flow

Unchanged from the established pattern:
- **Hydrate:** existing REST GETs populate the store; no new endpoint.
- **Mutate:** `createSchedule`/`updateSchedule`/`deleteSchedule` — optimistic-local + `If-Match` PUT/POST + rollback (412 → refetch-merge toast).
- **Live:** SockJS status broadcasts already route into the store; agenda/vertical now-highlight recompute reactively (and on a 1s tick like the now-line).

## Testing

- **node `--test`** (`tests/unit/js/`): `groupPlacementsByGroup`, `groupPlacementsByDay`, `formatRecurrence` (each freq/interval/byweekday/end shape), `sparklineSegments` (image/video/animation/mixed + Auto durations), `isNowPlacement`, and `agendaRowHtml` output assertions (contains time, name, now class when live, conflict badge when conflicting). Add the new modules to the module-load smoke (`test_timeline_smoke.js`).
- **Playwright e2e** (`tests/e2e/`): a new mobile-viewport spec — resize to phone width, assert agenda renders grouped by display group; tap a row → recurrence editor sheet opens; **"+ Schedule" → pick playlist + group → Save → the new row appears** (round-trips through `/api/schedules`); switch to Week → assert day-section headers; switch to Month → tap a day → Day-agenda for that date. Each spec creates + cleans up its own `__e2e_`-prefixed playlist/schedule (existing harness convention). The existing desktop-grid e2e is left intact and still runs at desktop viewport.
- **iPad-1:** not applicable — admin-only; no display-client change.

## File structure

- **Create:** `js/timeline/schedule/agenda-row.js`, `agenda-view.js`, `vertical-timeline.js`, `schedule-mobile.js`, `util.js` (pure helpers); `tests/unit/js/test_schedule_helpers.js`; `tests/e2e/test-schedule-mobile.spec.js`.
- **Modify:** `js/timeline/store.js` (add `isMobile` + its matchMedia wiring), `js/timeline/modals/recurrence-editor.js` (add `openScheduleCreator` + shared form), `js/timeline/toolbar.js` (add "+ Schedule" button), `js/timeline/timeline/timeline.js` (extract `renderMonth` calendar-dots into a shared helper the mobile month reuses — minimal, no behavior change), `admin.html` (responsive switch markup + mount `mmScheduleMobile`), `tests/unit/js/test_timeline_smoke.js` (register new modules), `js/timeline/index.js` (register the new component).

## Non-goals

- **No server / domain-model change.** `/api/schedules` is sufficient.
- **No touching the desktop grid's behavior** beyond extracting the month-calendar helper and adding one toolbar button.
- **No display-client (iPad-1) change.**
- **No new recurrence capabilities** — same freq/interval/byweekday/end model; this is presentation + a create path, not a scheduling-engine change.

## Decision log

- **Responsive by width, two trees, one store.** Mirrors the shell's 760px breakpoint; avoids a from-scratch rewrite — the good desktop grid stays.
- **Agenda row is the shared atom.** Day-agenda and Week both compose it; one component, tested once.
- **Full scope parity on mobile** (Day/Week/Month) at the operator's request, with mobile-appropriate forms (day-sectioned agenda for week, calendar-dots for month) rather than shrunken grids.
- **Rich agenda rows** — duration bar + item sparkline + recurrence hint + conflict badge, accepted as worth the extra render code.
- **One editor, two modes.** Create adds playlist/group pickers; edit keeps them read-only. Avoids a second modal and reuses the next-N preview.
- **Reuse `expandSchedule`/`detectConflicts`/store CRUD verbatim.** The mobile views are a new *presentation* over the same data and the same mutation paths.
