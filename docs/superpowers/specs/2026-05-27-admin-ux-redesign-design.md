# Admin Console UX Redesign — Design

**Date:** 2026-05-27
**Status:** Design approved, pending implementation plan
**Scope:** `admin.html` (the desktop control console) + a new `css/admin.css`. No server change; `index.html` (the ES5 display client) untouched.

## Context

`admin.html` grew organically into a single vertical scroll of seven bordered boxes with ad-hoc inline styles, fixed `em` widths, and no information architecture: calibration preview + upload, the displays/clients jsTree, a media drag-drop box, the playlist editor, the schedules panel, a log, and a raw-command query box. This slice reorganizes it into a navigable app shell with a consistent design system and reworked interaction flows, **preserving all existing functionality and JS wiring** (jsTree, `plRenderInspector`, `schRenderForm`, `mosiacMeshCallback`, SockJS, the playlist/schedule/effects CRUD).

## Goals

- A clear **information architecture**: a persistent status bar + left sidebar nav + a main pane showing one section at a time (`location.hash`-routed, linkable).
- A **design system** of CSS-variable tokens with a **light/dark theme toggle** (honoring `prefers-color-scheme`, persisted), and consistent components (buttons, inputs, cards, panels, badges, toasts).
- **Reworked flows** for the high-impact areas (status visibility, calibration, media, console) and re-skinned editors (playlists, schedules).
- Cross-cutting UX best practices: button hierarchy, disabled/loading states, inline feedback (toasts), empty states, confirm-on-destructive, focus styles, responsive sidebar.
- **Zero behavior regressions**: every existing feature keeps working; element IDs and JS handlers preserved.

## Non-goals (deferred)

- Any server-side change or new API (status bar uses existing `DISCOVERY_HEARTBEAT` + `DISPLAYS` data).
- Replacing jsTree with a custom tree (kept, re-skinned via CSS).
- Changes to `index.html` / the display client.
- A CSS framework or build step (hand-written `css/admin.css`, vanilla JS).
- Auth/login, multi-user, or i18n.

## Architecture: the app shell

A single-page shell in `admin.html`:
- **Status bar** (top, persistent): app name + a connection dot (SockJS connected/disconnected), online-client count (from `DISCOVERY_HEARTBEAT`), a "now playing" pill per active group (from the periodic `DISPLAYS` data — `action == PLAY` + assigned playlist), and the theme toggle.
- **Sidebar nav** (left, fixed): Overview · Displays · Media · Playlists · Schedules · Console. Clicking sets `location.hash`; the router shows the matching section and highlights the item. Collapses to icons / a top strip on narrow widths.
- **Main pane**: one `<section data-route="…">` visible at a time. Each existing widget is **moved into its section, keeping its element ID** so its JS is unaffected.

A tiny vanilla-JS router: on `hashchange`/load, show the `<section>` whose `data-route` matches `location.hash` (default `#overview`), set the active nav item. **Sections are addressed by their `data-route` attribute, not by `id`** — so the inner widget IDs (e.g. the `#displays` jsTree element) are untouched and never collide with route names.

## Design system (`css/admin.css`)

CSS custom-property tokens, no framework:
- **Theme:** tokens on `:root`; a `[data-theme="light"]` and `[data-theme="dark"]` override; default from `@media (prefers-color-scheme)`. The toggle sets `data-theme` on `<html>` and writes `localStorage.adminTheme`; on load the stored value (else the media preference) is applied.
- **Tokens:** `--bg`, `--surface`, `--surface-2`, `--border`, `--text`, `--text-muted`, `--accent`, `--accent-hover`, `--ok`, `--warn`, `--danger`; spacing scale `--s1…--s5`; `--radius`; `--font`; one `--shadow`.
- **Components** (token-based, theme-agnostic):
  - `.btn` + `.btn-primary` / `.btn-danger` / `.btn-ghost`, with `:disabled`.
  - `.field` (label + input/select), `.card`, `.panel` (titled container), `.toolbar`.
  - `.badge` status pills: `online` / `offline` / `syncing` / `ready` / `playing` / `active` / `warn` (reusing the heartbeat color language).
  - `.row` list items, `.empty` empty-state block, `.toast` (transient feedback), `.steps`/`.step` (numbered flow).
- **jsTree** keeps its vendored CDN theme; a small wrapper rule maps its colors/spacing to the tokens so it reads as native.
- Typography: one system font stack, a small type scale (section title / label / body). Replaces all inline styles and 1px black borders.

## Per-section reworks

- **Overview** (*new* landing, route `overview`): at-a-glance `.card`s — per group (screen count, calibration state, now-playing, today's active schedule) and counts for media/playlists/schedules. Cards link into their sections. Read-only; data from existing `DISPLAYS` / `LIST_*` / `GET_GROUP_DEFAULTS` requests.
- **Displays & Calibration** (route `displays`): the `#displays` jsTree in a `.panel`, with **status badges** on clients (online/syncing/ready/playing) and groups (calibrated/not). Calibration becomes a **contextual `.steps` flow for the selected group**: (1) Generate & show ArUco (`GENERATEARUCO`), (2) photograph, (3) upload photo (`/upload/calibrate`) → a detected-markers preview (the `#canvas`) + inline result toast. Replaces the bare global file input.
- **Media** (route `media`): the `/api/media` library as a **thumbnail grid** (images/videos), with a styled **dropzone** (reusing the existing upload to `/upload/image|video`). Canonical media manager.
- **Playlists** (route `playlists`): the existing 3-pane editor re-skinned — library / playlist / inspector as `.panel`s, the transport as a `.toolbar` (render badge + gated Play/Render), `.empty` when no items.
- **Schedules** (route `schedules`): the existing panel re-skinned — schedule list as `.row`s with `active` badges, the recurrence builder as a tidy `.field` form, the group-defaults section as a small table.
- **Console** (route `console`): the `#log` (monospace, autoscroll) + the raw-command box, in a de-emphasized developer section (last in the nav).

## Cross-cutting UX

- Clear **primary/secondary** button hierarchy (`.btn-primary` for the main action per view; `.btn-ghost` for secondary).
- **Disabled/loading** states on async actions (e.g. Render while rendering, Save in flight).
- **Inline toasts** (`.toast`) replace blocking `alert()` calls for save/assign/error feedback (a small `toast(msg, kind)` helper).
- **Empty states** (`.empty`) with a primary call-to-action where lists are empty.
- **Confirm on destructive** actions (delete playlist / delete schedule) via a lightweight confirm.
- Visible **focus** styles; the sidebar is keyboard-navigable.
- **Responsive**: the sidebar collapses on narrow widths; panels reflow.

## Migration strategy (low-risk)

- Existing widgets are **moved into section containers keeping their element IDs** (`#displays`, `#displays_q`, `#canvas`, `#calImageForm`, `#Media`, `#plLibrary`/`#plItems`/`#plInspectorHost`/`#plTransport`/`#plSelect`, `#schForm`/`#schSelect`/`#schDefaults`, `#log`, `#text`) so `mosiacMeshCallback`, jsTree init, `plRenderInspector`, `schRenderForm`, and all event handlers keep working untouched.
- New markup/JS (router, status bar, theme toggle, overview cards, calibration steps, media grid, console drawer, toasts, empty states) is **additive**.
- The CDN jsTree CSS/JS and jQuery/SockJS includes stay.

## Implementation sequencing (each step shippable)

1. `css/admin.css` token system + theme toggle; the shell (status bar, sidebar, main pane) + router; re-home all existing widgets into sections (works, reorganized & restyled).
2. Status-bar wiring (connection / online count / now-playing) + Overview cards.
3. Per-section flow reworks (calibration steps, media grid, console drawer, badges).
4. Cross-cutting UX (toasts replacing `alert()`, empty states, confirm-on-delete, responsive sidebar).

## Testing

- **pytest:** unchanged (no server change) — the full unit suite must stay green (regression guard that nothing server-side was touched).
- **Playwright** (the substance, since this is client UX):
  - Shell renders; the six sidebar items route to their sections via `location.hash`; the active item highlights; default route is Overview.
  - Theme toggle flips `data-theme` on `<html>`, updates visibly, and persists across reload (`localStorage`).
  - Status bar reflects connection state and the online-client count; a playing group shows a now-playing pill.
  - **Preserved-flow regression:** in the new shell, the jsTree loads groups/clients; the playlist editor still adds an item and edits it in the inspector; a schedule still saves; the effect dropdowns still populate. (These exercise that re-homing kept the IDs/handlers intact.)
  - A `.toast` appears on save/assign instead of an `alert()`; an empty list shows its `.empty` state; deleting prompts a confirm.
  - Visual: a screenshot of each section in both light and dark themes.

## Legacy / ES5

`admin.html` is a desktop console — modern JS/CSS is fine. `index.html` (the 1st-gen iPad ES5 client) is **not touched**, so the legacy constraint is unaffected. No server/Python change.
