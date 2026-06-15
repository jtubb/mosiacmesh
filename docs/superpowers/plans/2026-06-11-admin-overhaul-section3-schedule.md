# Admin Overhaul — Section 3 (Schedule) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Schedule destination fully responsive — add mobile agenda + vertical-timeline + day-sectioned week + month views (the desktop tracks×hours grid survives unchanged) and a unified "+ Schedule" create flow — reusing the existing store, `expandSchedule`, and recurrence-editor.

**Architecture:** A reactive `store.isMobile` flag (matchMedia, 760px) switches the Schedule section between the existing `mmTimeline` grid (≥760px) and a new `mmScheduleMobile` component (<760px). All new presentation lives in a new `js/timeline/schedule/` folder built from **pure, node-tested render helpers** (the established pattern: data-shaping + string-render helpers are pure; the Alpine component computes args from the store and emits HTML via `x-html`). No server changes — `/api/schedules` already does everything.

**Tech Stack:** Alpine.js 3.x + native ES modules (admin side, no build step), `node --test` for JS units, Playwright for e2e. Schedule data flows through the existing optimistic-`If-Match`-rollback store mutators.

**Spec:** `docs/superpowers/specs/2026-06-11-admin-overhaul-section3-schedule-design.md`

**Branch:** `feature/admin-overhaul-section3` (already created, stacked on Section 2; the spec is committed there).

**Conventions to follow (verified against the codebase):**
- Placement shape from `expandSchedule(s, startMs, endMs)`: `{ startMs, endMs, playlistName, displayID, priority, scheduleId }` — note the field is **`scheduleId`**, not `id`.
- Schedule shape: `{ id, playlistName, displayID, dtstart, startTime, endTime, freq, interval, byweekday, end:{type,untilDate?,count?}, priority, enabled, _serverVersion }`. `byweekday` is an array of `0..6` where **0=Mon … 6=Sun**.
- Playlist item shape: `{ file, duration?, playmode?, backgroundColor? }`; an animation item is `playmode:'SCRIPT'` with `file` = the animation key.
- Times are UTC HH:MM; all date math uses `Date.UTC` / `getUTC*` (matches `util/time.js` + `timeline.js`).
- Node unit tests: `tests/unit/js/test_*.js`, `import { test } from 'node:test'; import assert from 'node:assert';`, import the module under test by relative path. Run with `python pytest_runner.py --js` or `node --test tests/unit/js/<file>.js`.
- e2e: `tests/e2e/test-*.spec.js`, `export default async function () {...}`, `BASE = process.env.MM_BASE_URL || 'http://localhost:3000'`, `__e2e_`-prefixed fixtures created+deleted over `page.request`, `chromium.launch()`, resilient `waitForFunction` (never fixed sleeps). Run with `node tests/e2e/run.js <substr>` (dev server must be up).
- Commit trailer (verbatim): `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## File Structure

**Create:**
- `js/timeline/schedule/util.js` — pure helpers: `groupPlacementsByGroup`, `groupPlacementsByDay`, `formatRecurrence`, `sparklineSegments`, `isNowPlacement`, plus shared `escapeText`/`escapeAttr`/`formatHm`/`basename`/`kindForItem`/`kindColor`/`kindIcon`.
- `js/timeline/schedule/agenda-row.js` — `agendaRowHtml(placement, playlist, opts)` (the Rich row; the shared atom).
- `js/timeline/schedule/agenda-view.js` — `agendaDayHtml(args)` + `agendaWeekHtml(args)` (pure assemblers).
- `js/timeline/schedule/month-grid.js` — `monthGridHtml({schedules, viewDate, displayID, expandSchedule})` (extracted from `timeline.js renderMonth`; shared desktop+mobile).
- `js/timeline/schedule/vertical-timeline.js` — `verticalTimelineHtml(args)` (one group, hours top-to-bottom).
- `js/timeline/schedule/schedule-mobile.js` — `mmScheduleMobileComponent()` (the Alpine controller).
- `tests/unit/js/test_schedule_helpers.js` — node units for `util.js` + `agenda-row.js` + `month-grid.js`.
- `tests/e2e/test-schedule-mobile.spec.js` — mobile-viewport e2e.

**Modify:**
- `js/timeline/store.js` — add `isMobile` flag + `setIsMobile(b)` setter.
- `js/timeline/index.js` — wire matchMedia → `store.setIsMobile`; register `mmScheduleMobile`.
- `js/timeline/modals/recurrence-editor.js` — add `openScheduleCreator(store, prefill)` (create mode with playlist+group pickers) + share the form builder.
- `js/timeline/toolbar.js` — add `openCreateSchedule()` method.
- `js/timeline/timeline/timeline.js` — `renderMonth()` delegates to `monthGridHtml` (no behavior change).
- `admin.html` — responsive switch markup in the `data-route="schedule"` section; mount `mmScheduleMobile`; add the desktop "+ Schedule" toolbar button.
- `tests/unit/js/test_timeline_smoke.js` — register the new modules.

---

## Phase A — Foundation: responsive flag + pure helpers + agenda row

### Task A1: `store.isMobile` flag + setter

**Files:**
- Modify: `js/timeline/store.js` (add to the returned object in `makeStore`, near `viewMode`/`viewDate` ~line 52-54)
- Test: `tests/unit/js/test_schedule_helpers.js` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_schedule_helpers.js` with just this test for now (more added in A2/A3):

```js
import { test } from 'node:test';
import assert from 'node:assert';
import { makeStore } from '../../../js/timeline/store.js';

test('store exposes isMobile (default false) and setIsMobile', () => {
  const s = makeStore();
  assert.equal(s.isMobile, false);
  s.setIsMobile(true);
  assert.equal(s.isMobile, true);
  s.setIsMobile(false);
  assert.equal(s.isMobile, false);
});
```

- [ ] **Step 2: Run it, verify it fails**

Run: `node --test tests/unit/js/test_schedule_helpers.js`
Expected: FAIL — `s.isMobile` is `undefined` / `s.setIsMobile is not a function`.

- [ ] **Step 3: Implement**

In `js/timeline/store.js`, add the field next to the other UI-state fields (after `selectedDisplay: null,` ~line 54):

```js
    // Section 3: responsive switch. Set from matchMedia in index.js.
    // The Schedule section binds to this to choose the mobile stack
    // (mmScheduleMobile) vs the desktop grid (mmTimeline).
    isMobile: false,
```

And add the setter next to the other UI-state mutations (after `selectDisplay(id) { ... },` ~line 164):

```js
    setIsMobile(b) { this.isMobile = !!b; },
```

- [ ] **Step 4: Run it, verify it passes**

Run: `node --test tests/unit/js/test_schedule_helpers.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add js/timeline/store.js tests/unit/js/test_schedule_helpers.js
git commit -m "feat(schedule): store.isMobile flag for the responsive Schedule switch

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task A2: pure helpers in `schedule/util.js`

**Files:**
- Create: `js/timeline/schedule/util.js`
- Test: `tests/unit/js/test_schedule_helpers.js` (append)

- [ ] **Step 1: Write the failing tests** (append to `test_schedule_helpers.js`)

```js
import {
  groupPlacementsByGroup, groupPlacementsByDay, formatRecurrence,
  sparklineSegments, isNowPlacement,
} from '../../../js/timeline/schedule/util.js';

const P = (startMs, endMs, displayID, scheduleId, playlistName = 'PL') =>
  ({ startMs, endMs, displayID, scheduleId, playlistName, priority: 0 });

test('groupPlacementsByGroup buckets by displayID, sorted by startMs', () => {
  const out = groupPlacementsByGroup([
    P(30, 40, 'B', 's3'), P(10, 20, 'A', 's1'), P(5, 8, 'A', 's2'),
  ]);
  assert.deepEqual(Object.keys(out).sort(), ['A', 'B']);
  assert.deepEqual(out.A.map(p => p.scheduleId), ['s2', 's1']); // 5<10
  assert.deepEqual(out.B.map(p => p.scheduleId), ['s3']);
});

test('groupPlacementsByDay buckets by UTC day iso, only for listed days', () => {
  const d1 = Date.UTC(2026, 5, 1, 9);   // 2026-06-01 09:00Z
  const d2 = Date.UTC(2026, 5, 2, 13);  // 2026-06-02 13:00Z
  const out = groupPlacementsByDay(
    [P(d1, d1 + 3600e3, 'A', 's1'), P(d2, d2 + 3600e3, 'A', 's2')],
    ['2026-06-01', '2026-06-02', '2026-06-03']);
  assert.deepEqual(out['2026-06-01'].map(p => p.scheduleId), ['s1']);
  assert.deepEqual(out['2026-06-02'].map(p => p.scheduleId), ['s2']);
  assert.deepEqual(out['2026-06-03'], []);
});

test('formatRecurrence covers freq/interval/byweekday/once', () => {
  assert.equal(formatRecurrence({ freq: 'DAILY', interval: 1 }), 'Daily');
  assert.equal(formatRecurrence({ freq: 'DAILY', interval: 3 }), 'Every 3 days');
  assert.equal(formatRecurrence({ freq: 'WEEKLY', byweekday: [0,1,2,3,4] }), 'Mon–Fri');
  assert.equal(formatRecurrence({ freq: 'WEEKLY', byweekday: [5,6] }), 'Sat, Sun');
  assert.equal(formatRecurrence({ freq: 'WEEKLY', byweekday: [], interval: 2 }), 'Every 2 weeks');
  assert.equal(formatRecurrence({ freq: 'MONTHLY', interval: 1 }), 'Monthly');
  assert.equal(formatRecurrence({ freq: 'YEARLY', interval: 1 }), 'Yearly');
  assert.equal(formatRecurrence({ freq: 'DAILY', interval: 1, end: { type: 'count', count: 1 } }), 'Once');
});

test('sparklineSegments derives kind + fraction from items', () => {
  const pl = { items: [
    { file: 'a.jpg', duration: 10 },
    { file: 'b.mp4', duration: 30 },
    { file: 'lissajous', playmode: 'SCRIPT', duration: 10 },
  ] };
  const segs = sparklineSegments(pl);
  assert.deepEqual(segs.map(s => s.kind), ['image', 'video', 'animation']);
  // total 50 -> 0.2 / 0.6 / 0.2
  assert.ok(Math.abs(segs[1].frac - 0.6) < 1e-9);
});

test('sparklineSegments falls back to equal slices when no durations', () => {
  const segs = sparklineSegments({ items: [{ file: 'a.jpg' }, { file: 'b.jpg' }] });
  assert.ok(Math.abs(segs[0].frac - 0.5) < 1e-9);
});

test('isNowPlacement is half-open [start, end)', () => {
  assert.equal(isNowPlacement({ startMs: 10, endMs: 20 }, 10), true);
  assert.equal(isNowPlacement({ startMs: 10, endMs: 20 }, 19), true);
  assert.equal(isNowPlacement({ startMs: 10, endMs: 20 }, 20), false);
  assert.equal(isNowPlacement({ startMs: 10, endMs: 20 }, 9), false);
});
```

- [ ] **Step 2: Run it, verify it fails**

Run: `node --test tests/unit/js/test_schedule_helpers.js`
Expected: FAIL — module `schedule/util.js` not found.

- [ ] **Step 3: Implement** — create `js/timeline/schedule/util.js`

```js
/**
 * Pure helpers shared by the mobile Schedule views (Section 3).
 * No DOM, no fetch — importable in Node for tests.
 *
 * Placement shape (from util/time.js expandSchedule):
 *   { startMs, endMs, playlistName, displayID, priority, scheduleId }
 * byweekday: 0=Mon .. 6=Sun (matches util/time.js + the recurrence editor).
 */

const DOW_SHORT = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export function escapeText(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
export function escapeAttr(s) {
  return escapeText(s).replace(/"/g, '&quot;');
}
export function formatHm(ms) {
  const d = new Date(ms);
  return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
}
export function basename(p) { return String(p || '').split('/').pop() || ''; }

/** Content kind of a playlist item: 'animation' | 'video' | 'image'. */
export function kindForItem(it) {
  if (it && it.playmode === 'SCRIPT') return 'animation';
  const f = (it && it.file) || (typeof it === 'string' ? it : '');
  return /\.(mp4|webm|mov)$/i.test(f) ? 'video' : 'image';
}
export function kindColor(kind) {
  return kind === 'video' ? '#22c55e'
       : kind === 'animation' ? '#8b5cf6'
       : kind === 'mixed' ? '#ffb84e'
       : '#4ea1ff'; // image / default
}
export function kindIcon(kind) {
  return kind === 'video' ? '▶' : kind === 'animation' ? '✦' : kind === 'mixed' ? '◫' : '▦';
}

/** A playlist's dominant kind: the single kind of all items, else 'mixed'. */
export function playlistKind(playlist) {
  const items = (playlist && playlist.items) || [];
  if (items.length === 0) return 'image';
  const kinds = new Set(items.map(kindForItem));
  return kinds.size === 1 ? [...kinds][0] : 'mixed';
}

/** { [displayID]: placement[] }, each bucket sorted by startMs ascending. */
export function groupPlacementsByGroup(placements) {
  const out = {};
  for (const p of placements) {
    (out[p.displayID] = out[p.displayID] || []).push(p);
  }
  for (const k of Object.keys(out)) out[k].sort((a, b) => a.startMs - b.startMs);
  return out;
}

/** { [iso]: placement[] } for exactly the days in dayIsoList (empty arrays kept). */
export function groupPlacementsByDay(placements, dayIsoList) {
  const out = {};
  for (const iso of dayIsoList) out[iso] = [];
  for (const p of placements) {
    const d = new Date(p.startMs);
    const iso = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
    if (iso in out) out[iso].push(p);
  }
  for (const iso of dayIsoList) out[iso].sort((a, b) => a.startMs - b.startMs);
  return out;
}

/** Human recurrence label: "Daily" / "Mon–Fri" / "Every 2 weeks" / "Once". */
export function formatRecurrence(s) {
  if (!s) return '';
  if (s.end && s.end.type === 'count' && Number(s.end.count) === 1) return 'Once';
  const interval = Number(s.interval) || 1;
  const freq = s.freq || 'DAILY';
  if (freq === 'DAILY') return interval === 1 ? 'Daily' : `Every ${interval} days`;
  if (freq === 'WEEKLY') {
    const bwd = (s.byweekday || []).slice().sort((a, b) => a - b);
    let days = '';
    if (bwd.length && !(interval > 1)) {
      if (bwd.length === 5 && bwd.every((v, i) => v === i)) days = 'Mon–Fri';
      else days = bwd.map(i => DOW_SHORT[i]).join(', ');
    }
    if (interval > 1) return `Every ${interval} weeks`;
    return days || 'Weekly';
  }
  if (freq === 'MONTHLY') return interval === 1 ? 'Monthly' : `Every ${interval} months`;
  if (freq === 'YEARLY') return interval === 1 ? 'Yearly' : `Every ${interval} years`;
  return '';
}

/** [{kind, frac}] for the playlist-item sparkline (duration-proportional, else equal). */
export function sparklineSegments(playlist) {
  const items = (playlist && playlist.items) || [];
  if (items.length === 0) return [];
  const durs = items.map(it => Number((it && it.duration) || 0));
  const total = durs.reduce((a, b) => a + b, 0);
  return items.map((it, i) => ({
    kind: kindForItem(it),
    frac: total > 0 ? durs[i] / total : 1 / items.length,
  }));
}

/** Half-open [startMs, endMs) "is playing now" test. */
export function isNowPlacement(p, nowMs) {
  return nowMs >= p.startMs && nowMs < p.endMs;
}
```

- [ ] **Step 4: Run it, verify it passes**

Run: `node --test tests/unit/js/test_schedule_helpers.js`
Expected: PASS (all A1 + A2 tests).

- [ ] **Step 5: Commit**

```bash
git add js/timeline/schedule/util.js tests/unit/js/test_schedule_helpers.js
git commit -m "feat(schedule): pure helpers (grouping, recurrence label, sparkline)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task A3: `agenda-row.js` — the Rich row atom

**Files:**
- Create: `js/timeline/schedule/agenda-row.js`
- Test: `tests/unit/js/test_schedule_helpers.js` (append)

- [ ] **Step 1: Write the failing tests** (append)

```js
import { agendaRowHtml } from '../../../js/timeline/schedule/agenda-row.js';

const playlist = { name: 'Lunch', items: [{ file: 'a.mp4', duration: 30 }, { file: 'b.jpg', duration: 10 }] };
const placement = {
  startMs: Date.UTC(2026, 5, 1, 12), endMs: Date.UTC(2026, 5, 1, 13),
  playlistName: 'Lunch', displayID: 'Lobby', priority: 0, scheduleId: 'sched-1',
};

test('agendaRowHtml renders time, name, data-schedule-id and a sparkline', () => {
  const html = agendaRowHtml(placement, playlist, { isNow: false, conflict: false, recurrenceText: 'Daily' });
  assert.match(html, /12:00–13:00/);
  assert.match(html, /Lunch/);
  assert.match(html, /data-schedule-id="sched-1"/);
  assert.match(html, /mm-agenda-spark/);
  assert.match(html, /Daily/);
});

test('agendaRowHtml flags now and conflict', () => {
  const now = agendaRowHtml(placement, playlist, { isNow: true, conflict: false, recurrenceText: '' });
  assert.match(now, /mm-agenda-now/);
  const conflict = agendaRowHtml(placement, playlist, { isNow: false, conflict: true, recurrenceText: '' });
  assert.match(conflict, /mm-agenda-conflict/);
});

test('agendaRowHtml escapes the playlist name', () => {
  const html = agendaRowHtml({ ...placement, playlistName: '<x>' }, { name: '<x>', items: [] },
    { isNow: false, conflict: false, recurrenceText: '' });
  assert.doesNotMatch(html, /<x>/);
  assert.match(html, /&lt;x&gt;/);
});
```

- [ ] **Step 2: Run it, verify it fails**

Run: `node --test tests/unit/js/test_schedule_helpers.js`
Expected: FAIL — module `schedule/agenda-row.js` not found.

- [ ] **Step 3: Implement** — create `js/timeline/schedule/agenda-row.js`

```js
/**
 * The Rich agenda row — the shared atom across Day-agenda and Week
 * (day-sectioned). Pure: (placement, playlist, opts) -> HTML string.
 *
 * opts: { isNow: bool, conflict: bool, recurrenceText: string }
 */
import {
  escapeText, escapeAttr, formatHm, sparklineSegments,
  playlistKind, kindColor, kindIcon,
} from './util.js';

export function agendaRowHtml(placement, playlist, opts = {}) {
  const { isNow = false, conflict = false, recurrenceText = '' } = opts;
  const kind = playlistKind(playlist);
  const color = kindColor(kind);
  const icon = kindIcon(kind);
  const segs = sparklineSegments(playlist);
  const spark = segs.map(s =>
    `<span class="mm-agenda-seg" style="flex:${(s.frac * 1000) | 0};background:${kindColor(s.kind)}"></span>`
  ).join('');
  const cls = 'mm-agenda-row' + (isNow ? ' mm-agenda-now' : '');
  return `<div class="${cls}" data-schedule-id="${escapeAttr(placement.scheduleId)}" style="border-left:4px solid ${color}">
    <div class="mm-agenda-main">
      <span class="mm-agenda-ic">${icon}</span>
      <span class="mm-agenda-time">${formatHm(placement.startMs)}–${formatHm(placement.endMs)}</span>
      <span class="mm-agenda-name">${escapeText(placement.playlistName)}</span>
      ${isNow ? '<span class="mm-agenda-live">▶ now</span>' : ''}
      ${conflict ? '<span class="mm-agenda-conflict">conflict</span>' : ''}
      ${recurrenceText ? `<span class="mm-agenda-recur">· ${escapeText(recurrenceText)}</span>` : ''}
    </div>
    <div class="mm-agenda-spark">${spark}</div>
  </div>`;
}
```

- [ ] **Step 4: Run it, verify it passes**

Run: `node --test tests/unit/js/test_schedule_helpers.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add js/timeline/schedule/agenda-row.js tests/unit/js/test_schedule_helpers.js
git commit -m "feat(schedule): Rich agenda row (type accent, sparkline, now/conflict)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase B — Agenda + Week views + the mobile component

### Task B1: `agenda-view.js` — Day + Week assemblers

**Files:**
- Create: `js/timeline/schedule/agenda-view.js`
- Test: `tests/unit/js/test_schedule_helpers.js` (append)

The assemblers are pure: they take already-computed placements/playlists/conflicts/nowMs and produce HTML. The component (B2) feeds them from the store.

- [ ] **Step 1: Write the failing tests** (append)

```js
import { agendaDayHtml, agendaWeekHtml } from '../../../js/timeline/schedule/agenda-view.js';

const day = Date.UTC(2026, 5, 1);
const mk = (h, sid, did = 'Lobby', pl = 'Lunch') =>
  ({ startMs: day + h * 3600e3, endMs: day + (h + 1) * 3600e3, displayID: did, scheduleId: sid, playlistName: pl, priority: 0 });

const playlists = { Lunch: { name: 'Lunch', items: [{ file: 'a.mp4', duration: 30 }] } };

test('agendaDayHtml renders a section per group with its rows', () => {
  const html = agendaDayHtml({
    tracks: ['Lobby', 'Cafe'],
    placements: [mk(9, 's1'), mk(12, 's2'), mk(8, 's3', 'Cafe')],
    playlists, schedules: [], nowMs: day + 9.5 * 3600e3,
  });
  assert.match(html, /Lobby/);
  assert.match(html, /Cafe/);
  assert.match(html, /data-schedule-id="s1"/);
  assert.match(html, /data-schedule-id="s3"/);
  // The 09:00 row is "now" at 09:30.
  assert.match(html, /mm-agenda-now/);
});

test('agendaDayHtml shows empty-state for a group with no placements', () => {
  const html = agendaDayHtml({ tracks: ['Empty'], placements: [], playlists, schedules: [], nowMs: 0 });
  assert.match(html, /nothing scheduled/i);
});

test('agendaWeekHtml renders 7 day-section headers', () => {
  const html = agendaWeekHtml({
    weekStartMs: day, tracks: ['Lobby'],
    placements: [mk(9, 's1')], playlists, schedules: [], nowMs: 0,
  });
  // 7 day headers (Mon..Sun of the week containing `day`).
  const headers = (html.match(/mm-agenda-day-header/g) || []).length;
  assert.equal(headers, 7);
  assert.match(html, /data-schedule-id="s1"/);
});
```

- [ ] **Step 2: Run it, verify it fails**

Run: `node --test tests/unit/js/test_schedule_helpers.js`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** — create `js/timeline/schedule/agenda-view.js`

```js
/**
 * Day-agenda and Week (day-sectioned) assemblers. Pure string builders;
 * the mmScheduleMobile component computes the args from the store.
 *
 * agendaDayHtml({ tracks, placements, playlists, schedules, nowMs })
 *   - placements: all placements for the visible day (any group)
 *   - tracks: ordered displayIDs to show as sections
 * agendaWeekHtml({ weekStartMs, tracks, placements, playlists, schedules, nowMs })
 *   - placements: all placements across the 7-day window
 */
import { detectConflicts } from '../util/conflicts.js';
import {
  groupPlacementsByGroup, groupPlacementsByDay, isNowPlacement, escapeText,
  formatRecurrence,
} from './util.js';
import { agendaRowHtml } from './agenda-row.js';

const DAY_MS = 24 * 60 * 60 * 1000;

function recurrenceTextFor(schedules, scheduleId) {
  const s = schedules.find(x => x.id === scheduleId);
  return s ? formatRecurrence(s) : '';
}

/**
 * Render one group's rows. Conflict detection runs on THIS GROUP'S
 * placements only — detectConflicts has no per-display segmentation, so
 * feeding it cross-group placements would fabricate bogus conflicts
 * (a Lobby high-priority schedule "conflicting" with a Cafe one).
 */
function rowsForGroup(groupPlacements, playlists, schedules, nowMs) {
  if (groupPlacements.length === 0) {
    return '<div class="mm-agenda-empty">nothing scheduled</div>';
  }
  const losers = new Set(detectConflicts(groupPlacements).map(c => c.loserId));
  return groupPlacements.map(p => agendaRowHtml(p, playlists[p.playlistName], {
    isNow: isNowPlacement(p, nowMs),
    conflict: losers.has(p.scheduleId),
    recurrenceText: recurrenceTextFor(schedules, p.scheduleId),
  })).join('');
}

export function agendaDayHtml({ tracks, placements, playlists, schedules, nowMs }) {
  const byGroup = groupPlacementsByGroup(placements);
  let html = '<div class="mm-agenda">';
  for (const did of tracks) {
    const gp = byGroup[did] || [];
    html += `<section class="mm-agenda-group"><h3 class="mm-agenda-group-title">${escapeText(did)}</h3>`;
    html += rowsForGroup(gp, playlists, schedules, nowMs);
    html += '</section>';
  }
  html += '</div>';
  return html;
}

export function agendaWeekHtml({ weekStartMs, tracks, placements, playlists, schedules, nowMs }) {
  // Seven day-section headers; under each, the day's agenda grouped by group.
  const dayIsos = [];
  for (let i = 0; i < 7; i++) {
    const d = new Date(weekStartMs + i * DAY_MS);
    dayIsos.push(`${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`);
  }
  const byDay = groupPlacementsByDay(placements, dayIsos);
  let html = '<div class="mm-agenda mm-agenda-week">';
  for (let i = 0; i < 7; i++) {
    const iso = dayIsos[i];
    const dayMs = weekStartMs + i * DAY_MS;
    const label = new Date(dayMs).toLocaleDateString('en-US',
      { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' });
    const dayPlacements = byDay[iso];
    html += `<div class="mm-agenda-day-header">${escapeText(label)}</div>`;
    if (dayPlacements.length === 0) {
      html += '<div class="mm-agenda-empty">nothing scheduled</div>';
      continue;
    }
    const byGroup = groupPlacementsByGroup(dayPlacements);
    for (const did of tracks) {
      const gp = byGroup[did] || [];
      if (gp.length === 0) continue; // week view: omit empty groups to stay compact
      html += `<section class="mm-agenda-group"><h4 class="mm-agenda-group-title">${escapeText(did)}</h4>`;
      html += rowsForGroup(gp, playlists, schedules, nowMs);
      html += '</section>';
    }
  }
  html += '</div>';
  return html;
}
```

- [ ] **Step 4: Run it, verify it passes**

Run: `node --test tests/unit/js/test_schedule_helpers.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add js/timeline/schedule/agenda-view.js tests/unit/js/test_schedule_helpers.js
git commit -m "feat(schedule): Day + day-sectioned Week agenda assemblers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task B2: `mmScheduleMobile` component + admin.html responsive switch + register

**Files:**
- Create: `js/timeline/schedule/schedule-mobile.js`
- Modify: `js/timeline/index.js` (import + register + matchMedia wiring)
- Modify: `admin.html:796-856` (responsive switch markup)

This task wires the agenda views into the page so they're visible at phone width. Vertical timeline + month land in Phase C; until then the Day density toggle shows agenda only and Week works; Month/vertical render a "coming up in this section" placeholder that Phase C replaces. (No placeholder text ships — Phase C completes before the PR; the interim render just falls through to agenda.)

- [ ] **Step 1: Create the component** — `js/timeline/schedule/schedule-mobile.js`

```js
/**
 * mmScheduleMobile — the phone Schedule stack (Section 3). Renders one of:
 *   - Day:   agenda (default) or vertical timeline (density sub-toggle)
 *   - Week:  day-sectioned agenda
 *   - Month: calendar-with-dots (tap a day -> Day agenda for that date)
 * via x-html, mirroring mmTimeline's compute-in-component / render-string
 * pattern. Reads viewMode/viewDate/schedules/displayGroups from the store.
 *
 * Phase C adds verticalTimelineHtml + monthGridHtml wiring; until then Day
 * density is agenda-only and Month falls through to agenda for the day.
 */
import { expandSchedule } from '../util/time.js';
import { agendaDayHtml, agendaWeekHtml } from './agenda-view.js';
import { openRecurrenceEditor, openScheduleCreator } from '../modals/recurrence-editor.js';

const DAY_MS = 24 * 60 * 60 * 1000;

function isoToUtcMidnight(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return Date.UTC(y, m - 1, d);
}

export function mmScheduleMobileComponent() {
  return {
    density: 'agenda',          // 'agenda' | 'vertical' (Day scope only)
    nowTick: 0,                 // bumped on a 30s interval to refresh now-state
    _timer: null,

    init() {
      this._timer = setInterval(() => { this.nowTick++; }, 30_000);
      // Open the recurrence editor when an agenda row is tapped.
      this.$root.addEventListener('click', (ev) => {
        const row = ev.target.closest('[data-schedule-id]');
        if (row) openRecurrenceEditor(this.$store.mm, row.dataset.scheduleId);
      });
    },
    destroy() { if (this._timer) clearInterval(this._timer); },

    setDensity(d) { this.density = d; },
    openCreate() { openScheduleCreator(this.$store.mm, {}); },

    get tracks() {
      const groups = this.$store.mm.displayGroups;
      if (groups && groups.length > 0) return groups.map(g => g.displayID).filter(Boolean);
      const ids = new Set();
      for (const d of this.$store.mm.displays) if (d.displayID) ids.add(d.displayID);
      return Array.from(ids);
    },

    _dayWindow() {
      const startMs = isoToUtcMidnight(this.$store.mm.viewDate);
      return { startMs, endMs: startMs + DAY_MS };
    },
    _weekStartMs() {
      const baseMs = isoToUtcMidnight(this.$store.mm.viewDate);
      const dow = (new Date(baseMs).getUTCDay() + 6) % 7; // Mon=0
      return baseMs - dow * DAY_MS;
    },
    _expandWindow(startMs, endMs) {
      const out = [];
      for (const s of this.$store.mm.schedules) out.push(...expandSchedule(s, startMs, endMs));
      return out;
    },

    render() {
      // Touch nowTick so Alpine re-renders this view on the 30s tick.
      void this.nowTick;
      if (!this.$store.mm.hydrated) return '<div style="color:var(--text-muted)">Loading…</div>';
      const mode = this.$store.mm.viewMode;
      const nowMs = Date.now();
      const playlists = this.$store.mm.playlists;
      const schedules = this.$store.mm.schedules;

      if (mode === 'week') {
        const weekStartMs = this._weekStartMs();
        const placements = this._expandWindow(weekStartMs, weekStartMs + 7 * DAY_MS);
        return agendaWeekHtml({ weekStartMs, tracks: this.tracks, placements, playlists, schedules, nowMs });
      }
      // Day (and Month before Phase C) -> day agenda.
      const win = this._dayWindow();
      const placements = this._expandWindow(win.startMs, win.endMs);
      return agendaDayHtml({ tracks: this.tracks, placements, playlists, schedules, nowMs });
    },
  };
}
```

- [ ] **Step 2: Register in `js/timeline/index.js`**

Add the import near the other component imports (after line 42):

```js
import { mmScheduleMobileComponent } from './schedule/schedule-mobile.js';
```

Register it in `bootstrap()` next to the other `Alpine.data(...)` calls (after the `mmContent` registration ~line 64):

```js
  // eslint-disable-next-line no-undef
  Alpine.data('mmScheduleMobile', mmScheduleMobileComponent);
```

Wire matchMedia to the store inside `bootstrap()`, right after `const store = Alpine.store('mm');` (~line 66):

```js
  // Section 3: drive store.isMobile from the viewport so the Schedule
  // section can switch between the desktop grid and the mobile stack.
  if (typeof window.matchMedia === 'function') {
    const mq = window.matchMedia('(max-width: 759px)');
    store.setIsMobile(mq.matches);
    const onChange = (e) => store.setIsMobile(e.matches);
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else if (mq.addListener) mq.addListener(onChange); // older Safari
  }
```

- [ ] **Step 3: Responsive switch markup in `admin.html`**

Replace the single `<div x-show="$store.mm.hydrated" class="mm-timeline-layout">…</div>` block (currently `admin.html:800-854`) so the existing desktop layout is wrapped in a desktop-only guard and a mobile subtree is added. Keep the existing inner desktop markup verbatim; only wrap it and add the mobile block + FAB:

```html
        <!-- Desktop: the existing tracks×hours grid (unchanged) -->
        <div x-show="$store.mm.hydrated && !$store.mm.isMobile" class="mm-timeline-layout">
          <!-- ... existing <aside class="mm-bin"> ... and <div class="mm-timeline-main"> ... unchanged ... -->
        </div>

        <!-- Mobile: agenda / vertical / week / month stack (Section 3) -->
        <div x-show="$store.mm.hydrated && $store.mm.isMobile" x-data="mmScheduleMobile" class="mm-schedule-mobile">
          <div class="mm-sched-mobile-toolbar">
            <button class="btn" :class="{'btn-active': $store.mm.viewMode==='day'}"   @click="$store.mm.setViewMode('day')">Day</button>
            <button class="btn" :class="{'btn-active': $store.mm.viewMode==='week'}"  @click="$store.mm.setViewMode('week')">Week</button>
            <button class="btn" :class="{'btn-active': $store.mm.viewMode==='month'}" @click="$store.mm.setViewMode('month')">Month</button>
            <span style="flex:1"></span>
            <template x-if="$store.mm.viewMode==='day'">
              <span class="mm-density-toggle">
                <button class="btn btn-ghost" :class="{'btn-active': density==='agenda'}"   @click="setDensity('agenda')" title="Agenda list">☰</button>
                <button class="btn btn-ghost" :class="{'btn-active': density==='vertical'}" @click="setDensity('vertical')" title="Day timeline">▤</button>
              </span>
            </template>
          </div>
          <div class="mm-sched-mobile-datenav" x-data="mmToolbar">
            <button class="btn btn-ghost" @click="step(-1)">◀</button>
            <span class="mm-toolbar-date" x-text="formatDate()"></span>
            <button class="btn btn-ghost" @click="step(1)">▶</button>
            <button class="btn btn-ghost" @click="today()">Today</button>
          </div>
          <div x-html="render()"></div>
          <button class="btn btn-primary mm-sched-fab" @click="openCreate()" title="Schedule a playlist">+ Schedule</button>
        </div>
```

> Implementation note for the worker: do NOT rewrite the desktop inner markup — leave `admin.html:801-853` (`<aside class="mm-bin">` through the closing `</div>` of `.mm-timeline-main`) byte-for-byte; only change the wrapper `<div>`'s `x-show` to add `&& !$store.mm.isMobile`, then append the new mobile `<div>` block after it (before the section's closing `</section>` at line 856).

- [ ] **Step 4: Manual smoke** (no automated test in this step — the e2e in Phase E covers it; verify the module loads)

Run: `node --test tests/unit/js/test_timeline_smoke.js`
Expected: this still passes (it doesn't import schedule-mobile yet — that's added in Phase E Task E1). Also run `node -e "import('./js/timeline/schedule/schedule-mobile.js').then(()=>console.log('ok'))"` from the repo root.
Expected: prints `ok` (no syntax/import error).

- [ ] **Step 5: Commit**

```bash
git add js/timeline/schedule/schedule-mobile.js js/timeline/index.js admin.html
git commit -m "feat(schedule): mobile component + responsive switch (agenda + week live)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase C — Vertical timeline + Month drill-in

### Task C1: extract `monthGridHtml` and reuse in desktop + mobile

**Files:**
- Create: `js/timeline/schedule/month-grid.js`
- Modify: `js/timeline/timeline/timeline.js:199-242` (`renderMonth` delegates)
- Modify: `js/timeline/schedule/schedule-mobile.js` (Month branch)
- Test: `tests/unit/js/test_schedule_helpers.js` (append)

- [ ] **Step 1: Write the failing test** (append)

```js
import { monthGridHtml } from '../../../js/timeline/schedule/month-grid.js';
import { expandSchedule as expand } from '../../../js/timeline/util/time.js';

test('monthGridHtml renders a weekday header + day cells with dots', () => {
  const sched = [{
    id: 'm1', playlistName: 'Lunch', displayID: 'Lobby', dtstart: '2026-06-01',
    startTime: '12:00', endTime: '13:00', freq: 'DAILY', interval: 1, priority: 0,
    end: { type: 'never' },
  }];
  const html = monthGridHtml({ schedules: sched, viewDate: '2026-06-15', displayID: 'Lobby', expandSchedule: expand });
  assert.match(html, /mm-month-grid/);
  assert.match(html, /mm-month-cell/);
  assert.match(html, /mm-month-dot/);          // daily schedule -> dots present
  assert.match(html, /data-day-iso="2026-06-01"/); // tappable day cells
});

test('monthGridHtml returns a prompt when no display selected', () => {
  const html = monthGridHtml({ schedules: [], viewDate: '2026-06-15', displayID: null, expandSchedule: expand });
  assert.match(html, /Pick a display/i);
});
```

- [ ] **Step 2: Run it, verify it fails**

Run: `node --test tests/unit/js/test_schedule_helpers.js`
Expected: FAIL — module `schedule/month-grid.js` not found.

- [ ] **Step 3: Implement** — create `js/timeline/schedule/month-grid.js` (extracted verbatim from `timeline.js renderMonth`, plus a `data-day-iso` attribute on each cell for mobile tap-drill)

```js
/**
 * Month calendar-with-dots. Extracted from timeline.js renderMonth so the
 * desktop grid and the mobile Schedule stack share one renderer. Pure:
 * (schedules, viewDate, displayID, expandSchedule) -> HTML string.
 *
 * Each day cell carries data-day-iso so the mobile view can tap-drill into
 * that day's agenda. The desktop grid ignores the attribute.
 */
import { monthWeekdayHeaderHtml } from '../timeline/grid-axis.js';
import { escapeAttr } from './util.js';

const DAY_MS = 24 * 60 * 60 * 1000;

function colorForPlaylist(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return `hsl(${h % 360} 65% 55%)`;
}

export function monthGridHtml({ schedules, viewDate, displayID, expandSchedule }) {
  if (!displayID) return '<div style="color:var(--text-muted)">Pick a display to view the month.</div>';
  const [y, m] = viewDate.split('-').map(Number);
  const startMs = Date.UTC(y, m - 1, 1);
  const endMs = Date.UTC(y, m, 1);

  const perDay = {};
  for (const s of schedules) {
    if (s.displayID !== displayID) continue;
    for (const p of expandSchedule(s, startMs, endMs)) {
      const d = new Date(p.startMs);
      const iso = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
      (perDay[iso] = perDay[iso] || new Set()).add(p.playlistName);
    }
  }

  const firstDow = (new Date(startMs).getUTCDay() + 6) % 7;
  const daysInMonth = new Date(endMs - DAY_MS).getUTCDate();

  let html = '<div class="mm-month-grid" style="display:grid; grid-template-columns: repeat(7, 1fr); gap:2px;">';
  html += monthWeekdayHeaderHtml();
  for (let i = 0; i < firstDow; i++) html += '<div class="mm-month-cell mm-month-cell-blank"></div>';
  for (let day = 1; day <= daysInMonth; day++) {
    const iso = `${y}-${String(m).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    const playlists = Array.from(perDay[iso] || []);
    const dots = playlists.map(pl =>
      `<span class="mm-month-dot" title="${escapeAttr(pl)}" style="background:${colorForPlaylist(pl)}"></span>`
    ).join('');
    html += `<div class="mm-month-cell" data-day-iso="${iso}">
      <div class="mm-month-num">${day}</div>
      <div class="mm-month-dots">${dots}</div>
    </div>`;
  }
  html += '</div>';
  return html;
}
```

- [ ] **Step 4: Delegate from `timeline.js renderMonth`** — replace the body of `renderMonth()` (`timeline.js:199-242`) with:

```js
    renderMonth() {
      return monthGridHtml({
        schedules: this.$store.mm.schedules,
        viewDate: this.$store.mm.viewDate,
        displayID: this.$store.mm.selectedDisplay,
        expandSchedule,
      });
    },
```

And add the import at the top of `timeline.js` (next to the existing `expandSchedule` import ~line 15):

```js
import { monthGridHtml } from '../schedule/month-grid.js';
```

(`colorForPlaylist` in `timeline.js` is still used by week/day rendering — leave it. `monthWindow()` is now unused by `renderMonth` but harmless; leave it to keep the diff minimal.)

- [ ] **Step 5: Wire the mobile Month branch** in `schedule-mobile.js`

Add the import:

```js
import { monthGridHtml } from './month-grid.js';
```

In `render()`, replace the "Day (and Month before Phase C)" fallthrough with an explicit Month branch (placed before the Day fallthrough):

```js
      if (mode === 'month') {
        const did = this.$store.mm.selectedDisplay || this.tracks[0] || null;
        return monthGridHtml({
          schedules: this.$store.mm.schedules,
          viewDate: this.$store.mm.viewDate,
          displayID: did,
          expandSchedule,
        });
      }
```

And in `init()`, extend the click handler to handle month-day taps (drill into that day's agenda):

```js
      this.$root.addEventListener('click', (ev) => {
        const dayCell = ev.target.closest('[data-day-iso]');
        if (dayCell) {
          this.$store.mm.setViewDate(dayCell.dataset.dayIso);
          this.$store.mm.setViewMode('day');
          this.density = 'agenda';
          return;
        }
        const row = ev.target.closest('[data-schedule-id]');
        if (row) openRecurrenceEditor(this.$store.mm, row.dataset.scheduleId);
      });
```

- [ ] **Step 6: Run tests, verify pass + desktop month unaffected**

Run: `node --test tests/unit/js/test_schedule_helpers.js`
Expected: PASS (incl. new monthGridHtml tests).
Run: `node -e "import('./js/timeline/timeline/timeline.js').then(()=>console.log('ok'))"`
Expected: `ok` (timeline.js still imports cleanly after the refactor).

- [ ] **Step 7: Commit**

```bash
git add js/timeline/schedule/month-grid.js js/timeline/timeline/timeline.js js/timeline/schedule/schedule-mobile.js tests/unit/js/test_schedule_helpers.js
git commit -m "feat(schedule): shared month-grid renderer + mobile month tap-drill

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task C2: `vertical-timeline.js` — Day, one group, hours top-to-bottom

**Files:**
- Create: `js/timeline/schedule/vertical-timeline.js`
- Modify: `js/timeline/schedule/schedule-mobile.js` (Day density='vertical' branch + group selector)
- Test: `tests/unit/js/test_schedule_helpers.js` (append)

- [ ] **Step 1: Write the failing test** (append)

```js
import { verticalTimelineHtml } from '../../../js/timeline/schedule/vertical-timeline.js';

test('verticalTimelineHtml renders 24 hour labels and positions a block', () => {
  const day = Date.UTC(2026, 5, 1);
  const placements = [{ startMs: day + 9 * 3600e3, endMs: day + 11 * 3600e3, displayID: 'Lobby', scheduleId: 'v1', playlistName: 'Loop', priority: 0 }];
  const html = verticalTimelineHtml({ dayStartMs: day, placements, playlists: { Loop: { items: [] } }, nowMs: 0 });
  // 24 hour rows.
  assert.equal((html.match(/mm-vt-hour/g) || []).length, 24);
  assert.match(html, /data-schedule-id="v1"/);
  assert.match(html, /Loop/);
});
```

- [ ] **Step 2: Run it, verify it fails**

Run: `node --test tests/unit/js/test_schedule_helpers.js`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** — create `js/timeline/schedule/vertical-timeline.js`

```js
/**
 * Vertical day timeline for ONE group: hours 00..23 top-to-bottom, blocks
 * positioned by their fraction of the day, a now-line, tappable blocks.
 * Pure: ({ dayStartMs, placements, playlists, nowMs }) -> HTML string.
 * `placements` are pre-filtered to the single selected group + the day.
 */
import { escapeText, escapeAttr, formatHm, isNowPlacement } from './util.js';

const DAY_MS = 24 * 60 * 60 * 1000;
const HOUR_PX = 40;          // vertical scale: 40px per hour
const TOTAL_PX = 24 * HOUR_PX;

export function verticalTimelineHtml({ dayStartMs, placements, playlists, nowMs }) {
  let html = `<div class="mm-vt" style="position:relative; height:${TOTAL_PX}px;">`;
  // Hour gridlines + labels.
  for (let h = 0; h < 24; h++) {
    html += `<div class="mm-vt-hour" style="position:absolute; top:${h * HOUR_PX}px; height:${HOUR_PX}px;">
      <span class="mm-vt-hour-label">${String(h).padStart(2, '0')}</span>
    </div>`;
  }
  // Blocks.
  for (const p of placements) {
    const topFrac = Math.max(0, (p.startMs - dayStartMs) / DAY_MS);
    const endFrac = Math.min(1, (p.endMs - dayStartMs) / DAY_MS);
    const top = topFrac * TOTAL_PX;
    const height = Math.max(14, (endFrac - topFrac) * TOTAL_PX);
    const nowCls = isNowPlacement(p, nowMs) ? ' mm-vt-block-now' : '';
    html += `<div class="mm-vt-block${nowCls}" data-schedule-id="${escapeAttr(p.scheduleId)}"
      style="position:absolute; left:42px; right:6px; top:${top}px; height:${height}px;">
      <span class="mm-vt-block-name">${escapeText(p.playlistName)}</span>
      <span class="mm-vt-block-time">${formatHm(p.startMs)}–${formatHm(p.endMs)}</span>
    </div>`;
  }
  // Now-line (only when nowMs falls within this day).
  if (nowMs >= dayStartMs && nowMs < dayStartMs + DAY_MS) {
    const top = ((nowMs - dayStartMs) / DAY_MS) * TOTAL_PX;
    html += `<div class="mm-vt-nowline" style="position:absolute; left:0; right:0; top:${top}px;"></div>`;
  }
  html += '</div>';
  return html;
}
```

- [ ] **Step 4: Wire into `schedule-mobile.js`** — add a `selectedGroup` for the vertical view + render branch.

Add the import:

```js
import { verticalTimelineHtml } from './vertical-timeline.js';
```

Add component state (next to `density`):

```js
    vtGroup: null,             // group shown in the vertical day timeline
```

Add a getter + setter:

```js
    get vtGroupResolved() { return this.vtGroup || this.tracks[0] || null; },
    setVtGroup(id) { this.vtGroup = id; },
```

In `render()`, before the Day agenda fallthrough, add the vertical branch:

```js
      if (mode === 'day' && this.density === 'vertical') {
        const win = this._dayWindow();
        const did = this.vtGroupResolved;
        const placements = this._expandWindow(win.startMs, win.endMs).filter(p => p.displayID === did);
        return verticalTimelineHtml({ dayStartMs: win.startMs, placements, playlists, nowMs });
      }
```

Add a group `<select>` to the mobile toolbar in `admin.html` — inside the `<template x-if="$store.mm.viewMode==='day'">` density toggle area, append (so it only shows for the vertical density):

```html
            <template x-if="$store.mm.viewMode==='day' && density==='vertical'">
              <select class="input" :value="vtGroupResolved" @change="setVtGroup($event.target.value)">
                <template x-for="id in tracks" :key="id"><option :value="id" x-text="id"></option></template>
              </select>
            </template>
```

- [ ] **Step 5: Run tests + import smoke**

Run: `node --test tests/unit/js/test_schedule_helpers.js`
Expected: PASS.
Run: `node -e "import('./js/timeline/schedule/schedule-mobile.js').then(()=>console.log('ok'))"`
Expected: `ok`.

- [ ] **Step 6: Commit**

```bash
git add js/timeline/schedule/vertical-timeline.js js/timeline/schedule/schedule-mobile.js admin.html tests/unit/js/test_schedule_helpers.js
git commit -m "feat(schedule): vertical day timeline + group selector

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase D — Unified create/edit editor + "+ Schedule"

### Task D1: `openScheduleCreator` (create mode with playlist + group pickers)

**Files:**
- Modify: `js/timeline/modals/recurrence-editor.js`

The current `open(store, scheduleId)` builds the form from an existing schedule with playlist/group as `<input disabled>` and Saves via `updateSchedule`. Refactor so the field markup is shared and a `mode` ('edit' | 'create') decides: (a) whether playlist/group are read-only inputs or `<select>` pickers, and (b) whether Save calls `updateSchedule` or `createSchedule`.

- [ ] **Step 1: Add `openScheduleCreator` + refactor `open`** in `js/timeline/modals/recurrence-editor.js`

Change the exports/structure. Keep `openRecurrenceEditor(store, scheduleId)` as-is (delegates to edit mode). Add:

```js
export function openScheduleCreator(store, prefill = {}) {
  open(store, null, prefill);
}
```

Change `function open(store, scheduleId)` to `function open(store, scheduleId, prefill = {})` and at the top, build the working schedule object for both modes:

```js
function open(store, scheduleId, prefill = {}) {
  const isCreate = scheduleId == null;
  const playlistNames = Object.keys(store.playlists || {}).sort();
  const groupIds = (store.displayGroups || []).map(g => g.displayID).filter(Boolean);

  // The schedule the form edits. In create mode it's a fresh default
  // (no id, no _serverVersion) seeded from prefill; in edit mode it's
  // the stored schedule.
  const s = isCreate
    ? {
        playlistName: prefill.playlistName || playlistNames[0] || '',
        displayID: prefill.displayID || groupIds[0] || '',
        dtstart: prefill.dtstart || new Date().toISOString().slice(0, 10),
        startTime: prefill.startTime || '09:00',
        endTime: prefill.endTime || '10:00',
        freq: 'DAILY', interval: 1, byweekday: [], priority: 0,
        end: { type: 'never' },
      }
    : store.schedules.find(x => x.id === scheduleId);
  if (!s) return;
```

Replace the two read-only playlist/display rows in the template with mode-aware rows. Where the template currently has:

```js
      <label>Playlist <input type="text" disabled value="${escapeAttr(s.playlistName)}"></label>
      <label>Display <input type="text" disabled value="${escapeAttr(s.displayID)}"></label>
```

use instead:

```js
      ${isCreate
        ? `<label>Playlist
            <select data-field="playlistName">
              ${playlistNames.map(n => `<option value="${escapeAttr(n)}"${n === s.playlistName ? ' selected' : ''}>${escapeAttr(n)}</option>`).join('')}
            </select></label>
          <label>Display
            <select data-field="displayID">
              ${groupIds.map(g => `<option value="${escapeAttr(g)}"${g === s.displayID ? ' selected' : ''}>${escapeAttr(g)}</option>`).join('')}
            </select></label>`
        : `<label>Playlist <input type="text" disabled value="${escapeAttr(s.playlistName)}"></label>
          <label>Display <input type="text" disabled value="${escapeAttr(s.displayID)}"></label>`}
```

In `readDraft()`, include the playlist/group from the pickers when in create mode (so the created schedule carries them). At the end of the returned object, add:

```js
      ...(isCreate ? {
        playlistName: f('[data-field="playlistName"]').value,
        displayID: f('[data-field="displayID"]').value,
      } : {}),
```

Update the modal title and the Save handler:

```js
  const { dialog } = openModal({
    title: isCreate ? 'New schedule' : `Schedule: ${s.playlistName} on ${s.displayID}`,
    contentEl: root,
  });
```

```js
  root.querySelector('[data-action="save"]').addEventListener('click', async () => {
    const draft = readDraft();
    if (draft.end.type === 'until' && !draft.end.untilDate) {
      store.toast('Pick an "until" date or change End to Never / After N.', 'error');
      return;
    }
    if (isCreate && (!draft.playlistName || !draft.displayID)) {
      store.toast('Pick a playlist and a display group.', 'error');
      return;
    }
    try {
      if (isCreate) await store.createSchedule(draft);
      else await store.updateSchedule(scheduleId, draft);
      closeModal();
    } catch (_) { /* toast already shown via withRollback */ }
  });
```

The next-N preview already works in create mode: `refreshPreview()` builds `synthetic = { ...s, ...draft }` and calls `expandSchedule` — with the create defaults that yields a valid preview. (In create mode `s` has no `id`, so `placement.scheduleId` is `undefined` in the preview; the preview only reads `startMs`/`endMs`, so this is fine.)

- [ ] **Step 2: Verify the editor module still imports + edit path unchanged**

Run: `node -e "import('./js/timeline/modals/recurrence-editor.js').then(m => console.log(typeof m.openScheduleCreator, typeof m.openRecurrenceEditor))"`
Expected: prints `function function`.
Run: `node --test tests/unit/js/test_timeline_smoke.js`
Expected: PASS (the smoke imports recurrence-editor.js; this confirms no syntax error).

- [ ] **Step 3: Commit**

```bash
git add js/timeline/modals/recurrence-editor.js
git commit -m "feat(schedule): recurrence editor gains create mode (playlist+group pickers)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task D2: "+ Schedule" desktop toolbar button

**Files:**
- Modify: `js/timeline/toolbar.js`
- Modify: `admin.html` (desktop toolbar)

The mobile FAB (Task B2/C2) already calls `openScheduleCreator`. This adds the desktop entry point.

- [ ] **Step 1: Add `openCreateSchedule()` to the toolbar component** — in `js/timeline/toolbar.js`, add the import at the top (next to the other modal imports ~line 11-13):

```js
import { openScheduleCreator } from './modals/recurrence-editor.js';
```

Add a method inside the returned object (next to `openProfileEditor()` ~line 44):

```js
    openCreateSchedule() { openScheduleCreator(this.$store.mm, {}); },
```

- [ ] **Step 2: Add the button to the desktop toolbar in `admin.html`** — insert after the `Today` button (`admin.html:823`), before the `<template x-if="$store.mm.viewMode !== 'day'">` display picker:

```html
              <button class="btn btn-primary" @click="openCreateSchedule()" title="Schedule a playlist on a group">+ Schedule</button>
```

- [ ] **Step 3: Verify the toolbar module still imports**

Run: `node -e "import('./js/timeline/toolbar.js').then(()=>console.log('ok'))"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add js/timeline/toolbar.js admin.html
git commit -m "feat(schedule): + Schedule button on the desktop toolbar

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase E — Smoke registration, e2e, docs

### Task E1: register new modules in the module-load smoke

**Files:**
- Modify: `tests/unit/js/test_timeline_smoke.js`

- [ ] **Step 1: Add the new modules to `MODULES`** in `tests/unit/js/test_timeline_smoke.js` (append to the array, before the closing `];`):

```js
  'js/timeline/schedule/util.js',
  'js/timeline/schedule/agenda-row.js',
  'js/timeline/schedule/agenda-view.js',
  'js/timeline/schedule/month-grid.js',
  'js/timeline/schedule/vertical-timeline.js',
  'js/timeline/schedule/schedule-mobile.js',
```

- [ ] **Step 2: Run the smoke**

Run: `node --test tests/unit/js/test_timeline_smoke.js`
Expected: PASS — every new module loads without error.

- [ ] **Step 3: Run the full JS suite**

Run: `python pytest_runner.py --js`
Expected: all pass (existing + `test_schedule_helpers.js` + smoke).

- [ ] **Step 4: Commit**

```bash
git add tests/unit/js/test_timeline_smoke.js
git commit -m "test(schedule): register schedule/ modules in the load smoke

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task E2: mobile-viewport Playwright e2e

**Files:**
- Create: `tests/e2e/test-schedule-mobile.spec.js`

- [ ] **Step 1: Write the spec** — `tests/e2e/test-schedule-mobile.spec.js`

```js
/**
 * Section 3 — the responsive Schedule destination on a phone viewport.
 *
 * Drives the REAL admin page at a 390×844 (phone) viewport so store.isMobile
 * is true and the mmScheduleMobile stack renders. Asserts:
 *   1. Day agenda renders grouped by display group, with a row per schedule.
 *   2. Tapping a row opens the recurrence editor sheet.
 *   3. "+ Schedule" -> pick playlist + group -> Save -> the new schedule
 *      round-trips through /api/schedules (the create path, end-to-end).
 *   4. Week shows seven day-section headers.
 *
 * Owns its own state: a uniquely-named __e2e_sched playlist + the schedule
 * it creates, both deleted in cleanup so the shared dev server stays clean.
 */
import { chromium } from 'playwright';
import assert from 'node:assert';

const BASE = process.env.MM_BASE_URL || 'http://localhost:3000';
const ADMIN = () => BASE + '/admin.html?nocache=' + Date.now() + '#schedule';
const PL = '__e2e_sched';
const PHONE = { width: 390, height: 844 };

async function waitHydrated(page) {
  await page.waitForFunction(
    () => window.Alpine && Alpine.store('mm') && Alpine.store('mm').hydrated === true,
    null, { timeout: 15_000 });
  await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
}
async function settle(page) {
  await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
}

async function delPlaylist(page, name) {
  await page.request.delete(BASE + '/api/playlists/' + encodeURIComponent(name));
}
async function ensurePlaylist(page, name) {
  await delPlaylist(page, name);
  const r = await page.request.post(BASE + '/api/playlists', {
    headers: { 'Content-Type': 'application/json' },
    data: { name, items: [{ file: 'lissajous', playmode: 'SCRIPT' }], loop: false },
  });
  assert.ok(r.ok(), `POST /api/playlists ${name} -> ${r.status()}`);
}
async function listSchedules(page) {
  const r = await page.request.get(BASE + '/api/schedules');
  const j = await r.json();
  return j.schedules || [];
}
async function delSchedulesForPlaylist(page, name) {
  for (const s of await listSchedules(page)) {
    if (s.playlistName === name) await page.request.delete(BASE + '/api/schedules/' + encodeURIComponent(s.id));
  }
}
// First display group id (the create picker needs a real group to target).
async function firstGroupId(page) {
  const r = await page.request.get(BASE + '/api/displays');
  const j = await r.json();
  const list = j.displays || [];
  return list.length ? list[0].displayID : null;
}

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: PHONE });
  try {
    // Up-front cleanup of orphans from a prior crashed run.
    await page.goto(BASE + '/admin.html');
    await delSchedulesForPlaylist(page, PL);
    await delPlaylist(page, PL);

    const groupId = await firstGroupId(page);
    assert.ok(groupId, 'need at least one display group on the dev server for the create path');

    await ensurePlaylist(page, PL);

    // ---- 1. Mobile agenda renders ----
    await page.goto(ADMIN());
    await waitHydrated(page);
    await page.waitForFunction(() => Alpine.store('mm').isMobile === true, null, { timeout: 5_000 });
    await page.waitForFunction(() => Alpine.store('mm').activeTab === 'schedule', null, { timeout: 5_000 });
    // The mobile stack is present (not the desktop grid).
    await page.waitForFunction(
      () => document.querySelector('.mm-schedule-mobile') && document.querySelector('.mm-schedule-mobile').offsetParent !== null,
      null, { timeout: 5_000 });

    // ---- 3. Create a schedule via "+ Schedule" ----
    await page.evaluate(() => {
      const fab = document.querySelector('.mm-schedule-mobile .mm-sched-fab');
      if (!fab) throw new Error('no + Schedule FAB');
      fab.click();
    });
    await page.waitForFunction(
      () => document.querySelector('.mm-modal [data-field="playlistName"]') != null, null, { timeout: 5_000 });
    // Pick our playlist + the first group, then Save.
    await page.evaluate(({ pl, gid }) => {
      const setSel = (sel, val) => {
        const el = document.querySelector(sel);
        el.value = val;
        el.dispatchEvent(new Event('change', { bubbles: true }));
      };
      setSel('.mm-modal [data-field="playlistName"]', pl);
      setSel('.mm-modal [data-field="displayID"]', gid);
    }, { pl: PL, gid: groupId });
    await settle(page);
    await page.evaluate(() => {
      const save = Array.from(document.querySelectorAll('.mm-modal .mm-form-actions button'))
        .find(b => b.textContent.trim() === 'Save');
      if (!save) throw new Error('no Save button');
      save.click();
    });
    await page.waitForFunction(() => document.querySelector('.mm-modal') == null, null, { timeout: 5_000 });

    // Verify via REST: a schedule for PL now exists on groupId.
    let created = null;
    for (let i = 0; i < 20 && !created; i++) {
      const all = await listSchedules(page);
      created = all.find(s => s.playlistName === PL && s.displayID === groupId) || null;
      if (!created) await settle(page);
    }
    assert.ok(created, `expected a schedule for ${PL} on ${groupId} after + Schedule -> Save`);

    // ---- 2. Re-hydrate; the agenda shows the row; tapping opens the editor ----
    await page.goto(ADMIN());
    await waitHydrated(page);
    await page.waitForFunction(
      () => document.querySelector('.mm-schedule-mobile [data-schedule-id]') != null, null, { timeout: 5_000 });
    await page.evaluate(() => {
      const row = document.querySelector('.mm-schedule-mobile [data-schedule-id]');
      row.click();
    });
    await page.waitForFunction(
      () => document.querySelector('.mm-modal [data-field="freq"]') != null, null, { timeout: 5_000 });
    // Close it.
    await page.evaluate(() => {
      const cancel = Array.from(document.querySelectorAll('.mm-modal .mm-form-actions button'))
        .find(b => b.textContent.trim() === 'Cancel');
      cancel.click();
    });
    await page.waitForFunction(() => document.querySelector('.mm-modal') == null, null, { timeout: 5_000 });

    // ---- 4. Week shows seven day-section headers ----
    await page.evaluate(() => Alpine.store('mm').setViewMode('week'));
    await settle(page);
    const headerCount = await page.evaluate(() =>
      document.querySelectorAll('.mm-schedule-mobile .mm-agenda-day-header').length);
    assert.equal(headerCount, 7, `week view should render 7 day headers, got ${headerCount}`);

    return 'pass';
  } finally {
    try { await delSchedulesForPlaylist(page, PL); } catch (_) {}
    try { await delPlaylist(page, PL); } catch (_) {}
    await browser.close();
  }
}
```

- [ ] **Step 2: Run the spec** (dev server must be running on `MM_BASE_URL`)

Run: `node tests/e2e/run.js schedule-mobile`
Expected: `pass`. If it fails on "need at least one display group", create one first (`+ Group` in the UI or `POST /api/displays {displayID:'Lobby'}`) and re-run.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test-schedule-mobile.spec.js
git commit -m "test(schedule): mobile-viewport e2e (agenda, create, edit, week)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task E3: docs

**Files:**
- Modify: `js/timeline/README.md`
- Modify: `CLAUDE.md` (the `js/timeline/` layout note)

- [ ] **Step 1: Update `js/timeline/README.md`** — add a `schedule/` entry to the Module map:

```markdown
- **`schedule/`** — the responsive Schedule destination's mobile views
  (Section 3). Pure render helpers (`util.js`, `agenda-row.js`,
  `agenda-view.js`, `month-grid.js`, `vertical-timeline.js`) + the
  `mmScheduleMobile` Alpine component. `<760px` (store.isMobile) renders
  this stack; `≥760px` keeps the `timeline/` desktop grid. `month-grid.js`
  is shared by both. Create flow is the unified "+ Schedule" →
  `openScheduleCreator` in `modals/recurrence-editor.js`.
```

- [ ] **Step 2: Update `CLAUDE.md`** — add a bullet under the `js/timeline/` Layout section describing `js/timeline/schedule/` and the responsive switch (mirror the README wording, one or two sentences).

- [ ] **Step 3: Commit**

```bash
git add js/timeline/README.md CLAUDE.md
git commit -m "docs(schedule): document js/timeline/schedule/ + responsive switch

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification (after all tasks)

- [ ] **JS units + smoke:** `python pytest_runner.py --js` → all pass.
- [ ] **e2e (dev server up):** `node tests/e2e/run.js schedule-mobile` → `pass`; `node tests/e2e/run.js content-tab` and `node tests/e2e/run.js shell-nav` → still `pass` (no regression in sibling specs).
- [ ] **Manual desktop check:** at ≥760px the Schedule tab still shows the existing grid (Day/Week/Month, drag/drop, drill-in) AND the new "+ Schedule" button creates a schedule via the editor.
- [ ] **Manual mobile check:** narrow the window <760px → agenda renders; Day density toggle → vertical timeline with group selector; Week → 7 day-sections; Month → calendar dots, tap a day → that day's agenda; "+ Schedule" FAB → create.
- [ ] **Dispatch a final code-reviewer** over the whole branch (per subagent-driven-development), then **superpowers:finishing-a-development-branch** to open the Section 3 PR stacked on Section 2.

## Notes for the implementer

- **Do not touch the iPad-1 display clients** (`index.html`, `js/mosiacmesh.js`, `js/GoTime.js`). This is admin-only.
- **Do not modify the server.** `/api/schedules` already supports create/edit/delete; `createSchedule`/`updateSchedule` store mutators already exist.
- **CSS:** the new classes (`mm-agenda*`, `mm-vt*`, `mm-schedule-mobile`, `mm-sched-fab`, `mm-sched-mobile-toolbar`, `mm-density-toggle`) need styling. Add a consolidated block to `admin.html`'s `<style>` (the design tokens consolidated in Section 1 live there). Keep it lean — the agenda rows are flex rows; the vertical timeline is `position:absolute` blocks in a `position:relative` track; the FAB is `position:fixed; right:16px; bottom:72px` (above the mobile tab-bar). This is presentation polish; no test depends on exact pixels, only on class presence + structure.
- **`store.playlists` is a dict** (name→playlist), **`store.schedules` is an array**, **`store.displayGroups` is an array** of `{displayID, ...}`. Match these shapes exactly (verified in `store.js`).
