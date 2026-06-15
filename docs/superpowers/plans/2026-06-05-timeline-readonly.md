# Admin Timeline (Read-Only) Implementation Plan — PR-4a

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a read-only admin timeline view at `#timeline` that hydrates from the PR-2 REST endpoints, expands schedule recurrence to clip placements across Day/Week/Month views, and renders the result with a live now-line and conflict overlay. **No mutations** — drag/drop, click-handlers, and modals land in PR-4b and PR-4c.

**Architecture:** A single-page Alpine.js view inside `admin.html`, loaded as ES modules under `js/timeline/`. The new `[data-route="timeline"]` section sits alongside the existing four routes (`#overview`, `#displays`, `#media`, `#playlists`, `#schedules`, `#console`) — PR-6 deletes those. Hydration: parallel `fetch`es of `/api/playlists`, `/api/schedules`, `/api/profiles`, `/api/media`, `/api/discovery/devices`. Live SockJS broadcasts (`DISCOVERY_HEARTBEAT`, `CLIENTS_WENT_OFFLINE`, `RENDER_IN_PROGRESS`) update display-status indicators only — no schedule mutations.

**Tech Stack:** Alpine.js 3.x (CDN, no build step); native ES modules; pure-JS recurrence expansion mirroring `mosaicmesh/scheduling.py`; Node 20+'s built-in `node --test` runner for the pure-function tests; no npm install required.

**Stacks on:** `feature/pr3-scripting-profile-dispatcher` (PR #5). Merge after PR-3 lands.

**Spec reference:** `docs/superpowers/specs/2026-06-04-admin-timeline-redesign-design.md` sections 2-4, 8, 9 (read-only portions), 10 (validation), 11 (testing).

---

## Visual Mockups

### Day view (default) — empty

```
┌──────────────────────────────────────────────────────────────────────────┐
│ [Day][Week][Month]   ◀ Thu Jun 5, 2026 ▶   T   Fleet: 🔓 ▶ ⏹ ⟲ 🐞 ⚙Cal │
├─────────────┬────────────────────────────────────────────────────────────┤
│ ▾ Media (12)│  TRACK         │00  06  09 12 15 18 21  24                 │
│   news.mp4  │ ──────────────┼─────────────────────────────────           │
│   logo.png  │  Lobby Wall   │  ░░░░░░░░░░░░░░░░░░░░░░░░░    (idle row)  │
│   brand.png │   6/6 online ●│                                           │
│ + Upload    │ ──────────────┼─────────────────────────────────           │
│             │  Conf Room A  │  ░░░░░░░░░░░░░░░░░░░░░░░░░    (idle row)  │
│ ▾ Playlists │   1/1 online ●│                                           │
│   Morning…  │ ──────────────┼─────────────────────────────────           │
│   Lunch…    │  Default ▾idle│  ░░░░░░░░░░░░░░░░░░░░░░░░░                │
│ + New       │ ──────────────┴─────────────────┬───────────────           │
│             │                                  │← red now-line at 14:23  │
└─────────────┴────────────────────────────────────────────────────────────┘
```

### Day view — populated

```
│  Lobby Wall    │ [Morning News──][Lunch Menu]   [Marketing──────]        │
│   6/6 online ● │  08:00-11:00     11:00-13:00    16:00-21:00             │
│                │                                                          │
│  Conf Room A   │              [Meeting Agenda─]                          │
│   1/1 online ● │              09:30-15:00                                │
```

### Conflict overlay (overlap between schedules — lower priority wins-loses with diagonal stripe)

```
│  Lobby Wall    │ [Morning News═══════]                                   │
│   6/6 online ● │      [Lunch Menu (p=10)─░░░] ← stripe over overlap area │
│                │       ↑ overlap with Morning(p=5) — hover: "Lunch Menu  │
│                │         (priority 10) overrides Morning News here."     │
```

### Week view (one display selected)

```
│  ←  Lobby Wall  ▼ (selecting display)                                   │
├──────┬────────┬────────┬────────┬────────┬────────┬────────┬────────────┤
│  hr  │  Mon   │  Tue   │  Wed   │  Thu   │  Fri   │  Sat   │  Sun       │
├──────┼────────┼────────┼────────┼────────┼────────┼────────┼────────────┤
│ 06   │        │        │        │        │        │        │            │
│ 09   │ [News] │ [News] │ [News] │ [News] │ [News] │        │            │
│ 12   │ [Lunch]│ [Lunch]│ [Lunch]│ [Lunch]│ [Lunch]│        │            │
│ 15   │        │        │        │        │        │        │            │
│ 18   │ [Mktg] │ [Mktg] │ [Mktg] │ [Mktg] │ [Mktg] │ [Mktg] │            │
└──────┴────────┴────────┴────────┴────────┴────────┴────────┴────────────┘
```

### Month view (calendar — dots per day)

```
│        June 2026   ◀ ▶                                                   │
├────────┬────────┬────────┬────────┬────────┬────────┬────────────────────┤
│  Mon   │  Tue   │  Wed   │  Thu   │  Fri   │  Sat   │  Sun               │
├────────┼────────┼────────┼────────┼────────┼────────┼────────────────────┤
│   1    │   2    │   3    │   4    │   5    │   6    │   7                │
│  ● ● ● │  ● ● ● │  ● ● ● │  ● ● ● │  ● ● ● │  ● ●   │   ●                │
├────────┼────────┼────────┼────────┼────────┼────────┼────────────────────┤
│   8    │   9    │  10    │  11    │  12    │  13    │  14                │
│  ● ● ● │  ● ● ● │  ● ● ● │  ● ● ● │  ● ● ● │  ● ●   │   ●                │
└────────┴────────┴────────┴────────┴────────┴────────┴────────────────────┘
```
Each `●` = one playlist active on that day (color-keyed to the playlist in the bin).

---

## File Structure

| File | Action | Responsibility after this PR |
|---|---|---|
| `admin.html` | **Modify** | Add Alpine.js 3.x CDN script, new `<button data-nav="timeline">` in nav, new `<section data-route="timeline">` skeleton, ES-module bootstrap line. Existing routes untouched. |
| `js/timeline/index.js` | **Create** | Entry point. Imports Alpine, registers store, attaches Alpine globally. Idempotent on hashchange. |
| `js/timeline/api.js` | **Create** | Thin async fetch wrappers around `/api/playlists`, `/api/schedules`, `/api/profiles`, `/api/media`, `/api/discovery/devices`. GET-only in PR-4a. Returns parsed JSON or throws on non-2xx. |
| `js/timeline/store.js` | **Create** | `Alpine.store('mm')` definition: `displays{}`, `playlists{}`, `schedules[]`, `media[]`, `profiles{}`, `viewMode`, `viewDate`, plus a `hydrate()` method that fires all five GETs in parallel and a `setStatus()` for one-display status updates. |
| `js/timeline/util/time.js` | **Create** | Pure recurrence-expansion functions: `expandSchedule(schedule, windowStartMs, windowEndMs)` → `[{startMs, endMs, displayID, playlistName, priority, scheduleId}, ...]`. Mirrors `mosaicmesh.scheduling.schedule_active_at`. Importable in Node for tests. |
| `js/timeline/util/conflicts.js` | **Create** | Given a list of placements on one track + window, return `[{placement, overrides: [otherPlacementId], overlapStartMs, overlapEndMs}, ...]`. Used to render the diagonal-stripe overlay. |
| `js/timeline/timeline/grid-axis.js` | **Create** | Day-view header strip (00..23 hour labels), Week-view header (Mon..Sun day labels), Month-view weekday labels. Pure render-helpers; no state. |
| `js/timeline/timeline/track-header.js` | **Create** | Left column rendering for one display group: name, online/total dots, optional render-progress badge. Read-only. |
| `js/timeline/timeline/clip.js` | **Create** | Renders one clip as a CSS-grid positioned block with playlist name, time range, conflict overlay if applicable. Read-only — click handlers deferred to PR-4b. |
| `js/timeline/timeline/timeline.js` | **Create** | Top-level renderer that consumes `$store.mm` and emits the grid for the current `viewMode`. Day = tracks-per-display × 24h grid; Week = one display × 7-day × hour grid; Month = calendar with dots. |
| `js/timeline/timeline/now-line.js` | **Create** | Vertical red 2px line positioned at `currentTime`. Advances every second via single `setInterval`. Autoscrolls into view on first paint. Hidden in Month view (no clock hand on a calendar). |
| `js/timeline/timeline/sockjs-status.js` | **Create** | Subscribes to existing window-global `socketmanager`/`sock` (or a new tiny SockJS client if not present) and routes `DISCOVERY_HEARTBEAT` / `CLIENTS_WENT_OFFLINE` / `RENDER_IN_PROGRESS` broadcasts into `store.setStatus()`. Read-only; no schedule mutations. |
| `js/timeline/toolbar.js` | **Create** | Day/Week/Month toggle + date-nav (◀ ▶) + Today button. Mutations are UI-state only (`store.viewMode`, `store.viewDate`) — no server calls. Fleet action buttons (Login/Start/Stop/Reboot/Test all) link to the existing jQuery handlers via `window.runScriptAll(...)`. |
| `js/timeline/bin/media-bin.js` | **Create** | Left-bin section listing media files. Read-only display (drag source deferred to PR-4b). |
| `js/timeline/bin/playlist-bin.js` | **Create** | Left-bin section listing playlists. Read-only display. |
| `tests/unit/js/test_time_recurrence.js` | **Create** | Node `--test` suite for `util/time.js`: DAILY+interval, WEEKLY+byweekday, dtstart honoring, end={never\|until\|count}, exdates, HH:MM startTime/endTime, cross-midnight schedules (endTime < startTime), priority ordering. |
| `tests/unit/js/test_clip_conflicts.js` | **Create** | Node `--test` suite for `util/conflicts.js`: no-overlap returns empty, full-overlap → loser gets stripe, partial-overlap → stripe over overlap region only, equal-priority returns neither flagged. |
| `tests/unit/js/test_timeline_smoke.js` | **Create** | Node `--test` smoke: import every `js/timeline/*.js` module and verify the exports load cleanly (catches syntax errors, missing imports). Standalone — does not require a browser. |
| `tests/unit/js/run_js_tests.bat` | **Create** | One-line Windows runner: `node --test tests/unit/js/*.js`. Mirror in `pytest_runner.py --js` (added in Task 18). |
| `js/timeline/README.md` | **Create** | Single-paragraph map of which module owns what + the data-flow diagram from spec section 8. Engineers landing in this directory should be oriented within 30 seconds. |

---

## Conventions specific to this PR

1. **Alpine 3.x via CDN.** Add `<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>` to `admin.html` head, matching the existing CDN-loaded jstree script.
2. **ES modules.** All `js/timeline/*.js` files use `import`/`export`. Bootstrap via `<script type="module" src="/js/timeline/index.js"></script>` in `admin.html`. Existing `js/mosiacmesh.js` and `js/GoTime.js` stay classic — they're imported by both the iPad client (ES5) and the admin (modern). The new modules are admin-only.
3. **No jQuery in `js/timeline/*`.** Alpine handles reactivity; vanilla DOM elsewhere. The new code can coexist with jQuery in `admin.html` but shouldn't depend on it. (Toolbar's fleet-action buttons proxy to existing `window.runScriptAll(...)` which IS jQuery — that's intentional and OK.)
4. **No mutations.** Every server call in this PR is a GET. PR-4b adds POST/PUT/DELETE. The mutation methods stubbed in `store.js` (`createSchedule`, `updateSchedule`, etc.) throw `new Error('not implemented in PR-4a')` so a misclick during PR-4b development gets a clear error.
5. **iPad-1 client compatibility unaffected.** No file under `js/timeline/` runs on the iPad. Display clients still load `index.html` (ES5). This PR only touches `admin.html` and adds new files under `js/timeline/`.
6. **Test runner.** Use Node 20+'s built-in `--test` mode (no Jest/Mocha install). The Node tests run cross-platform, no browser, no Playwright dep.

---

## Task 1: Add the timeline route skeleton + Alpine.js bootstrap

Get the `#timeline` route navigable (empty section) and Alpine loaded. No real UI yet — this proves the routing + Alpine wiring before any reactivity is added.

**Files:**
- Modify: `admin.html` (sidebar nav, new section, head script)
- Create: `js/timeline/index.js` (Alpine init stub)
- Create: `js/timeline/README.md`

### Step 1.1: Modify `admin.html` — add Alpine CDN + new nav button + section

The pinned Alpine.js build is loaded with Subresource Integrity (SRI) so a future CDN compromise can't ship a malicious script under the same URL. The existing jstree CDN tag predates this convention; new external scripts get SRI. The hash below was computed on 2026-06-05 via:

```bash
curl -sS https://cdn.jsdelivr.net/npm/alpinejs@3.13.10/dist/cdn.min.js \
  | openssl dgst -sha384 -binary | openssl base64 -A
# -> XBJ5+bq4ga1+0s+J4sl6njqQ9C/YIfKeQw18HypSuGEaPm1g/VWaNdsQ5d3sE1qi
```

Find the existing `<script src="https://cdnjs.cloudflare.com/ajax/libs/jstree/3.3.16/jstree.min.js"></script>` near line 12. Immediately after it, add:

```html
  <script defer
          src="https://cdn.jsdelivr.net/npm/alpinejs@3.13.10/dist/cdn.min.js"
          integrity="sha384-XBJ5+bq4ga1+0s+J4sl6njqQ9C/YIfKeQw18HypSuGEaPm1g/VWaNdsQ5d3sE1qi"
          crossorigin="anonymous"></script>
  <script type="module" src="/js/timeline/index.js"></script>
```

If the hash doesn't match the served bytes (e.g. the CDN ships a tampered file), the browser refuses to execute Alpine and devtools console shows an SRI error — that's the desired failure mode. If you bump the Alpine version, recompute the hash with the openssl pipeline above.

Find the sidebar nav block (around lines 1073-1080) and **insert** a new button at the top of the list (above Overview), so it becomes the new default home:

```html
      <button class="navitem" data-nav="timeline">Timeline</button>
      <button class="navitem" data-nav="overview">Overview</button>
      <button class="navitem" data-nav="displays">Displays</button>
      <button class="navitem" data-nav="media">Media</button>
      <button class="navitem" data-nav="playlists">Playlists</button>
      <button class="navitem" data-nav="schedules">Schedules</button>
      <button class="navitem" data-nav="console">Console</button>
```

Find the line with `<section class="section" data-route="overview">` (around line 1082) and **insert** above it:

```html
      <section class="section" data-route="timeline" x-data="{ ready: false }" x-init="ready = true">
        <h2>Timeline</h2>
        <div x-show="!ready" style="color:var(--text-muted)">Loading…</div>
        <div x-show="ready" id="timelineRoot">
          <!-- Filled in by subsequent tasks. -->
          <p style="color:var(--text-muted)">Timeline view will render here (PR-4a scaffolding).</p>
        </div>
      </section>
```

Find the `adminRoute()` function (around line 1644). The default route on first load is `overview`. Update to make `timeline` the default by changing this line:

```js
  if (!found) { $('.section[data-route=overview]').addClass('active'); r = "overview"; }
```

to:

```js
  if (!found) { $('.section[data-route=timeline]').addClass('active'); r = "timeline"; }
```

### Step 1.2: Create `js/timeline/index.js`

```javascript
/**
 * Admin timeline view — entry point.
 *
 * Bootstrap order: Alpine.js auto-starts when the `defer` script loads,
 * triggering `alpine:init`. We register the store + components in that
 * handler so everything is set up before any `x-data` on the page
 * evaluates.
 *
 * PR-4a (this PR): scaffolding + read-only render. Subsequent PRs
 * (4b interactivity, 4c modals) extend store.js + add component
 * modules, but this file stays small.
 */

document.addEventListener('alpine:init', () => {
  // Subsequent tasks register Alpine.store('mm') and components here.
  console.log('[timeline] alpine:init fired');
});
```

### Step 1.3: Create `js/timeline/README.md`

```markdown
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
```

### Step 1.4: Verify the route is navigable

- [ ] Open the running server's admin page: `http://localhost:3000/admin#timeline`
- [ ] Expected: page shows "Loading…" briefly, then "Timeline view will render here (PR-4a scaffolding)."
- [ ] Open browser devtools console. Expected: log line `[timeline] alpine:init fired`. No JS errors.
- [ ] Click each existing nav item (Overview, Displays, …, Console). Expected: each route still works exactly as before.

### Step 1.5: Commit

- [ ] **Commit:**

```bash
git add admin.html js/timeline/index.js js/timeline/README.md
git commit -m "feat(admin): add #timeline route skeleton + Alpine.js bootstrap

Adds the new [data-route='timeline'] section as a sibling of the
existing routes (Overview, Displays, Media, Playlists, Schedules,
Console) — PR-6 deletes the four schedule-related routes. Timeline
becomes the new default landing route.

Alpine.js 3.13.10 loaded via CDN (matches the existing jstree
CDN-loaded dependency). Module bootstrap entry point at
js/timeline/index.js registers a no-op alpine:init listener that
subsequent tasks fill in with store + components.

js/timeline/README.md documents the module map for future readers.

Part of PR-4a (read-only timeline) of the admin-timeline-redesign
spec."
```

---

## Task 2: `js/timeline/api.js` — read-only REST wrappers

Pure async fetch wrappers around the PR-2 endpoints. No store, no rendering — just typed responses or thrown errors.

**Files:**
- Create: `js/timeline/api.js`
- Create: `tests/unit/js/test_timeline_smoke.js` (initial module-load smoke)
- Create: `tests/unit/js/run_js_tests.bat`

### Step 2.1: Write the module-load smoke test FIRST

The first JS test in the project — establishes the test pattern other tasks reuse. Create `tests/unit/js/test_timeline_smoke.js`:

```javascript
/**
 * Module-load smoke for js/timeline/*.js. Catches syntax errors and
 * missing imports without touching the DOM. Run with:
 *
 *   node --test tests/unit/js/test_timeline_smoke.js
 *
 * As new modules land in subsequent tasks, ADD them to MODULES below.
 * Tests should fail-closed: a typo or missing export in any module
 * breaks the test, not silently degrades the admin page.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '../../..');

const MODULES = [
  'js/timeline/api.js',
];

for (const rel of MODULES) {
  test(`${rel} loads without error`, async () => {
    const url = pathToFileURL(path.join(ROOT, rel)).href;
    const mod = await import(url);
    assert.ok(mod, `expected ${rel} to export something`);
  });
}
```

### Step 2.2: Create the Windows test runner

Create `tests/unit/js/run_js_tests.bat`:

```batch
@echo off
REM Node-based JS unit tests. Requires Node 20+ (built-in --test runner).
REM Run from repo root.
node --test tests/unit/js/*.js
```

### Step 2.3: Run the test — expect FAIL

Run from repo root (Windows cmd or PowerShell):

```bash
node --test tests/unit/js/test_timeline_smoke.js
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` because `js/timeline/api.js` doesn't exist yet.

### Step 2.4: Create `js/timeline/api.js`

```javascript
/**
 * Thin async wrappers over the PR-2 REST endpoints.
 *
 * GET-only in PR-4a (read-only timeline). PR-4b adds POST/PUT/DELETE
 * methods for the create/edit/delete flows.
 *
 * Every method returns the parsed JSON body on success, or throws on
 * non-2xx. The thrown Error has `.status` and `.body` fields so the
 * caller can render a precise toast (PR-4b — not used yet).
 */

class ApiError extends Error {
  constructor(message, { status, body }) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function getJson(url) {
  const resp = await fetch(url, {
    method: 'GET',
    headers: { 'Accept': 'application/json' },
    credentials: 'same-origin',
  });
  const text = await resp.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch (_) { body = text; }
  if (!resp.ok) {
    throw new ApiError(
      `GET ${url} -> ${resp.status} ${resp.statusText}`,
      { status: resp.status, body }
    );
  }
  return body;
}

export const api = {
  /** GET /api/playlists -> [{name, items, loop, _serverVersion}, ...] */
  async listPlaylists() {
    const b = await getJson('/api/playlists');
    return b?.playlists ?? [];
  },

  /** GET /api/schedules -> [Schedule, ...] */
  async listSchedules() {
    const b = await getJson('/api/schedules');
    return b?.schedules ?? [];
  },

  /** GET /api/profiles -> [ScriptingProfile, ...] */
  async listProfiles() {
    const b = await getJson('/api/profiles');
    return b?.profiles ?? [];
  },

  /** GET /api/media -> {images, videos, videoDurations} */
  async listMedia() {
    return await getJson('/api/media');
  },

  /**
   * GET /api/discovery/devices ->
   *   {devices: [{clientKey, displayID, friendlyName, isOnline, ...}], total, online}
   */
  async listDevices() {
    return await getJson('/api/discovery/devices');
  },
};

export { ApiError };
```

### Step 2.5: Run the smoke test — expect PASS

```bash
node --test tests/unit/js/test_timeline_smoke.js
```

Expected:
```
✓ js/timeline/api.js loads without error
ℹ tests 1
ℹ pass 1
ℹ fail 0
```

### Step 2.6: Commit

- [ ] **Commit:**

```bash
git add js/timeline/api.js tests/unit/js/test_timeline_smoke.js tests/unit/js/run_js_tests.bat
git commit -m "feat(timeline): api.js — GET-only REST wrappers + JS test scaffold

Five typed-error-throwing async wrappers around the PR-2 endpoints:
listPlaylists, listSchedules, listProfiles, listMedia, listDevices.
GET-only in PR-4a; POST/PUT/DELETE land in PR-4b.

Adds tests/unit/js/ as the home for Node --test JS unit suites.
test_timeline_smoke.js loads each module to catch syntax/import
errors early — extended in subsequent tasks as new modules land.
run_js_tests.bat is the Windows-native runner mirror of the pytest
runners.

Part of PR-4a of the admin-timeline-redesign spec."
```

---

## Task 3: `js/timeline/store.js` — Alpine store + hydration

The single source of truth. Hydrates on first access by firing all five REST GETs in parallel. UI state (viewMode, viewDate) lives here too. Mutation methods stubbed to throw — PR-4b implements them.

**Files:**
- Create: `js/timeline/store.js`
- Modify: `js/timeline/index.js` (register store)
- Modify: `tests/unit/js/test_timeline_smoke.js` (add store.js)

### Step 3.1: Extend the smoke test

Add `'js/timeline/store.js'` to the `MODULES` array in `tests/unit/js/test_timeline_smoke.js`:

```javascript
const MODULES = [
  'js/timeline/api.js',
  'js/timeline/store.js',
];
```

Run: `node --test tests/unit/js/test_timeline_smoke.js`. Expected FAIL — store.js doesn't exist yet.

### Step 3.2: Create `js/timeline/store.js`

```javascript
/**
 * Alpine.store('mm') — single source of truth for the timeline view.
 *
 * Shape:
 *   {
 *     // hydrated from REST
 *     displays:  [{clientKey, displayID, friendlyName, isOnline, ...}, ...],
 *     playlists: { name -> {name, items, loop, _serverVersion} },
 *     schedules: [{id, playlistName, displayID, freq, ..., _serverVersion}, ...],
 *     media:     {images: [...], videos: [...], videoDurations: {...}},
 *     profiles:  { name -> ScriptingProfile },
 *     // UI state
 *     viewMode:  'day' | 'week' | 'month',
 *     viewDate:  ISO-date string ('YYYY-MM-DD'),
 *     selectedDisplay: displayID | null,   // for Week view
 *     // bookkeeping
 *     hydrated: false,
 *     hydrateError: null,
 *     renderInProgress: {},  // displayID -> bool
 *   }
 *
 * Mutation methods (createSchedule, updateSchedule, etc.) throw in
 * PR-4a — they're stubbed so a misclick during PR-4b development
 * surfaces a clear error rather than a silent no-op.
 */
import { api } from './api.js';

function todayIso() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${dd}`;
}

export function makeStore() {
  return {
    displays: [],
    playlists: {},
    schedules: [],
    media: { images: [], videos: [], videoDurations: {} },
    profiles: {},

    viewMode: 'day',
    viewDate: todayIso(),
    selectedDisplay: null,

    hydrated: false,
    hydrateError: null,
    renderInProgress: {},

    /**
     * Fire all five GETs in parallel; populate state on success.
     * On error, leaves the store empty and sets `hydrateError` so the
     * UI can show a retry banner.
     */
    async hydrate() {
      this.hydrated = false;
      this.hydrateError = null;
      try {
        const [pl, sc, pr, me, dv] = await Promise.all([
          api.listPlaylists(),
          api.listSchedules(),
          api.listProfiles(),
          api.listMedia(),
          api.listDevices(),
        ]);
        // Re-shape playlists + profiles to lookup dicts (server returns arrays)
        this.playlists = Object.fromEntries((pl ?? []).map(p => [p.name, p]));
        this.profiles  = Object.fromEntries((pr ?? []).map(p => [p.name, p]));
        this.schedules = sc ?? [];
        this.media     = me ?? { images: [], videos: [], videoDurations: {} };
        this.displays  = (dv?.devices) ?? [];
        // Default Week-view display = first display
        if (this.selectedDisplay == null && this.displays.length > 0) {
          this.selectedDisplay = this.displays[0].displayID
                              ?? this.displays[0].clientKey;
        }
        this.hydrated = true;
      } catch (e) {
        console.error('[timeline] hydrate failed:', e);
        this.hydrateError = e.message || String(e);
        this.hydrated = false;
      }
    },

    /**
     * SockJS-broadcast hook (wired in Task 13). Updates one display's
     * status fields in-place without re-fetching the full list.
     */
    setStatus(displayID, patch) {
      const d = this.displays.find(x => (x.displayID === displayID)
                                     || (x.clientKey === displayID));
      if (d) Object.assign(d, patch);
    },

    setRenderInProgress(displayID, inProgress) {
      this.renderInProgress = { ...this.renderInProgress, [displayID]: !!inProgress };
    },

    // ---- UI-state mutations (no server calls) ----
    setViewMode(mode)   { this.viewMode = mode; },
    setViewDate(isoYmd) { this.viewDate = isoYmd; },
    goToday()           { this.viewDate = todayIso(); },
    selectDisplay(id)   { this.selectedDisplay = id; },

    // ---- Stubs for PR-4b. Implemented later; throw if called now. ----
    async createSchedule(/*partial*/) {
      throw new Error('createSchedule: not implemented in PR-4a (lands in PR-4b)');
    },
    async updateSchedule(/*id, patch*/) {
      throw new Error('updateSchedule: not implemented in PR-4a (lands in PR-4b)');
    },
    async deleteSchedule(/*id*/) {
      throw new Error('deleteSchedule: not implemented in PR-4a (lands in PR-4b)');
    },
    async updatePlaylist(/*name, patch*/) {
      throw new Error('updatePlaylist: not implemented in PR-4a (lands in PR-4b)');
    },
  };
}
```

### Step 3.3: Wire the store in `index.js`

Replace the contents of `js/timeline/index.js` with:

```javascript
/**
 * Admin timeline view — entry point.
 *
 * Bootstrap order: Alpine.js auto-starts when the `defer` script loads,
 * triggering `alpine:init`. We register the store + components in that
 * handler so everything is set up before any `x-data` on the page
 * evaluates.
 *
 * PR-4a (this PR): scaffolding + read-only render. Subsequent PRs
 * (4b interactivity, 4c modals) extend store.js + add component
 * modules, but this file stays small.
 */
import { makeStore } from './store.js';

document.addEventListener('alpine:init', () => {
  // eslint-disable-next-line no-undef
  Alpine.store('mm', makeStore());

  // Kick off hydration — the section's x-init can also call this, but
  // doing it here means the timeline is ready as soon as Alpine is.
  // eslint-disable-next-line no-undef
  Alpine.store('mm').hydrate();
});
```

### Step 3.4: Run smoke — expect PASS

```bash
node --test tests/unit/js/test_timeline_smoke.js
```

Expected: 2 modules load, 0 failures.

### Step 3.5: Manual browser smoke

- [ ] Reload `http://localhost:3000/admin#timeline` in the browser
- [ ] Open devtools console
- [ ] Run: `Alpine.store('mm')`
- [ ] Expected: an object showing the populated fields after hydration completes. `playlists` should contain `Morning` (the playlist that persisted across earlier testing); `displays` should show all 24 fleet iPads with `isOnline: true`.

### Step 3.6: Commit

- [ ] **Commit:**

```bash
git add js/timeline/store.js js/timeline/index.js tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): store.js — Alpine.store('mm') + parallel hydration

Single source of truth for the timeline view. hydrate() fires all
five PR-2 GETs in parallel (Playlists, Schedules, Profiles, Media,
Discovery devices) and populates the reactive store; on error, sets
hydrateError so the UI can show a retry banner.

UI state (viewMode, viewDate, selectedDisplay) lives here too —
toolbar mutations are pure UI-state changes with no server calls.

Mutation methods (createSchedule, updateSchedule, deleteSchedule,
updatePlaylist) are stubbed and throw 'not implemented in PR-4a'
so a misclick during PR-4b development surfaces a clear error
rather than a silent no-op.

setStatus + setRenderInProgress are placeholders for the SockJS
broadcast subscriber wired in Task 13.

Part of PR-4a of the admin-timeline-redesign spec."
```

---

## Task 4: `js/timeline/util/time.js` — recurrence expansion (the hardest pure-function task)

Mirror of `mosaicmesh.scheduling.schedule_active_at()`. Given a Schedule and a window, return concrete clip placements within that window. **Pure function**, no DOM, no fetch, importable in Node. Comprehensive test coverage because every clip rendered downstream depends on this being right.

**Files:**
- Create: `js/timeline/util/time.js`
- Create: `tests/unit/js/test_time_recurrence.js`
- Modify: `tests/unit/js/test_timeline_smoke.js` (add util/time.js)

### Step 4.1: Read the server's expansion logic

Read `mosaicmesh/scheduling.py` end-to-end. Key contract to preserve:
- `freq ∈ {DAILY, WEEKLY, MONTHLY, YEARLY}` (DAILY + WEEKLY are the cases in active use; MONTHLY/YEARLY are supported but rare)
- `interval ≥ 1` (every Nth period)
- `byweekday: [int]` where 0=Mon..6=Sun (WEEKLY only)
- `dtstart: "YYYY-MM-DD"` — schedule doesn't fire before this date
- `end: {type: 'never'} | {type: 'until', untilDate: 'YYYY-MM-DD'} | {type: 'count', count: N}`
- `exdates: ["YYYY-MM-DD", ...]` — explicitly skip these dates
- `startTime: "HH:MM"`, `endTime: "HH:MM"` — local-time window each occurrence day. If `endTime <= startTime`, the window wraps past midnight into the next day.
- `enabled: bool` — disabled schedules return zero placements
- `priority: int` — higher wins on overlap (the conflict-detection module uses this, not this one)

### Step 4.2: Write the failing tests

Create `tests/unit/js/test_time_recurrence.js`:

```javascript
/**
 * Recurrence-expansion tests for js/timeline/util/time.js.
 *
 * Times are kept in UTC throughout the tests so DST doesn't change
 * results between summer and winter runs. (Production runs in local
 * time; the test harness pins both schedule and window in UTC.)
 */
import { test, describe } from 'node:test';
import assert from 'node:assert';
import { expandSchedule } from '../../../js/timeline/util/time.js';

// Helper: "2026-06-04T08:00:00Z" -> ms since epoch
const ms = (iso) => Date.parse(iso);

function S(overrides) {
  // Schedule with sensible defaults — override anything per-test.
  return {
    id: 'test-1',
    name: 'Test',
    playlistName: 'Pl',
    displayID: 'D',
    priority: 0,
    enabled: true,
    freq: 'DAILY',
    interval: 1,
    byweekday: [],
    dtstart: '2026-06-01',
    end: { type: 'never' },
    exdates: [],
    startTime: '08:00',
    endTime: '11:00',
    _serverVersion: 1,
    ...overrides,
  };
}

describe('expandSchedule — DAILY', () => {
  test('daily within a single day window yields one placement', () => {
    const s = S({ freq: 'DAILY' });
    const out = expandSchedule(s,
      ms('2026-06-04T00:00:00Z'),
      ms('2026-06-05T00:00:00Z'));
    assert.equal(out.length, 1);
    assert.equal(out[0].playlistName, 'Pl');
    assert.equal(out[0].displayID, 'D');
    assert.equal(out[0].scheduleId, 'test-1');
    assert.equal(out[0].startMs, ms('2026-06-04T08:00:00Z'));
    assert.equal(out[0].endMs,   ms('2026-06-04T11:00:00Z'));
  });

  test('daily across a 3-day window yields 3 placements', () => {
    const s = S({ freq: 'DAILY' });
    const out = expandSchedule(s,
      ms('2026-06-04T00:00:00Z'),
      ms('2026-06-07T00:00:00Z'));
    assert.equal(out.length, 3);
  });

  test('dtstart in the future yields zero placements', () => {
    const s = S({ freq: 'DAILY', dtstart: '2026-12-01' });
    const out = expandSchedule(s,
      ms('2026-06-04T00:00:00Z'),
      ms('2026-06-05T00:00:00Z'));
    assert.equal(out.length, 0);
  });

  test('interval=2 fires every other day', () => {
    const s = S({ freq: 'DAILY', interval: 2, dtstart: '2026-06-01' });
    const out = expandSchedule(s,
      ms('2026-06-01T00:00:00Z'),
      ms('2026-06-08T00:00:00Z'));
    // Days 1, 3, 5, 7 fire = 4 placements
    assert.equal(out.length, 4);
  });

  test('disabled schedule yields zero placements', () => {
    const s = S({ enabled: false });
    const out = expandSchedule(s,
      ms('2026-06-04T00:00:00Z'),
      ms('2026-06-05T00:00:00Z'));
    assert.equal(out.length, 0);
  });

  test('exdate skips that specific day', () => {
    const s = S({ exdates: ['2026-06-04'] });
    const out = expandSchedule(s,
      ms('2026-06-03T00:00:00Z'),
      ms('2026-06-06T00:00:00Z'));
    assert.equal(out.length, 2); // 3rd and 5th — 4th excluded
    for (const p of out) {
      assert.notEqual(new Date(p.startMs).getUTCDate(), 4);
    }
  });
});

describe('expandSchedule — WEEKLY', () => {
  test('byweekday=[0,1,2,3,4] (Mon-Fri) skips Sat+Sun', () => {
    const s = S({ freq: 'WEEKLY', byweekday: [0, 1, 2, 3, 4], dtstart: '2026-06-01' });
    // 2026-06-01 is a Monday. Window Mon-Sun.
    const out = expandSchedule(s,
      ms('2026-06-01T00:00:00Z'),
      ms('2026-06-08T00:00:00Z'));
    assert.equal(out.length, 5);
  });

  test('byweekday=[6] (Sun only) returns one placement in a week', () => {
    const s = S({ freq: 'WEEKLY', byweekday: [6], dtstart: '2026-06-01' });
    const out = expandSchedule(s,
      ms('2026-06-01T00:00:00Z'),
      ms('2026-06-08T00:00:00Z'));
    assert.equal(out.length, 1);
    // Should be the Sunday (2026-06-07)
    assert.equal(new Date(out[0].startMs).getUTCDay(), 0); // JS getUTCDay: 0=Sun
  });
});

describe('expandSchedule — end={count}', () => {
  test('count=3 yields at most 3 placements', () => {
    const s = S({ freq: 'DAILY', end: { type: 'count', count: 3 } });
    const out = expandSchedule(s,
      ms('2026-06-01T00:00:00Z'),
      ms('2026-06-30T00:00:00Z'));
    assert.equal(out.length, 3);
  });
});

describe('expandSchedule — end={until}', () => {
  test('untilDate inclusive of last day', () => {
    const s = S({ freq: 'DAILY', dtstart: '2026-06-01',
                  end: { type: 'until', untilDate: '2026-06-03' } });
    const out = expandSchedule(s,
      ms('2026-06-01T00:00:00Z'),
      ms('2026-06-30T00:00:00Z'));
    assert.equal(out.length, 3); // 1, 2, 3
  });
});

describe('expandSchedule — cross-midnight (endTime <= startTime)', () => {
  test('22:00 → 02:00 fires from 22:00 of day to 02:00 next day', () => {
    const s = S({ freq: 'DAILY', startTime: '22:00', endTime: '02:00',
                  dtstart: '2026-06-04' });
    const out = expandSchedule(s,
      ms('2026-06-04T00:00:00Z'),
      ms('2026-06-05T12:00:00Z'));
    // We expect one placement starting 2026-06-04 22:00 and ending 2026-06-05 02:00
    assert.ok(out.length >= 1);
    const first = out[0];
    assert.equal(first.startMs, ms('2026-06-04T22:00:00Z'));
    assert.equal(first.endMs,   ms('2026-06-05T02:00:00Z'));
  });
});

describe('expandSchedule — clipping to window', () => {
  test('placement straddling window-start is clipped at window start', () => {
    const s = S({ freq: 'DAILY', startTime: '22:00', endTime: '02:00',
                  dtstart: '2026-06-01' });
    // Window starts mid-placement: 2026-06-05T00:00Z
    // The placement from 06-04 22:00 to 06-05 02:00 should appear, clipped
    // to start at the window-start.
    const winStart = ms('2026-06-05T00:00:00Z');
    const out = expandSchedule(s, winStart, ms('2026-06-05T12:00:00Z'));
    const cross = out.find(p => p.endMs === ms('2026-06-05T02:00:00Z'));
    assert.ok(cross, 'expected the cross-midnight placement to appear');
    assert.equal(cross.startMs, winStart, 'expected start clipped to window');
  });
});
```

Run: `node --test tests/unit/js/test_time_recurrence.js`. Expected: FAIL — module doesn't exist.

### Step 4.3: Add the module to the smoke

In `tests/unit/js/test_timeline_smoke.js`, add `'js/timeline/util/time.js'` to `MODULES`.

### Step 4.4: Implement `js/timeline/util/time.js`

```javascript
/**
 * Recurrence expansion for the admin timeline.
 *
 * `expandSchedule(schedule, windowStartMs, windowEndMs)` returns an array
 * of concrete clip placements within the window:
 *
 *     [{startMs, endMs, displayID, playlistName, priority, scheduleId}, ...]
 *
 * Mirrors `mosaicmesh.scheduling.schedule_active_at()`. Pure function —
 * no DOM, no fetch — safe to import in Node for tests.
 *
 * **Time zones**: dtstart/exdates/untilDate are interpreted as UTC dates
 * for now (matching the server's date-only handling). startTime/endTime
 * are HH:MM in UTC. A future task could make this local-time-aware via
 * a tzId field on the Schedule.
 */

const DAY_MS = 24 * 60 * 60 * 1000;

function parseYmd(s) {
  // 'YYYY-MM-DD' -> {y, m, d}
  const [y, m, d] = s.split('-').map(Number);
  return { y, m, d };
}

function ymdToMs(s) {
  // Midnight UTC of the given Y-M-D
  const { y, m, d } = parseYmd(s);
  return Date.UTC(y, m - 1, d);
}

function parseHHMM(s) {
  const [h, m] = s.split(':').map(Number);
  return h * 3600_000 + m * 60_000;
}

function jsDow(ms) {
  // JS getUTCDay: 0=Sun..6=Sat. Convert to 0=Mon..6=Sun matching server.
  const js = new Date(ms).getUTCDay();
  return (js + 6) % 7;
}

/**
 * Generate the candidate occurrence start-of-day timestamps within
 * [dtstart, end-clamp ∩ window] given freq + interval + byweekday.
 *
 * Returns midnight-UTC ms for each candidate day.
 */
function candidateDays(s, fromMs, toMs) {
  const dtstartMs = ymdToMs(s.dtstart);
  const startMs = Math.max(dtstartMs, fromMs - DAY_MS);  // pad 1 day for cross-midnight
  const days = [];
  for (let t = startMs; t <= toMs; t += DAY_MS) {
    if (s.freq === 'DAILY') {
      const idx = Math.round((t - dtstartMs) / DAY_MS);
      if (idx >= 0 && (idx % (s.interval || 1) === 0)) days.push(t);
    } else if (s.freq === 'WEEKLY') {
      const dow = jsDow(t);
      if (s.byweekday && s.byweekday.length > 0 && !s.byweekday.includes(dow)) continue;
      const weekIdx = Math.floor((t - dtstartMs) / (7 * DAY_MS));
      if (weekIdx >= 0 && (weekIdx % (s.interval || 1) === 0)) days.push(t);
    } else if (s.freq === 'MONTHLY' || s.freq === 'YEARLY') {
      // Minimal support: fire on same day-of-month as dtstart for MONTHLY,
      // or same month+day for YEARLY. interval respected the same way.
      const ds = new Date(dtstartMs);
      const td = new Date(t);
      if (s.freq === 'MONTHLY') {
        if (td.getUTCDate() !== ds.getUTCDate()) continue;
        const monthsBetween = (td.getUTCFullYear() - ds.getUTCFullYear()) * 12
                            + (td.getUTCMonth() - ds.getUTCMonth());
        if (monthsBetween >= 0 && monthsBetween % (s.interval || 1) === 0) days.push(t);
      } else {  // YEARLY
        if (td.getUTCMonth() !== ds.getUTCMonth()) continue;
        if (td.getUTCDate() !== ds.getUTCDate()) continue;
        const yearsBetween = td.getUTCFullYear() - ds.getUTCFullYear();
        if (yearsBetween >= 0 && yearsBetween % (s.interval || 1) === 0) days.push(t);
      }
    }
  }
  return days;
}

export function expandSchedule(s, windowStartMs, windowEndMs) {
  if (!s || s.enabled === false) return [];
  if (windowEndMs <= windowStartMs) return [];

  const exdateSet = new Set(s.exdates || []);
  const startOfDayOffset = parseHHMM(s.startTime || '00:00');
  let endOfDayOffset = parseHHMM(s.endTime || '23:59');
  const wrapsMidnight = endOfDayOffset <= startOfDayOffset;
  // For cross-midnight (endTime <= startTime), the window actually
  // extends into the NEXT day; add 24h to the end offset.
  if (wrapsMidnight) endOfDayOffset += DAY_MS;

  const days = candidateDays(s, windowStartMs, windowEndMs);

  // end={count} clamp: we count the Nth occurrence FROM dtstart, not
  // from windowStart, so a count-3 schedule yields the same 3 fires
  // regardless of which window we ask about.
  let count = null;
  if (s.end && s.end.type === 'count') count = Math.max(0, s.end.count|0);
  let emitted = 0;

  // end={until} clamp
  let untilMs = null;
  if (s.end && s.end.type === 'until' && s.end.untilDate) {
    // inclusive: 'until' includes that whole day
    untilMs = ymdToMs(s.end.untilDate) + DAY_MS - 1;
  }

  const out = [];
  for (const dayMs of days) {
    // Check exdates by YYYY-MM-DD (UTC)
    const dStr = isoDate(dayMs);
    if (exdateSet.has(dStr)) continue;

    if (count != null && emitted >= count) break;
    emitted += 1;

    let placeStart = dayMs + startOfDayOffset;
    let placeEnd   = dayMs + endOfDayOffset;

    if (untilMs != null && placeStart > untilMs) break;

    // Clip to window
    placeStart = Math.max(placeStart, windowStartMs);
    placeEnd   = Math.min(placeEnd,   windowEndMs);
    if (placeEnd <= placeStart) continue;

    out.push({
      startMs: placeStart,
      endMs:   placeEnd,
      playlistName: s.playlistName,
      displayID: s.displayID,
      priority: s.priority || 0,
      scheduleId: s.id,
    });
  }

  return out;
}

function isoDate(ms) {
  const d = new Date(ms);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  const dd = String(d.getUTCDate()).padStart(2, '0');
  return `${y}-${m}-${dd}`;
}
```

### Step 4.5: Run the recurrence tests — expect PASS

```bash
node --test tests/unit/js/test_time_recurrence.js
```

Expected: all tests pass. If any fail, fix the implementation (the test is the spec).

### Step 4.6: Run the smoke + recurrence tests together

```bash
node --test tests/unit/js/test_timeline_smoke.js tests/unit/js/test_time_recurrence.js
```

Expected: all green.

### Step 4.7: Commit

- [ ] **Commit:**

```bash
git add js/timeline/util/time.js tests/unit/js/test_time_recurrence.js tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): util/time.js — recurrence expansion + Node tests

Client-side mirror of mosaicmesh.scheduling.schedule_active_at().
Pure function, no DOM, importable in Node for tests.

Covers: DAILY+interval, WEEKLY+byweekday, MONTHLY (same day-of-month
each period), YEARLY (same month+day each period), exdates, end=
{never|until|count}, cross-midnight schedules (endTime <= startTime),
and window-clipping (placements that straddle the window boundary
are clipped at the boundary rather than dropped).

Tests in tests/unit/js/test_time_recurrence.js pin the contract.
Run via 'node --test tests/unit/js/*.js'.

Part of PR-4a of the admin-timeline-redesign spec."
```

---

## Task 5: `js/timeline/util/conflicts.js` — conflict detection

Pure helper that compares placements on the same display+window and flags lower-priority overlaps. Used by `clip.js` to render the diagonal-stripe overlay.

**Files:**
- Create: `js/timeline/util/conflicts.js`
- Create: `tests/unit/js/test_clip_conflicts.js`
- Modify: `tests/unit/js/test_timeline_smoke.js`

### Step 5.1: Failing tests

Create `tests/unit/js/test_clip_conflicts.js`:

```javascript
import { test, describe } from 'node:test';
import assert from 'node:assert';
import { detectConflicts } from '../../../js/timeline/util/conflicts.js';

function P(over) {
  return {
    scheduleId: 'a',
    startMs: 0,
    endMs:   100,
    playlistName: 'Pl',
    displayID: 'D',
    priority: 0,
    ...over,
  };
}

describe('detectConflicts', () => {
  test('no overlap → empty', () => {
    const res = detectConflicts([
      P({ scheduleId: 'a', startMs: 0,   endMs: 50  }),
      P({ scheduleId: 'b', startMs: 100, endMs: 150 }),
    ]);
    assert.deepEqual(res, []);
  });

  test('full overlap → lower priority is loser, full-range stripe', () => {
    const res = detectConflicts([
      P({ scheduleId: 'a', priority: 0, startMs: 0,  endMs: 100 }),
      P({ scheduleId: 'b', priority: 5, startMs: 0,  endMs: 100 }),
    ]);
    assert.equal(res.length, 1);
    assert.equal(res[0].loserId, 'a');
    assert.equal(res[0].winnerId, 'b');
    assert.equal(res[0].overlapStartMs, 0);
    assert.equal(res[0].overlapEndMs, 100);
  });

  test('partial overlap → stripe over overlap region only', () => {
    const res = detectConflicts([
      P({ scheduleId: 'a', priority: 0, startMs: 0,  endMs: 60  }),
      P({ scheduleId: 'b', priority: 5, startMs: 30, endMs: 100 }),
    ]);
    assert.equal(res.length, 1);
    assert.equal(res[0].loserId, 'a');
    assert.equal(res[0].overlapStartMs, 30);
    assert.equal(res[0].overlapEndMs,   60);
  });

  test('equal priority → no conflict flagged (server tiebreaker is undefined; UI treats as parallel)', () => {
    const res = detectConflicts([
      P({ scheduleId: 'a', priority: 5, startMs: 0,  endMs: 100 }),
      P({ scheduleId: 'b', priority: 5, startMs: 0,  endMs: 100 }),
    ]);
    assert.deepEqual(res, []);
  });

  test('three-way overlap: lowest loses to both', () => {
    const res = detectConflicts([
      P({ scheduleId: 'a', priority: 0, startMs: 0,  endMs: 100 }),
      P({ scheduleId: 'b', priority: 5, startMs: 20, endMs: 60  }),
      P({ scheduleId: 'c', priority: 5, startMs: 70, endMs: 90  }),
    ]);
    // 'a' has two conflict entries — one for each overlap range
    const aRanges = res.filter(r => r.loserId === 'a');
    assert.equal(aRanges.length, 2);
  });
});
```

Run: expect FAIL — module missing.

### Step 5.2: Add to smoke

Add `'js/timeline/util/conflicts.js'` to `MODULES` in `test_timeline_smoke.js`.

### Step 5.3: Implement `js/timeline/util/conflicts.js`

```javascript
/**
 * Given placements (from util/time.js's expandSchedule applied to all
 * schedules on one display + window), return conflict descriptors:
 *
 *     [{loserId, winnerId, overlapStartMs, overlapEndMs}, ...]
 *
 * One entry per (loser × overlapping-higher-priority-placement) pair.
 * `clip.js` uses these to render the diagonal-stripe overlay on the
 * loser's clip across the overlap region.
 *
 * Equal-priority overlaps are NOT flagged — the server's schedule_active_at
 * does not define a tiebreaker, and the UI shows both clips as parallel
 * (a stripe would imply one wins, which would be misleading).
 */

export function detectConflicts(placements) {
  const out = [];
  // O(n^2) — fine for the per-display visible placement count (<100 typically)
  for (let i = 0; i < placements.length; i++) {
    const a = placements[i];
    for (let j = 0; j < placements.length; j++) {
      if (i === j) continue;
      const b = placements[j];
      if (b.priority <= a.priority) continue;
      const oStart = Math.max(a.startMs, b.startMs);
      const oEnd   = Math.min(a.endMs,   b.endMs);
      if (oEnd <= oStart) continue;
      out.push({
        loserId:  a.scheduleId,
        winnerId: b.scheduleId,
        overlapStartMs: oStart,
        overlapEndMs:   oEnd,
      });
    }
  }
  return out;
}
```

### Step 5.4: Run tests — expect PASS

```bash
node --test tests/unit/js/test_clip_conflicts.js
```

Expected: 5/5 pass.

### Step 5.5: Commit

```bash
git add js/timeline/util/conflicts.js tests/unit/js/test_clip_conflicts.js tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): util/conflicts.js — overlap detection for stripe overlay

Pure helper: given placements on one display within one window,
returns descriptors for each (loser, winner, overlapRange) pair so
clip.js can render the diagonal-stripe overlay over the overlap
region of the lower-priority clip.

Equal-priority overlaps are NOT flagged — server's schedule_active_at
does not define a tiebreaker, and rendering a stripe would mislead
the operator into thinking one wins. UI shows them parallel.

Part of PR-4a of the admin-timeline-redesign spec."
```

---

## Task 6: `js/timeline/timeline/grid-axis.js` — Day grid header

The 00..23 hour-label strip across the top of the Day view. Pure render helper that emits a string of `<div>`s with CSS Grid positions.

**Files:**
- Create: `js/timeline/timeline/grid-axis.js`
- Modify: `tests/unit/js/test_timeline_smoke.js`

### Step 6.1: Implement

```javascript
/**
 * Pure render-helpers for the time-grid axes. No state, no DOM access —
 * returns HTML strings or template fragments that callers inject.
 *
 * Day view: 24 hourly columns. Returns a header strip.
 * Week view: 7 day columns. Returns Mon..Sun header strip.
 * Month view: 7 weekday labels (Mon..Sun) above the calendar grid.
 *
 * CSS Grid columns are defined at the component level (timeline.js);
 * these helpers just produce the column header cells.
 */

const DOW_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export function dayAxisHtml() {
  let html = '';
  for (let h = 0; h < 24; h++) {
    const label = String(h).padStart(2, '0');
    html += `<div class="mm-axis-cell" style="grid-column:${h + 2}">${label}</div>`;
  }
  return html;
}

export function weekAxisHtml(viewDateMs) {
  // viewDateMs is any timestamp within the desired week (UTC). We render
  // Mon..Sun labels with the actual date (e.g. "Mon Jun 1").
  const d = new Date(viewDateMs);
  // Find Monday of this week (UTC)
  const dow = (d.getUTCDay() + 6) % 7;
  const monday = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() - dow));
  let html = '';
  for (let i = 0; i < 7; i++) {
    const cell = new Date(Date.UTC(monday.getUTCFullYear(), monday.getUTCMonth(), monday.getUTCDate() + i));
    const day = cell.getUTCDate();
    const mon = cell.toLocaleString('en-US', { month: 'short', timeZone: 'UTC' });
    html += `<div class="mm-axis-cell" style="grid-column:${i + 2}">${DOW_LABELS[i]} ${mon} ${day}</div>`;
  }
  return html;
}

export function monthWeekdayHeaderHtml() {
  let html = '';
  for (let i = 0; i < 7; i++) {
    html += `<div class="mm-axis-cell" style="grid-column:${i + 1}">${DOW_LABELS[i]}</div>`;
  }
  return html;
}
```

### Step 6.2: Add to smoke

Add `'js/timeline/timeline/grid-axis.js'` to `MODULES`. Run smoke — should pass.

### Step 6.3: Commit

```bash
git add js/timeline/timeline/grid-axis.js tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): grid-axis.js — Day/Week/Month header strips

Pure render-helpers that emit the column-header cells for each view
mode. No state. timeline.js (Task 9) injects them and pairs with
CSS Grid column definitions.

Part of PR-4a of the admin-timeline-redesign spec."
```

---

## Task 7: `js/timeline/timeline/track-header.js` — display group rows

Left-column rendering for one display group: name, online/total count + status dot, optional render-progress badge. Read-only — popovers (PR-4b) are deferred.

**Files:**
- Create: `js/timeline/timeline/track-header.js`
- Modify: `tests/unit/js/test_timeline_smoke.js`

### Step 7.1: Implement

```javascript
/**
 * Track-header row: per-display label, status, render-progress badge.
 *
 * Inputs (function-style — caller passes in resolved values from the
 * store; we don't reach into Alpine here, to keep this importable in
 * Node for future tests):
 *   {
 *     displayID:       string,
 *     friendlyName:    string | null,
 *     onlineCount:     int,
 *     totalCount:      int,
 *     renderInProgress: bool,
 *   }
 *
 * Returns an HTML string.
 *
 * Read-only in PR-4a. PR-4b adds a click handler that opens the
 * track popover (default-playlist + profile override).
 */

function dotColor(online, total) {
  if (total === 0) return '#888';            // no devices
  if (online === 0) return 'var(--err)';     // all offline
  if (online < total) return 'var(--warn)';  // partial
  return 'var(--ok)';                        // all online
}

export function trackHeaderHtml({ displayID, friendlyName, onlineCount, totalCount, renderInProgress }) {
  const label = friendlyName || displayID;
  const color = dotColor(onlineCount, totalCount);
  const badge = renderInProgress
    ? `<span class="mm-render-badge" title="render in progress">⟳ rendering</span>`
    : '';
  return `
    <div class="mm-track-header" data-display-id="${escapeAttr(displayID)}">
      <div class="mm-track-name">${escapeText(label)}</div>
      <div class="mm-track-status">
        <span class="mm-status-dot" style="background:${color}"></span>
        <span class="mm-status-count">${onlineCount}/${totalCount} online</span>
        ${badge}
      </div>
    </div>
  `;
}

function escapeText(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function escapeAttr(s) {
  return escapeText(s).replace(/"/g, '&quot;');
}
```

### Step 7.2: Smoke + commit

Add to smoke `MODULES`, run, commit:

```bash
git add js/timeline/timeline/track-header.js tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): track-header.js — left-column display group rows

Read-only render of per-display name + online/total + status dot +
optional render-progress badge. PR-4b adds the click-popover for
default-playlist + profile override.

Part of PR-4a of the admin-timeline-redesign spec."
```

---

## Task 8: `js/timeline/timeline/clip.js` — single clip block

CSS-grid positioned block with playlist name, time range, and conflict-stripe overlay if applicable. Read-only.

**Files:**
- Create: `js/timeline/timeline/clip.js`
- Modify: `tests/unit/js/test_timeline_smoke.js`

### Step 8.1: Implement

```javascript
/**
 * Render one clip block, positioned via CSS grid-column.
 *
 * Day view positioning:
 *   - The grid has 25 columns: column 1 = track-header label, columns
 *     2..25 = hours 0..23.
 *   - A placement from 08:00 to 11:00 occupies columns 2 + 8 = 10
 *     through 2 + 11 = 13 → `grid-column: 10 / 13`.
 *
 * Week view positioning is handled by a different helper inside
 * timeline.js — this function is Day-view specific.
 *
 * Inputs:
 *   {
 *     placement: {scheduleId, startMs, endMs, playlistName, priority},
 *     viewDateMs:   midnight UTC of the day being rendered,
 *     conflictRanges: [{overlapStartMs, overlapEndMs}, ...]  // for the
 *         stripe overlay on this clip (may be empty),
 *   }
 *
 * Returns an HTML string with `data-schedule-id` so PR-4b's click
 * handlers can target it.
 */

const DAY_MS = 24 * 60 * 60 * 1000;
const HOUR_MS = 60 * 60 * 1000;

function hourFractionFromDayStart(absMs, dayStartMs) {
  return Math.max(0, Math.min(24, (absMs - dayStartMs) / HOUR_MS));
}

export function clipDayHtml({ placement, viewDateMs, conflictRanges = [] }) {
  const startHr = hourFractionFromDayStart(placement.startMs, viewDateMs);
  const endHr   = hourFractionFromDayStart(placement.endMs,   viewDateMs);
  if (endHr <= startHr) return '';

  // Use sub-hour precision via CSS percentages within a single column
  // group. Simpler: pin to integer columns + override left/right with %.
  const colStart = 2 + Math.floor(startHr);
  const colEnd   = 2 + Math.ceil(endHr);
  // sub-hour offsets (0..1) for both ends, applied via inline style:
  const leftPct  = (startHr - Math.floor(startHr)) * 100;
  const rightPct = (Math.ceil(endHr) - endHr) * 100;

  const stripes = renderStripes(conflictRanges, viewDateMs, startHr, endHr);
  const tStart  = formatHm(placement.startMs);
  const tEnd    = formatHm(placement.endMs);

  return `
    <div class="mm-clip" data-schedule-id="${escapeAttr(placement.scheduleId)}"
         style="grid-column:${colStart} / ${colEnd}; margin-left:${leftPct}%; margin-right:${rightPct}%;">
      <div class="mm-clip-title">${escapeText(placement.playlistName)}</div>
      <div class="mm-clip-time">${tStart}–${tEnd}</div>
      ${stripes}
    </div>
  `;
}

function renderStripes(ranges, viewDateMs, clipStartHr, clipEndHr) {
  if (!ranges.length) return '';
  const total = clipEndHr - clipStartHr;
  return ranges.map(r => {
    const rStart = hourFractionFromDayStart(r.overlapStartMs, viewDateMs);
    const rEnd   = hourFractionFromDayStart(r.overlapEndMs,   viewDateMs);
    const leftPct  = ((rStart - clipStartHr) / total) * 100;
    const widthPct = ((rEnd - rStart) / total) * 100;
    return `<div class="mm-clip-stripe" style="left:${leftPct}%; width:${widthPct}%"></div>`;
  }).join('');
}

function formatHm(ms) {
  const d = new Date(ms);
  const h = String(d.getUTCHours()).padStart(2, '0');
  const m = String(d.getUTCMinutes()).padStart(2, '0');
  return `${h}:${m}`;
}

function escapeText(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function escapeAttr(s) {
  return escapeText(s).replace(/"/g, '&quot;');
}
```

### Step 8.2: Smoke + commit

Add to smoke `MODULES`, run, commit:

```bash
git add js/timeline/timeline/clip.js tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): clip.js — Day-view positioned clip block

CSS-grid positioning with sub-hour precision via margin-left/right
percentages. Conflict stripes overlaid via absolutely-positioned
inner divs scoped to the clip's coordinate frame.

Read-only — click handlers and drag are PR-4b.

Part of PR-4a of the admin-timeline-redesign spec."
```

---

## Task 9: `js/timeline/timeline/timeline.js` — top-level Day-view renderer

Glue: consumes `$store.mm`, expands all schedules into placements via util/time.js, groups by display, detects conflicts, emits the full Day-view grid HTML.

**Files:**
- Create: `js/timeline/timeline/timeline.js`
- Modify: `js/timeline/index.js` (import + register Alpine component)
- Modify: `admin.html` (replace placeholder with the Alpine component div)
- Modify: `tests/unit/js/test_timeline_smoke.js`

### Step 9.1: Implement

```javascript
/**
 * Top-level Day-view renderer.
 *
 * The Alpine x-data='mmTimeline' wraps the timeline DOM region. On
 * store updates, the x-html binding re-runs render() which produces
 * the full grid HTML.
 *
 * Day view layout: CSS Grid with 25 columns (track-header label +
 * 24 hour columns) and N + 1 rows (axis header + N display tracks).
 *
 * Week and Month renderers land in Tasks 10-11; this task is
 * Day-view only so we can see something work end-to-end before
 * expanding.
 */
import { expandSchedule } from '../util/time.js';
import { detectConflicts } from '../util/conflicts.js';
import { dayAxisHtml }   from './grid-axis.js';
import { trackHeaderHtml } from './track-header.js';
import { clipDayHtml }   from './clip.js';

const DAY_MS = 24 * 60 * 60 * 1000;

export function mmTimelineComponent() {
  return {
    get visibleWindow() {
      const [y, m, d] = this.$store.mm.viewDate.split('-').map(Number);
      const startMs = Date.UTC(y, m - 1, d);
      return { startMs, endMs: startMs + DAY_MS };
    },

    get tracks() {
      // Unique displayIDs from the device list + 'Default' fallback
      const ids = new Set();
      for (const d of this.$store.mm.displays) {
        if (d.displayID) ids.add(d.displayID);
      }
      if (ids.size === 0) ids.add('Default');
      return Array.from(ids);
    },

    placementsForTrack(displayID) {
      const win = this.visibleWindow;
      const out = [];
      for (const s of this.$store.mm.schedules) {
        if (s.displayID !== displayID) continue;
        out.push(...expandSchedule(s, win.startMs, win.endMs));
      }
      return out;
    },

    statusForTrack(displayID) {
      let online = 0, total = 0;
      let renderInProgress = false;
      for (const c of this.$store.mm.displays) {
        if (c.displayID !== displayID) continue;
        total += 1;
        if (c.isOnline) online += 1;
      }
      if (this.$store.mm.renderInProgress[displayID]) renderInProgress = true;
      return { online, total, renderInProgress };
    },

    renderDay() {
      const tracks = this.tracks;
      const win = this.visibleWindow;
      let html = `<div class="mm-day-grid" style="display:grid; grid-template-columns: 110px repeat(24, 1fr); gap:2px;">`;
      // Axis row
      html += `<div class="mm-axis-cell" style="grid-column:1">Track</div>`;
      html += dayAxisHtml();
      // Tracks
      for (const did of tracks) {
        const placements = this.placementsForTrack(did);
        const conflicts = detectConflicts(placements);
        const status = this.statusForTrack(did);
        const friendly = (this.$store.mm.displays.find(c => c.displayID === did) || {}).friendlyName || did;
        html += `<div class="mm-track-row" style="grid-column:1">${trackHeaderHtml({
          displayID: did, friendlyName: friendly,
          onlineCount: status.online, totalCount: status.total,
          renderInProgress: status.renderInProgress
        })}</div>`;
        for (const p of placements) {
          const conflictRanges = conflicts
            .filter(c => c.loserId === p.scheduleId)
            .map(c => ({ overlapStartMs: c.overlapStartMs, overlapEndMs: c.overlapEndMs }));
          html += clipDayHtml({ placement: p, viewDateMs: win.startMs, conflictRanges });
        }
      }
      html += `</div>`;
      return html;
    },

    render() {
      if (!this.$store.mm.hydrated) return '<div style="color:var(--text-muted)">Loading timeline…</div>';
      if (this.$store.mm.viewMode === 'day')   return this.renderDay();
      if (this.$store.mm.viewMode === 'week')  return '<div>Week view: implemented in Task 10.</div>';
      if (this.$store.mm.viewMode === 'month') return '<div>Month view: implemented in Task 11.</div>';
      return '';
    },
  };
}
```

### Step 9.2: Register the Alpine component

Update `js/timeline/index.js`:

```javascript
import { makeStore } from './store.js';
import { mmTimelineComponent } from './timeline/timeline.js';

document.addEventListener('alpine:init', () => {
  // eslint-disable-next-line no-undef
  Alpine.store('mm', makeStore());
  // eslint-disable-next-line no-undef
  Alpine.data('mmTimeline', mmTimelineComponent);

  // Kick off hydration immediately.
  // eslint-disable-next-line no-undef
  Alpine.store('mm').hydrate();
});
```

### Step 9.3: Replace the placeholder in `admin.html`

Find the existing scaffolding inside `<section data-route="timeline">` and replace the body:

```html
      <section class="section" data-route="timeline" x-data="{}">
        <h2>Timeline</h2>
        <div x-show="!$store.mm.hydrated && !$store.mm.hydrateError" style="color:var(--text-muted)">Loading…</div>
        <div x-show="$store.mm.hydrateError" style="color:var(--err)" x-text="'Failed to load: ' + $store.mm.hydrateError"></div>
        <div x-show="$store.mm.hydrated" x-data="mmTimeline" x-html="render()"></div>
      </section>
```

### Step 9.4: Add minimal CSS

Insert near the existing CSS block in `admin.html` (around line 947, after `.section.active`):

```css
.mm-day-grid { font-size: 12px; }
.mm-axis-cell { color: var(--text-muted); padding: 2px 4px; }
.mm-track-row .mm-track-header { padding: 4px; }
.mm-track-name { font-weight: 600; }
.mm-track-status { color: var(--text-muted); font-size: 11px; }
.mm-status-dot { display:inline-block; width:8px; height:8px; border-radius:50%; vertical-align:middle; margin-right:4px; }
.mm-render-badge { color: var(--warn); font-size: 10px; margin-left: 6px; }
.mm-clip { background: var(--accent, #345); color: white; border-radius: 4px; padding: 4px 6px; position: relative; overflow: hidden; }
.mm-clip-title { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mm-clip-time { font-size: 10px; opacity: 0.8; }
.mm-clip-stripe {
  position: absolute; top:0; bottom:0;
  background: repeating-linear-gradient(45deg, rgba(255,0,0,0.2) 0 6px, transparent 6px 12px);
  pointer-events: none;
}
```

### Step 9.5: Manual browser smoke

Reload `http://localhost:3000/admin#timeline`. Expected:
- Day-view grid renders
- Track-header column on the left shows each display group with online/total + status dot
- The "Morning" playlist appears as a clip if any schedule references it (it does in our test fixture from PR-2 testing)
- No JS errors in devtools

### Step 9.6: Smoke + commit

Add to smoke `MODULES`, run, commit:

```bash
git add js/timeline/timeline/timeline.js js/timeline/index.js admin.html tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): timeline.js — Day-view grid renderer + Alpine wiring

Top-level renderer that consumes \$store.mm, expands schedules to
placements via util/time.js, detects per-track conflicts, and emits
the full Day-view grid. Week and Month views land in Tasks 10-11.

Wired into admin.html: the [data-route='timeline'] section now uses
x-html='render()' bound to the Alpine mmTimeline component.

Minimal CSS for grid + clip + stripe added alongside the existing
admin.html stylesheet. Theme colors reused via CSS custom props.

Part of PR-4a of the admin-timeline-redesign spec."
```

---

## Task 10: Week view — 7-day × hour grid for one selected display

Same recurrence + conflict logic, different layout. Week view selects ONE display from a dropdown (in toolbar — Task 12) and renders a 7-column × hour-row grid.

**Files:**
- Modify: `js/timeline/timeline/timeline.js` (add `renderWeek()`)
- Modify: `tests/unit/js/test_timeline_smoke.js` (re-load smoke after change — same file)

### Step 10.1: Add `renderWeek()` to `timeline.js`

Append (inside the `mmTimelineComponent` returned object, before `render()`):

```javascript
    weekWindow() {
      const [y, m, d] = this.$store.mm.viewDate.split('-').map(Number);
      const baseMs = Date.UTC(y, m - 1, d);
      // Find Monday of the week containing viewDate
      const dow = (new Date(baseMs).getUTCDay() + 6) % 7;
      const startMs = baseMs - dow * DAY_MS;
      return { startMs, endMs: startMs + 7 * DAY_MS };
    },

    renderWeek() {
      const did = this.$store.mm.selectedDisplay;
      if (!did) return '<div style="color:var(--text-muted)">Pick a display to view the week.</div>';
      const win = this.weekWindow();
      // Expand schedules for this one display across the week
      const all = [];
      for (const s of this.$store.mm.schedules) {
        if (s.displayID !== did) continue;
        all.push(...expandSchedule(s, win.startMs, win.endMs));
      }
      const conflicts = detectConflicts(all);

      // Hour rows 06..22 (typical wall-display operating hours); midnight
      // shoulders show as bonus rows.
      const HOUR_START = 0, HOUR_END = 24;
      let html = `<div class="mm-week-grid" style="display:grid; grid-template-columns: 60px repeat(7, 1fr); gap:2px;">`;
      // Header: hour-label col + 7 day labels
      html += `<div class="mm-axis-cell" style="grid-column:1">hr</div>`;
      html += weekAxisHtml(win.startMs);
      // Rows: one per hour
      for (let h = HOUR_START; h < HOUR_END; h++) {
        html += `<div class="mm-axis-cell" style="grid-column:1">${String(h).padStart(2,'0')}</div>`;
        for (let dIdx = 0; dIdx < 7; dIdx++) {
          html += `<div class="mm-week-cell" style="grid-column:${dIdx + 2}"></div>`;
        }
      }
      // Position clips: 1 column per day, vertical extent = % of hour range
      for (const p of all) {
        const dayIdx = Math.floor((p.startMs - win.startMs) / DAY_MS);
        if (dayIdx < 0 || dayIdx > 6) continue;
        const dayStart = win.startMs + dayIdx * DAY_MS;
        const hStart = (p.startMs - dayStart) / (60*60*1000);
        const hEnd   = Math.min(24, (p.endMs   - dayStart) / (60*60*1000));
        const conflictRanges = conflicts
          .filter(c => c.loserId === p.scheduleId)
          .map(c => ({ overlapStartMs: c.overlapStartMs, overlapEndMs: c.overlapEndMs }));
        // Position absolutely inside the day column. We use top/bottom %
        // relative to the (HOUR_END - HOUR_START)*100% total height.
        // Simpler: clip spans grid-row from hour h_start to h_end.
        const rowStart = 2 + Math.floor(hStart);
        const rowEnd   = 2 + Math.ceil(hEnd);
        html += `
          <div class="mm-clip" data-schedule-id="${escapeAttr(p.scheduleId)}"
               style="grid-column:${dayIdx + 2}; grid-row:${rowStart} / ${rowEnd};">
            <div class="mm-clip-title">${escapeText(p.playlistName)}</div>
            <div class="mm-clip-time">${formatHm(p.startMs)}–${formatHm(p.endMs)}</div>
          </div>
        `;
      }
      html += `</div>`;
      return html;
    },
```

Also add the top-of-file helper imports for `weekAxisHtml`, `escapeAttr`, `escapeText`, `formatHm` (or duplicate them — keep it self-contained):

```javascript
import { dayAxisHtml, weekAxisHtml } from './grid-axis.js';
```

And put `escapeAttr`/`escapeText`/`formatHm` helpers at the bottom of `timeline.js`:

```javascript
function escapeText(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function escapeAttr(s) {
  return escapeText(s).replace(/"/g, '&quot;');
}
function formatHm(ms) {
  const d = new Date(ms);
  return `${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`;
}
```

Wire it: in `render()`, replace `if (this.$store.mm.viewMode === 'week') return '<div>Week view: implemented in Task 10.</div>';` with `if (this.$store.mm.viewMode === 'week') return this.renderWeek();`.

### Step 10.2: CSS

Add to admin.html stylesheet:

```css
.mm-week-grid { font-size: 12px; }
.mm-week-cell { background: rgba(255,255,255,0.02); min-height: 18px; }
```

### Step 10.3: Manual smoke

In devtools: `Alpine.store('mm').setViewMode('week')`. Expected: week view renders with the selected display's schedules.

### Step 10.4: Commit

```bash
git add js/timeline/timeline/timeline.js admin.html
git commit -m "feat(timeline): Week view — 7-day × hour grid for one selected display

Same recurrence expansion + conflict detection as Day view, different
layout. Renders hours 00..23 vertically × Mon..Sun horizontally for
the store.selectedDisplay. Toolbar's display picker (Task 12) sets
selectedDisplay.

Part of PR-4a of the admin-timeline-redesign spec."
```

---

## Task 11: Month view — calendar with per-day dot summaries

A 7-column × ~5-row calendar with one dot per active playlist on each day. Cheaper than Week view because we only need per-day occupancy, not minute-level positioning.

**Files:**
- Modify: `js/timeline/timeline/timeline.js` (add `renderMonth()`)
- Modify: `admin.html` (CSS for calendar cells)

### Step 11.1: Implement `renderMonth()`

Add to `timeline.js`:

```javascript
    monthWindow() {
      const [y, m] = this.$store.mm.viewDate.split('-').map(Number);
      const startMs = Date.UTC(y, m - 1, 1);
      const endMs   = Date.UTC(y, m, 1);
      return { startMs, endMs };
    },

    renderMonth() {
      const did = this.$store.mm.selectedDisplay;
      if (!did) return '<div style="color:var(--text-muted)">Pick a display to view the month.</div>';
      const win = this.monthWindow();
      // Build a map: dayIso -> [unique playlist names]
      const perDay = {};
      for (const s of this.$store.mm.schedules) {
        if (s.displayID !== did) continue;
        const placements = expandSchedule(s, win.startMs, win.endMs);
        for (const p of placements) {
          const d = new Date(p.startMs);
          const iso = `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
          if (!perDay[iso]) perDay[iso] = new Set();
          perDay[iso].add(p.playlistName);
        }
      }
      // Render calendar cells. Use the first day of the month, align
      // by getUTCDay (Mon=0..Sun=6).
      const firstDow = (new Date(win.startMs).getUTCDay() + 6) % 7;
      const daysInMonth = (new Date(win.endMs - DAY_MS).getUTCDate());

      let html = `<div class="mm-month-grid" style="display:grid; grid-template-columns: repeat(7, 1fr); gap:2px;">`;
      // Day-of-week header
      html += monthWeekdayHeaderHtml();
      // Leading blanks (cells before day 1)
      for (let i = 0; i < firstDow; i++) {
        html += `<div class="mm-month-cell mm-month-cell-blank"></div>`;
      }
      // Days
      for (let day = 1; day <= daysInMonth; day++) {
        const [y, m] = this.$store.mm.viewDate.split('-').map(Number);
        const iso = `${y}-${String(m).padStart(2,'0')}-${String(day).padStart(2,'0')}`;
        const playlists = Array.from(perDay[iso] || []);
        const dots = playlists.map(pl =>
          `<span class="mm-month-dot" title="${escapeAttr(pl)}" style="background:${colorForPlaylist(pl)}"></span>`
        ).join('');
        html += `<div class="mm-month-cell">
          <div class="mm-month-num">${day}</div>
          <div class="mm-month-dots">${dots}</div>
        </div>`;
      }
      html += `</div>`;
      return html;
    },
```

Add helper at the bottom of `timeline.js`:

```javascript
function colorForPlaylist(name) {
  // Stable, content-derived color via a tiny string hash.
  let h = 0;
  for (let i = 0; i < name.length; i++) {
    h = (h * 31 + name.charCodeAt(i)) >>> 0;
  }
  return `hsl(${h % 360} 65% 55%)`;
}
```

Add to imports at top of `timeline.js`:

```javascript
import { dayAxisHtml, weekAxisHtml, monthWeekdayHeaderHtml } from './grid-axis.js';
```

Wire it: in `render()`, replace the month-stub return with `return this.renderMonth();`.

### Step 11.2: CSS

Add to admin.html:

```css
.mm-month-grid { font-size: 12px; }
.mm-month-cell { background: rgba(255,255,255,0.02); min-height: 60px; padding: 4px; position: relative; }
.mm-month-cell-blank { background: transparent; }
.mm-month-num { font-weight: 600; }
.mm-month-dots { margin-top: 4px; }
.mm-month-dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:3px; }
```

### Step 11.3: Manual smoke

`Alpine.store('mm').setViewMode('month')`. Expected: month calendar with dots for days that have active schedules.

### Step 11.4: Commit

```bash
git add js/timeline/timeline/timeline.js admin.html
git commit -m "feat(timeline): Month view — calendar with per-day playlist dots

Lighter-weight than Week view (per-day occupancy only, no minute-
level placement). Each unique playlist active on a day gets one
colored dot, hash-derived from the playlist name so colors are
stable and unique without operator config.

Part of PR-4a of the admin-timeline-redesign spec."
```

---

## Task 12: `js/timeline/toolbar.js` — view-mode toggle, date nav, fleet actions

The top bar above the grid. Day/Week/Month toggle, date nav arrows + Today, display picker (Week/Month), fleet-action buttons that proxy to existing jQuery handlers.

**Files:**
- Create: `js/timeline/toolbar.js`
- Modify: `js/timeline/index.js` (register component)
- Modify: `admin.html` (insert toolbar div in timeline section)

### Step 12.1: Implement `js/timeline/toolbar.js`

```javascript
/**
 * Top toolbar: view-mode toggle, date nav, Today, display picker
 * (Week/Month modes), fleet-action buttons.
 *
 * Fleet actions proxy to the existing jQuery globals
 * (window.runScriptAll, etc.) rather than going through Alpine — this
 * keeps PR-4a compatible with the legacy SockJS-based fleet-action UX
 * that's been working in production.
 */

const DAY_MS = 24 * 60 * 60 * 1000;

function isoDate(ms) {
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
}

export function mmToolbarComponent() {
  return {
    get displays() { return this.$store.mm.displays; },
    get availableDisplayIds() {
      const ids = new Set();
      for (const d of this.$store.mm.displays) if (d.displayID) ids.add(d.displayID);
      return Array.from(ids);
    },

    setMode(m) { this.$store.mm.setViewMode(m); },

    today() { this.$store.mm.goToday(); },

    /** Step by 1 day (Day view) / 7 days (Week) / 1 month (Month). */
    step(dir) {
      const cur = this.$store.mm.viewDate;
      const [y, m, d] = cur.split('-').map(Number);
      let next;
      if (this.$store.mm.viewMode === 'day') {
        next = Date.UTC(y, m - 1, d) + dir * DAY_MS;
        this.$store.mm.setViewDate(isoDate(next));
      } else if (this.$store.mm.viewMode === 'week') {
        next = Date.UTC(y, m - 1, d) + dir * 7 * DAY_MS;
        this.$store.mm.setViewDate(isoDate(next));
      } else {  // month
        next = Date.UTC(y, m - 1 + dir, 1);
        this.$store.mm.setViewDate(isoDate(next));
      }
    },

    setSelectedDisplay(id) { this.$store.mm.selectDisplay(id); },

    // Fleet actions proxy to existing jQuery handlers
    fleetAction(which) {
      if (typeof window.runScriptAll === 'function') {
        window.runScriptAll(which);
      } else {
        console.warn('[timeline] runScriptAll not available on window');
      }
    },

    formatDate() {
      const cur = this.$store.mm.viewDate;
      const [y, m, d] = cur.split('-').map(Number);
      const dt = new Date(Date.UTC(y, m - 1, d));
      if (this.$store.mm.viewMode === 'day') {
        return dt.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
      }
      if (this.$store.mm.viewMode === 'week') {
        // Show week-of "Jun 1 – Jun 7, 2026"
        const dow = (dt.getUTCDay() + 6) % 7;
        const mon = new Date(Date.UTC(dt.getUTCFullYear(), dt.getUTCMonth(), dt.getUTCDate() - dow));
        const sun = new Date(mon.getTime() + 6 * DAY_MS);
        const fmt = (x) => x.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
        return `${fmt(mon)} – ${fmt(sun)}, ${sun.getUTCFullYear()}`;
      }
      // month
      return dt.toLocaleDateString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });
    },
  };
}
```

### Step 12.2: Register in `index.js`

Add the import + Alpine.data registration:

```javascript
import { makeStore } from './store.js';
import { mmTimelineComponent } from './timeline/timeline.js';
import { mmToolbarComponent } from './toolbar.js';

document.addEventListener('alpine:init', () => {
  Alpine.store('mm', makeStore());
  Alpine.data('mmTimeline', mmTimelineComponent);
  Alpine.data('mmToolbar', mmToolbarComponent);
  Alpine.store('mm').hydrate();
});
```

### Step 12.3: Add the toolbar HTML to `admin.html`

Replace the existing timeline section body:

```html
      <section class="section" data-route="timeline" x-data="{}">
        <h2>Timeline</h2>
        <div x-show="!$store.mm.hydrated && !$store.mm.hydrateError" style="color:var(--text-muted)">Loading…</div>
        <div x-show="$store.mm.hydrateError" style="color:var(--err)" x-text="'Failed to load: ' + $store.mm.hydrateError"></div>

        <div x-show="$store.mm.hydrated" x-data="mmToolbar" class="mm-toolbar">
          <button class="btn" :class="{'btn-active': $store.mm.viewMode==='day'}"   @click="setMode('day')">Day</button>
          <button class="btn" :class="{'btn-active': $store.mm.viewMode==='week'}"  @click="setMode('week')">Week</button>
          <button class="btn" :class="{'btn-active': $store.mm.viewMode==='month'}" @click="setMode('month')">Month</button>
          <button class="btn btn-ghost" @click="step(-1)">◀</button>
          <span class="mm-toolbar-date" x-text="formatDate()"></span>
          <button class="btn btn-ghost" @click="step(1)">▶</button>
          <button class="btn btn-ghost" @click="today()">Today</button>

          <template x-if="$store.mm.viewMode !== 'day'">
            <select class="input" :value="$store.mm.selectedDisplay"
                    @change="setSelectedDisplay($event.target.value)">
              <template x-for="id in availableDisplayIds" :key="id">
                <option :value="id" x-text="id"></option>
              </template>
            </select>
          </template>

          <span style="flex:1"></span>
          <span class="size" style="color:var(--text-muted)">Fleet:</span>
          <button class="btn btn-ghost" @click="fleetAction('login')"  title="Wake + unlock every device (SSH)">🔓</button>
          <button class="btn btn-ghost" @click="fleetAction('start')"  title="Open the display page on every device">▶</button>
          <button class="btn btn-ghost" @click="fleetAction('stop')"   title="Close the display on every device">⏹</button>
          <button class="btn btn-ghost" @click="fleetAction('reboot')" title="Reboot every device">⟲</button>
          <button class="btn btn-ghost" @click="fleetAction('test')"   title="Open in diagnostics (?tdbg)">🐞</button>
        </div>

        <div x-show="$store.mm.hydrated" x-data="mmTimeline" x-html="render()"></div>
      </section>
```

### Step 12.4: Toolbar CSS

Add to admin.html:

```css
.mm-toolbar { display: flex; align-items: center; gap: 6px; padding: 8px 0; flex-wrap: wrap; }
.mm-toolbar-date { font-weight: 600; min-width: 220px; text-align: center; }
.btn-active { background: var(--accent, #345); color: white; }
```

### Step 12.5: Smoke + commit

Add `js/timeline/toolbar.js` to `MODULES`. Run smoke. Manual: switch views in the browser, navigate dates, click Today, watch the grid update.

```bash
git add js/timeline/toolbar.js js/timeline/index.js admin.html tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): toolbar.js — view-mode toggle + date nav + fleet actions

Day/Week/Month toggle + date navigation (◀ ▶ Today) + display picker
(Week/Month modes) + fleet-action buttons. Fleet actions proxy to
the existing window.runScriptAll jQuery handler, keeping the
legacy SockJS-based fleet UX working in PR-4a without re-implementing
it through REST.

Part of PR-4a of the admin-timeline-redesign spec."
```

---

## Task 13: `js/timeline/timeline/sockjs-status.js` — live status updates

Wire DISCOVERY_HEARTBEAT / CLIENTS_WENT_OFFLINE / RENDER_IN_PROGRESS broadcasts into the store.

**Files:**
- Create: `js/timeline/timeline/sockjs-status.js`
- Modify: `js/timeline/index.js` (start the subscriber after hydrate)

### Step 13.1: Implement

```javascript
/**
 * Subscribe to the existing window-global SockJS connection and route
 * fleet status broadcasts into Alpine.store('mm').
 *
 * The legacy admin code already opens a SockJS connection at
 * window.sock and dispatches messages via REQUEST type. We add a tiny
 * listener that forwards the three status broadcasts we care about
 * without re-implementing the connection.
 *
 * Read-only in PR-4a: we update displays[].isOnline and per-display
 * renderInProgress, never mutate schedules.
 */

export function startStatusSubscriber(store) {
  function handle(msg) {
    if (!msg || typeof msg !== 'object') return;
    const req = msg.REQUEST;
    const payload = msg.PAYLOAD;
    if (req === 'DISCOVERY_HEARTBEAT') {
      // payload: {devices: [{clientKey, displayID, isOnline, ...}, ...]}
      const devs = payload?.devices ?? [];
      for (const d of devs) {
        store.setStatus(d.displayID || d.clientKey, {
          isOnline: !!d.isOnline,
          friendlyName: d.friendlyName,
        });
      }
    } else if (req === 'CLIENTS_WENT_OFFLINE') {
      // payload: {clientKeys: [...]} or {displayIDs: [...]}
      const keys = payload?.clientKeys ?? [];
      for (const k of keys) store.setStatus(k, { isOnline: false });
      const ids  = payload?.displayIDs ?? [];
      for (const id of ids) store.setStatus(id, { isOnline: false });
    } else if (req === 'RENDER_IN_PROGRESS') {
      // payload: {displayID, inProgress}
      if (payload?.displayID) {
        store.setRenderInProgress(payload.displayID, !!payload.inProgress);
      }
    }
  }

  // The legacy code stores a SockJS connection in window.sock and
  // registers message handlers via $(window).on('mm:msg', ...). The
  // shape varies — we try both common paths and warn if we can't hook.
  if (window.sock && typeof window.sock.onmessage !== 'undefined') {
    const prev = window.sock.onmessage;
    window.sock.onmessage = function (ev) {
      try {
        const data = (typeof ev.data === 'string') ? JSON.parse(ev.data) : ev.data;
        handle(data);
      } catch (e) { /* ignore parse errors — not all messages are JSON */ }
      if (prev) prev.call(this, ev);
    };
  } else if (window.jQuery) {
    window.jQuery(window).on('mm:msg', (_e, msg) => handle(msg));
  } else {
    console.warn('[timeline] no SockJS hook available; status will not auto-refresh');
  }
}
```

### Step 13.2: Start it in `index.js`

```javascript
import { makeStore } from './store.js';
import { mmTimelineComponent } from './timeline/timeline.js';
import { mmToolbarComponent } from './toolbar.js';
import { startStatusSubscriber } from './timeline/sockjs-status.js';

document.addEventListener('alpine:init', () => {
  const store = makeStore();
  Alpine.store('mm', store);
  Alpine.data('mmTimeline', mmTimelineComponent);
  Alpine.data('mmToolbar', mmToolbarComponent);
  store.hydrate();
  startStatusSubscriber(store);
});
```

### Step 13.3: Smoke + commit

Add `js/timeline/timeline/sockjs-status.js` to `MODULES`. Run smoke. Manual: toggle one iPad's wifi off → wait 60s → expect that display's status dot turns red (CLIENTS_WENT_OFFLINE broadcast).

```bash
git add js/timeline/timeline/sockjs-status.js js/timeline/index.js tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): sockjs-status.js — live DISCOVERY_HEARTBEAT subscriber

Routes the three fleet-status broadcasts (DISCOVERY_HEARTBEAT,
CLIENTS_WENT_OFFLINE, RENDER_IN_PROGRESS) into store.setStatus /
setRenderInProgress so the track-header dots + render badge update
without re-fetching /api/discovery/devices.

Hooks the existing window.sock connection (legacy admin's SockJS),
falling back to jQuery's mm:msg event if available. Read-only — no
schedule mutations.

Part of PR-4a of the admin-timeline-redesign spec."
```

---

## Task 14: `js/timeline/timeline/now-line.js` — red now-indicator

Single vertical 2px line at the current clock position; advances every second via one `setInterval`; autoscrolls into view on first paint; hidden in Month view.

**Files:**
- Create: `js/timeline/timeline/now-line.js`
- Modify: `js/timeline/timeline/timeline.js` (overlay the line in Day view)
- Modify: `admin.html` (CSS for the line)

### Step 14.1: Implement

```javascript
/**
 * Red now-line overlay for Day and Week views.
 *
 * Day view: horizontal 100%, the line is a vertical 2px bar at
 * (now - viewDateStart) / 24h * width.
 *
 * Week view: positioned in the column matching today's day-of-week,
 * if today is within the displayed week; otherwise hidden.
 *
 * Month view: hidden (a single position would be ambiguous on a
 * calendar).
 *
 * One setInterval at the module level updates the line's transform
 * every 1s. autoscrollIntoView() runs once on first paint to bring
 * the current time into view on Day-view load.
 */

const DAY_MS = 24 * 60 * 60 * 1000;

let _intervalId = null;

export function startNowLine(getStore) {
  function tick() {
    const lines = document.querySelectorAll('.mm-now-line');
    for (const el of lines) updateOne(el, getStore());
  }
  if (_intervalId) clearInterval(_intervalId);
  _intervalId = setInterval(tick, 1000);
  tick();
}

function updateOne(el, store) {
  const mode = store.viewMode;
  if (mode === 'month') { el.style.display = 'none'; return; }

  const now = Date.now();
  const [y, m, d] = store.viewDate.split('-').map(Number);
  const baseMs = Date.UTC(y, m - 1, d);

  if (mode === 'day') {
    if (now < baseMs || now >= baseMs + DAY_MS) { el.style.display = 'none'; return; }
    el.style.display = 'block';
    const frac = (now - baseMs) / DAY_MS;
    el.style.left = (frac * 100) + '%';
  } else if (mode === 'week') {
    const dow = (new Date(baseMs).getUTCDay() + 6) % 7;
    const monday = baseMs - dow * DAY_MS;
    const today = new Date();
    const todayBase = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
    if (todayBase < monday || todayBase >= monday + 7 * DAY_MS) {
      el.style.display = 'none'; return;
    }
    el.style.display = 'block';
    const colIdx = Math.floor((todayBase - monday) / DAY_MS);
    // Anchor to the day column: 1 (label) + colIdx + 1 (1-indexed grid)
    // Approximate horizontal placement: ~60px label + colIdx * (col width)
    // We can't easily compute the exact px without measuring; instead
    // CSS Grid spans handle column placement and we offset by a tiny
    // hour-fraction. Practical approach: skip horizontal anim in Week
    // view and just show the line at the day-column boundary. Vertical
    // anim happens via top % within the day column.
    const hourFrac = ((now - todayBase) / (24*3600_000));
    el.style.top  = (hourFrac * 100) + '%';
    el.style.left = `calc(60px + ${colIdx} * ((100% - 60px) / 7))`;
    el.style.width = `calc((100% - 60px) / 7)`;
  }
}

export function autoscrollIntoView() {
  // Day view only — bring the now-line into the visible scrollport
  const el = document.querySelector('.mm-day-grid .mm-now-line');
  if (!el) return;
  el.scrollIntoView({ behavior: 'auto', inline: 'center', block: 'nearest' });
}
```

### Step 14.2: Inject the now-line into the grid

Modify `renderDay()` and `renderWeek()` in `timeline.js` to append a `<div class="mm-now-line"></div>` at the end of the grid container. Update `index.js`:

```javascript
import { startNowLine, autoscrollIntoView } from './timeline/now-line.js';

document.addEventListener('alpine:init', () => {
  const store = makeStore();
  Alpine.store('mm', store);
  Alpine.data('mmTimeline', mmTimelineComponent);
  Alpine.data('mmToolbar', mmToolbarComponent);
  store.hydrate().then(() => {
    requestAnimationFrame(() => autoscrollIntoView());
  });
  startStatusSubscriber(store);
  startNowLine(() => Alpine.store('mm'));
});
```

### Step 14.3: CSS

Add to admin.html:

```css
.mm-day-grid, .mm-week-grid { position: relative; }
.mm-now-line {
  position: absolute; top: 0; bottom: 0; width: 2px;
  background: var(--err, red); pointer-events: none;
  left: 0; z-index: 5;
}
```

### Step 14.4: Smoke + commit

```bash
git add js/timeline/timeline/now-line.js js/timeline/timeline/timeline.js js/timeline/index.js admin.html tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): now-line.js — red current-time indicator

Day view: full-height vertical 2px bar at (now - viewDateStart) /
24h fraction.
Week view: positioned in today's column, with vertical hour-fraction.
Month view: hidden (single position ambiguous on a calendar).

Single setInterval at 1Hz updates all visible lines. autoscrollInto
View() runs once after hydrate to bring the current time into view.

Part of PR-4a of the admin-timeline-redesign spec."
```

---

## Task 15: `js/timeline/bin/*.js` — left bin (read-only display)

Media library + Playlist library lists. Drag is deferred to PR-4b — this task just shows the lists.

**Files:**
- Create: `js/timeline/bin/media-bin.js`
- Create: `js/timeline/bin/playlist-bin.js`
- Modify: `js/timeline/index.js`
- Modify: `admin.html` (left bin div)

### Step 15.1: Implement both bin components

`js/timeline/bin/media-bin.js`:

```javascript
export function mmMediaBinComponent() {
  return {
    get items() {
      const media = this.$store.mm.media || {};
      const images = (media.images || []).map(url => ({ kind: 'image', url, name: basename(url) }));
      const videos = (media.videos || []).map(url => ({ kind: 'video', url, name: basename(url),
        duration: media.videoDurations?.[url] }));
      return [...images, ...videos];
    },
    search: '',
    filtered() {
      const q = this.search.trim().toLowerCase();
      if (!q) return this.items;
      return this.items.filter(it => it.name.toLowerCase().includes(q));
    },
  };
}

function basename(p) { return p.split('/').pop(); }
```

`js/timeline/bin/playlist-bin.js`:

```javascript
export function mmPlaylistBinComponent() {
  return {
    get list() {
      return Object.values(this.$store.mm.playlists || {})
        .sort((a, b) => a.name.localeCompare(b.name));
    },
  };
}
```

### Step 15.2: Register and wire HTML

Update `index.js`:

```javascript
import { mmMediaBinComponent } from './bin/media-bin.js';
import { mmPlaylistBinComponent } from './bin/playlist-bin.js';

document.addEventListener('alpine:init', () => {
  // ... existing registrations ...
  Alpine.data('mmMediaBin', mmMediaBinComponent);
  Alpine.data('mmPlaylistBin', mmPlaylistBinComponent);
  // ...
});
```

In `admin.html` timeline section, wrap toolbar+grid in a left/right layout and insert the bin:

```html
      <section class="section" data-route="timeline" x-data="{}">
        <h2>Timeline</h2>
        <div x-show="!$store.mm.hydrated && !$store.mm.hydrateError" style="color:var(--text-muted)">Loading…</div>
        <div x-show="$store.mm.hydrateError" style="color:var(--err)" x-text="'Failed to load: ' + $store.mm.hydrateError"></div>

        <div x-show="$store.mm.hydrated" class="mm-timeline-layout">
          <aside class="mm-bin">
            <div x-data="mmMediaBin" class="mm-bin-section">
              <div class="mm-bin-title">Media (<span x-text="filtered().length"></span>)</div>
              <input class="input" type="text" x-model="search" placeholder="Search…" />
              <ul class="mm-bin-list">
                <template x-for="it in filtered()" :key="it.url">
                  <li class="mm-bin-item">
                    <span x-text="it.name"></span>
                    <span class="size" style="color:var(--text-muted)" x-text="it.duration ? ' ' + it.duration + 's' : ''"></span>
                  </li>
                </template>
              </ul>
            </div>
            <div x-data="mmPlaylistBin" class="mm-bin-section">
              <div class="mm-bin-title">Playlists</div>
              <ul class="mm-bin-list">
                <template x-for="p in list" :key="p.name">
                  <li class="mm-bin-item" x-text="p.name"></li>
                </template>
              </ul>
            </div>
          </aside>
          <div class="mm-timeline-main">
            <div x-data="mmToolbar" class="mm-toolbar">
              <!-- toolbar contents from Task 12 -->
            </div>
            <div x-data="mmTimeline" x-html="render()"></div>
          </div>
        </div>
      </section>
```

### Step 15.3: Layout CSS

```css
.mm-timeline-layout { display: flex; gap: 12px; }
.mm-bin { width: 200px; flex: 0 0 200px; }
.mm-bin-section { margin-bottom: 16px; }
.mm-bin-title { font-weight: 600; margin-bottom: 6px; }
.mm-bin-list { list-style: none; padding: 0; margin: 0; max-height: 240px; overflow-y: auto; }
.mm-bin-item { padding: 4px 6px; cursor: default; }
.mm-bin-item:hover { background: rgba(255,255,255,0.05); }
.mm-timeline-main { flex: 1 1 auto; min-width: 0; }
```

### Step 15.4: Smoke + commit

Add both bin files to `MODULES`. Run smoke. Browser check: media + playlist lists visible.

```bash
git add js/timeline/bin/ js/timeline/index.js admin.html tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): media-bin + playlist-bin — left-column lists

Read-only display of /api/media and /api/playlists. Search input on
media. Drag-source behavior (drop-onto-track to create a schedule,
drop-onto-clip to extend a playlist) lands in PR-4b.

Part of PR-4a of the admin-timeline-redesign spec."
```

---

## Task 16: Pytest runner integration + CLAUDE.md update + manual smoke

Wire the JS tests into `pytest_runner.py` so they run alongside the Python suite. Update CLAUDE.md to mention `js/timeline/`. Smoke the full admin page.

**Files:**
- Modify: `pytest_runner.py`
- Modify: `CLAUDE.md` (Layout section)

### Step 16.1: Add `--js` flag to `pytest_runner.py`

Read `pytest_runner.py` first to see the existing flag pattern. Add a `--js` mode that runs `node --test tests/unit/js/*.js`. The exact code depends on the existing runner shape — match its style.

Specifically: find the argparse section, add:

```python
parser.add_argument('--js', action='store_true', help='Run Node-based JS unit tests under tests/unit/js/')
```

And after the existing test-invocation block, add:

```python
if args.js:
    import subprocess, glob
    files = sorted(glob.glob('tests/unit/js/test_*.js'))
    if not files:
        print('No JS tests found at tests/unit/js/test_*.js')
        sys.exit(0)
    print(f'Running {len(files)} JS test files via node --test...')
    rc = subprocess.call(['node', '--test'] + files)
    sys.exit(rc)
```

### Step 16.2: Update `CLAUDE.md`

Find the existing `Layout` section. After the `mosaicmesh/` package bullets, add a new `js/` block:

```markdown
- `js/timeline/` — admin-side ES modules (PR-4a+, modern JS). NOT loaded on the iPad-1 display clients (those load `js/mosiacmesh.js` + `js/GoTime.js` which stay ES5 + jQuery 1.x). Top-level `js/timeline/index.js` is the Alpine.js bootstrap loaded from `admin.html`. See `js/timeline/README.md` for the module map.
- `tests/unit/js/` — Node 20+ `--test` suites for the pure-function JS modules (`util/time.js`, `util/conflicts.js`) + a module-load smoke. Run via `python pytest_runner.py --js` or `node --test tests/unit/js/*.js`.
```

In the Conventions section, add:

```markdown
- **Admin UI uses Alpine.js 3.x + native ES modules.** Loaded from CDN; no build step. Coexists with jQuery 1.x in `admin.html` (Alpine sits alongside, doesn't replace). Display clients on the iPad-1 are unaffected — they still load ES5 + jQuery 1.x.
```

### Step 16.3: Smoke the full admin page

- [ ] Open `http://localhost:3000/admin#timeline` in a modern browser (Chrome/Firefox/Safari)
- [ ] Expected:
  - Timeline view is the default landing route
  - Toolbar: Day mode highlighted, today's date in the date label
  - Left bin: media list shows the existing files (`big_buck_bunny_1080p_h264.mov`, `probe_test.mp4`); playlist list shows `Morning`
  - Grid: 24 hourly columns visible. Track headers for each display group with online/total dots. If a schedule exists, its clip renders.
  - Red now-line vertical bar at current time, advancing every second.
  - Switch to Week view: 7-day grid, one display selected.
  - Switch to Month view: calendar with dots on days that have schedules.
  - Switch to Day → date nav ◀ ▶ Today works.
  - Devtools console: no errors.
- [ ] Click each old route (Overview, Displays, etc.). Expected: still work unchanged.

### Step 16.4: Run the full suite

```bash
python pytest_runner.py --unit
python pytest_runner.py --js
```

Expected: unit suite same as PR-3's baseline (13 pre-existing failures unchanged); JS suite all green.

### Step 16.5: Commit

```bash
git add pytest_runner.py CLAUDE.md
git commit -m "docs(claude-md): document PR-4a admin timeline + js/timeline layout

CLAUDE.md Layout + Conventions updated to describe the new
js/timeline/ ES-module home, the Node-based JS test directory,
and the Alpine.js + ES module convention for admin code (display
clients on iPad-1 remain ES5 + jQuery 1.x).

pytest_runner.py gains a --js flag that runs all
tests/unit/js/test_*.js files via 'node --test'.

Closes PR-4a (read-only timeline) of the admin-timeline-redesign
spec."
```

---

## Self-Review Checklist (run before opening the PR)

- [ ] `python pytest_runner.py --unit` shows same 13 pre-existing failures as PR-3's baseline (no regressions from this branch)
- [ ] `python pytest_runner.py --js` is all green
- [ ] `node --test tests/unit/js/test_time_recurrence.js` — every recurrence test passes (this is the highest-leverage suite — wrong here means every clip in the UI is wrong)
- [ ] `node --test tests/unit/js/test_clip_conflicts.js` — all conflict tests pass
- [ ] Admin page renders cleanly in Chrome, Firefox, and Safari (just one or two minutes each)
- [ ] `#timeline` is the default landing route
- [ ] Each of the four old routes (`#overview`, `#displays`, `#media`, `#playlists`, `#schedules`, `#console`) still works unchanged
- [ ] No console errors in any view
- [ ] `git log --oneline feature/pr4a-timeline-readonly ^feature/pr3-scripting-profile-dispatcher` shows ~16 task commits

---

## Notes for the implementing engineer

1. **Time zones.** PR-4a treats all schedule times as UTC. The server's `mosaicmesh.scheduling.schedule_active_at` does the same. A future PR can add `tzId` to Schedule and convert at the boundary; the recurrence-expansion code is designed to be tz-naive by treating HH:MM as offsets from the same midnight as `dtstart`.

2. **No mutations from this PR.** Every server call is GET. If any task tempts you to add POST/PUT/DELETE — STOP. That's PR-4b. The stubbed mutation methods in `store.js` will throw `'not implemented in PR-4a'` if called, which is the desired safety net.

3. **CSS variables.** Reuse the existing admin.html CSS custom properties (`--accent`, `--err`, `--warn`, `--ok`, `--text-muted`, etc.) rather than hardcoding colors. The theme toggle (☀/🌙) flips them at runtime — hardcoded colors would break in light mode.

4. **Alpine `x-html`.** `x-html` directly injects HTML — fast for our grid (1-100 clips per view) but BE CAREFUL with the escape helpers in `clip.js`/`track-header.js` for any operator-controlled string (playlist name, friendly name). The escape helpers are there for a reason; don't bypass them.

5. **No build step.** Spec mandate. Alpine 3.13.10 is loaded from a CDN; ES modules are native (`<script type="module">`); no Webpack, no Vite, no Rollup. If a future requirement makes the build step feel necessary, that decision needs a spec amendment first.

6. **iPad-1 compatibility.** Nothing in this PR touches files loaded on the iPad. The display clients still load `index.html` + `js/mosiacmesh.js` + `js/GoTime.js`, all of which stay ES5 + jQuery 1.x. Verify by `grep -L 'arrow\|=>' js/mosiacmesh.js js/GoTime.js` (should print both filenames — empty grep means "no arrow functions found").

7. **Read-only contract.** The PR-4b plan will reference this PR's modules and extend them with drag-handlers and click-mutation flows. If a module's interface needs to change to support 4b, do it in PR-4b — don't speculatively change interfaces here.
