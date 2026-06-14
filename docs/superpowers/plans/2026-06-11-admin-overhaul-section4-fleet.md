# Admin Overhaul — Section 4 (Fleet) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dedicated master-detail Fleet destination (groups list → per-group detail with sectioned cards) and relocate all device/group/calibration/profile/script/render management out of the Schedule screen.

**Architecture:** A new `mmFleet` Alpine component switches a master-detail layout on the existing `store.isMobile` flag (760px). New code lives in `js/timeline/fleet/`: pure, node-tested status helpers (`fleet-status.js`) + the component (`fleet-view.js`). The Fleet markup lives in `admin.html` as **reactive Alpine templates** (`x-for`/`@click`/`x-model`) — not `x-html` strings — because device rows carry `<select>` dropdowns + checkboxes. Every action reuses existing store mutators + modals; "Render now" is the one new (one-line SockJS) affordance.

**Tech Stack:** Alpine.js 3.x + native ES modules (no build step), `node --test`, Playwright.

**Spec:** `docs/superpowers/specs/2026-06-11-admin-overhaul-section4-fleet-design.md`

**Branch:** `feature/admin-overhaul-section4` (already created, stacked on Section 2 which holds Sections 2+3; the spec is committed there).

**Conventions (verified against the codebase):**
- Store device shape (`store.displays[]`): `{ clientKey, displayID, friendlyName, isOnline, profileName, deviceType, measuredPerimeter?, ... }`. `profileName` empty/absent = auto-match.
- Store group shape (`store.displayGroups[]`): `{ displayID, clients[], clientCount, onlineCount, scheduleCount }`.
- Store `playback[displayID]`: `{ state, currentPlaylist, startedEpoch, renderStatus }` (state values include `'PLAY'`, `'PAUSE'`, `'STOP'`, `'PREPARING'`, `'IDLE'`).
- Store `renderInProgress[displayID]`: bool.
- Store mutators (all optimistic + rollback): `createDisplayGroup(displayID)`, `deleteDisplayGroup(displayID)`, `assignDeviceToDisplay(clientKey, newDisplayID)`, `bulkAssignDevicesToDisplay(clientKeys, newDisplayID)` → `{moved, missing}`, `assignProfileToClient(clientKey, profileName)`. `store.profiles` is a name→profile dict. `store.goTo(tab)` sets `location.hash`.
- Reused modal/action functions:
  - `openPlayNowModal(store, displayID)` + `fireStopNow(store, displayID)` from `js/timeline/modals/play-now.js`.
  - `fireFleetAction(store, which, scope)` from `js/timeline/modals/fleet-confirm.js` (scope = displayID; >3-device confirm built in).
  - `openCalibrationModal(store)` from `js/timeline/modals/calibration.js` (Task C1 extends it to accept an optional pre-selected `displayID`).
  - `openProfileEditor(store)` from `js/timeline/modals/profile-editor.js`.
- SockJS render: `window.sock.send(window.generateMessage('SRV', 'RENDER', { displayID }))` (server `RENDER` handler at `mosaicmesh/websocket/legacy.py:461` renders if the group has a playlist, calibration, and renderable items; otherwise replies with an error status — no client-side guard needed).
- Track header element: `.mm-track-header` with `data-display-id` (`dataset.displayId`).
- Node tests: `tests/unit/js/test_*.js`, `import { test } from 'node:test'; import assert from 'node:assert';`. Run: `node --test tests/unit/js/<file>.js` or `python pytest_runner.py --js`.
- e2e: `tests/e2e/test-*.spec.js`, `export default async function () {...}`, `BASE = process.env.MM_BASE_URL || 'http://localhost:3000'`, `__e2e_`-prefixed fixtures via `page.request`, resilient `waitForFunction`. Run: `node tests/e2e/run.js <substr>`.
- Commit trailer (verbatim): `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## File Structure

**Create:**
- `js/timeline/fleet/fleet-status.js` — pure helpers: `groupStatusLine`, `deviceRowsForGroup`, `calibrationSummary`.
- `js/timeline/fleet/fleet-view.js` — `mmFleetComponent()` (state + getters + thin action methods).
- `tests/unit/js/test_fleet_helpers.js` — node units for the pure helpers.
- `tests/e2e/test-fleet.spec.js` — desktop + mobile e2e.

**Modify:**
- `admin.html` — replace the Fleet placeholder with the master-detail markup; trim the Schedule toolbar; add the Fleet CSS; remove the track-header popover mount.
- `js/timeline/index.js` — register `mmFleet`; drop `attachTrackHeaderPopover`.
- `js/timeline/modals/calibration.js` — accept an optional pre-selected `displayID`.
- `js/timeline/track-header-context-menu.js` — replace the fleet/reload/delete items with a single "Manage in Fleet →".
- `tests/unit/js/test_timeline_smoke.js` — register the fleet modules (and drop the deleted popover entry).
- `js/timeline/README.md` + `CLAUDE.md` — document `js/timeline/fleet/`.

**Delete:**
- `js/timeline/track-header-popover.js` — its device-management role moves into the Fleet Devices card.

---

## Phase A — Pure status helpers

### Task A1: `fleet-status.js` + tests

**Files:**
- Create: `js/timeline/fleet/fleet-status.js`
- Test: `tests/unit/js/test_fleet_helpers.js` (create)

- [ ] **Step 1: Write the failing tests** — create `tests/unit/js/test_fleet_helpers.js`

```js
import { test } from 'node:test';
import assert from 'node:assert';
import { groupStatusLine, deviceRowsForGroup, calibrationSummary } from '../../../js/timeline/fleet/fleet-status.js';

const group = { displayID: 'Lobby', clientCount: 3, onlineCount: 2 };

test('groupStatusLine reads counts + playback + render state', () => {
  const s = groupStatusLine(group,
    { Lobby: { state: 'PLAY', currentPlaylist: 'Menu' } },
    { Lobby: true });
  assert.equal(s.displayID, 'Lobby');
  assert.equal(s.online, 2);
  assert.equal(s.total, 3);
  assert.equal(s.playing, true);
  assert.equal(s.playlistName, 'Menu');
  assert.equal(s.rendering, true);
});

test('groupStatusLine: idle group has no playback, not playing/rendering', () => {
  const s = groupStatusLine(group, {}, {});
  assert.equal(s.playing, false);
  assert.equal(s.playlistName, null);
  assert.equal(s.rendering, false);
});

test('groupStatusLine: STOP/IDLE states are not "playing"', () => {
  assert.equal(groupStatusLine(group, { Lobby: { state: 'STOP' } }, {}).playing, false);
  assert.equal(groupStatusLine(group, { Lobby: { state: 'IDLE' } }, {}).playing, false);
  // PAUSE counts as an active (non-idle) playlist.
  assert.equal(groupStatusLine(group, { Lobby: { state: 'PAUSE', currentPlaylist: 'X' } }, {}).playing, true);
});

test('deviceRowsForGroup filters by displayID and sorts online-first then name', () => {
  const displays = [
    { clientKey: 'a', displayID: 'Lobby', friendlyName: 'Zed', isOnline: false },
    { clientKey: 'b', displayID: 'Lobby', friendlyName: 'Ann', isOnline: true },
    { clientKey: 'c', displayID: 'Cafe',  friendlyName: 'Cy',  isOnline: true },
    { clientKey: 'd', displayID: 'Lobby', friendlyName: 'Bob', isOnline: true },
  ];
  const rows = deviceRowsForGroup({ displayID: 'Lobby' }, displays);
  assert.deepEqual(rows.map(d => d.clientKey), ['b', 'd', 'a']); // online (Ann,Bob) then offline (Zed)
});

test('calibrationSummary counts devices with a measuredPerimeter', () => {
  const rows = [
    { clientKey: 'a', measuredPerimeter: [[0, 0]] },
    { clientKey: 'b', measuredPerimeter: null },
    { clientKey: 'c' },
  ];
  assert.deepEqual(calibrationSummary(rows), { calibratedCount: 1, total: 3 });
  assert.deepEqual(calibrationSummary([]), { calibratedCount: 0, total: 0 });
});
```

- [ ] **Step 2: Run it, verify it fails**

Run: `node --test tests/unit/js/test_fleet_helpers.js`
Expected: FAIL — module `fleet/fleet-status.js` not found.

- [ ] **Step 3: Implement** — create `js/timeline/fleet/fleet-status.js`

```js
/**
 * Pure helpers for the Fleet view (Section 4). No DOM, no fetch —
 * node-importable for tests.
 *
 *   group shape:   { displayID, clientCount, onlineCount, scheduleCount, clients[] }
 *   device shape:  { clientKey, displayID, friendlyName, isOnline, profileName,
 *                    deviceType, measuredPerimeter? }
 *   playback[id]:  { state, currentPlaylist, ... }   state in PLAY|PAUSE|STOP|IDLE|PREPARING
 */

/** Group-level status for the master list + detail header. */
export function groupStatusLine(group, playback, renderInProgress) {
  const displayID = group && group.displayID;
  const online = (group && group.onlineCount != null) ? group.onlineCount : 0;
  const total = (group && group.clientCount != null) ? group.clientCount : 0;
  const pb = (playback && displayID && playback[displayID]) || null;
  // "playing" = an active (non-stopped, non-idle) playlist is mounted.
  const state = pb && pb.state;
  const playing = !!(pb && state && state !== 'STOP' && state !== 'IDLE' && state !== 'NOACTION');
  const playlistName = (playing && pb) ? (pb.currentPlaylist || null) : null;
  const rendering = !!(renderInProgress && displayID && renderInProgress[displayID]);
  return { displayID, online, total, playing, playlistName, rendering };
}

/** The devices in a group, online-first then by friendly name. */
export function deviceRowsForGroup(group, displays) {
  const id = group && group.displayID;
  const rows = (displays || []).filter(d => d.displayID === id);
  rows.sort((a, b) => {
    if (!!a.isOnline !== !!b.isOnline) return a.isOnline ? -1 : 1;
    const an = (a.friendlyName || a.clientKey || '').toLowerCase();
    const bn = (b.friendlyName || b.clientKey || '').toLowerCase();
    return an < bn ? -1 : an > bn ? 1 : 0;
  });
  return rows;
}

/** How many of these devices report a calibration quad. */
export function calibrationSummary(devices) {
  const list = devices || [];
  let calibratedCount = 0;
  for (const d of list) if (d.measuredPerimeter != null) calibratedCount += 1;
  return { calibratedCount, total: list.length };
}
```

- [ ] **Step 4: Run it, verify it passes**

Run: `node --test tests/unit/js/test_fleet_helpers.js`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add js/timeline/fleet/fleet-status.js tests/unit/js/test_fleet_helpers.js
git commit -m "feat(fleet): pure group/device status helpers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase B — mmFleet component + master-detail shell

### Task B1: `fleet-view.js` component + register + admin.html master/detail shell

**Files:**
- Create: `js/timeline/fleet/fleet-view.js`
- Modify: `js/timeline/index.js`
- Modify: `admin.html` (replace the Fleet placeholder at the `data-route="fleet"` section)

This task creates the full component (all action methods — the cards that use them land in C1/D1) plus the master list + detail status-header markup. The action cards (Playback/Calibration/Device-scripts/Devices) are added in C1/D1.

- [ ] **Step 1: Create `js/timeline/fleet/fleet-view.js`**

```js
/**
 * mmFleet — the Fleet destination (Section 4). Master-detail:
 *   - groups list (master) — one row per store.displayGroups entry
 *   - per-group detail — sectioned cards (Playback / Calibration /
 *     Device scripts / Devices), shown as a full-screen sheet on mobile.
 *
 * State + thin method wrappers only; the markup lives in admin.html as
 * Alpine templates so the device <select>/checkbox controls stay reactive.
 * Every action reuses an existing store mutator or modal.
 */
import { groupStatusLine, deviceRowsForGroup, calibrationSummary } from './fleet-status.js';
import { openPlayNowModal, fireStopNow } from '../modals/play-now.js';
import { fireFleetAction } from '../modals/fleet-confirm.js';
import { openCalibrationModal } from '../modals/calibration.js';
import { openProfileEditor } from '../modals/profile-editor.js';

export function mmFleetComponent() {
  return {
    selectedGroupId: null,
    bulkSelection: new Set(),   // clientKeys; reassigned on change for Alpine reactivity
    bulkTarget: '',

    // ---- derived ----
    get groups() { return this.$store.mm.displayGroups || []; },
    get selectedGroup() {
      return this.groups.find(g => g.displayID === this.selectedGroupId) || null;
    },
    get devices() { return deviceRowsForGroup(this.selectedGroup, this.$store.mm.displays); },
    get profileNames() { return Object.keys(this.$store.mm.profiles || {}).sort(); },
    get allSelected() {
      const d = this.devices;
      return d.length > 0 && d.every(x => this.bulkSelection.has(x.clientKey));
    },
    statusFor(group) {
      return groupStatusLine(group, this.$store.mm.playback, this.$store.mm.renderInProgress);
    },
    calibrationFor(group) {
      return calibrationSummary(deviceRowsForGroup(group, this.$store.mm.displays));
    },

    // ---- navigation ----
    selectGroup(id) { this.selectedGroupId = id; this.bulkSelection = new Set(); this.bulkTarget = ''; },
    backToList() { this.selectedGroupId = null; },

    // ---- group-level actions (reuse existing modals/helpers) ----
    playNow() { if (this.selectedGroupId) openPlayNowModal(this.$store.mm, this.selectedGroupId); },
    stopNow() { if (this.selectedGroupId) fireStopNow(this.$store.mm, this.selectedGroupId); },
    renderNow() {
      const id = this.selectedGroupId;
      if (!id) return;
      if (typeof window.sock === 'undefined' || typeof window.generateMessage !== 'function') {
        this.$store.mm.toast('SockJS not available; reload the page.', 'error');
        return;
      }
      try {
        window.sock.send(window.generateMessage('SRV', 'RENDER', { displayID: id }));
        this.$store.mm.toast(`Render requested for "${id}".`, 'info');
      } catch (e) {
        this.$store.mm.toast(`Failed to send render: ${e?.message || e}`, 'error');
      }
    },
    calibrate() { if (this.selectedGroupId) openCalibrationModal(this.$store.mm, this.selectedGroupId); },
    runScript(which) { if (this.selectedGroupId) fireFleetAction(this.$store.mm, which, this.selectedGroupId); },
    openProfiles() { openProfileEditor(this.$store.mm); },

    // ---- device management ----
    setDeviceProfile(clientKey, name) {
      this.$store.mm.assignProfileToClient(clientKey, name).catch(() => {});
    },
    moveDevice(clientKey, displayID) {
      this.$store.mm.assignDeviceToDisplay(clientKey, displayID).catch(() => {});
    },
    toggleBulk(clientKey) {
      const s = new Set(this.bulkSelection);
      if (s.has(clientKey)) s.delete(clientKey); else s.add(clientKey);
      this.bulkSelection = s;
    },
    toggleBulkAll() {
      this.bulkSelection = this.allSelected ? new Set() : new Set(this.devices.map(d => d.clientKey));
    },
    async bulkMove(displayID) {
      if (!displayID || this.bulkSelection.size === 0) return;
      const keys = [...this.bulkSelection];
      try {
        const res = await this.$store.mm.bulkAssignDevicesToDisplay(keys, displayID);
        const moved = (res && res.moved ? res.moved.length : keys.length);
        this.$store.mm.toast(`Moved ${moved} device${moved === 1 ? '' : 's'} to "${displayID}".`, 'info');
      } catch (_) { /* store toasts on failure */ }
      this.bulkSelection = new Set();
      this.bulkTarget = '';
    },

    // ---- group CRUD ----
    newGroup() {
      const raw = window.prompt('New display group name (e.g. Lobby, Tablet):');
      if (raw == null) return;
      const name = raw.trim();
      if (!name) return;
      if (this.groups.some(g => g.displayID === name)) {
        this.$store.mm.toast(`Display group "${name}" already exists.`, 'warn');
        return;
      }
      this.$store.mm.createDisplayGroup(name).catch(() => {});
    },
    async deleteGroup() {
      const id = this.selectedGroupId;
      if (!id) return;
      if (!window.confirm(`Delete display group "${id}"? This cannot be undone.`)) return;
      try { await this.$store.mm.deleteDisplayGroup(id); this.backToList(); }
      catch (_) { /* store toasts 409+refs */ }
    },
  };
}
```

- [ ] **Step 2: Register in `js/timeline/index.js`**

Add the import near the other component imports (after `mmContentComponent`):
```js
import { mmFleetComponent } from './fleet/fleet-view.js';
```
Register it in `bootstrap()` next to the other `Alpine.data(...)` calls:
```js
  // eslint-disable-next-line no-undef
  Alpine.data('mmFleet', mmFleetComponent);
```

- [ ] **Step 3: Replace the Fleet placeholder in `admin.html`**

Find the section (currently):
```html
      <section class="section" data-route="fleet" x-show="$store.mm.activeTab==='fleet'">
        <div class="placeholder-tab">Fleet — coming soon.</div>
      </section>
```
Replace its inner content with the master-detail shell (the action cards land in C1/D1 — for now the detail has the head + status header + an empty `<div class="mm-fleet-cards"></div>`):
```html
      <section class="section" data-route="fleet" x-show="$store.mm.activeTab==='fleet'" x-data="mmFleet">
        <div x-show="!$store.mm.hydrated && !$store.mm.hydrateError" style="color:var(--text-muted)">Loading…</div>
        <div x-show="$store.mm.hydrateError" style="color:var(--err)" x-text="'Failed to load: ' + $store.mm.hydrateError"></div>
        <div x-show="$store.mm.hydrated" class="mm-fleet">
          <aside class="mm-fleet-list" x-show="!$store.mm.isMobile || selectedGroupId===null">
            <div class="mm-fleet-list-head">
              <span class="mm-fleet-list-title">Groups</span>
              <span style="flex:1"></span>
              <button class="btn btn-ghost" @click="openProfiles()" title="Edit device profiles">⚙ Profiles</button>
              <button class="btn btn-primary" @click="newGroup()">+ New group</button>
            </div>
            <ul class="mm-fleet-groups">
              <template x-for="g in groups" :key="g.displayID">
                <li class="mm-fleet-group" :class="{'sel': g.displayID===selectedGroupId}" @click="selectGroup(g.displayID)">
                  <span class="mm-fleet-group-name" x-text="g.displayID"></span>
                  <span class="mm-fleet-group-badges">
                    <span class="mm-fleet-online" x-text="statusFor(g).online + '/' + statusFor(g).total"></span>
                    <template x-if="statusFor(g).playing"><span class="mm-fleet-badge playing" title="Playing">▶</span></template>
                    <template x-if="statusFor(g).rendering"><span class="mm-fleet-badge rendering" title="Rendering">⟳</span></template>
                    <template x-if="calibrationFor(g).total>0 && calibrationFor(g).calibratedCount===calibrationFor(g).total"><span class="mm-fleet-badge calibrated" title="Calibrated">✓</span></template>
                  </span>
                </li>
              </template>
              <template x-if="groups.length===0">
                <li class="mm-fleet-empty">No display groups yet. Create one with “+ New group”.</li>
              </template>
            </ul>
          </aside>
          <section class="mm-fleet-detail" x-show="selectedGroup" :class="{'mm-fleet-sheet': $store.mm.isMobile}">
            <template x-if="selectedGroup">
              <div>
                <div class="mm-fleet-detail-head">
                  <button class="btn btn-ghost mm-fleet-back" x-show="$store.mm.isMobile" @click="backToList()">‹ Fleet</button>
                  <h2 class="mm-fleet-detail-name" x-text="selectedGroup.displayID"></h2>
                  <span style="flex:1"></span>
                  <button class="btn mm-btn-danger" @click="deleteGroup()">Delete group</button>
                </div>
                <div class="mm-fleet-statusline">
                  <span x-text="statusFor(selectedGroup).online + '/' + statusFor(selectedGroup).total + ' online'"></span>
                  <template x-if="statusFor(selectedGroup).playing"><span> · ▶ <span x-text="statusFor(selectedGroup).playlistName || 'playing'"></span></span></template>
                  <template x-if="!statusFor(selectedGroup).playing"><span> · idle</span></template>
                </div>
                <div class="mm-fleet-cards">
                  <!-- C1 adds Playback / Calibration / Device scripts cards here -->
                  <!-- D1 adds the Devices card here -->
                </div>
              </div>
            </template>
          </section>
        </div>
      </section>
```

- [ ] **Step 4: Verify modules load + smoke passes**

Run: `node -e "import('./js/timeline/fleet/fleet-view.js').then(()=>console.log('ok')).catch(e=>{console.error(e.message);process.exit(1)})"`
Expected: `ok`.
Run: `node --test tests/unit/js/test_timeline_smoke.js`
Expected: PASS (unchanged — fleet modules registered in F1).

- [ ] **Step 5: Commit**

```bash
git add js/timeline/fleet/fleet-view.js js/timeline/index.js admin.html
git commit -m "feat(fleet): mmFleet component + master-detail shell

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase C — Action cards (Playback / Calibration / Device scripts)

### Task C1: the three action cards + calibration pre-scope

**Files:**
- Modify: `admin.html` (fill `.mm-fleet-cards` with the three cards)
- Modify: `js/timeline/modals/calibration.js` (accept optional pre-selected `displayID`)

- [ ] **Step 1: Pre-scope the calibration modal** — in `js/timeline/modals/calibration.js`, change the signature and pre-select the group.

Change `export function openCalibrationModal(store) {` to:
```js
export function openCalibrationModal(store, preGroup) {
```
After the group `<select>` is populated (right after the `groups.forEach(...)` block that appends options, before `label1.appendChild(select);`), add:
```js
  // Section 4: when opened from a group's Fleet detail, pre-select that
  // group so the operator doesn't re-pick it. The picker stays editable.
  if (preGroup && groups.includes(preGroup)) {
    select.value = preGroup;
  }
```

- [ ] **Step 2: Add the three cards in `admin.html`** — replace the `<!-- C1 adds ... -->` comment inside `.mm-fleet-cards` with:

```html
                  <div class="mm-fleet-card">
                    <h3 class="mm-fleet-card-title">Playback</h3>
                    <div class="mm-fleet-card-actions">
                      <button class="btn btn-primary" @click="playNow()">▶ Play now</button>
                      <button class="btn" @click="stopNow()">⏹ Stop</button>
                      <button class="btn" @click="renderNow()" title="Pre-bake mosaic segments for this group">⟳ Render now</button>
                    </div>
                  </div>
                  <div class="mm-fleet-card">
                    <h3 class="mm-fleet-card-title">Calibration</h3>
                    <div class="mm-fleet-card-actions">
                      <span class="mm-fleet-calib" x-text="calibrationFor(selectedGroup).calibratedCount + '/' + calibrationFor(selectedGroup).total + ' calibrated'"></span>
                      <button class="btn" @click="calibrate()">🎯 Calibrate…</button>
                    </div>
                  </div>
                  <div class="mm-fleet-card">
                    <h3 class="mm-fleet-card-title">Device scripts</h3>
                    <div class="mm-fleet-card-actions">
                      <button class="btn" @click="runScript('login')">Login</button>
                      <button class="btn" @click="runScript('start')">Start</button>
                      <button class="btn" @click="runScript('stop')">Stop</button>
                      <button class="btn" @click="runScript('reboot')">Reboot</button>
                      <button class="btn" @click="runScript('test')">Test</button>
                    </div>
                  </div>
```

- [ ] **Step 3: Verify**

Run: `node -e "import('./js/timeline/modals/calibration.js').then(m=>console.log(typeof m.openCalibrationModal))"`
Expected: `function`.
Run: `node --test tests/unit/js/test_timeline_smoke.js`
Expected: PASS (calibration.js still imports cleanly).

- [ ] **Step 4: Commit**

```bash
git add admin.html js/timeline/modals/calibration.js
git commit -m "feat(fleet): Playback / Calibration / Device-scripts cards (+ calibrate pre-scope)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase D — Devices card

### Task D1: device rows + profile/move dropdowns + bulk move + delete group

**Files:**
- Modify: `admin.html` (add the Devices card after the Device-scripts card inside `.mm-fleet-cards`)

The component already has `devices`, `profileNames`, `allSelected`, `bulkSelection`, `bulkTarget`, `setDeviceProfile`, `moveDevice`, `toggleBulk`, `toggleBulkAll`, `bulkMove` (from B1). This task only adds markup.

- [ ] **Step 1: Add the Devices card** — append inside `.mm-fleet-cards`, after the Device-scripts card:

```html
                  <div class="mm-fleet-card">
                    <h3 class="mm-fleet-card-title">Devices (<span x-text="devices.length"></span>)</h3>
                    <label class="mm-fleet-selall"><input type="checkbox" :checked="allSelected" @change="toggleBulkAll()"> Select all</label>
                    <ul class="mm-fleet-devices">
                      <template x-for="d in devices" :key="d.clientKey">
                        <li class="mm-fleet-device">
                          <input type="checkbox" :checked="bulkSelection.has(d.clientKey)" @change="toggleBulk(d.clientKey)">
                          <span class="mm-fleet-dev-dot" :class="{online: d.isOnline}" :title="d.isOnline ? 'online' : 'offline'"></span>
                          <span class="mm-fleet-dev-name" x-text="d.friendlyName || d.clientKey"></span>
                          <span class="mm-fleet-dev-type" x-text="d.deviceType || ''"></span>
                          <label class="mm-fleet-dev-field">Profile
                            <select @change="setDeviceProfile(d.clientKey, $event.target.value)">
                              <option value="" :selected="!d.profileName">Auto-match</option>
                              <template x-for="p in profileNames" :key="p"><option :value="p" :selected="d.profileName===p" x-text="p"></option></template>
                            </select>
                          </label>
                          <label class="mm-fleet-dev-field">Group
                            <select @change="moveDevice(d.clientKey, $event.target.value)">
                              <template x-for="g in groups" :key="g.displayID"><option :value="g.displayID" :selected="g.displayID===selectedGroupId" x-text="g.displayID"></option></template>
                            </select>
                          </label>
                        </li>
                      </template>
                      <template x-if="devices.length===0">
                        <li class="mm-fleet-empty">No devices in this group.</li>
                      </template>
                    </ul>
                    <div class="mm-fleet-bulkbar" x-show="bulkSelection.size>0">
                      <span x-text="bulkSelection.size + ' selected'"></span>
                      <select x-model="bulkTarget">
                        <option value="">Move selected to…</option>
                        <template x-for="g in groups" :key="g.displayID">
                          <template x-if="g.displayID!==selectedGroupId"><option :value="g.displayID" x-text="g.displayID"></option></template>
                        </template>
                      </select>
                      <button class="btn btn-primary" :disabled="!bulkTarget" @click="bulkMove(bulkTarget)">Apply</button>
                    </div>
                  </div>
```

- [ ] **Step 2: Verify**

Run: `node --test tests/unit/js/test_timeline_smoke.js` → PASS.
Run: `node --test tests/unit/js/test_fleet_helpers.js` → 5 pass.

- [ ] **Step 3: Commit**

```bash
git add admin.html
git commit -m "feat(fleet): Devices card — profile/move dropdowns + bulk move

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase E — Schedule cleanup (relocation)

### Task E1: trim Schedule toolbar, slim track-header menu, delete popover

**Files:**
- Modify: `admin.html` (Schedule desktop toolbar)
- Modify: `js/timeline/track-header-context-menu.js`
- Modify: `js/timeline/index.js` (drop the popover attach)
- Delete: `js/timeline/track-header-popover.js`

- [ ] **Step 1: Trim the Schedule desktop toolbar in `admin.html`** — read the `<div x-data="mmToolbar" class="mm-toolbar">` block in the Schedule section first. REMOVE these elements (leave Day/Week/Month, ◀/date/▶, Today, the `+ Schedule` button, and the `viewMode !== 'day'` display picker):
  - the `<span class="size">Fleet:</span>` label,
  - the `<select class="input mm-fleet-scope" ...>` and its options,
  - the five fleet-action buttons (`@click="fleetAction('login')"` … `'test'`),
  - the `⚙ Profiles` button (`@click="openProfileEditor()"`),
  - the `🎯 Calibrate` button (`@click="openCalibration()"`),
  - the `+ Group` button (`@click="addDisplayGroup()"`),
  - and the now-orphaned `<span style="flex:1"></span>` spacer that preceded the Fleet group (keep layout tidy).

Leave the `mmToolbar` component file (`js/timeline/toolbar.js`) as-is — its now-unused methods (`fleetAction`, `openProfileEditor`, `openCalibration`, `addDisplayGroup`, `fleetScope`) are harmless and may still be referenced by the mobile Schedule date-nav `mmToolbar` instance; do NOT delete them in this task.

- [ ] **Step 2: Slim the track-header context menu** — the Schedule timeline's right-click menu keeps a SINGLE "Manage in Fleet →" item (Play/Stop ad-hoc playback now lives in the Fleet detail's Playback card + the Now tab; device/group management lives in Fleet). In `js/timeline/track-header-context-menu.js`:

Remove BOTH imports at the top:
```js
import { fireFleetAction } from './modals/fleet-confirm.js';
import { openPlayNowModal, fireStopNow } from './modals/play-now.js';
```
(delete both lines — neither is used after this change). Replace the entire `items` array with:
```js
    const items = [
      {
        label: 'Manage in Fleet →',
        action: () => {
          // Section 4: device/group/playback management lives in the
          // Fleet destination now. Route there and select this group.
          store.goTo('fleet');
          const fleet = document.querySelector('[x-data="mmFleet"]');
          if (fleet && fleet._x_dataStack) {
            try { window.Alpine.$data(fleet).selectGroup(displayID); } catch (_) { /* tolerate */ }
          }
        },
      },
    ];
```
The separator-handling loop below the array stays unchanged (it tolerates an array with no separators).

- [ ] **Step 2b: Confirm no stale imports** — `grep -nE "fireFleetAction|openPlayNowModal|fireStopNow" js/timeline/track-header-context-menu.js` → expect NO results (both imports + all uses removed).

- [ ] **Step 3: Remove the popover attach in `js/timeline/index.js`** — delete the import line `import { attachTrackHeaderPopover } from './track-header-popover.js';` and the call `attachTrackHeaderPopover(store);` from `bootstrap()`.

- [ ] **Step 4: Delete the popover file**
```bash
git rm js/timeline/track-header-popover.js
```

- [ ] **Step 5: Verify nothing else imports the deleted file + modules load**

Run: `grep -rn "track-header-popover" js/ tests/ admin.html` → expect NO results (all references removed).
Run: `node -e "import('./js/timeline/index.js').catch(e=>{ if(String(e).includes('track-header-popover')){console.error('STALE IMPORT');process.exit(1)} console.log('ok (index imports a browser global; that is fine)'); })"` — this will likely throw on `Alpine`/`window` (browser globals) which is EXPECTED; the ONLY failure that matters is a "Cannot find module track-header-popover" — confirm that specific error does NOT appear. Safer check: `node --check js/timeline/index.js` (exit 0) + `node --check js/timeline/track-header-context-menu.js` (exit 0).
Run: `node --test tests/unit/js/test_timeline_smoke.js` — see Task F1 note: the smoke still lists `track-header-popover.js` until F1 removes it, so this will FAIL here. That is expected and fixed in F1. (If you prefer green-at-every-commit, do F1 Step "remove the popover entry" now and fold it into this commit.)

**To keep every commit green:** also remove `'js/timeline/track-header-popover.js'` from the `MODULES` array in `tests/unit/js/test_timeline_smoke.js` in THIS task, then `node --test tests/unit/js/test_timeline_smoke.js` passes.

- [ ] **Step 6: Commit**

```bash
git add admin.html js/timeline/track-header-context-menu.js js/timeline/index.js tests/unit/js/test_timeline_smoke.js
git rm js/timeline/track-header-popover.js
git commit -m "refactor(fleet): relocate device mgmt out of Schedule; deep-link to Fleet

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase F — Smoke registration, e2e, docs

### Task F1: register fleet modules in the load smoke

**Files:**
- Modify: `tests/unit/js/test_timeline_smoke.js`

- [ ] **Step 1: Update the `MODULES` array** — add the two fleet modules and (if not already removed in E1) remove the deleted popover entry:

Add:
```js
  'js/timeline/fleet/fleet-status.js',
  'js/timeline/fleet/fleet-view.js',
```
Ensure `'js/timeline/track-header-popover.js'` is NOT present (removed in E1).

- [ ] **Step 2: Run smoke + full JS suite**

Run: `node --test tests/unit/js/test_timeline_smoke.js` → PASS (fleet modules load).
Run: `python pytest_runner.py --js` → all pass.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/js/test_timeline_smoke.js
git commit -m "test(fleet): register fleet/ modules in the load smoke

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task F2: Fleet e2e (desktop + mobile)

**Files:**
- Create: `tests/e2e/test-fleet.spec.js`

- [ ] **Step 1: Write the spec** — `tests/e2e/test-fleet.spec.js`

```js
/**
 * Section 4 — the Fleet destination.
 *
 * Drives the real admin page:
 *   1. Desktop: the groups list renders one row per display group; selecting
 *      a group shows the detail cards (Playback / Calibration / Device scripts
 *      / Devices).
 *   2. Create a __e2e_ group via "+ New group" -> it appears -> Delete group
 *      -> gone (REST round-trip).
 *   3. Mobile: at phone width the list shows; selecting a group opens the
 *      detail sheet; "‹ Fleet" returns to the list.
 *
 * Owns its own state: a uniquely-named __e2e_fleet group, removed in cleanup.
 */
import { chromium } from 'playwright';
import assert from 'node:assert';

const BASE = process.env.MM_BASE_URL || 'http://localhost:3000';
const ADMIN = () => BASE + '/admin.html?nocache=' + Date.now() + '#fleet';
const GROUP = '__e2e_fleet';

async function waitHydrated(page) {
  await page.waitForFunction(
    () => window.Alpine && Alpine.store('mm') && Alpine.store('mm').hydrated === true,
    null, { timeout: 15_000 });
  await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
}
async function settle(page) {
  await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
}
async function listGroups(page) {
  const r = await page.request.get(BASE + '/api/displays');
  const j = await r.json();
  return (j.displays || []).map(g => g.displayID);
}
async function delGroup(page, id) {
  await page.request.delete(BASE + '/api/displays/' + encodeURIComponent(id));
}

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('dialog', (d) => {
    // "+ New group" uses prompt(); Delete uses confirm(). Accept with our name.
    if (d.type() === 'prompt') d.accept(GROUP).catch(() => {});
    else d.accept().catch(() => {});
  });
  try {
    // Up-front cleanup of any orphan.
    await page.goto(BASE + '/admin.html');
    await delGroup(page, GROUP);

    // ---- 1. Desktop: groups list renders ----
    await page.goto(ADMIN());
    await waitHydrated(page);
    await page.waitForFunction(() => Alpine.store('mm').activeTab === 'fleet', null, { timeout: 5_000 });
    await page.waitForFunction(
      () => document.querySelectorAll('[data-route="fleet"] .mm-fleet-group').length > 0,
      null, { timeout: 5_000 });
    const groupRowCount = await page.evaluate(() =>
      document.querySelectorAll('[data-route="fleet"] .mm-fleet-group').length);
    const restGroups = await listGroups(page);
    assert.equal(groupRowCount, restGroups.length,
      `fleet list should have one row per group (${groupRowCount} vs REST ${restGroups.length})`);

    // Select the first group -> detail cards appear.
    await page.evaluate(() => document.querySelector('[data-route="fleet"] .mm-fleet-group').click());
    await page.waitForFunction(
      () => document.querySelector('[data-route="fleet"] .mm-fleet-card') != null, null, { timeout: 5_000 });
    const cardTitles = await page.evaluate(() =>
      Array.from(document.querySelectorAll('[data-route="fleet"] .mm-fleet-card-title')).map(h => h.textContent.replace(/\s*\(.*\)/, '').trim()));
    for (const t of ['Playback', 'Calibration', 'Device scripts', 'Devices']) {
      assert.ok(cardTitles.includes(t), `expected a "${t}" card, got ${JSON.stringify(cardTitles)}`);
    }

    // ---- 2. Create a group via the UI -> appears -> delete -> gone ----
    await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('[data-route="fleet"] .mm-fleet-list-head button'))
        .find(b => b.textContent.includes('New group'));
      if (!btn) throw new Error('no + New group button');
      btn.click();   // prompt() auto-accepted with GROUP by the dialog handler
    });
    // Wait for REST to reflect the create (optimistic + POST round-trip).
    await page.waitForFunction(async () => true, null, { timeout: 100 });
    let created = false;
    for (let i = 0; i < 20 && !created; i++) {
      created = (await listGroups(page)).includes(GROUP);
      if (!created) await settle(page);
    }
    assert.ok(created, `group ${GROUP} should exist after + New group`);

    // Re-hydrate so the new row is in the list, select it, delete it.
    await page.goto(ADMIN());
    await waitHydrated(page);
    await page.waitForFunction((g) => Array.from(document.querySelectorAll('[data-route="fleet"] .mm-fleet-group-name')).some(s => s.textContent.trim() === g), GROUP, { timeout: 5_000 });
    await page.evaluate((g) => {
      const row = Array.from(document.querySelectorAll('[data-route="fleet"] .mm-fleet-group'))
        .find(li => li.querySelector('.mm-fleet-group-name')?.textContent.trim() === g);
      row.click();
    }, GROUP);
    await page.waitForFunction(
      () => document.querySelector('[data-route="fleet"] .mm-btn-danger') != null, null, { timeout: 5_000 });
    await page.evaluate(() => document.querySelector('[data-route="fleet"] .mm-btn-danger').click()); // confirm() auto-accepted
    let gone = false;
    for (let i = 0; i < 20 && !gone; i++) {
      gone = !(await listGroups(page)).includes(GROUP);
      if (!gone) await settle(page);
    }
    assert.ok(gone, `group ${GROUP} should be deleted after Delete group`);

    // ---- 3. Mobile: list -> detail sheet -> back ----
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(ADMIN());
    await waitHydrated(page);
    await page.waitForFunction(() => Alpine.store('mm').isMobile === true, null, { timeout: 5_000 });
    await page.waitForFunction(
      () => document.querySelector('[data-route="fleet"] .mm-fleet-group') != null, null, { timeout: 5_000 });
    await page.evaluate(() => document.querySelector('[data-route="fleet"] .mm-fleet-group').click());
    // The detail sheet shows + a back control.
    await page.waitForFunction(
      () => { const back = document.querySelector('[data-route="fleet"] .mm-fleet-back'); return back && back.offsetParent !== null; },
      null, { timeout: 5_000 });
    await page.evaluate(() => document.querySelector('[data-route="fleet"] .mm-fleet-back').click());
    // Back to the list (detail hidden).
    await page.waitForFunction(
      () => Alpine.$data(document.querySelector('[x-data="mmFleet"]')).selectedGroupId === null,
      null, { timeout: 5_000 });

    return 'pass';
  } finally {
    try { await delGroup(page, GROUP); } catch (_) {}
    await browser.close();
  }
}
```

- [ ] **Step 2: Run the spec** (dev server up on `MM_BASE_URL`)

Run: `node tests/e2e/run.js fleet`
Expected: `pass`. Debug spec-side selector/timing issues if any (do NOT change product code unless you find a genuine product bug — report it as DONE_WITH_CONCERNS).

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test-fleet.spec.js
git commit -m "test(fleet): desktop + mobile e2e (list, detail cards, group CRUD)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task F3: CSS + docs

**Files:**
- Modify: `admin.html` (Fleet CSS in the `<style>` block)
- Modify: `js/timeline/README.md`, `CLAUDE.md`

- [ ] **Step 1: Add the Fleet CSS** — insert before `</style>` in `admin.html` (reuse the existing design tokens `--border`, `--bg-elev`, `--text-muted`, `--accent`, `--ok`, `--err`):

```css
/* ---- Section 4: Fleet ---- */
.mm-fleet { display:flex; gap:14px; align-items:flex-start; }
.mm-fleet-list { flex:0 0 38%; max-width:380px; }
.mm-fleet-list-head { display:flex; align-items:center; gap:6px; padding:4px 0 8px; }
.mm-fleet-list-title { font-weight:700; }
.mm-fleet-groups { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:4px; }
.mm-fleet-group { display:flex; align-items:center; gap:8px; padding:9px 10px; border:1px solid var(--border); border-radius:8px; background:var(--bg-elev); cursor:pointer; }
.mm-fleet-group.sel { outline:2px solid var(--accent); }
.mm-fleet-group-name { flex:1; font-weight:600; }
.mm-fleet-group-badges { display:flex; align-items:center; gap:6px; font-size:12px; }
.mm-fleet-online { color:var(--text-muted); font-variant-numeric:tabular-nums; }
.mm-fleet-badge.playing { color:var(--ok); }
.mm-fleet-badge.calibrated { color:var(--ok); }
.mm-fleet-empty { color:var(--text-muted); font-style:italic; padding:10px; }
.mm-fleet-detail { flex:1; min-width:0; }
.mm-fleet-detail-head { display:flex; align-items:center; gap:8px; margin-bottom:6px; }
.mm-fleet-detail-name { margin:0; font-size:18px; }
.mm-fleet-statusline { color:var(--text-muted); font-size:13px; margin-bottom:12px; }
.mm-fleet-cards { display:flex; flex-direction:column; gap:12px; }
.mm-fleet-card { border:1px solid var(--border); border-radius:8px; background:var(--bg-elev); padding:10px 12px; }
.mm-fleet-card-title { margin:0 0 8px; font-size:13px; font-weight:700; text-transform:uppercase; letter-spacing:.04em; color:var(--text-muted); }
.mm-fleet-card-actions { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.mm-fleet-calib { color:var(--text-muted); font-size:13px; }
.mm-fleet-selall { display:inline-flex; align-items:center; gap:6px; font-size:13px; margin-bottom:8px; }
.mm-fleet-devices { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:4px; }
.mm-fleet-device { display:flex; align-items:center; gap:8px; flex-wrap:wrap; padding:6px 4px; border-bottom:1px solid var(--border); font-size:13px; }
.mm-fleet-dev-dot { width:8px; height:8px; border-radius:50%; background:#888; flex:0 0 auto; }
.mm-fleet-dev-dot.online { background:var(--ok); }
.mm-fleet-dev-name { font-weight:600; }
.mm-fleet-dev-type { color:var(--text-muted); }
.mm-fleet-dev-field { display:inline-flex; align-items:center; gap:4px; font-size:12px; color:var(--text-muted); }
.mm-fleet-bulkbar { display:flex; align-items:center; gap:8px; margin-top:8px; padding-top:8px; border-top:1px solid var(--border); }
/* Mobile: list is full-width; detail becomes a full-screen sheet. */
@media (max-width:759px){
  .mm-fleet { flex-direction:column; }
  .mm-fleet-list { flex:1 1 auto; max-width:none; }
  .mm-fleet-detail.mm-fleet-sheet { position:fixed; inset:0; z-index:60; background:var(--bg); overflow:auto; padding:14px; }
}
```

- [ ] **Step 2: Update `js/timeline/README.md`** — add to the Module map:
```markdown
- **`fleet/`** — the Fleet destination (Section 4). Pure status helpers
  (`fleet-status.js`) + the `mmFleet` master-detail component
  (`fleet-view.js`). Groups list → per-group detail cards (Playback /
  Calibration / Device scripts / Devices). Reuses the existing modals
  (play-now, fleet-confirm, calibration, profile-editor) + store CRUD
  mutators; device/group management was relocated here out of the
  Schedule track-headers/toolbar.
```

- [ ] **Step 3: Update `CLAUDE.md`** — add a bullet under the `js/timeline/` layout notes describing `js/timeline/fleet/` + the relocation (one or two sentences, mirroring the README wording).

- [ ] **Step 4: Commit**

```bash
git add admin.html js/timeline/README.md CLAUDE.md
git commit -m "feat(fleet): styling + docs for the Fleet destination

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification (after all tasks)

- [ ] `python pytest_runner.py --js` → all pass (incl. `test_fleet_helpers.js` + smoke).
- [ ] `node tests/e2e/run.js fleet` → `pass`; `node tests/e2e/run.js schedule-mobile` and `node tests/e2e/run.js content-tab` → still `pass` (no sibling regression — especially confirm the Schedule track-header changes didn't break the Schedule e2e).
- [ ] **Manual desktop:** Fleet tab shows the groups list + detail cards; Play/Stop/Render/Calibrate/scripts work; device Profile/Move dropdowns + bulk move work; create/delete group work; ⚙ Profiles opens the editor. The Schedule toolbar no longer has the fleet controls; right-click a Schedule track header → "Manage in Fleet →" jumps to Fleet with that group selected.
- [ ] **Manual mobile (<760px):** Fleet shows the list; tap a group → full-screen detail sheet; "‹ Fleet" returns.
- [ ] Dispatch a final code-reviewer over the branch, then **superpowers:finishing-a-development-branch**.

## Notes for the implementer

- **Do not touch the iPad-1 display clients** or **the server** — every endpoint + SockJS message already exists.
- **Reactivity with `Set`:** `bulkSelection` is reassigned (`new Set(...)`) on every change so Alpine tracks it — never mutate it in place.
- **`store.profiles` is a dict** (name→profile); **`store.displays`/`displayGroups` are arrays**. Match these shapes.
- The `mmToolbar` component file is intentionally left with now-unused fleet methods (the mobile Schedule date-nav reuses `mmToolbar`); removing those is out of scope for this section.
