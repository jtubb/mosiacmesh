# Admin Overhaul — Section 1: App Shell + Now — Design

**Date:** 2026-06-09
**Status:** Draft — pending review
**Parent:** [Admin UI Overhaul — Information Architecture Design](./2026-06-09-admin-ui-overhaul-design.md). This is the first of four sequenced per-section build specs. It produces a working, responsive admin shell with a live Now landing; the other three destinations are placeholders until their own specs.

**Goal:** Replace the single crammed admin screen with a responsive four-destination shell (Now · Content · Schedule · Fleet), build a live "what's playing where" Now landing backed by a new read-only playback-state surface, and remove the dead legacy jQuery — without redesigning the other three sections yet.

**Architecture:** A small `js/timeline/shell/` set of ES-module components owns responsive navigation (bottom tab-bar ⇄ desktop sidebar), hash-based routing into Alpine store state, and a modal-or-sheet system. The Now view derives per-group cards from store data plus a new `/api/playback` surface (endpoint + `PLAYBACK_CHANGED` SockJS broadcast) that exposes the server's real playback state. `admin.html` is restructured to the new shell and stripped of dead jQuery; `window.sock` + `generateMessage()` are kept.

**Tech Stack:** Alpine 3.x + native ES modules (no build step), aiohttp + SockJS server, `node --test` JS units, pytest, Playwright e2e — all existing.

---

## Context

The admin UI grew into one timeline-centric screen with a dead jQuery admin bolted alongside; it is unintuitive and unusable on mobile (see parent IA doc). Section 1 lays the foundation every later section plugs into: the responsive shell and its navigation, the modal/sheet system, the consolidated design tokens, and the Now landing — the most-frequent "what's playing + play something" need.

Grounded in the current code:
- Routing today is hash + jQuery `.active`-toggling over `.section[data-route]` with a `Timeline | Console` sidebar (`admin.html`'s `adminRoute()`).
- Alpine boots from `js/timeline/index.js` on `alpine:init`, creating `Alpine.store('mm', makeStore())` and registering `mmTimeline/mmToolbar/mmMediaBin/mmPlaylistBin/mmToast`. Script order: jQuery/SockJS/GoTime/mosiacmesh.js, then `index.js`, then Alpine CDN (index.js must precede Alpine).
- `window.sock` + `generateMessage()` (from `js/mosiacmesh.js`) are the only path to displays — used by `play-now.js`, `fleet-confirm.js`, `calibration.js`, `track-header-context-menu.js`. **Must be kept.**
- Theming is `data-theme="dark|light"` over ~30 CSS custom properties — a solid base to consolidate.
- **There is no current-playback signal exposed to the admin.** The store knows schedules, online counts, and `RENDER_IN_PROGRESS`; it cannot answer "what is playing right now" — especially ad-hoc Play-Now. `Display` has `action` (PlayState NOACTION/STOP/PLAY/PAUSE/PREPARING), `playStartEpoch`, `renderStatus`, but **no current-playlist-name field**.

## Goals

- A responsive four-destination shell: **Now · Content · Schedule · Fleet**, bottom tab-bar on mobile (≤760px) / left sidebar on desktop, one markup CSS-swapped.
- Hash routing (`#now`/`#content`/`#schedule`/`#fleet`, default `#now`) driving Alpine store state — bookmarkable, back-button friendly.
- A modal-or-sheet system: the existing `openModal()` renders a centered modal on desktop / full-screen bottom sheet on mobile, with no change to caller code.
- A live **Now** landing: per-group cards (current playlist/animation, screen count, online count, render/idle state), a fleet-health summary, and a Play-now entry (reusing `play-now.js`).
- A new read-only **playback-state surface** so Now reflects reality (scheduled and ad-hoc), with live updates.
- Removal of the dead legacy jQuery (`toast()`, `ProgrammableTimer`, Console section, dead `mosiacMeshCallback` branches); connection status moves to an Alpine binding.
- Consolidated design tokens (one documented block; dark/light preserved).

## Non-goals (deferred to later specs)

- **Redesigning Content / Schedule / Fleet.** In Section 1 these are **placeholder tabs** ("coming soon"). The old admin remains available on `main` during overhaul development; the new shell doesn't have to host the old functionality.
- The unified content library, shared animations module, preview, user-uploaded animations (parent doc, later sections).
- Changing the playback engine, schedule evaluation, or any write path. The playback-state surface is **read-only** (plus one persisted field) — it observes, it doesn't control. Play/Stop/Pause continue through existing SockJS requests.
- Replacing SockJS or the message protocol. SockJS stays; only the dead jQuery *dispatch/UI* is removed.

## File structure

| File | Responsibility | Create/Modify |
|------|----------------|---------------|
| `js/timeline/shell/nav.js` | The responsive nav component (tab-bar ⇄ sidebar): renders the four destinations, reflects `store.activeTab`, handles clicks → route change. | Create |
| `js/timeline/shell/router.js` | Hash ⇄ `store.activeTab` sync: parse `location.hash` on load + `hashchange`; expose `goTo(tab)`. The single source of route truth. | Create |
| `js/timeline/now/now-view.js` | The Now landing Alpine component: builds per-group card data from `store` (displays + displayGroups + playback) and renders cards + fleet-glance + Play-now entry. | Create |
| `js/timeline/now/now-summary.js` | Pure function `buildNowSummary({displayGroups, displays, playback, renderInProgress})` → `[{displayID, screenCount, onlineCount, state, currentPlaylist, renderStatus}]`. Unit-tested in isolation. | Create |
| `js/timeline/store.js` | Add `activeTab`, `connection` (`{connected, onlineClients}`), `playback` (`displayID → {state, currentPlaylist, startedEpoch, renderStatus}`); `setActiveTab`, `setConnection`, `setPlayback`; hydrate `playback` via `api.getPlayback()`. | Modify |
| `js/timeline/api.js` | Add `getPlayback()` → `GET /api/playback`. | Modify |
| `js/timeline/timeline/sockjs-status.js` | Handle `PLAYBACK_CHANGED` → `store.setPlayback`; route connection up/down + online count → `store.setConnection` (replacing the jQuery connection DOM-poking). | Modify |
| `js/timeline/modals/modal-shell.js` | Add responsive sheet behavior (desktop modal ⇄ mobile bottom sheet) via a body/overlay class + CSS; no API change. | Modify |
| `js/timeline/index.js` | Register `shellNav` + `nowView` components; start the router; keep existing component registration (the placeholder tabs may still mount the existing components later, but Section 1 registers what it needs). | Modify |
| `admin.html` | Restructure to the new shell (statusbar + responsive nav + four `.section`s, Now real + three placeholders); remove dead jQuery; consolidate CSS tokens; add nav + sheet responsive CSS; Alpine-bind the connection indicator. | Modify |
| `server.py` | Add `GET /api/playback` handler + register route; add `_broadcast_playback_state(display_id)` helper; call it from playback transition points. | Modify |
| `mosaicmesh/state.py` | Add `Display.currentPlaylistName = None`. | Modify |
| `mosaicmesh/render.py` | Set `display.currentPlaylistName = pl.name` in `_apply_playlist`; clear it in `_stop_group_playback`; emit `PLAYBACK_CHANGED` at start/stop. | Modify |
| `mosaicmesh/websocket/legacy.py` | Emit `PLAYBACK_CHANGED` on `PLAY`/`STOP`/`PAUSE` transitions (or via the shared helper). | Modify |
| `tests/unit/test_api_endpoints.py` (or new) | `/api/playback` shape + state mapping. | Modify/Create |
| `tests/unit/js/test_now_summary.js` | `buildNowSummary` derivation. | Create |
| `tests/unit/js/test_router.js` | hash ⇄ activeTab. | Create |
| `tests/e2e/test-shell-nav.spec.js` | Tab switching, hash sync, mobile vs desktop nav, modal→sheet swap, Now renders cards. | Create |

## Component design

### 1. Responsive shell — nav + routing

**Markup (admin.html):** a fixed slim **statusbar** (brand · connection dot/text · theme toggle), then a `shell` containing the **nav** and a **main** with four `<section data-route="...">`. The nav is a single `<ul>` of four destinations styled two ways:
- **Desktop (>760px):** vertical left sidebar.
- **Mobile (≤760px):** fixed bottom tab-bar (icon + label), `main` gets bottom padding so content clears it.

One markup, swapped by a CSS media query — no JS branching on width.

**Routing (`router.js`):** the route is a single piece of truth in `store.activeTab` (`'now'|'content'|'schedule'|'fleet'`).
- On load and on `hashchange`, parse `location.hash` (strip `#`), validate against the four routes (fallback `'now'`), call `store.setActiveTab(tab)`.
- `goTo(tab)` sets `location.hash = '#'+tab` (which triggers the listener → store). Nav clicks call `goTo`.
- `.section` visibility is driven by `store.activeTab` (Alpine `x-show` or a bound class), replacing the jQuery `.active` toggle.

`store.activeTab` default `'now'`. The nav component (`shell/nav.js`) renders the four items and an `aria-current` on the active one; it reads `store.activeTab` reactively.

### 2. Modal-or-sheet system (`modal-shell.js`)

Keep the existing `openModal({title, contentEl, onClose})` API and focus-trap/Esc/one-at-a-time behavior. Add responsiveness purely in CSS + a class:
- The overlay/dialog get a class; CSS at ≤760px restyles `.mm-modal` as a **full-screen bottom sheet** (slides up, full width, max-height 100%, rounded top corners, internal scroll) instead of a centered box.
- No caller changes — every existing modal (recurrence/playlist/profile/fleet-confirm/play-now/calibration) inherits the sheet on mobile.

### 3. Design tokens

Consolidate the existing ~30 CSS custom properties into one documented `:root` block (colors, surfaces, text, spacing scale, radius, shadow, font) with the `[data-theme="light"]` overrides and the `prefers-color-scheme` fallback kept. No new theme behavior — this is a tidy-up so later sections build on one token vocabulary. The theme toggle (localStorage `adminTheme` + `data-theme`) is unchanged.

### 4. Statusbar + connection binding

Replace the jQuery connection DOM-poking (`$('#connDot').addClass('on')`, `$('#connText').text(...)`) with an Alpine binding off `store.connection = {connected: bool, onlineClients: number}`. `sockjs-status.js` (or the SockJS open/close hooks) sets it: connected on socket open, online count from `DISCOVERY_HEARTBEAT`/`CLIENTS_*`. The statusbar markup binds dot color + text to `store.connection`.

### 5. Now landing

**`now-summary.js`** — a pure function so the derivation is unit-testable without a DOM:
```
buildNowSummary({ displayGroups, displays, playback, renderInProgress }) -> [
  { displayID, screenCount, onlineCount, state, currentPlaylist, renderStatus }
]
```
- `screenCount`/`onlineCount` from `displayGroups` (which already carry `clientCount`/`onlineCount`) — fall back to counting `displays` by `displayID` if a group lacks them.
- `state` + `currentPlaylist` from `playback[displayID]` (the new surface); `renderStatus` from `playback[displayID].renderStatus` or `renderInProgress[displayID]`.
- A group with no playback entry or `state === 'idle'/'stopped'` → rendered as idle.

**`now-view.js`** — the Now Alpine component renders:
- One card per group: name, `currentPlaylist` (or "Idle"), `onlineCount/screenCount`, a state pill (playing/paused/idle), and a render indicator when rendering.
- A fleet-glance line: total online, calibrated count (from displays/groups), groups playing (count where `state==='playing'`).
- A **Play-now** entry that opens the existing `play-now.js` modal (group-scoped from a card, or global from the header).

Now re-renders reactively when `store.playback`, `store.displays`, or `store.displayGroups` change (driven by hydrate + SockJS).

### 6. Playback-state surface (server)

**New persisted field:** `Display.currentPlaylistName = None` (`state.py`). Accessed via `getattr(display, 'currentPlaylistName', None)` everywhere, so `Display`s loaded from an older `settings.dat` are safe (also backfilled by the existing `migrate_*` startup path if convenient).

**Set/clear:**
- `_apply_playlist(display_id, pl)` (render.py) — `display.currentPlaylistName = pl.name`. This is the single choke-point both ad-hoc (`ASSIGN_PLAYLIST`) and scheduled (`evaluate_schedules`) paths flow through, so both are covered.
- `_stop_group_playback(display_id)` — `display.currentPlaylistName = None`.

**State mapping** (`Display.action` → surface `state`):
```
PLAY, PREPARING   -> "playing"
PAUSE             -> "paused"
STOP, NOACTION    -> "stopped"
(no currentPlaylistName / no mediaElements) -> "idle"
```

**`GET /api/playback`** — read-only handler iterating `settings.displays`:
```json
{ "success": true, "groups": [
  { "displayID": "OEB Sign 1", "state": "playing",
    "currentPlaylist": "Lunch Menu", "startedEpoch": 1781120000000,
    "renderStatus": "" }
] }
```
(`startedEpoch` = `display.playStartEpoch`; `renderStatus` = `display.renderStatus`.)

**`PLAYBACK_CHANGED` broadcast** — a helper `_broadcast_playback_state(display_id)` encodes one group's surface row and `socketmanager.broadcast`s `{"REQUEST":"PLAYBACK_CHANGED","PAYLOAD":{"groups":[<row>]}}`. Called from each transition: `_start_group_playback`, `_stop_group_playback`, the `PAUSE` handler, and `_begin_prepare` (entering PREPARING). Wrapped in try/except (a broadcast failure must never break playback), matching the PR-27 pattern.

**Client integration:** `api.getPlayback()` hydrates `store.playback` (keyed by displayID) on load; `sockjs-status.js` handles `PLAYBACK_CHANGED` → `store.setPlayback(row)` (deferred during drags via the existing `mm-dragging` queue, consistent with PR-20).

### 7. Remove dead jQuery

- **Delete** from `admin.html`: `toast()` (Alpine `store.toast` replaces it), the `ProgrammableTimer` setup, the Console `.section` + its sidebar item + the debug `<form>` submit handler, and the dead branches of `mosiacMeshCallback` (everything except what's superseded by the Alpine connection binding + `sockjs-status.js`). If `mosiacMeshCallback` becomes empty, remove it and its wiring.
- **Keep:** `window.sock`, `generateMessage()`, `js/mosiacmesh.js`, SockJS itself, GoTime.
- Verify no `js/timeline/*` module references the removed globals (the explore confirmed only `window.sock`/`generateMessage` are used, both kept).

## Data flow

- **Hydrate:** existing six REST GETs + new `api.getPlayback()` → `store.playback`.
- **Route:** `location.hash` ⇄ `store.activeTab` (router) → section visibility + nav `aria-current`.
- **Live:** SockJS — `DISCOVERY_HEARTBEAT`/`CLIENTS_*` → `store.connection` + group online counts; `RENDER_IN_PROGRESS` → `store.renderInProgress`; **`PLAYBACK_CHANGED`** → `store.playback`. All reactive into Now.
- **Play-now:** unchanged (`ASSIGN_PLAYLIST` + `PLAY` over `window.sock`); the resulting `PLAYBACK_CHANGED` is what makes Now update — closing the loop the derive-only approach couldn't.

## Testing strategy

**Python (pytest):**
- `GET /api/playback` returns the documented shape; state mapping for each `PlayState` (PLAY→playing, PAUSE→paused, STOP/NOACTION→stopped, no-playlist→idle); `currentPlaylist` echoes `Display.currentPlaylistName`.
- `_apply_playlist` sets `currentPlaylistName = pl.name`; `_stop_group_playback` clears it.
- `_broadcast_playback_state` emits the `PLAYBACK_CHANGED` shape (assert the broadcast payload for a playing and a stopped group).

**Node `--test` (JS units):**
- `buildNowSummary` — idle group, playing group (from playback), paused, render-in-progress, missing-group-counts fallback; pure-function determinism.
- `router` — hash parse + validation + fallback to `now`; `goTo` sets hash; `hashchange` updates `activeTab`.

**Playwright (e2e):**
- Shell nav: clicking each destination updates the visible section + the hash + `aria-current`; deep-linking `#schedule` lands on that tab.
- Responsive: at mobile width the bottom tab-bar is present (sidebar hidden) and vice-versa; opening any modal renders as a full-screen sheet at mobile width, centered modal at desktop width.
- Now: with seeded playback state, the correct cards + state pills render; a simulated `PLAYBACK_CHANGED` updates a card live.

## Open questions

1. **Calibrated count source for the fleet glance** — derive "calibrated" from displays/groups (a group is calibrated when its devices have `measuredPerimeter`)? The Now glance needs a definition; if not cheaply available client-side, drop "calibrated" from the v1 glance and show online + playing only. Decide during the plan.
2. **Per-card vs. global Play-now** — Section 1 wires at least the global Play-now (header). Per-card group-scoped Play-now is a small add; include if free, else Fleet-spec territory.
3. **`PLAYBACK_CHANGED` granularity** — one-group-per-broadcast (simplest, matches transitions) vs. batched. Start with one-group; the handler accepts a `groups[]` array either way.

## Decision log

- **Hash routing into store state**, not a heavyweight router. Bookmarkable, minimal, evolves the existing hash mechanism.
- **Placeholder Content/Schedule/Fleet tabs.** Old admin stays available on `main` during development; keeps Section 1 small and focused.
- **Read-only playback-state surface (endpoint + broadcast + one persisted field).** Now must reflect reality including ad-hoc Play-Now; deriving from schedules would mislabel groups. `_apply_playlist` is the single choke-point for the playlist name.
- **Modal→sheet via CSS + class, no API change.** Every existing modal inherits mobile sheets for free.
- **Keep `window.sock`/`generateMessage`; remove only dead jQuery.** They're the sole path to displays; connection status moves to an Alpine binding.
- **Consolidate, don't redesign, the design tokens.** The existing dark/light token set is sound; later sections need one vocabulary.
