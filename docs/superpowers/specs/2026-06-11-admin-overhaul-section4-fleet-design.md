# Admin Overhaul — Section 4 (Fleet)

**Date:** 2026-06-11
**Status:** Draft — approved in conversation
**Parent:** [Admin UI Overhaul IA](./2026-06-09-admin-ui-overhaul-design.md) → "Destination: Fleet". The **fourth and final** build section (Shell+Now ✓, Content ✓, Schedule ✓, **Fleet**).

**Goal:** Give the Fleet destination a real home. Today it's a placeholder; the functionality it needs already exists but is **scattered onto the Schedule screen** (toolbar fleet buttons + Profiles + Calibrate + "+ Group", plus the track-header device-management popover and right-click menu). Section 4 builds a proper **master-detail Fleet view** (groups list → per-group detail), surfaces live status and a "Render now" action, and **relocates** all device/group management here — decluttering Schedule.

## Why

`admin.html`'s `data-route="fleet"` section is literally `<div class="placeholder-tab">Fleet — coming soon.</div>`. Meanwhile, fleet/device management was crammed into Schedule during earlier PRs:

- The **Schedule toolbar** carries a fleet-scope `<select>` + 5 fleet-script buttons (Login/Start/Stop/Reboot/Test) + `⚙ Profiles` + `🎯 Calibrate` + `+ Group`.
- The **track-header popover** (left-click a Schedule track) holds per-device Profile + Group dropdowns, select-all, and a bulk-move bar.
- The **track-header context menu** (right-click a Schedule track) holds Play-now / Stop / fleet actions / Reload / Delete-group.

This is the IA's "everything smashed onto one screen" problem. Fleet is fundamentally *where the screens physically are and what they're doing* — device/group management, calibration, lifecycle scripts, and render belong here, not on the scheduling screen.

## Scope (from the brainstorm)

**In:** master-detail groups+devices; live status; all per-group actions (Play now / Stop / Render now / Calibrate / the 5 fleet scripts); device move (single + bulk) + per-device profile assignment; group create + delete; the global Profiles editor. Everything is **relocated out of** the Schedule toolbar + track-headers.

**Deferred (not now):** the discovery power-user actions (force-push segments, clear cache, set cache-mode, swap orientation); group **rename** (no server API exists — `/api/displays` is POST/DELETE only).

**Out:** `discovery.html` (a separate legacy console) is untouched. No server/domain-model changes — every endpoint and SockJS message already exists.

## What already exists (reused, not rebuilt)

All confirmed present in the codebase:

- **Store methods** (`js/timeline/store.js`): `createDisplayGroup(displayID)`, `deleteDisplayGroup(displayID)` (409+refs aware), `assignDeviceToDisplay(clientKey, newDisplayID)`, `bulkAssignDevicesToDisplay(clientKeys, newDisplayID)` → `{moved, missing}`, `assignProfileToClient(clientKey, profileName)`. All optimistic + rollback.
- **Store state**: `displays[]` (client: `clientKey, displayID, friendlyName, isOnline, profileName, deviceType, ...`), `displayGroups[]` (`{displayID, clients[], clientCount, onlineCount, scheduleCount}`), `playback{}` (per-displayID `{state, currentPlaylist, startedEpoch, renderStatus}`), `renderInProgress{}`.
- **Modals**: `openCalibrationModal(store)` (`js/timeline/modals/calibration.js` — ArUco generate → `/upload/calibrate`), `openProfileEditor(store)` (`js/timeline/modals/profile-editor.js` — 3-pane CRUD), `openPlayNowModal(store, displayID)` + `fireStopNow(store, displayID)` (`js/timeline/track-header-context-menu.js`), `fireFleetAction(store, which, scope)` (`js/timeline/modals/fleet-confirm.js` — scope-aware, >3-device confirm).
- **api.js**: `listDisplays`, `createDisplay`, `deleteDisplay`, `listDevices`, `assignDeviceToDisplay`, `bulkAssignDevicesToDisplay`, `listProfiles` + profile CRUD, `assignProfile`, `getPlayback`.
- **SockJS RENDER** path: `render_group_async(displayID)` is triggered by a `RENDER {displayID}` message (handled in `mosaicmesh/websocket/legacy.py`); `renderInProgress[displayID]` reflects progress via existing status broadcasts. Render-now is the one **new** UI affordance; the server side already exists.

## Architecture

### Master-detail, responsive

A new `mmFleet` Alpine component switches layout on the existing `store.isMobile` flag (matchMedia 760px, from Section 3):

- **Desktop (≥760px):** two-pane — groups list (~38% width) + the selected group's detail.
- **Mobile (<760px):** the groups list full-width; selecting a group opens its detail as a **full-screen sheet** (reusing Section 1's `modal-shell.js` sheet) with a "‹ Fleet" back control. `selectedGroupId === null` shows the list; non-null shows the detail.

### New modules — `js/timeline/fleet/`

- **`fleet-status.js`** — pure, node-testable helpers (no DOM/fetch):
  - `groupStatusLine(group, playback, renderInProgress)` → `{ online, total, playing, playlistName, calibrated, rendering }`. `online`/`total` from the group's counts (fallback to counting `displays` by displayID); `playing`/`playlistName` from `playback[displayID]`; `rendering` from `renderInProgress[displayID]`; `calibrated` true when every online device in the group has a `measuredPerimeter`/calibration quad (best-effort; documented).
  - `deviceRowsForGroup(group, displays)` → the array of client objects whose `displayID` matches the group, sorted (online first, then name).
- **`fleet-view.js`** — `mmFleetComponent()`: the Alpine component. State: `selectedGroupId` (displayID|null), `bulkSelection` (Set of clientKeys). Getters: `groups` (from `store.displayGroups`), `selectedGroup`, `devices` (`deviceRowsForGroup`), `status` (`groupStatusLine`). Methods (thin wrappers over existing store/modals): `selectGroup(id)`, `backToList()`, `playNow()`, `stopNow()`, `renderNow()`, `calibrate()`, `runScript(which)`, `setDeviceProfile(clientKey, name)`, `moveDevice(clientKey, displayID)`, `toggleBulk(clientKey)`, `toggleBulkAll()`, `bulkMove(displayID)`, `newGroup()`, `deleteGroup()`, `openProfiles()`.

The component holds state + methods; the **markup lives in `admin.html`** as Alpine templates (`x-for`, `@click`, `x-model`) so the per-device `<select>` dropdowns and checkboxes stay reactive (not string-rendered via `x-html`).

### The Fleet view

**Master — groups list** (`x-for` over `mmFleet.groups`): each row shows group name, `online/total`, a ▶ playing badge (+ playlist name), a ✓ calibrated badge, a ⟳ rendering badge. A **"+ New group"** button in the list header (`store.createDisplayGroup` via a `prompt`, matching the current toolbar behavior). Selecting a row sets `selectedGroupId`.

**Detail — sectioned cards** for the selected group:
1. **Status header** — name · online/total · what's playing · calibration state.
2. **Playback** — `▶ Play now` (`openPlayNowModal(store, id)`), `⏹ Stop` (`fireStopNow(store, id)`), `⟳ Render now` (new helper: `window.sock.send(window.generateMessage('SRV', 'RENDER', { displayID: id }))`).
3. **Calibration** — calibrated/not + `🎯 Calibrate…` → `openCalibrationModal(store, id)` (small change: accept an optional `displayID` to pre-select the group and skip the modal's group-picker step).
4. **Device scripts** — `Login / Start / Stop / Reboot / Test`, each `fireFleetAction(store, which, id)` (the existing >3-device confirm applies).
5. **Devices** — list of `mmFleet.devices`: each row = name · type · online dot · **Profile ▾** (`assignProfileToClient`; first option = "Auto-match" sentinel = empty string) · **Move ▾** (`assignDeviceToDisplay`; options from `store.displayGroups`). A select-all + per-row checkbox drives `bulkSelection`; a **bulk-move bar** (target `<select>` + Apply) appears when `bulkSelection.size > 0` and calls `bulkAssignDevicesToDisplay`. A **Delete group** danger button sits at the bottom (`store.deleteDisplayGroup`; the store surfaces the server's 409+refs error via toast).

The global **Profiles** editor opens from a button in the Fleet header (`openProfileEditor(store)`) — profiles are fleet-wide, not per-group.

### Schedule cleanup (the relocation)

- **Schedule toolbar** (`admin.html`): remove the fleet-scope `<select>`, the 5 fleet-script buttons, `⚙ Profiles`, `🎯 Calibrate`, and `+ Group`. (Keep Day/Week/Month, date nav, Today, the Week/Month display picker, and the Section-3 `+ Schedule` button.)
- **Track-header popover** (`js/timeline/track-header-popover.js`) and the fleet/delete items in the **track-header context menu** (`js/timeline/track-header-context-menu.js`): drop the device-management UI. The context menu keeps a single **"Manage in Fleet →"** item that routes to Fleet (`store.goTo('fleet')`) and selects that group. The popover's left-click handler is removed (or repointed to the same deep-link).
- `js/timeline/index.js`: stop attaching the removed popover; keep the slimmed context menu.

These deletions are scoped to the Schedule track-header wiring; the underlying store methods + modals stay (Fleet now calls them). The Section-3 mobile Schedule is untouched.

## Data flow

Unchanged from the established pattern. Hydrate via the existing REST GETs (already in `store.hydrate`); mutate optimistically with `If-Match` + rollback; live status (online counts, playback, render progress) already flows into the store via SockJS and reactively updates the Fleet view.

## Testing

- **node `--test`** (`tests/unit/js/`): `groupStatusLine` (online/total incl. the displays fallback; playing/playlistName from playback; rendering flag; calibrated all-vs-some) and `deviceRowsForGroup` (membership filter + online-first sort). Add the two fleet modules to `test_timeline_smoke.js`.
- **Playwright e2e** (`tests/e2e/`): a `test-fleet.spec.js` — the groups list renders one row per group with status; selecting a group shows the detail cards + device rows; **create a `__e2e_` group → it appears → delete it → gone** (REST round-trip); a device **Move ▾** round-trips through `/api/discovery/configure` (verify via REST the client's displayID changed, then move it back). Run once at desktop viewport and once at phone viewport (list → detail sheet → back). Self-cleaning fixtures per the harness convention.
- **iPad-1:** not applicable — admin-only.

## File structure

- **Create:** `js/timeline/fleet/fleet-status.js`, `js/timeline/fleet/fleet-view.js`; `tests/unit/js/test_fleet_helpers.js`; `tests/e2e/test-fleet.spec.js`.
- **Modify:** `admin.html` (Fleet section markup replacing the placeholder + Schedule-toolbar trims + remove the popover mount), `js/timeline/index.js` (register `mmFleet`; drop the track-header popover attach; slim the context-menu wiring), `js/timeline/modals/calibration.js` (accept an optional pre-selected `displayID`), `js/timeline/track-header-context-menu.js` (replace fleet/delete items with the "Manage in Fleet →" deep-link), `js/timeline/timeline/track-header.js` (drop the left-click popover trigger if present), `tests/unit/js/test_timeline_smoke.js` (register fleet modules), `js/timeline/README.md` + `CLAUDE.md` (document `js/timeline/fleet/`).
- **Delete:** `js/timeline/track-header-popover.js` (its device-management role moves into the Fleet Devices card; remove the file + its import/attach). *(If any of its helpers are genuinely shared, lift them into `fleet-view.js`; otherwise delete.)*

## Non-goals

- **No server/domain change.** Every endpoint + SockJS message (RENDER, RUN_SCRIPT, GENERATEARUCO, `/api/displays`, `/api/discovery/configure`, `/api/profiles`, `/upload/calibrate`) already exists.
- **No `discovery.html` change.**
- **No group rename, no cache power-tools** (deferred).
- **No iPad-1 client change.**

## Decision log

- **Full consolidation in this section.** Fleet absorbs all device/group/calibration/profile/script/render management; Schedule sheds it. Chosen over a core-first split — the relocation is the whole point and half-relocating leaves Schedule cluttered.
- **Master-detail, responsive via `store.isMobile`.** Matches the IA and reuses Section 1's sheet + Section 3's breakpoint. Mobile is list → full-screen detail sheet.
- **Sectioned detail (cards), not a dense action bar.** Status + Playback + Calibration + Device-scripts + Devices cards stack cleanly on a phone and keep rare actions out of the common Play/Stop path.
- **Reuse existing store methods + modals verbatim.** Fleet is a new *presentation/relocation* over machinery that already works (calibration, profiles, fleet-confirm, play-now, the CRUD mutators). Only "Render now" is a new (one-line) affordance over the existing server RENDER path.
- **Markup as Alpine templates, not `x-html` strings.** The device rows carry interactive `<select>`/checkbox controls; Alpine `x-for`/`x-model` keeps them reactive (the Section-3 string-render + delegation pattern fits read-mostly views, not these forms).
- **Group rename deferred.** `/api/displays` has no PUT; rename would be delete+recreate+reassign — out of scope.
- **Schedule keeps only a "Manage in Fleet →" deep-link.** Clean separation; the timeline stops being a device-management surface.
