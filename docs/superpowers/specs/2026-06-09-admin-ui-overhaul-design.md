# MosaicMesh Admin UI Overhaul — Information Architecture Design

**Date:** 2026-06-09
**Status:** Draft — pending review
**Type:** High-level information-architecture design. This document defines the whole-system IA + cross-cutting architecture for the admin UI overhaul, then **decomposes into per-section build specs** (each its own spec → plan → implementation cycle). It is intentionally not a buildable implementation plan on its own.

## Context

The admin UI (`admin.html` + `js/timeline/`) grew organically across ~30 PRs. The new Alpine timeline was bolted alongside a now-dead jQuery admin, and every workflow — scheduling, content, devices, calibration, fleet actions — was smashed onto one timeline-centric screen. The result is unintuitive and does not work on mobile.

The proximate trigger: there is **no usable way to add an animation to a playlist**. Animations (shipped in PR #31) aren't a first-class thing you can pick — an operator must add a media item, flip its play-mode to `SCRIPT`, then find a dropdown that only appears after the flip. That gap is a symptom; the disease is information architecture.

This overhaul re-thinks the admin from the IA up: a responsive, multi-destination app where each area is focused enough to work on a phone and rich enough to work on a desktop.

## Goals

- **Fully responsive.** Every workflow is usable at any size — phone and desktop are coequal, not desktop-with-a-mobile-afterthought.
- **Intuitive task-focused structure.** Replace the single crammed screen with a small set of coequal destinations, each with one clear job.
- **Fix the content/animation model.** Media and animations become one unified concept ("content items") added the same way — eliminating the trigger problem at the root.
- **Separate concerns that were blurred.** Source content vs. a physical wall vs. the render step that binds them are distinct ideas and should live in distinct places.
- **Remove dead weight.** Retire the half-migrated legacy jQuery admin.
- **Design once for forward-looking features.** Content preview, user-uploaded animations, and mosaic-spanning are future build slices, but the IA + contracts must accommodate them now so nothing needs re-plumbing.

## Non-goals

- **Touching the display clients.** `index.html` / `js/mosiacmesh.js` / `js/GoTime.js` (the iPad-1 ES5 clients) are out of scope except where the shared animations module is concerned. The overhaul is admin-only.
- **Changing the server's domain model or persistence.** The overhaul reuses the existing REST API + SockJS protocols. New endpoints may be added (e.g. `/api/animations`), but `Settings`/`Display`/`Playlist`/`Schedule`/`Client` shapes are not redesigned here.
- **Building preview / user-uploaded animations / mosaic-spanning now.** These are designed-for, not built, in the first specs.
- **A build step.** The admin stays Alpine 3.x + native ES modules, no bundler — consistent with the existing constraint.

## Guiding principles

1. **One mental model, two sizes.** The same destinations and the same data appear on phone and desktop; only the *density* of rendering changes. No separate mobile app.
2. **Source vs. binding vs. preparation are separate.** *Content* is source material (Content tab). A *display group* is a physical wall (Fleet tab). *Rendering* binds content to a specific wall's geometry — it is neither, and surfaces where you watch playback (Now) and manage walls (Fleet).
3. **Unify kinds so plumbing doesn't multiply.** Images, videos, animations, and (later) user-uploaded animations are all "content items." New kinds slot into the same picker / playlist / preview / play paths without new plumbing.
4. **Live where REST is too slow; REST where SockJS is too chatty.** SockJS drives live status (online counts, render progress, what's-playing); REST drives CRUD (playlists, schedules, groups, profiles). The dead jQuery dispatch path is removed.
5. **Responsive defaults favor usability over spectacle, without conceding polish.** Where a usable layout and a pretty layout diverge on mobile (e.g. Schedule), the usable one is the default and the pretty one is one tap away — and the usable one still gets visual care.

## Information architecture

Four top-level destinations. **Bottom tab-bar on mobile, left sidebar on desktop** — same four, mapped 1:1.

```
  Now   ·   Content   ·   Schedule   ·   Fleet
   ⚡         ▦            📅            📡
```

Four coequal destinations is the sweet spot for a mobile tab-bar (the most reliable mobile nav pattern) and maps directly to a desktop sidebar. A modal/sheet system overlays all destinations (full-screen sheet on mobile, centered modal on desktop) for focused editing.

### Destination: Now (landing)

The default landing, because "what's playing where + play something now" is the most frequent need.

- **What's playing where** — one card per display group: current playlist/animation, screen count, loop state, start time, or "idle / nothing scheduled."
- **Fast play-now** — a prominent action to push content to a group immediately (the ad-hoc path, bypassing the scheduler).
- **Fleet glance** — a one-line health summary (N online · M calibrated · K groups playing).
- **Render status** — when a group needs/began a render (mosaic content on a calibrated wall), it shows here ("render required → [Render now]", "rendering… 40%").

### Destination: Content

Two sub-views: **Library** and **Playlists**.

- **Library** — the unified content grid. Every content item (image, video, animation) appears as a tile, filterable by type (All / Images / Videos / Animations). This is also where **media upload** lives ("+ Upload"). Three origins feed the library:
  - *Uploaded media* (images, videos) — from `/upload/{dest}`, stored server-side.
  - *Built-in animations* — from the shared animations module (code, not uploaded).
  - *User-uploaded animations* (future) — ES5 `.js` files uploaded + validated.
- **Playlists** — the list of playlists; create/rename/delete; open one to edit.
- **Playlist editor** — a **vertical reorderable list** of items (drag a grip handle), *not* the horizontal duration-ribbon. Each row shows the item, its type, and duration; tapping a row opens per-item settings (duration, per-item options) in a sheet. A single **"+ Add content"** button opens the *same unified picker* as the library — tap an animation exactly like an image; it drops into the playlist. **This is the trigger fix:** no play-mode flip, no hidden dropdown. (Desktop may add an optional duration-bar visualization beside the list, but the list is the primary model at all sizes.)
- **Preview (future slice, designed-for here)** — every content item is previewable: images show a thumbnail/full view, videos a poster + scrubbable player, animations a *live canvas running the real animation*. Tiles carry live thumbnails; tapping opens a detail/preview view; a mini live-preview can sit beside the duration field while configuring an item.

### Destination: Schedule

Same schedule data, three densities:

- **Phone default — agenda list.** Per-group sections, each a time-ordered list of "what's on today," with a live highlight, per-type color accents, and a thin duration indicator (usable *and* pleasant). Fastest answer to "what's playing when?"
- **Phone toggle — vertical day timeline.** One group at a time (group selector), hours top-to-bottom, scheduled blocks as cards, the now-line. For eyeballing gaps and overlaps.
- **Desktop — the tracks×hours grid.** The current grid survives at desktop width, where it's genuinely good: drag to create/move, the now-line, all groups at a glance.

Tapping any block/row opens the **recurrence editor** (full-screen sheet on mobile, modal on desktop): start/end time, frequency, by-weekday, end condition, next-N preview.

### Destination: Fleet

A list of **display groups**, each expandable to its devices (master-detail on desktop; list → detail sheet on mobile). Per group:

- **Status** — N/M online, calibrated?, what's playing, render state.
- **Group actions** — Play now, Stop, the fleet scripts (Login/Start/Stop/Reboot/Test), **Render now** (pre-bake before showtime), Calibrate.
- **Device management** — move devices between groups (single + bulk), assign per-device profiles, create/rename/delete groups.
- **Calibration** — the ArUco generate → photograph → upload → detect flow, relocated here as a focused per-group wizard (calibration is fundamentally a Fleet concern: *where the screens physically are*).
- **Profiles** — the ScriptingProfile editor (device-launch config: login/start/stop/test/reboot templates, launch method, SSH/webclip options) lives here.

## The unified content model (the keystone)

A **content item** is the single concept the picker, playlist, preview, and play paths all operate on. It has a *kind* (`image` | `video` | `animation`) and an *origin* (`uploaded` | `builtin` | `user`). The UI treats them uniformly:

| | image | video | animation |
|---|---|---|---|
| Origin | uploaded | uploaded | builtin / user |
| Add to playlist | unified picker | unified picker | unified picker |
| Preview | thumbnail | poster + scrubber | **live canvas (real code)** |
| Renders for a wall? | only in mosaic mode | only in mosaic mode | **never** (client-computed) |

Animations are not a play-mode; they are a kind of content. The playlist item still serializes as `{file, duration, playmode}` (so no server model change is required — `playmode:'SCRIPT'` + `file:'<animationKey>'` round-trips as today), but the *operator never sees or sets play-mode for an animation* — picking an animation sets both fields implicitly. This keeps the wire format stable while removing the conceptual leak.

## Cross-cutting architecture

These decisions keep the four destinations coherent rather than four mini-apps.

### Shared ES5 animations module

Today there are three copies of the animation registry: the live one in `index.html` (ES5), a hand-maintained `js/timeline/animations-catalog.js` (admin metadata), and a test `_animations_mirror.js`. They drift.

Replace all three with **one ES5 module** that is the single source of truth: runnable, self-describing, and shared.

- Each animation registers `{ name, label, description, draw }` (+ optional metadata) into a registry.
- The **iPad-1 client** loads it via `<script>` (ES5, no imports) — same behavior as today's inline registry.
- The **admin** loads the same file to (a) list animations in the picker, (b) render live previews by calling `draw` in a canvas — *the exact code the wall runs*.
- **Tests** import the same module — no mirror to maintain.
- It must stay ES5 (the iPad-1 ceiling), which is fine: ES5 runs everywhere.

This module is the substrate for preview, user-uploaded animations, and the catalog — one move that retires duplication and unlocks three features.

### One animation contract

Every animation — built-in, user-uploaded, or mosaic-spanning — implements a single signature:

```js
function draw(ctx, frame) {
  // frame = {
  //   tMs,                          // shared-clock elapsed ms — THE sync input
  //   w, h,                         // this screen's canvas pixels
  //   wall:    { w, h },            // overall wall bounding box (mosaic space)
  //   display: { x, y, w, h,        // this screen's rect within the wall
  //              orientation,       // 0 / 90 / 180 / 270
  //              skew },            // calibration quad / perspective info
  // }
}
```

Today's `draw(ctx, tMs, w, h)` becomes the degenerate case (`frame.tMs`, `frame.w`, `frame.h`). Built-ins ignore the geometry; mosaic-spanning and user animations use it. Designing this now means the contract never breaks when those features land.

**Purity is the sync guarantee.** The wall stays in lockstep only because `draw` is a pure function of `frame` (chiefly `tMs`). Animations using `Math.random()`, `Date.now()`, or frame-to-frame state desync the screens. The contract *requires* purity; tooling *warns* on impurity (see user-uploaded animations).

### Rendering is binding-time

Rendering (the ffmpeg perspective-warp/segment pipeline in `render.py`) prepares content for a *specific calibrated wall's geometry*. It cannot happen at upload time (no wall yet) and is not a content property. It is triggered when content is bound to a group (assign / schedule / play) and only for mosaic (`SEGMENT`/`INDIVIDUAL`) items on a calibrated group. Full-screen media and SCRIPT animations never render.

```
Content (source: upload or built-in)          ← Content
        │  bind: assign / schedule / play on a group
Display group (calibrated geometry)            ← Fleet
        │  render  (mosaic items + calibrated only)
Prepared per-screen slices  →  Play            ← status in Now / Fleet
```

Render **status + control** therefore lives in **Now** (what's playing) and **Fleet** (group detail), never in Content.

### Live status vs. CRUD; remove legacy jQuery

- **SockJS** drives live status: online/offline, render progress, what's-playing, device join/leave. The new shell subscribes and routes these into the store (extending the existing PR-27 status plumbing).
- **REST** drives CRUD: playlists, schedules, groups, profiles, media, animations.
- The **dead legacy jQuery admin** (`mosiacMeshCallback` wiring to deleted panels, the Console debug section) is removed from `admin.html`. SockJS itself stays (it's the live-status transport); only the jQuery UI dispatch is retired. Display clients are untouched.

### Responsive shell + design system

A small app-shell module owns: the responsive nav (tab-bar ⇄ sidebar), client-side routing between the four destinations, the modal/sheet system (full-screen sheet on mobile, modal on desktop), and a consolidated design-token set (the current CSS is ~450 lines of ad-hoc tokens; the overhaul consolidates them). Existing focused modules (store, api, the modal bodies) are reused and refactored into the new shell rather than rewritten wholesale.

## Forward-looking features (designed-for, built later)

These are **not** in the first build specs, but the IA + contracts above accommodate them with no re-plumbing.

- **Content preview** — enabled by the shared animations module (admin runs `draw` live) + the "every content item is previewable" contract. A later Content sub-slice.
- **User-uploaded animations** — a third content origin. Under the **iPad-1 ES5 constraint** (confirmed: custom animations must run on Safari 5.1):
  - *Authoring:* ES5 only (no `let`/`const`/arrow/`class`/template literals). The upload is an ES5 `.js` that registers via `MosaicMesh.registerAnimation({name, label, description}, draw)` into the shared registry. No `eval`.
  - *Execution:* loaded as a `<script>` on the clients; driven by the same per-tick runner as built-ins, wrapped in try/catch + a per-frame time budget so a bad upload degrades (blank/skip) instead of freezing a screen.
  - *Security model:* **trusted-operator.** Safari 5.1 offers no meaningful sandbox (no Workers/OffscreenCanvas for canvas, unreliable `<iframe sandbox>`, no `Proxy`/Realms), so true isolation is not available on iPad-1. The uploader *is* the admin; the risk is operational (buggy code), not adversarial. This is a deliberate, documented limitation — revisit only if non-iPad-1 walls become a target.
  - *Upload-time validation:* (1) file loads without error, (2) registers exactly one animation implementing `draw`, (3) **ES5 lint** (reject ES6 syntax that silently breaks iPad-1), (4) **purity warnings** on `Math.random`/`Date.now`/`new Date`.
- **Mosaic-spanning animations** — each screen renders its slice of one wall-sized composition, using `frame.wall` + `frame.display`. The contract above already carries the geometry; mosaic-spanning is then a client-side viewport-transform slice + a per-client SCRIPT payload extension. (See the deferred section of `2026-06-09-script-animations-pack-design.md`.)

## Decomposition into build specs

This IA spawns sequenced per-section specs, each its own spec → plan → implementation, each producing working, testable software:

1. **Shell + Now** — the responsive app shell (nav, routing, modal/sheet system, design tokens), the SockJS-fed `Now` landing, and removal of the dead jQuery admin. Establishes the framework every section plugs into.
2. **Content** — the unified content library, the shared ES5 animations module (retiring catalog + mirror), and the rebuilt vertical-list playlist editor with the unified picker. **Fixes the trigger.** Preview is a follow-on sub-slice.
3. **Schedule** — agenda (default) + vertical-timeline (toggle) on mobile, the tracks×hours grid on desktop, and the recurrence editor as sheet/modal.
4. **Fleet** — group master-detail, device move/bulk, calibration wizard, profiles, fleet actions, render-now.

Sequence rationale: the shell is the foundation; Content lands early because it fixes the trigger and proves the unified model; Schedule and Fleet follow in either order. Preview, user-uploaded animations, and mosaic-spanning are later slices layered on the Content foundation.

## Data flow (summary)

- **Hydrate:** REST GETs (`/api/playlists`, `/api/schedules`, `/api/profiles`, `/api/media`, `/api/displays`, devices) on load → Alpine store. A new `/api/animations` (or the shared module read directly) feeds the animation side of the library.
- **Mutate:** optimistic-local + `If-Match` REST PUT/POST + rollback (existing pattern) for CRUD.
- **Live:** SockJS broadcasts (status, render progress, what's-playing) → store updates → reactive re-render across destinations.
- **Play:** ad-hoc (`ASSIGN_PLAYLIST` + `PLAY` over SockJS) from Now/Fleet; scheduled via `evaluate_schedules` server-side (unchanged).

## Open questions

1. **Animation source of truth for the admin** — does the admin read the shared ES5 module directly (load the `<script>`, read the registry), or does the server expose `/api/animations` derived from it? The module-direct path is simpler (no new endpoint) but couples the admin to loading an ES5 script; a thin endpoint is cleaner REST. Decide in the Content spec.
2. **Playlist item duration-bar on desktop** — is the optional duration visualization worth building, or is the vertical list sufficient everywhere? Defer to the Content spec; the list is the committed primary.
3. **Schedule agenda visual richness** — exactly how much (color bars, mini-timeline sparkline per row) without re-creating clutter. A polish decision for the Schedule spec.
4. **Calibration on mobile** — the ArUco flow involves photographing the wall; the camera-capture UX on a phone is a Fleet-spec detail (likely a real advantage of the mobile-first push — you're already holding the camera).

## Decision log

- **Four destinations (Now/Content/Schedule/Fleet), tab-bar ⇄ sidebar.** Coequal-activity user; sweet spot for mobile nav; one model at both sizes.
- **Unified content library (media + animations as content items).** Cleanest fix for the trigger; new kinds slot in without new plumbing.
- **Vertical-list playlist editor, not the horizontal ribbon.** The ribbon is a desktop metaphor that fails on mobile; the list works at all sizes.
- **Agenda as the phone Schedule default, vertical timeline as a toggle, grid on desktop.** Usability wins the default without discarding the prettier spatial view.
- **Rendering is binding-time, surfaced in Now/Fleet, never Content.** Keeps source/wall/prep separate and stops Content from bloating.
- **One shared ES5 animations module.** Retires the catalog + mirror duplication; powers preview + user animations + tests from one source.
- **One animation contract `draw(ctx, frame)` with geometry.** Serves built-in, mosaic-spanning, and user animations without a future breaking change.
- **User-uploaded animations are ES5, trusted-operator, no real sandbox on iPad-1.** Honest limitation given Safari 5.1; mitigated by try/catch + per-frame budget + upload-time validation.
- **Remove the dead legacy jQuery admin; keep SockJS for live status.** Removes confusion + dead code; display clients untouched.
- **Design the whole IA first, then per-section build specs.** Prevents the sections from not fitting together (the original failure mode).
