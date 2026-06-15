# Admin Overhaul — Section 2: Content — Design

**Date:** 2026-06-09
**Status:** Draft — pending review
**Parent:** [Admin UI Overhaul — Information Architecture Design](./2026-06-09-admin-ui-overhaul-design.md). Second of four per-section build specs. Builds on Section 1 ([spec](./2026-06-09-admin-overhaul-section1-shell-now-design.md), shipped in PR #32), which left a `data-route="content"` placeholder tab.

**Goal:** Fill in the Content tab with a unified content library (images + videos + animations as one kind of "content item") and a rebuilt vertical-list playlist editor — **fixing the original trigger: there is finally a usable way to add an animation to a playlist** — and migrate the animations registry into one shared ES5 module.

**Architecture:** A new shared ES5 file `js/animations.js` becomes the single source of truth for animations (self-describing entries, loaded by the iPad-1 client *and* the admin *and* the tests via a global bridge), retiring the hand-maintained catalog + the test mirror. The admin merges `/api/media` + that registry **client-side** into unified content items (no new endpoint). A new `mmContent` Alpine component renders the Content tab's Library | Playlists sub-views (with upload/delete relocated here), and `playlist-editor.js` is rewritten from the horizontal ribbon into a vertical reorderable list whose **+ Add content** picker lets an operator pick an animation exactly like an image — picking an animation sets `playmode:'SCRIPT'` implicitly.

**Tech Stack:** Alpine 3.x + native ES modules (admin, no build step), ES5 (`index.html` + `js/animations.js` for the iPad-1 client), `node --test`, pytest, Playwright. No server changes.

---

## Context

The proximate trigger for the whole overhaul: animations can't be *added* to a playlist usefully. Today an operator must add a media item, flip its play-mode to `SCRIPT`, then find a dropdown that only appears after the flip (PR #31). Section 2 fixes this at the root by making animations first-class content.

Grounded in the current code (branch `feature/admin-ui-overhaul`):

- **Animations are a 3-way split.** `index.html:425` defines `var animations = {bouncingBalls, lissajous, phyllotaxis, wireframeCube}` inline (ES5), called as `animations[name](ctx, tMs, w, h)` from `runScriptLoop` (index.html:517) / `showItem` (index.html:632, `item.file` = the animation key for SCRIPT items). `js/timeline/animations-catalog.js` is a hand-maintained `ANIMATIONS = [{key,label,description}]` (read by the playlist editor). `tests/unit/js/_animations_mirror.js` is a copy-paste mirror of the three batch-1 animations, used by determinism tests; `test_animations_registry_sync.js` + `test_animations_catalog.js` exist solely to guard the drift between these three copies.
- **The playlist editor** (`js/timeline/modals/playlist-editor.js`, ~591 lines) is a horizontal-ribbon modal (duration-proportional clips, right-edge resize) with a media-only picker (`pickerEntries()` → images+videos from `store.media`) and the PR #31 SCRIPT play-mode dropdown + animation `<select>`. Saves via `store.updatePlaylist(name, {items, loop})`. Opened from the clip context menu + the timeline drill-in.
- **REST is animation-blind.** `GET /api/media` → `{images, videos, videoDurations}`. `/api/playlists` items round-trip verbatim as `{file, playmode, backgroundColor?, duration?}` — the server never transforms `file`/`playmode`. No `/api/content` or `/api/animations` exists.
- **Section 1 left** a `data-route="content"` placeholder (`admin.html:737`), the `mmContent` mount pattern (`x-data` + `Alpine.data` in `index.js`), and the timeline relocated under `data-route="schedule"` with a left-sidebar media-bin (`bin/media-bin.js`) + playlist-bin (`bin/playlist-bin.js`).

## Goals

- **One shared animations module** (`js/animations.js`), self-describing, loaded by `index.html` + admin + tests; retire `animations-catalog.js` + `_animations_mirror.js` and collapse the drift-guard tests. No change to the animation call signature or playback behavior.
- **Unified content library**: a client-merged list of content items (image/video/animation) with type filters, rendered in the Content tab's **Library** sub-view; **+ Upload** and per-item **delete** relocated here.
- **Playlists** sub-view: list / create / rename / delete; open the editor.
- **Rebuilt vertical-list playlist editor** replacing the ribbon: reorderable rows, per-item settings sheet, and a **+ Add content** unified picker where **picking an animation sets `{file:key, playmode:'SCRIPT'}` automatically** (the trigger fix). The PR #31 SCRIPT dropdown + animation `<select>` are retired.
- **Remove the media-bin from the Schedule view** (Content is the sole media home); keep the playlist-bin there for drag-to-schedule.

## Non-goals (deferred)

- **No server changes.** `/api/media` + `/api/playlists` are untouched; content aggregation is client-side. The playlist item wire shape (`{file, duration, playmode}`) is unchanged — an animation item is still `{file:'lissajous', playmode:'SCRIPT'}`, so the server + iPad-1 client need no changes beyond loading `js/animations.js`.
- **Animation preview** (live canvas thumbnails / detail view) — designed-for by the shared module, but a later Content sub-slice. Library tiles show a type icon + name in Section 2, not a live preview.
- **User-uploaded animations** — a later slice (the shared module accommodates it).
- **Schedule / Fleet redesigns** (Sections 3–4). Section 2 touches the Schedule view *only* to remove the now-redundant media-bin; the timeline + playlist-bin stay.
- **The Now landing + shell** (Section 1) — unchanged.

## File structure

| File | Responsibility | Create/Modify/Delete |
|------|----------------|----------------------|
| `js/animations.js` | Shared ES5 registry: `window.MM_ANIMATIONS = [{key,label,description,draw}]`. No import/export. | Create |
| `index.html` | `<script src="/js/animations.js">`; replace inline `var animations` with a key→draw lookup built from `window.MM_ANIMATIONS`. | Modify |
| `js/timeline/content/content-items.js` | `buildContentItems({media, animations})` pure merge → unified items. | Create |
| `js/timeline/content/content-view.js` | `mmContent` Alpine component: Library \| Playlists sub-views, filters, upload/delete, open editor. | Create |
| `js/timeline/modals/playlist-editor.js` | Rewrite: vertical reorderable list + unified picker (animation→SCRIPT). | Modify (rewrite) |
| `js/timeline/animations-catalog.js` | Retire — replaced by reading the shared module. | Delete |
| `js/timeline/bin/media-bin.js` | Remove from the Schedule view (component retired or unmounted). | Delete/Modify |
| `js/timeline/store.js` | `contentItems` getter (merge media+animations); keep existing playlist/media mutators. | Modify |
| `js/timeline/index.js` | Register `mmContent`; drop the media-bin registration. | Modify |
| `admin.html` | Content section → `mmContent` view (Library/Playlists, upload input); remove the Schedule media-bin markup; Content + library CSS. | Modify |
| `tests/unit/js/_animations_mirror.js` | Retire — tests import the shared module. | Delete |
| `tests/unit/js/test_animations_{lissajous,phyllotaxis,wireframe}.js` | Re-point from `mirror` to the shared module registry. | Modify |
| `tests/unit/js/test_animations_registry_sync.js` | Retire (no 3-way split to sync). | Delete |
| `tests/unit/js/test_animations_catalog.js` | Rework → assert shared-module entries are well-formed. | Modify |
| `tests/unit/js/test_content_items.js` | `buildContentItems` merge/filter. | Create |
| `tests/unit/js/test_playlist_editor_add.js` | Add-content → item shape (animation→SCRIPT, media→loop). | Create |
| `tests/unit/js/test_timeline_smoke.js` | Add new modules; drop deleted ones. | Modify |
| `tests/e2e/test-content-tab.spec.js` | Library renders+filters, upload/delete, **add animation to playlist e2e**, editor reorder+save. | Create |

## Component design

### 1. Shared animations module (`js/animations.js`)

A single ES5 file, no `import`/`export` (so it is simultaneously a valid classic `<script>` for iPad-1 Safari 5.1 *and* a side-effect ES-module import for the admin + Node). It assigns a global:

```js
/* js/animations.js — ES5, no module syntax. Single source of truth for SCRIPT
   animations. Loaded by index.html (<script>), the admin, and tests. */
(function (root) {
  var animations = [
    { key: 'bouncingBalls', label: 'Bouncing balls', description: '…',
      draw: function (ctx, tMs, w, h) { /* migrated from index.html */ } },
    { key: 'lissajous',     label: 'Lissajous curve', description: '…',
      draw: function (ctx, tMs, w, h) { /* … */ } },
    { key: 'phyllotaxis',   label: 'Phyllotaxis spiral', description: '…',
      draw: function (ctx, tMs, w, h) { /* … */ } },
    { key: 'wireframeCube', label: 'Wireframe cube', description: '…',
      draw: function (ctx, tMs, w, h) { /* … */ } }
  ];
  root.MM_ANIMATIONS = animations;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
```

The `draw` bodies are moved verbatim (ES5) from the current `index.html` registry — same `(ctx, tMs, w, h)` signature, so playback math + determinism are unchanged.

**`index.html`** loads it before the inline display script and rebuilds the name→fn map it already uses, so `runScriptLoop`/`showItem` are untouched downstream:
```html
<script src="/js/animations.js"></script>
```
```js
// replaces `var animations = { ... }`
var animations = {};
(function () {
  var list = window.MM_ANIMATIONS || [];
  for (var i = 0; i < list.length; i++) { animations[list[i].key] = list[i].draw; }
})();
```
ES5 only (var/function). If `window.MM_ANIMATIONS` is missing (script failed to load), `animations` is empty → SCRIPT items render blank (the existing `if (animations[name])` guard already handles this) — no crash.

**Retirements + test rework:**
- `js/timeline/animations-catalog.js` is deleted. Its only consumer (the playlist editor) is rewritten in this section to read the registry via the content layer.
- `tests/unit/js/_animations_mirror.js` is deleted. The determinism tests (`test_animations_lissajous.js` etc.) change their import from `import { mirror }` to importing `js/animations.js` for side-effect and reading the registry, e.g.:
  ```js
  await import('../../../js/animations.js');
  const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map(a => [a.key, a.draw]));
  // byKey.lissajous(ctx, tMs, w, h)  — same fn the iPad-1 + admin run
  ```
  This is strictly *better* coverage: the tests now exercise the real shipped code, not a copy.
- `test_animations_registry_sync.js` is deleted (it guarded the 3-way drift; there's one source now).
- `test_animations_catalog.js` is reworked to assert each `MM_ANIMATIONS` entry has a string `key`/`label`/`description` and a function `draw`, and that the four expected keys exist.

### 2. Unified content model (`content-items.js`)

A pure merge, no endpoint:

```js
buildContentItems({ media, animations }) -> [
  { kind: 'image'|'video'|'animation', ref, name, label?, duration? }
]
```
- Media: `media.images` → `{kind:'image', ref:url, name:basename(url)}`; `media.videos` → `{kind:'video', ref:url, name:basename(url), duration: media.videoDurations[url]}`.
- Animations: `animations` (= `window.MM_ANIMATIONS`) → `{kind:'animation', ref:key, name:key, label}`.
- `ref` is what goes into a playlist item's `file` (a media URL or an animation key).

A `store.contentItems` getter calls this over `store.media` + the registry. The Library view and the editor's **+ Add content** picker both render the same list (filterable by `kind`).

### 3. Content tab (`mmContent`)

Replaces the placeholder. Two sub-views toggled by a small `subview` state (`'library'|'playlists'`):

- **Library** — the unified grid: filter chips (All / Images / Videos / Animations) over `store.contentItems`; each tile shows a kind icon (▦ image / ▶ video / ✦ animation) + name. **+ Upload** (wires the existing `api.uploadMedia` flow, relocated from the media-bin) and per-tile **delete** for media items (animations aren't deletable — they're code; the × is hidden for `kind:'animation'`). Delete reuses the existing `store.deleteMedia` (409-refs handling intact).
- **Playlists** — `store.playlists` list; create (name prompt → `store.createPlaylist`), rename, delete (`store.deletePlaylist`, 409-refs); click → `openPlaylistEditor`.

Mounted via `<section data-route="content" x-show="…content"><div x-data="mmContent">…</div></section>`; registered with `Alpine.data('mmContent', mmContentComponent)` in `index.js`.

### 4. Rebuilt playlist editor (the trigger fix)

`playlist-editor.js` rewritten from ribbon → **vertical reorderable list**:
- Each item is a row: kind icon + name + duration; drag a grip handle to reorder; an × to remove. Click a row → it's the "selected" item whose settings show in a sidebar/sheet.
- Per-item settings: **duration** (number; capped to a video's natural length where probed), **backgroundColor**; **play mode** loop/once is shown **only for media items**. Animation items show no play-mode control — their mode is implicitly `SCRIPT`.
- **+ Add content** opens the unified picker (the `contentItems` list with the same filter chips). Selecting an item appends a playlist item via a pure helper:
  ```js
  contentItemToPlaylistItem(ci) ->
    ci.kind === 'animation' ? { file: ci.ref, playmode: 'SCRIPT', duration: 20 }
                            : { file: ci.ref, playmode: 'loop', duration: ci.duration ?? undefined }
  ```
  This is the trigger fix — an animation drops in as a ready-to-play SCRIPT item; the operator never touches play-mode for it.
- Retired: the duration-proportional ribbon, the right-edge resize handle, the media-only `pickerEntries`, and the PR #31 play-mode-`SCRIPT` + animation-`<select>` fields (replaced by "pick an animation from the picker").
- Saves unchanged: `store.updatePlaylist(name, {items, loop})` (optimistic + If-Match rollback). Item wire shape unchanged, so the iPad-1 client + server are unaffected.
- Both entry points (Content > Playlists, and the timeline drill-in) call the rewritten `openPlaylistEditor`.

### 5. Schedule view cleanup

Remove the media-bin component (`bin/media-bin.js`) + its markup from the Schedule (timeline) section in `admin.html`, and drop its `Alpine.data('mmMediaBin', …)` registration in `index.js`. Keep the playlist-bin (drag playlist → track creates a schedule). Move the upload `<input>`/button into the Content Library.

## Data flow

- **Hydrate (unchanged):** `/api/media` → `store.media`; `/api/playlists` → `store.playlists`. The animations registry comes from `js/animations.js` (loaded as a `<script>` in `admin.html` → `window.MM_ANIMATIONS`).
- **Library/picker:** `store.contentItems` getter = `buildContentItems({media, animations: window.MM_ANIMATIONS})`, filtered by kind in the view.
- **Add to playlist:** picker selection → `contentItemToPlaylistItem` → appended to the editor's `draft.items` → `store.updatePlaylist` on Save.
- **Upload/delete:** `api.uploadMedia` then re-fetch `store.media`; `store.deleteMedia(url)` (409-refs).
- **Playback (unchanged):** the saved `{file, playmode}` items flow to the iPad-1 exactly as today; `playmode:'SCRIPT'` + `file:<key>` → `animations[key]` (now sourced from `js/animations.js`).

## Testing

- **Node `--test`:**
  - `buildContentItems` — merges media + animations, correct kinds/refs/names, filter behavior.
  - `contentItemToPlaylistItem` — animation → `{playmode:'SCRIPT'}`, media → `{playmode:'loop'}` (the trigger fix, unit-pinned).
  - Shared module — every `MM_ANIMATIONS` entry well-formed (`key/label/description` strings, `draw` function); the four keys present.
  - Determinism tests re-pointed at `js/animations.js` (same assertions, real code).
  - Module-load smoke updated (add `js/animations.js`, `content/content-items.js`, `content/content-view.js`; drop deleted modules).
- **Playwright (`test-content-tab.spec.js`):**
  - Content > Library renders mixed items; filter chips narrow by kind (animations appear).
  - Upload (stub a small file) appears; delete a media item (and a 409-refs path).
  - **Add an animation to a playlist end-to-end:** open a playlist, + Add content, filter Animations, pick `lissajous`, save; reopen and confirm the item persisted as a SCRIPT item. *This is the trigger, finally exercised.*
  - Editor reorder + per-item duration edit + save.
- **pytest:** unchanged; a guard test that `/api/media` + `/api/playlists` shapes are untouched (no server change). Confirm the batch-1 determinism + e2e still pass after the module migration.
- **iPad-1 regression note:** because `index.html`'s SCRIPT path now sources animations from `js/animations.js`, the iPad-1 hardware sign-off (the still-pending animations Task 9) should be re-run after this lands — the *code path* changed even though the math didn't.

## Open questions

1. **Rename in the Playlists sub-view** — `/api/playlists` has create/update/delete but rename = create-new + delete-old (no rename endpoint). Section 2 implements rename as that two-step, or omits rename for now (create/delete only). Decide in the plan; lean: omit rename (YAGNI) unless trivial.
2. **Picker as modal-within-modal** — the editor is itself a modal; + Add content opening the picker is a modal-over-modal. Option: the picker is an inline panel within the editor (no second modal) to avoid the one-modal-at-a-time shell constraint. Lean: inline panel inside the editor. Decide in the plan.
3. **`bouncingBalls` description/label** — it had no catalog entry historically (only the batch-1 three did). The shared module gives it a proper `{label, description}`. Confirm wording in the plan.

## Decision log

- **One shared ES5 module via a global bridge** (no import/export) — the only no-build way one file serves iPad-1 `<script>` + admin/Node ESM. Retires the catalog + mirror + drift-guard tests; determinism tests now run the real code.
- **Client-merge content aggregation, no `/api/content`** — animations are client code; the server can't enumerate them. Media stays REST.
- **Animations are content, not a play-mode** — the picker sets `playmode:'SCRIPT'` implicitly; the wire shape is unchanged so the server + iPad-1 are unaffected. Fixes the trigger at the model level.
- **Vertical-list editor, ribbon retired** — works at all sizes (the ribbon was desktop-only); the resize handle is dropped in favor of a duration field.
- **Content is the sole media home** — the Schedule media-bin is removed; the playlist-bin stays for scheduling.
- **Preview deferred** — tiles show icons in Section 2; the shared module sets up live preview for a later slice.
- **Touch the batch-1 test suite deliberately** — retiring the mirror/catalog/sync tests is correct cleanup (they guarded a split we're removing); the determinism + Playwright coverage stays, re-pointed at the real module.
