# Admin Timeline Modals + Polish Implementation Plan — PR-4c

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the spec's modal/menu chapter — full recurrence editor, right-click context menu, 412 conflict-resolution UX, playlist-item editor, profile editor (3-pane), and calibration modal — wired in on top of PR-4b's interactivity layer.

**Architecture:** All four feature areas share a small reusable `modals/modal-shell.js` (focus trap + Esc-close + click-outside + ARIA) so individual modals stay narrow and DRY. Mutations continue through the PR-4b optimistic-local + rollback pipeline; this PR adds a 412 branch that *refetches the entity* and re-renders before toasting, rather than just rolling back. The right-click context menu is a tiny positioned `<ul>` rendered into a top-level container — same pattern as the recurrence popover, generalised.

**Tech Stack:** Same as PR-4a/4b — Alpine.js 3.x + native ES modules + Playwright e2e. No new dependencies.

**Stacks on:** `feature/pr4b-timeline-interactivity` (PR #7). Merge after PR-4b lands; until then, branch off and rebase.

**Spec reference:** `docs/superpowers/specs/2026-06-04-admin-timeline-redesign-design.md`
- Section 7 (Profile editor UI — 3-pane modal)
- Section 9 (Interaction model — context menu items, selection model, fleet actions confirm)
- Section 10 (Error handling — 412 refetch UX, calibration failure modal stay-open)
- New-file table around line 540 (modals/* layout)

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `js/timeline/modals/modal-shell.js` | **Create** | Reusable modal scaffold. Exports `openModal(title, contentEl)` + `closeModal()`. Handles focus trap, Esc-to-close, click-on-overlay, ARIA `role=dialog` + `aria-labelledby`. Single modal at a time; existing modal closes before new one opens. |
| `js/timeline/modals/recurrence-editor.js` | **Create** | Full schedule editor replacing PR-4b T13's inline popover. Same fields plus dtstart, priority, startTime/endTime, and a live "next 5 occurrences" preview. Save PUTs via `store.updateSchedule`. |
| `js/timeline/modals/playlist-editor.js` | **Create** | Per-item editor for a playlist item: `playmode` (loop/once), `backgroundColor`, optional `duration` override. Opened from a drilled-in row item or context menu → Edit playlist items. Save PUTs the whole playlist via `store.updatePlaylist`. |
| `js/timeline/modals/profile-editor.js` | **Create** | 3-pane (list + form + preview) editor for `ScriptingProfile`. Opened from a new toolbar ⚙ button. Talks to `/api/profiles` via new `store.profiles` accessors. |
| `js/timeline/modals/calibration.js` | **Create** | Relocates the existing ArUco flow (display-group picker → Generate ArUco websocket → image upload → result) into a toolbar-launched modal. No new server code. |
| `js/timeline/context-menu.js` | **Create** | Right-click `<ul>` with selectable items: Edit schedule, Edit playlist items, Duplicate, Delete. Positioned at the cursor; click-outside dismisses. Generalised from PR-4b T13's popover-positioning. |
| `js/timeline/util/refetch-merge.js` | **Create** | `refetchAfterConflict(store, kind, id)` helper. Called from `util/optimistic.js` when withRollback's apiFn throws an `ApiError` with status 412. Fetches the fresh entity, replaces the store slice, toasts a *"'<name>' updated by another admin"* message. Used for Playlist and Schedule. |
| `js/timeline/store.js` | **Modify** | Add `nextOccurrences(scheduleId, n)` selector for the recurrence modal's preview (re-uses `util/time.js`'s expander). Add `createProfile/updateProfile/deleteProfile` mutation methods following the same optimistic + rollback pattern. Add `assignProfileToClient(clientKey, profileName)` (POST already exists in api.js from PR-4b T1). |
| `js/timeline/api.js` | **Modify** | Add `getProfile(name)` + `listProfiles()` thin wrappers (`/api/profiles` already supports GET; just symmetric naming with create/update/delete). Also expose `refetchSchedule(id)` and `refetchPlaylist(name)` for `util/refetch-merge.js`. |
| `js/timeline/util/optimistic.js` | **Modify** | `withRollback` gains a 412 branch that invokes `util/refetch-merge.js` instead of leaving the store in its rolled-back state. |
| `js/timeline/select.js` | **Modify** | Right-click handler defers to `context-menu.js` instead of `confirm()`. |
| `js/timeline/recurrence-popover.js` | **Delete** | Replaced by `modals/recurrence-editor.js`. Alt+click still opens it, but via the modal-shell path. |
| `js/timeline/toolbar.js` | **Modify** | Add ⚙ Profiles + 🎯 Calibrate buttons after the existing Day/Week/Month + date-nav buttons. Each `@click` opens the relevant modal. |
| `js/timeline/index.js` | **Modify** | Wire `attachContextMenu(store)` into `bootstrap()`. Drop the `attachRecurrencePopover` import + call (replaced by the modal-shell path; the Alt+click trigger now lives inside `recurrence-editor.js`). Wire `attachCalibrationModal(store)` and `attachProfileEditor(store)`. |
| `admin.html` | **Modify** | Single `<div id="mmModalHost"></div>` near the timeline section root + a `<ul id="mmContextMenu"></ul>` overlay. Toolbar gains ⚙ + 🎯 buttons via the toolbar component. CSS for `.mm-modal-overlay`, `.mm-modal`, `.mm-modal-header`, `.mm-context-menu`. Delete the now-orphaned `#mmRecurrencePopover` markup. |
| `tests/unit/js/test_refetch_merge.js` | **Create** | Node test for `refetchAfterConflict`: mocks fetch, asserts the entity is replaced + toast emitted. (Module-load smoke for modal-shell.js + the new modal files is via `test_timeline_smoke.js` — no separate file.) |
| `tests/unit/js/test_timeline_smoke.js` | **Modify** | Add the 6 new modules to the MODULES list. Remove `js/timeline/recurrence-popover.js`. |
| `tests/e2e/test-recurrence-modal.spec.js` | **Create** | Open via Alt+click clip, switch freq → WEEKLY, check Mon/Wed/Fri, click Save, assert `store.schedules[i]` patched + preview shows three next dates. |
| `tests/e2e/test-context-menu.spec.js` | **Create** | Right-click a clip → menu appears → click Delete → schedule removed. |
| `tests/e2e/test-conflict-412.spec.js` | **Create** | Force a 412 by editing the same schedule via two `fetch`s, then a third PUT through the store; assert the store ends up at the server's version and a toast appears with the conflict message. |
| `tests/e2e/test-playlist-editor.spec.js` | **Create** | Drill into a clip, double-click an item → editor opens → change backgroundColor → Save → assert `store.playlists[name].items[i].backgroundColor` updated. |
| `tests/e2e/test-profile-editor.spec.js` | **Create** | Click toolbar ⚙ → modal opens → select `ipad1-ios5` from list → change `label` → Save → re-open → label persisted. (Script editor + launch config are exercised at the unit level; e2e covers the modal plumbing.) |
| `tests/e2e/test-calibration-modal.spec.js` | **Create** | Click toolbar 🎯 → modal opens with the display-group dropdown + Generate button + upload input. Smoke only (no real ArUco photo). |
| `CLAUDE.md` | **Modify** | Layout: add `js/timeline/modals/`, `js/timeline/context-menu.js`, `js/timeline/util/refetch-merge.js`. Conventions: shared modal shell pattern + 412-as-refetch-rather-than-rollback convention. |

---

## Phase A: Schedule editing polish

Three deliverables: full recurrence modal, right-click context menu, 412 conflict resolution UX. Commit each task individually so a partial-PR rescue stays clean.

### Task A1: `util/refetch-merge.js` — 412 conflict resolver

**Files:**
- Create: `js/timeline/util/refetch-merge.js`
- Modify: `js/timeline/api.js` — add `refetchSchedule(id)` + `refetchPlaylist(name)`
- Modify: `tests/unit/js/test_timeline_smoke.js`

- [ ] **Step 1: Extend `api.js` with refetch helpers**

```javascript
// Add to api object in js/timeline/api.js (alongside the existing GET/PUT/DELETE methods)
  async refetchSchedule(id)        { return await getJson('/api/schedules/' + encodeURIComponent(id)); },
  async refetchPlaylist(name)      { return await getJson('/api/playlists/' + encodeURIComponent(name)); },
```

The existing `getJson` helper already throws `ApiError` on non-2xx — same shape as PR-4b's mutation paths.

- [ ] **Step 2: Create `js/timeline/util/refetch-merge.js`**

```javascript
/**
 * 412 conflict resolver. When a PUT fails with 412 ("If-Match stale"),
 * fetching the fresh entity + replacing the store slice + toasting the
 * server's update message is friendlier than the bare rollback that
 * PR-4b shipped — operators get to keep their session state and know
 * what happened.
 *
 * Returns nothing; mutates the store as a side-effect. Throws if the
 * refetch itself fails (rare; caller falls back to plain rollback).
 */
import { api } from '../api.js';

export async function refetchAfterConflict(store, kind, id) {
  if (kind === 'schedule') {
    const fresh = await api.refetchSchedule(id);
    const idx = store.schedules.findIndex(s => s.id === id);
    if (idx !== -1) store.schedules[idx] = fresh;
  } else if (kind === 'playlist') {
    const fresh = await api.refetchPlaylist(id);   // id = playlist name
    store.playlists[id] = fresh;
  } else {
    throw new Error(`refetchAfterConflict: unknown kind ${kind}`);
  }
  const name = (kind === 'schedule')
    ? (store.schedules.find(s => s.id === id)?.playlistName || id)
    : id;
  store.toast(`"${name}" was updated by another admin — pulled latest.`, 'info');
}
```

- [ ] **Step 3: Smoke-test module load**

Add `'js/timeline/util/refetch-merge.js'` to `tests/unit/js/test_timeline_smoke.js` MODULES list.

Run: `node --test tests/unit/js/test_timeline_smoke.js`
Expected: PASS, module loads cleanly.

- [ ] **Step 4: Unit-test the refetch path**

Create `tests/unit/js/test_refetch_merge.js`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert';

test('refetchAfterConflict replaces schedule + toasts', async () => {
  // Mock the api module by overriding globalThis.fetch.
  const fetchCalls = [];
  globalThis.fetch = async (url) => {
    fetchCalls.push(url);
    return {
      ok: true, status: 200,
      json: async () => ({ id: 'sch1', playlistName: 'Morning', startTime: '12:00', endTime: '13:00', _serverVersion: 5 }),
    };
  };
  // Re-import after fetch stub so api.js sees it.
  const { refetchAfterConflict } = await import('../../../js/timeline/util/refetch-merge.js?t=' + Date.now());

  const store = {
    schedules: [{ id: 'sch1', playlistName: 'Morning', startTime: '09:00', endTime: '10:00', _serverVersion: 4 }],
    playlists: {},
    toasts: [], toast(msg, kind) { this.toasts.push({ msg, kind }); },
  };
  await refetchAfterConflict(store, 'schedule', 'sch1');
  assert.equal(store.schedules[0].startTime, '12:00');
  assert.equal(store.schedules[0]._serverVersion, 5);
  assert.equal(store.toasts.length, 1);
  assert.match(store.toasts[0].msg, /updated by another admin/);
  assert.equal(store.toasts[0].kind, 'info');
});

test('refetchAfterConflict for playlist replaces by name', async () => {
  globalThis.fetch = async () => ({
    ok: true, status: 200,
    json: async () => ({ name: 'Morning', items: [{ file: 'a.mp4' }, { file: 'b.mp4' }], _serverVersion: 9 }),
  });
  const { refetchAfterConflict } = await import('../../../js/timeline/util/refetch-merge.js?t=' + (Date.now()+1));
  const store = {
    schedules: [], playlists: { Morning: { name: 'Morning', items: [{ file: 'a.mp4' }], _serverVersion: 8 } },
    toasts: [], toast(msg, kind) { this.toasts.push({ msg, kind }); },
  };
  await refetchAfterConflict(store, 'playlist', 'Morning');
  assert.equal(store.playlists.Morning.items.length, 2);
  assert.equal(store.playlists.Morning._serverVersion, 9);
});
```

Run: `node --test tests/unit/js/test_refetch_merge.js`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add js/timeline/util/refetch-merge.js js/timeline/api.js tests/unit/js/test_refetch_merge.js tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline/util): refetch-merge.js — 412 conflict resolver

When a PUT fails 'If-Match' (server returned 412), refetch the fresh
entity + replace the store slice + toast 'updated by another admin'
rather than the bare rollback PR-4b shipped. Operators keep their
session state and learn why their edit didn't stick.

Schedules + playlists both supported. The api wrapper gains
refetchSchedule/refetchPlaylist thin helpers (GET via existing
getJson, which already throws ApiError on non-2xx).

PR-4c T-A1."
```

---

### Task A2: Wire 412-branch into `util/optimistic.js`

**Files:**
- Modify: `js/timeline/util/optimistic.js`
- Modify: `js/timeline/store.js` — pass a `conflictKind` hint into withRollback for the mutation methods

- [ ] **Step 1: Extend `withRollback` to take an optional `conflictKind`**

```javascript
// js/timeline/util/optimistic.js
export async function withRollback(store, snapshotKeys, mutationFn, apiFn, opts = {}) {
  const snapshot = {};
  for (const k of snapshotKeys) snapshot[k] = deepClone(store[k]);
  try {
    mutationFn();
    return await apiFn();
  } catch (e) {
    for (const k of snapshotKeys) store[k] = snapshot[k];
    // PR-4c: 412 means the server saw a newer version. Replace the
    // rollback toast with a refetch + 'updated by another admin' toast.
    if (e && e.status === 412 && opts.conflictKind && opts.conflictId) {
      try {
        const { refetchAfterConflict } = await import('./refetch-merge.js');
        await refetchAfterConflict(store, opts.conflictKind, opts.conflictId);
        throw e;  // still throw so the caller .catch() chain runs
      } catch (refetchErr) {
        // Refetch failure — fall through to plain-toast path.
      }
    }
    const errMsg = (e && e.body && e.body.error) || (e && e.message) || String(e);
    if (typeof store.toast === 'function') store.toast(errMsg, 'error');
    throw e;
  }
}
```

- [ ] **Step 2: Pass conflictKind+conflictId from the three mutation methods**

In `js/timeline/store.js`, locate the three callers of `withRollback` and append the opts arg:

```javascript
// updateSchedule:
return withRollback(this, ['schedules'], () => { /* existing */ }, async () => { /* existing */ }, { conflictKind: 'schedule', conflictId: id });

// deleteSchedule: no opts (404 on delete is not a conflict, leave plain)

// updatePlaylist:
return withRollback(this, ['playlists'], () => { /* existing */ }, async () => { /* existing */ }, { conflictKind: 'playlist', conflictId: name });
```

- [ ] **Step 3: Verify the existing optimistic + store mutation tests still pass**

Run: `node --test tests/unit/js/test_optimistic.js tests/unit/js/test_store_mutations.js`
Expected: all PASS (existing assertions don't touch 412).

- [ ] **Step 4: Commit**

```bash
git add js/timeline/util/optimistic.js js/timeline/store.js
git commit -m "feat(timeline/util): withRollback dispatches refetch on 412

When the API throws ApiError(status=412), withRollback now invokes
refetch-merge.js (T-A1) so the store updates to the server's version
instead of staying rolled-back. Callers pass conflictKind+conflictId
in the opts arg to opt in — delete paths intentionally don't (404 on
delete isn't a conflict).

Refetch failure falls through to the existing plain-toast path so a
hard-broken server still surfaces the error.

PR-4c T-A2."
```

---

### Task A3: `modals/modal-shell.js` — reusable modal scaffold

**Files:**
- Create: `js/timeline/modals/modal-shell.js`
- Modify: `admin.html` — add `<div id="mmModalHost"></div>` + CSS
- Modify: `tests/unit/js/test_timeline_smoke.js`

- [ ] **Step 1: Create the shell module**

```javascript
// js/timeline/modals/modal-shell.js
/**
 * Tiny reusable modal scaffold. ONE modal at a time — opening a new
 * modal closes the current one. Handles:
 *   - focus trap (focus moves to first focusable; Tab cycles within)
 *   - Esc closes
 *   - click on overlay closes
 *   - aria-labelledby points at the title element
 *
 * Modals own their own content (form, layout, save handler). The shell
 * just wires the chrome.
 */

let currentClose = null;

export function openModal({ title, contentEl, onClose }) {
  if (currentClose) currentClose();
  const host = document.getElementById('mmModalHost');
  if (!host) throw new Error('modal-shell: #mmModalHost not found');

  const overlay = document.createElement('div');
  overlay.className = 'mm-modal-overlay';
  overlay.setAttribute('role', 'presentation');

  const dialog = document.createElement('div');
  dialog.className = 'mm-modal';
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');
  const titleId = 'mm-modal-title-' + Math.floor(Math.random() * 1e9).toString(36);
  dialog.setAttribute('aria-labelledby', titleId);

  const header = document.createElement('div');
  header.className = 'mm-modal-header';
  const h2 = document.createElement('h2');
  h2.id = titleId;
  h2.textContent = title;
  const closeBtn = document.createElement('button');
  closeBtn.className = 'mm-modal-close btn btn-ghost';
  closeBtn.type = 'button';
  closeBtn.setAttribute('aria-label', 'Close');
  closeBtn.textContent = '✕';
  header.appendChild(h2);
  header.appendChild(closeBtn);

  const body = document.createElement('div');
  body.className = 'mm-modal-body';
  body.appendChild(contentEl);

  dialog.appendChild(header);
  dialog.appendChild(body);
  overlay.appendChild(dialog);
  host.appendChild(overlay);

  function close() {
    if (currentClose !== close) return;
    document.removeEventListener('keydown', onKey);
    overlay.remove();
    currentClose = null;
    if (typeof onClose === 'function') onClose();
  }

  function onKey(ev) {
    if (ev.key === 'Escape') { ev.preventDefault(); close(); }
    else if (ev.key === 'Tab') trapFocus(ev, dialog);
  }
  closeBtn.addEventListener('click', close);
  overlay.addEventListener('mousedown', (ev) => { if (ev.target === overlay) close(); });
  document.addEventListener('keydown', onKey);

  // Focus the first focusable; fall back to dialog itself for Esc.
  const first = dialog.querySelector('input, select, textarea, button:not(.mm-modal-close), [tabindex]:not([tabindex="-1"])');
  (first || closeBtn).focus();

  currentClose = close;
  return { close, dialog };
}

export function closeModal() {
  if (currentClose) currentClose();
}

function trapFocus(ev, root) {
  const els = Array.from(root.querySelectorAll(
    'input, select, textarea, button, [tabindex]:not([tabindex="-1"])'
  )).filter(el => !el.disabled && el.offsetParent !== null);
  if (!els.length) return;
  const first = els[0], last = els[els.length - 1];
  if (ev.shiftKey && document.activeElement === first) {
    ev.preventDefault(); last.focus();
  } else if (!ev.shiftKey && document.activeElement === last) {
    ev.preventDefault(); first.focus();
  }
}
```

- [ ] **Step 2: Add `<div id="mmModalHost">` + CSS to admin.html**

Find the timeline section's closing tags (where PR-4b put `#mmRecurrencePopover`). Replace that popover markup with:

```html
        <!-- PR-4c: single host element for all modals. modal-shell.js
             appends an overlay+dialog and removes it on close. -->
        <div id="mmModalHost"></div>
        <!-- PR-4c: context menu overlay. context-menu.js positions this
             at the cursor + populates items. -->
        <ul id="mmContextMenu" class="mm-context-menu" style="display:none"></ul>
      </section>
```

And add the modal CSS:

```css
/* PR-4c: modal scaffold (modal-shell.js) */
.mm-modal-overlay { position: fixed; inset: 0; z-index: 1500; background: rgba(0,0,0,0.55); display: flex; align-items: center; justify-content: center; }
.mm-modal { background: var(--bg-elev, #2a2a2a); color: var(--text, #eee); border-radius: 8px; box-shadow: 0 8px 32px rgba(0,0,0,0.7); min-width: 360px; max-width: 90vw; max-height: 90vh; display: flex; flex-direction: column; }
.mm-modal-header { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 12px; border-bottom: 1px solid rgba(255,255,255,0.08); }
.mm-modal-header h2 { font-size: 14px; margin: 0; }
.mm-modal-body { padding: 12px; overflow: auto; }
.mm-modal-close { padding: 2px 8px; min-width: auto; }
.mm-context-menu { position: fixed; z-index: 1600; background: var(--bg-elev, #2a2a2a); color: var(--text, #eee); border: 1px solid rgba(255,255,255,0.12); border-radius: 6px; padding: 4px 0; min-width: 160px; box-shadow: 0 4px 20px rgba(0,0,0,0.5); list-style: none; margin: 0; }
.mm-context-menu li { padding: 6px 12px; cursor: pointer; font-size: 12px; }
.mm-context-menu li:hover { background: rgba(255,255,255,0.08); }
.mm-context-menu li.mm-context-divider { padding: 0; border-top: 1px solid rgba(255,255,255,0.08); margin: 2px 0; pointer-events: none; }
.mm-context-menu li.mm-context-danger { color: var(--err, #f88); }
```

- [ ] **Step 3: Add to smoke test + run**

Add `'js/timeline/modals/modal-shell.js'` to MODULES list.

Run: `node --test tests/unit/js/test_timeline_smoke.js`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add js/timeline/modals/modal-shell.js admin.html tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline/modals): modal-shell.js — reusable scaffold

Focus-trapped, Esc-dismissable, click-outside-closable modal base.
One modal at a time — opening a new one closes the current. Used by
the four modals in this PR (recurrence, playlist-item, profile,
calibration).

Replaces PR-4b T13's free-floating #mmRecurrencePopover markup with a
single #mmModalHost host element + dynamic DOM nodes. CSS centralises
the chrome (overlay, header, body, close button) so individual
modals just supply their form content.

PR-4c T-A3."
```

---

### Task A4: `modals/recurrence-editor.js` — full schedule editor

**Files:**
- Create: `js/timeline/modals/recurrence-editor.js`
- Delete: `js/timeline/recurrence-popover.js`
- Modify: `js/timeline/index.js` — drop `attachRecurrencePopover` import + call; add `attachRecurrenceEditor`
- Modify: `admin.html` — delete `#mmRecurrencePopover` block (if not removed in A3)
- Modify: `js/timeline/store.js` — add `nextOccurrences(scheduleId, n)` selector
- Modify: `tests/unit/js/test_timeline_smoke.js`

- [ ] **Step 1: Add `nextOccurrences` selector to store**

At the top of `js/timeline/store.js`, add a static import:

```javascript
import { expandSchedule } from './util/time.js';
```

In the store factory, alongside the other selectors:

```javascript
    // PR-4c: returns the next N concrete clip placements for a schedule,
    // looking forward from `fromIso` (default = today). Powers the
    // recurrence modal's "next 5 occurrences" preview. Re-uses the same
    // expander the day-grid renders with so the preview matches what
    // the operator will see once the schedule lands.
    nextOccurrences(scheduleId, n = 5, fromIso = null) {
      const s = this.schedules.find(x => x.id === scheduleId);
      if (!s) return [];
      const startIso = fromIso || new Date().toISOString().slice(0, 10);
      const [y, m, d] = startIso.split('-').map(Number);
      const fromMs = Date.UTC(y, m - 1, d);
      // 365 days forward is sufficient for any DAILY..YEARLY recurrence.
      const HORIZON_MS = 365 * 24 * 60 * 60 * 1000;
      const placements = expandSchedule(s, fromMs, fromMs + HORIZON_MS);
      return placements.slice(0, n);
    },
```

- [ ] **Step 2: Create the modal component**

```javascript
// js/timeline/modals/recurrence-editor.js
/**
 * Full schedule editor modal. Replaces PR-4b T13's inline popover.
 *
 * Fields:
 *   - dtstart (date input)
 *   - startTime + endTime (HH:MM time inputs)
 *   - freq (Daily/Weekly/Monthly/Yearly)
 *   - interval (number)
 *   - byweekday checkboxes (Weekly only)
 *   - end-type radio (Never / Until date / After N) with conditional inputs
 *   - priority (number)
 *
 * "Next 5 occurrences" preview shown below the form, recomputed on
 * every input change so the operator can see the recurrence resolve.
 *
 * Save calls store.updateSchedule (PR-4b T-A2 412 branch active).
 */
import { openModal, closeModal } from './modal-shell.js';

const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export function attachRecurrenceEditor(store) {
  // Alt+click on a clip opens the modal. (Same trigger as PR-4b T13,
  // now routes through the shell.)
  document.addEventListener('click', (ev) => {
    if (!ev.altKey) return;
    const clip = ev.target.closest('.mm-clip');
    if (!clip) return;
    ev.preventDefault();
    ev.stopPropagation();
    open(store, clip.dataset.scheduleId);
  }, true);
}

export function openRecurrenceEditor(store, scheduleId) {
  open(store, scheduleId);
}

function open(store, scheduleId) {
  const s = store.schedules.find(x => x.id === scheduleId);
  if (!s) return;
  const root = document.createElement('div');
  root.innerHTML = `
    <div class="mm-form-grid">
      <label>Playlist <input type="text" disabled value="${escapeAttr(s.playlistName)}"></label>
      <label>Display <input type="text" disabled value="${escapeAttr(s.displayID)}"></label>
      <label>Starts on <input type="date" data-field="dtstart" value="${escapeAttr(s.dtstart || '')}"></label>
      <label>Priority <input type="number" data-field="priority" min="0" value="${Number(s.priority || 0)}"></label>
      <label>Start time <input type="time" data-field="startTime" value="${escapeAttr(s.startTime || '00:00')}"></label>
      <label>End time <input type="time" data-field="endTime" value="${escapeAttr(s.endTime || '01:00')}"></label>
      <label>Frequency
        <select data-field="freq">
          ${['DAILY','WEEKLY','MONTHLY','YEARLY'].map(f =>
            `<option value="${f}"${(s.freq||'DAILY')===f?' selected':''}>${f[0]+f.slice(1).toLowerCase()}</option>`
          ).join('')}
        </select>
      </label>
      <label>Every <input type="number" data-field="interval" min="1" value="${Number(s.interval || 1)}" style="width:4em"> period(s)</label>
      <div class="mm-form-row" data-field="byweekday">
        ${DOW.map((d, i) => `<label><input type="checkbox" value="${i}"${(s.byweekday||[]).includes(i)?' checked':''}> ${d}</label>`).join('')}
      </div>
      <div class="mm-form-row" data-field="endType">
        <label><input type="radio" name="mmRcEnd" value="never"${((s.end&&s.end.type)||'never')==='never'?' checked':''}> Never</label>
        <label><input type="radio" name="mmRcEnd" value="until"${(s.end&&s.end.type)==='until'?' checked':''}> Until</label>
        <label><input type="radio" name="mmRcEnd" value="count"${(s.end&&s.end.type)==='count'?' checked':''}> After N times</label>
      </div>
      <label data-field="untilRow">Until <input type="date" data-field="untilDate" value="${escapeAttr(s.end?.untilDate || '')}"></label>
      <label data-field="countRow">Count <input type="number" data-field="count" min="1" value="${Number(s.end?.count || 1)}" style="width:5em"></label>
    </div>
    <div class="mm-form-actions">
      <button type="button" class="btn btn-ghost" data-action="cancel">Cancel</button>
      <button type="button" class="btn btn-primary" data-action="save">Save</button>
    </div>
    <div class="mm-recurrence-preview"><strong>Next occurrences</strong><ol data-field="preview"></ol></div>
  `;

  function readDraft() {
    const f = (sel) => root.querySelector(sel);
    const freq = f('[data-field="freq"]').value;
    const endTypeEl = f('[data-field="endType"] input:checked');
    const endType = endTypeEl ? endTypeEl.value : 'never';
    let end = { type: 'never' };
    if (endType === 'until') end = { type: 'until', untilDate: f('[data-field="untilDate"]').value };
    if (endType === 'count') end = { type: 'count', count: Math.max(1, parseInt(f('[data-field="count"]').value, 10) || 1) };
    return {
      dtstart: f('[data-field="dtstart"]').value,
      startTime: f('[data-field="startTime"]').value,
      endTime: f('[data-field="endTime"]').value,
      freq,
      interval: Math.max(1, parseInt(f('[data-field="interval"]').value, 10) || 1),
      byweekday: freq === 'WEEKLY'
        ? Array.from(root.querySelectorAll('[data-field="byweekday"] input:checked')).map(cb => Number(cb.value))
        : [],
      end,
      priority: Math.max(0, parseInt(f('[data-field="priority"]').value, 10) || 0),
    };
  }

  function updateConditionals() {
    const freq = root.querySelector('[data-field="freq"]').value;
    root.querySelector('[data-field="byweekday"]').style.display = (freq === 'WEEKLY') ? '' : 'none';
    const endType = root.querySelector('[data-field="endType"] input:checked')?.value || 'never';
    root.querySelector('[data-field="untilRow"]').style.display = (endType === 'until') ? '' : 'none';
    root.querySelector('[data-field="countRow"]').style.display = (endType === 'count') ? '' : 'none';
  }

  function refreshPreview() {
    const draft = readDraft();
    // Build a hypothetical schedule for the preview without committing
    // — feed the draft into the store's expander indirectly by mutating
    // a clone of the schedule. We attach the clone temporarily, ask for
    // nextOccurrences, then revert.
    const originalIdx = store.schedules.findIndex(x => x.id === scheduleId);
    const original = store.schedules[originalIdx];
    store.schedules[originalIdx] = { ...original, ...draft };
    try {
      const items = store.nextOccurrences(scheduleId, 5);
      const ol = root.querySelector('[data-field="preview"]');
      ol.innerHTML = items.length
        ? items.map(p => `<li>${new Date(p.startMs).toISOString().slice(0,10)} ${formatHm(p.startMs)}–${formatHm(p.endMs)}</li>`).join('')
        : '<li class="mm-recurrence-empty">No occurrences in the next 365 days.</li>';
    } finally {
      store.schedules[originalIdx] = original;
    }
  }

  root.addEventListener('input', () => { updateConditionals(); refreshPreview(); });
  root.addEventListener('change', () => { updateConditionals(); refreshPreview(); });

  const { dialog } = openModal({ title: `Schedule: ${s.playlistName} on ${s.displayID}`, contentEl: root });

  root.querySelector('[data-action="cancel"]').addEventListener('click', () => closeModal());
  root.querySelector('[data-action="save"]').addEventListener('click', async () => {
    const draft = readDraft();
    // Inline validation: endTime > startTime; end.count >= 1; end.untilDate present when chosen.
    if (draft.endTime <= draft.startTime) {
      store.toast('End time must be after start time.', 'error');
      return;
    }
    if (draft.end.type === 'until' && !draft.end.untilDate) {
      store.toast('Pick an "until" date or change End to Never / After N.', 'error');
      return;
    }
    try {
      await store.updateSchedule(scheduleId, draft);
      closeModal();
    } catch (_) { /* toast already shown via withRollback */ }
  });

  updateConditionals();
  refreshPreview();
}

function escapeAttr(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;'); }
function formatHm(ms) {
  const d = new Date(ms);
  return `${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`;
}
```

- [ ] **Step 3: Add the form CSS to admin.html**

Append next to the modal CSS from A3:

```css
.mm-form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px; margin-bottom: 12px; }
.mm-form-grid label { display: flex; flex-direction: column; gap: 4px; font-size: 11px; }
.mm-form-grid label input, .mm-form-grid label select { font-size: 12px; padding: 4px; box-sizing: border-box; width: 100%; }
.mm-form-row { grid-column: 1 / -1; display: flex; gap: 12px; flex-wrap: wrap; align-items: center; font-size: 12px; }
.mm-form-row label { flex-direction: row; gap: 4px; }
.mm-form-actions { display: flex; gap: 8px; justify-content: flex-end; margin-bottom: 12px; }
.mm-recurrence-preview { border-top: 1px solid rgba(255,255,255,0.08); padding-top: 10px; font-size: 11px; }
.mm-recurrence-preview ol { margin: 6px 0 0 18px; padding: 0; }
.mm-recurrence-preview li { padding: 2px 0; }
.mm-recurrence-empty { color: var(--text-muted, #888); list-style: none; margin-left: -18px; }
```

- [ ] **Step 4: Wire into index.js + delete the old popover**

In `js/timeline/index.js`, replace the `attachRecurrencePopover` import + call with the new one:

```javascript
// Before:
// import { attachRecurrencePopover } from './recurrence-popover.js';
// ...
// attachRecurrencePopover(store);

// After:
import { attachRecurrenceEditor } from './modals/recurrence-editor.js';
// ...
attachRecurrenceEditor(store);
```

Delete the file:

```bash
git rm js/timeline/recurrence-popover.js
```

In `admin.html`, find the `#mmRecurrencePopover` block and remove it (if not already removed in A3).

In `tests/unit/js/test_timeline_smoke.js` MODULES list:
- Remove `'js/timeline/recurrence-popover.js'`
- Add `'js/timeline/modals/recurrence-editor.js'`

- [ ] **Step 5: Run smoke**

```bash
node --test tests/unit/js/test_timeline_smoke.js
```
Expected: PASS, the new module loads.

- [ ] **Step 6: MCP-verify against running server**

Navigate to `http://localhost:3000/admin?nocache=t4#timeline`, click the Timeline nav, Alt+click an existing clip. Confirm:
- Modal opens with the fields populated from the clip's schedule.
- Switching freq to WEEKLY reveals the byweekday row.
- Switching end-type to "Until" reveals the untilDate input.
- The "Next occurrences" preview shows live dates as you change fields.
- Save persists; Cancel doesn't.

- [ ] **Step 7: Commit**

```bash
git add js/timeline/modals/recurrence-editor.js js/timeline/store.js js/timeline/index.js admin.html tests/unit/js/test_timeline_smoke.js
git rm js/timeline/recurrence-popover.js
git commit -m "feat(timeline/modals): recurrence-editor.js — full schedule modal

Replaces PR-4b T13's inline popover. Same trigger (Alt+click clip) +
same fields, plus dtstart, priority, startTime/endTime, and a live
'next 5 occurrences' preview powered by store.nextOccurrences ->
util/time.js's expander. Save flows through store.updateSchedule
which inherits T-A2's 412 refetch path.

Inline validation: endTime > startTime, until-date required when
End=Until. Save toast'd on validation failure, no API call.

PR-4c T-A4."
```

---

### Task A5: `context-menu.js` — right-click menu

**Files:**
- Create: `js/timeline/context-menu.js`
- Modify: `js/timeline/select.js` — remove the `confirm()`-based right-click handler
- Modify: `js/timeline/index.js` — wire `attachContextMenu(store)`
- Modify: `tests/unit/js/test_timeline_smoke.js`

- [ ] **Step 1: Create the context-menu module**

```javascript
// js/timeline/context-menu.js
/**
 * Right-click context menu for clips. Renders a <ul> at the cursor
 * with: Edit schedule, Edit playlist items, Duplicate, Delete.
 * Click on an item invokes the appropriate action + closes the menu.
 * Click anywhere else closes it.
 *
 * The menu element lives in admin.html as #mmContextMenu so we can
 * style it without injecting CSS at runtime.
 */
import { openRecurrenceEditor } from './modals/recurrence-editor.js';
import { openPlaylistEditor }   from './modals/playlist-editor.js';

export function attachContextMenu(store) {
  const menu = document.getElementById('mmContextMenu');
  if (!menu) return;

  function close() { menu.style.display = 'none'; menu.innerHTML = ''; }

  function open(ev, scheduleId) {
    const s = store.schedules.find(x => x.id === scheduleId);
    if (!s) return;
    menu.innerHTML = '';
    const items = [
      { label: 'Edit schedule…',        action: () => openRecurrenceEditor(store, scheduleId) },
      { label: 'Edit playlist items…',  action: () => openPlaylistEditor(store, s.playlistName) },
      { label: 'Duplicate',             action: () => duplicate(store, s) },
      { divider: true },
      { label: 'Delete', danger: true,  action: () => deleteOne(store, scheduleId) },
    ];
    for (const it of items) {
      const li = document.createElement('li');
      if (it.divider) { li.className = 'mm-context-divider'; menu.appendChild(li); continue; }
      li.textContent = it.label;
      if (it.danger) li.className = 'mm-context-danger';
      li.addEventListener('click', () => { it.action(); close(); });
      menu.appendChild(li);
    }
    // Position. Clamp to viewport so the menu never opens off-screen.
    const vw = window.innerWidth, vh = window.innerHeight;
    menu.style.display = 'block';
    const mw = menu.offsetWidth || 160, mh = menu.offsetHeight || 100;
    menu.style.left = `${Math.min(ev.clientX, vw - mw - 4)}px`;
    menu.style.top  = `${Math.min(ev.clientY, vh - mh - 4)}px`;
  }

  document.addEventListener('contextmenu', (ev) => {
    const clip = ev.target.closest('.mm-clip');
    if (!clip) return;
    ev.preventDefault();
    open(ev, clip.dataset.scheduleId);
  }, true);

  document.addEventListener('mousedown', (ev) => {
    if (menu.style.display === 'none') return;
    if (!menu.contains(ev.target)) close();
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && menu.style.display !== 'none') close();
  });
}

function duplicate(store, sched) {
  // Same shape, no id (server generates one), default to one hour later
  // to avoid visual stacking on the same start time.
  const [sh, sm] = (sched.startTime || '09:00').split(':').map(Number);
  const [eh, em] = (sched.endTime   || '10:00').split(':').map(Number);
  const startMin = sh * 60 + sm + 60;
  const endMin   = eh * 60 + em + 60;
  const newStart = `${String(Math.min(23, Math.floor(startMin / 60))).padStart(2,'0')}:${String(startMin % 60).padStart(2,'0')}`;
  const newEnd   = `${String(Math.min(23, Math.floor(endMin   / 60))).padStart(2,'0')}:${String(endMin   % 60).padStart(2,'0')}`;
  store.createSchedule({
    playlistName: sched.playlistName,
    displayID: sched.displayID,
    freq: sched.freq, interval: sched.interval,
    byweekday: [...(sched.byweekday || [])],
    dtstart: sched.dtstart, end: { ...(sched.end || { type: 'never' }) },
    startTime: newStart, endTime: newEnd,
    priority: sched.priority,
  }).catch(() => {});
}

function deleteOne(store, scheduleId) {
  store.deleteSchedule(scheduleId).catch(() => {});
}
```

- [ ] **Step 2: Remove the old right-click handler from `select.js`**

In `js/timeline/select.js`, delete the `contextmenu` listener block (the `confirm('Delete this schedule?')` path). The context menu now owns this.

- [ ] **Step 3: Wire into index.js + smoke test**

```javascript
// js/timeline/index.js
import { attachContextMenu } from './context-menu.js';
// ...
attachContextMenu(store);
```

Add `'js/timeline/context-menu.js'` to MODULES list.

Run: `node --test tests/unit/js/test_timeline_smoke.js`
Expected: PASS.

- [ ] **Step 4: MCP-verify**

Right-click a clip → menu appears with 4 actionable items + 1 divider. Click Delete → schedule removed. Right-click near the right edge of the viewport → menu clamps to stay on-screen.

- [ ] **Step 5: Commit**

```bash
git add js/timeline/context-menu.js js/timeline/select.js js/timeline/index.js tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): right-click context menu on clips

Edit schedule, Edit playlist items, Duplicate, Delete. Replaces
PR-4b T9's confirm-then-delete prompt. Menu clamps to viewport so it
never opens off-screen. Click outside / Esc / picking an item all
dismiss the menu.

Duplicate offsets startTime+endTime by 1 hour to avoid overlap on
the same track.

PR-4c T-A5."
```

---

### Task A6: Phase A e2e specs

**Files:**
- Create: `tests/e2e/test-recurrence-modal.spec.js`
- Create: `tests/e2e/test-context-menu.spec.js`
- Create: `tests/e2e/test-conflict-412.spec.js`

- [ ] **Step 1: `test-recurrence-modal.spec.js`**

```javascript
import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, createTestPlaylist, deleteScheduleByPlaylist, deletePlaylist, seedSchedule, cleanupE2eOrphans } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const PLAYLIST = '__e2e_rec_' + Date.now();
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);
    await createTestPlaylist(page, PLAYLIST);
    await seedSchedule(page, { playlistName: PLAYLIST, displayID: 'Mobile', startTime: '09:00', endTime: '10:00' });
    await page.reload(); await waitForHydrated(page);

    const scheduleId = await page.evaluate(
      (pn) => Alpine.store('mm').schedules.find(s => s.playlistName === pn)?.id, PLAYLIST);
    // Open via synthetic Alt+click (HTML5 click event with altKey).
    await page.evaluate((sid) => {
      const clip = document.querySelector(`.mm-clip[data-schedule-id="${sid}"]`);
      clip.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, altKey: true }));
    }, scheduleId);
    await page.waitForSelector('.mm-modal', { timeout: 5000 });

    // Change freq to WEEKLY, check Mon/Wed/Fri, change interval to 2.
    await page.evaluate(() => {
      const root = document.querySelector('.mm-modal');
      const sel = root.querySelector('[data-field="freq"]');
      sel.value = 'WEEKLY'; sel.dispatchEvent(new Event('change', { bubbles: true }));
      root.querySelector('[data-field="interval"]').value = '2';
      root.querySelectorAll('[data-field="byweekday"] input').forEach(cb => {
        cb.checked = ['0','2','4'].includes(cb.value);
      });
      root.querySelector('[data-action="save"]').click();
    });

    await page.waitForFunction(
      (pn) => {
        const s = Alpine.store('mm').schedules.find(x => x.playlistName === pn);
        return s && s.freq === 'WEEKLY' && s.interval === 2 && (s.byweekday || []).length === 3;
      }, PLAYLIST, { timeout: 5000 });
    await deleteScheduleByPlaylist(page, PLAYLIST);
    await deletePlaylist(page, PLAYLIST);
    return 'pass';
  } finally { await browser.close(); }
}
```

- [ ] **Step 2: `test-context-menu.spec.js`**

```javascript
import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, createTestPlaylist, deletePlaylist, seedSchedule, cleanupE2eOrphans } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const PLAYLIST = '__e2e_ctx_' + Date.now();
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);
    await createTestPlaylist(page, PLAYLIST);
    await seedSchedule(page, { playlistName: PLAYLIST, displayID: 'Mobile', startTime: '15:00', endTime: '16:00' });
    await page.reload(); await waitForHydrated(page);

    const scheduleId = await page.evaluate(
      (pn) => Alpine.store('mm').schedules.find(s => s.playlistName === pn)?.id, PLAYLIST);
    // Synthetic contextmenu event on the clip.
    await page.evaluate((sid) => {
      const clip = document.querySelector(`.mm-clip[data-schedule-id="${sid}"]`);
      const r = clip.getBoundingClientRect();
      clip.dispatchEvent(new MouseEvent('contextmenu', {
        bubbles: true, cancelable: true, clientX: r.left + 5, clientY: r.top + 5,
      }));
    }, scheduleId);
    await page.waitForSelector('.mm-context-menu li:not(.mm-context-divider)', { timeout: 5000 });

    // Find + click the Delete item.
    await page.evaluate(() => {
      const items = Array.from(document.querySelectorAll('.mm-context-menu li'));
      const del = items.find(li => li.textContent.trim() === 'Delete');
      del.click();
    });
    await page.waitForFunction(
      (pn) => !Alpine.store('mm').schedules.find(x => x.playlistName === pn),
      PLAYLIST, { timeout: 5000 });
    await deletePlaylist(page, PLAYLIST);
    return 'pass';
  } finally { await browser.close(); }
}
```

- [ ] **Step 3: `test-conflict-412.spec.js`**

```javascript
import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, createTestPlaylist, deleteScheduleByPlaylist, deletePlaylist, seedSchedule, cleanupE2eOrphans } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const PLAYLIST = '__e2e_412_' + Date.now();
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);
    await createTestPlaylist(page, PLAYLIST);
    await seedSchedule(page, { playlistName: PLAYLIST, displayID: 'Mobile', startTime: '09:00', endTime: '10:00' });
    await page.reload(); await waitForHydrated(page);

    const scheduleId = await page.evaluate(
      (pn) => Alpine.store('mm').schedules.find(s => s.playlistName === pn)?.id, PLAYLIST);

    // Out-of-band edit the schedule (bumps _serverVersion).
    await page.evaluate(async (sid) => {
      const fresh = await (await fetch('/api/schedules/' + sid)).json();
      await fetch('/api/schedules/' + sid, {
        method: 'PUT', headers: { 'Content-Type': 'application/json', 'If-Match': String(fresh._serverVersion) },
        body: JSON.stringify({ startTime: '11:00', endTime: '12:00' }),
      });
    }, scheduleId);

    // Now the store still holds the old version. Try to update — should 412.
    await page.evaluate(async (sid) => {
      try { await Alpine.store('mm').updateSchedule(sid, { startTime: '14:00', endTime: '15:00' }); }
      catch (_) {}
    }, scheduleId);
    // After refetch, the schedule should be at 11:00 (the OOB edit), NOT 14:00.
    await page.waitForFunction((sid) => {
      const s = Alpine.store('mm').schedules.find(x => x.id === sid);
      return s && s.startTime === '11:00';
    }, scheduleId, { timeout: 5000 });
    // Toast should mention 'another admin'.
    const sawToast = await page.evaluate(
      () => Alpine.store('mm').toasts.some(t => /another admin/.test(t.msg)));
    assert.ok(sawToast, 'expected "another admin" toast after 412');

    await deleteScheduleByPlaylist(page, PLAYLIST);
    await deletePlaylist(page, PLAYLIST);
    return 'pass';
  } finally { await browser.close(); }
}
```

- [ ] **Step 4: Run the full e2e suite**

```bash
node tests/e2e/run.js
```
Expected: 7 pass / 0 fail (4 from PR-4b + 3 new).

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test-recurrence-modal.spec.js tests/e2e/test-context-menu.spec.js tests/e2e/test-conflict-412.spec.js
git commit -m "test(e2e): recurrence modal + context menu + 412 refetch

Three new specs covering Phase A interactions:
- test-recurrence-modal: Alt+click clip -> modal -> WEEKLY +
  byweekday + interval=2 + Save -> schedule patched.
- test-context-menu:     right-click clip -> menu -> Delete ->
  schedule removed.
- test-conflict-412:     out-of-band PUT bumps server version ->
  next store.updateSchedule 412s -> refetch-merge.js pulls the OOB
  version + emits 'another admin' toast.

PR-4c T-A6 (Phase A complete)."
```

---

## Phase B: Playlist-item editor

One modal, one task (small surface — single form, one PUT).

### Task B1: `modals/playlist-editor.js` + wiring + e2e

**Files:**
- Create: `js/timeline/modals/playlist-editor.js`
- Modify: `js/timeline/drill-in.js` — single-click on a drilled item opens the editor
- Modify: `js/timeline/index.js` — `attachPlaylistEditor(store)`
- Modify: `tests/unit/js/test_timeline_smoke.js`
- Create: `tests/e2e/test-playlist-editor.spec.js`

- [ ] **Step 1: Create the modal**

```javascript
// js/timeline/modals/playlist-editor.js
/**
 * Per-item editor for a playlist item. Currently we let operators
 * tweak playmode (loop/once), backgroundColor (hex/CSS), and an
 * optional duration override that wins over the file's own video
 * length.
 *
 * The modal edits a single item — not the whole playlist — but Save
 * issues a single PUT /api/playlists/{name} with the full items array
 * (the server has no per-item endpoint). withRollback handles the
 * optimistic + 412-refetch dance.
 *
 * Called by:
 *   - context-menu Edit playlist items (opens at the FIRST item; user
 *     can switch via the dropdown inside the modal)
 *   - drill-in single-click on a .mm-drillin-item
 */
import { openModal, closeModal } from './modal-shell.js';

export function attachPlaylistEditor(store) {
  // Single-click on a drilled-in item opens the editor at that item.
  document.addEventListener('click', (ev) => {
    const item = ev.target.closest('.mm-drillin-item');
    if (!item) return;
    const row = item.closest('.mm-drillin-row');
    if (!row) return;
    ev.preventDefault();
    ev.stopPropagation();
    const playlistName = row.dataset.playlistName;
    const itemIndex = Number(item.dataset.itemIndex || 0);
    openPlaylistEditor(store, playlistName, itemIndex);
  }, true);
}

export function openPlaylistEditor(store, playlistName, initialIndex = 0) {
  const pl = store.playlists[playlistName];
  if (!pl) return;
  const items = (pl.items || []).slice();   // shallow draft; modal mutates copies
  if (items.length === 0) {
    store.toast(`Playlist "${playlistName}" has no items to edit.`, 'info');
    return;
  }
  let idx = Math.min(Math.max(0, initialIndex), items.length - 1);

  const root = document.createElement('div');
  root.innerHTML = `
    <label>Item
      <select data-field="itemPicker"></select>
    </label>
    <div class="mm-form-grid">
      <label>File <input type="text" data-field="file" disabled></label>
      <label>Play mode
        <select data-field="playmode">
          <option value="loop">Loop</option>
          <option value="once">Play once</option>
        </select>
      </label>
      <label>Background color <input type="text" data-field="backgroundColor" placeholder="#000000 or rgb(0,0,0)"></label>
      <label>Duration override (s) <input type="number" data-field="duration" min="0" step="0.1" placeholder="auto"></label>
    </div>
    <div class="mm-form-actions">
      <button type="button" class="btn btn-ghost" data-action="cancel">Cancel</button>
      <button type="button" class="btn btn-primary" data-action="save">Save</button>
    </div>
  `;
  const picker = root.querySelector('[data-field="itemPicker"]');
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    const file = (typeof it === 'string') ? it : (it.file || '');
    const opt = document.createElement('option');
    opt.value = String(i);
    opt.textContent = `${i + 1}. ${basename(file)}`;
    picker.appendChild(opt);
  }
  picker.value = String(idx);

  function asObject(it) { return (typeof it === 'string') ? { file: it } : { ...it }; }

  function loadItem() {
    const it = asObject(items[idx]);
    root.querySelector('[data-field="file"]').value = it.file || '';
    root.querySelector('[data-field="playmode"]').value = it.playmode || 'loop';
    root.querySelector('[data-field="backgroundColor"]').value = it.backgroundColor || '';
    root.querySelector('[data-field="duration"]').value = (it.duration == null) ? '' : String(it.duration);
  }

  function captureItem() {
    const draft = asObject(items[idx]);
    draft.playmode = root.querySelector('[data-field="playmode"]').value;
    const bg = root.querySelector('[data-field="backgroundColor"]').value.trim();
    if (bg) draft.backgroundColor = bg; else delete draft.backgroundColor;
    const dur = root.querySelector('[data-field="duration"]').value.trim();
    if (dur) draft.duration = Number(dur); else delete draft.duration;
    items[idx] = draft;
  }

  picker.addEventListener('change', () => { captureItem(); idx = Number(picker.value); loadItem(); });

  openModal({ title: `Edit items — ${playlistName}`, contentEl: root });

  root.querySelector('[data-action="cancel"]').addEventListener('click', () => closeModal());
  root.querySelector('[data-action="save"]').addEventListener('click', async () => {
    captureItem();
    try {
      await store.updatePlaylist(playlistName, { items });
      closeModal();
    } catch (_) { /* toast via withRollback */ }
  });
  loadItem();
}

function basename(p) { return String(p || '').split('/').pop() || ''; }
```

- [ ] **Step 2: Wire into index.js + smoke**

```javascript
// js/timeline/index.js
import { attachPlaylistEditor } from './modals/playlist-editor.js';
// ...
attachPlaylistEditor(store);
```

Add to MODULES list.

Run: `node --test tests/unit/js/test_timeline_smoke.js`
Expected: PASS.

- [ ] **Step 3: e2e spec `tests/e2e/test-playlist-editor.spec.js`**

```javascript
import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, createTestPlaylist, deleteScheduleByPlaylist, deletePlaylist, seedSchedule, cleanupE2eOrphans } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const PLAYLIST = '__e2e_ple_' + Date.now();
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);
    await createTestPlaylist(page, PLAYLIST);
    await seedSchedule(page, { playlistName: PLAYLIST, displayID: 'Mobile', startTime: '09:00', endTime: '12:00' });
    await page.reload(); await waitForHydrated(page);

    const scheduleId = await page.evaluate(
      (pn) => Alpine.store('mm').schedules.find(s => s.playlistName === pn)?.id, PLAYLIST);
    // Drill in.
    await page.evaluate((sid) => {
      const clip = document.querySelector(`.mm-clip[data-schedule-id="${sid}"]`);
      clip.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }));
    }, scheduleId);
    await page.waitForSelector('.mm-drillin-item', { timeout: 5000 });

    // Click the first item -> editor opens.
    await page.evaluate(() => {
      const it = document.querySelector('.mm-drillin-item');
      it.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    });
    await page.waitForSelector('.mm-modal', { timeout: 5000 });

    // Set backgroundColor + Save.
    await page.evaluate(() => {
      const root = document.querySelector('.mm-modal');
      root.querySelector('[data-field="backgroundColor"]').value = '#123456';
      root.querySelector('[data-action="save"]').click();
    });
    await page.waitForFunction(
      (pn) => {
        const pl = Alpine.store('mm').playlists[pn];
        return pl && pl.items && pl.items[0] && pl.items[0].backgroundColor === '#123456';
      }, PLAYLIST, { timeout: 5000 });

    await deleteScheduleByPlaylist(page, PLAYLIST);
    await deletePlaylist(page, PLAYLIST);
    return 'pass';
  } finally { await browser.close(); }
}
```

- [ ] **Step 4: Run e2e suite (full)**

```bash
node tests/e2e/run.js
```
Expected: 8 pass / 0 fail.

- [ ] **Step 5: MCP-verify**

Drill into a clip. Single-click an item → modal opens. Switch via dropdown to the second item — the fields update. Edit backgroundColor + save → re-drill-in confirms the change applied.

- [ ] **Step 6: Commit**

```bash
git add js/timeline/modals/playlist-editor.js js/timeline/index.js tests/unit/js/test_timeline_smoke.js tests/e2e/test-playlist-editor.spec.js
git commit -m "feat(timeline/modals): playlist-editor.js — per-item form

Single-click an item in the drilled-in sub-track to open the editor.
Form: playmode (loop/once), backgroundColor, duration override.
Item dropdown lets the operator switch within the same modal — Save
captures the current draft before switching, so multi-item edits
in one open are possible.

Save PUTs the full items array (server has no per-item endpoint),
optimistic via store.updatePlaylist which inherits the 412 refetch
path.

PR-4c T-B1 (Phase B complete)."
```

---

## Phase C: Profile editor (3-pane)

The heaviest single piece. Split across 4 tasks: data access, shell layout, list/form panes, preview pane.

### Task C1: store accessors for profiles

**Files:**
- Modify: `js/timeline/store.js`

- [ ] **Step 1: Add `createProfile`, `updateProfile`, `deleteProfile`, `assignProfileToClient`**

```javascript
// In js/timeline/store.js, alongside the other mutation methods:

    async createProfile(profile) {
      const { withRollback } = await import('./util/optimistic.js');
      return withRollback(this, ['profiles'], () => {
        // Optimistic: insert with a placeholder _serverVersion until
        // the server returns the authoritative copy.
        this.profiles[profile.name] = { ...profile, _serverVersion: 0 };
      }, async () => {
        const fresh = await this.api.createProfile(profile);
        this.profiles[fresh.name] = fresh;
      });
    },

    async updateProfile(name, patch) {
      const { withRollback } = await import('./util/optimistic.js');
      const current = this.profiles[name];
      if (!current) throw new Error(`profile not found: ${name}`);
      return withRollback(this, ['profiles'], () => {
        this.profiles[name] = { ...current, ...patch };
      }, async () => {
        const fresh = await this.api.updateProfile(name, patch, current._serverVersion);
        this.profiles[name] = fresh;
      });
    },

    async deleteProfile(name) {
      const { withRollback } = await import('./util/optimistic.js');
      return withRollback(this, ['profiles'], () => {
        delete this.profiles[name];
      }, async () => {
        await this.api.deleteProfile(name);
      });
    },

    async assignProfileToClient(clientKey, profileName) {
      const { withRollback } = await import('./util/optimistic.js');
      return withRollback(this, ['displays'], () => {
        const c = this.displays.find(d => d.clientKey === clientKey);
        if (c) c.profileName = profileName;
      }, async () => {
        await this.api.assignProfile(clientKey, profileName);
      });
    },
```

- [ ] **Step 2: Unit-test the four methods**

Append to `tests/unit/js/test_store_mutations.js`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert';
import { makeStore } from '../../../js/timeline/store.js';

test('store.createProfile happy path', async () => {
  globalThis.fetch = async (url, init) => ({
    ok: true, status: 201,
    json: async () => ({ name: 'p1', label: 'Profile 1', _serverVersion: 1 }),
  });
  const s = makeStore();
  await s.createProfile({ name: 'p1', label: 'Profile 1' });
  assert.equal(s.profiles.p1.label, 'Profile 1');
  assert.equal(s.profiles.p1._serverVersion, 1);
});

test('store.updateProfile rollback on 4xx', async () => {
  globalThis.fetch = async () => ({
    ok: false, status: 400,
    json: async () => ({ success: false, error: 'bad name' }),
  });
  const s = makeStore();
  s.profiles = { p1: { name: 'p1', label: 'orig', _serverVersion: 1 } };
  await assert.rejects(s.updateProfile('p1', { label: 'new' }));
  assert.equal(s.profiles.p1.label, 'orig');   // rolled back
});

test('store.deleteProfile removes optimistic + survives success', async () => {
  globalThis.fetch = async () => ({ ok: true, status: 204, json: async () => ({}) });
  const s = makeStore();
  s.profiles = { p1: { name: 'p1' } };
  await s.deleteProfile('p1');
  assert.equal(s.profiles.p1, undefined);
});
```

Run: `node --test tests/unit/js/test_store_mutations.js`
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add js/timeline/store.js tests/unit/js/test_store_mutations.js
git commit -m "feat(timeline/store): profile CRUD + per-client assignment

createProfile / updateProfile / deleteProfile / assignProfileToClient
all flow through withRollback so they inherit the optimistic-local +
412 refetch behaviour. The api wrappers are already in place from
PR-4b T1.

PR-4c T-C1."
```

---

### Task C2: Toolbar ⚙ + 🎯 buttons

**Files:**
- Modify: `js/timeline/toolbar.js`
- Modify: `admin.html` (mmToolbar template adds the two buttons)

- [ ] **Step 1: Add openProfileEditor + openCalibration to the component**

In `js/timeline/toolbar.js`, alongside the existing methods, add stub openers:

```javascript
// At top of toolbar.js, after the existing imports:
import { openProfileEditor } from './modals/profile-editor.js';
import { openCalibrationModal } from './modals/calibration.js';

// Inside the returned component object, add:
    openProfileEditor()  { openProfileEditor(this.$store.mm); },
    openCalibration()    { openCalibrationModal(this.$store.mm); },
```

(These call into Tasks C3-C6 and D1-D2 respectively. C2 lands the wiring; C3 lands the actual modal so the click does something.)

- [ ] **Step 2: Add the buttons to the toolbar template**

In `admin.html`, find the `x-data="mmToolbar"` block (around the Day/Week/Month buttons) and append:

```html
                <button class="btn btn-ghost" type="button" @click="openProfileEditor()" title="Edit profiles">⚙ Profiles</button>
                <button class="btn btn-ghost" type="button" @click="openCalibration()" title="Display calibration">🎯 Calibrate</button>
```

- [ ] **Step 3: Stub the modal modules so the imports resolve**

Until C3-C6 land, create placeholder files so toolbar.js's static imports don't break:

```javascript
// js/timeline/modals/profile-editor.js
export function openProfileEditor(store) {
  store.toast('Profile editor — coming in T-C3', 'info');
}
```

```javascript
// js/timeline/modals/calibration.js
export function openCalibrationModal(store) {
  store.toast('Calibration modal — coming in T-D1', 'info');
}
```

Add both to the smoke MODULES list.

Run: `node --test tests/unit/js/test_timeline_smoke.js`
Expected: PASS.

- [ ] **Step 4: MCP-verify**

Open admin → Timeline. Toolbar should show ⚙ Profiles + 🎯 Calibrate buttons. Clicking each → toast appears (since the modal modules are still stubs).

- [ ] **Step 5: Commit**

```bash
git add js/timeline/toolbar.js js/timeline/modals/profile-editor.js js/timeline/modals/calibration.js admin.html tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline/toolbar): + Profiles + Calibrate buttons

Two toolbar buttons that open the new modals (T-C3 + T-D1 land the
actual modal bodies). Stub openers in place so the wiring is
testable now.

PR-4c T-C2."
```

---

### Task C3: Profile editor shell (3-pane layout)

**Files:**
- Modify: `js/timeline/modals/profile-editor.js` (replace stub with real 3-pane shell)
- Modify: `admin.html` — add 3-pane CSS

- [ ] **Step 1: Write the shell**

```javascript
// js/timeline/modals/profile-editor.js
/**
 * 3-pane profile editor modal.
 *   Left:   profile list (scroll + New + Delete buttons)
 *   Center: profile form (name, label, matchDeviceType, 5 script
 *           textareas, launch config, webclip, ssh)
 *   Right:  preview — currently-edited script rendered through
 *           SafeDict against a selected sample client; unresolved
 *           tokens highlighted red.
 *
 * Selecting a profile loads it into the form. Editing the form mutates
 * a local DRAFT; Save commits via store.updateProfile (or createProfile
 * if it's a brand-new entry). Switching profiles before saving prompts.
 */
import { openModal, closeModal } from './modal-shell.js';

let activeUi = null;

export function openProfileEditor(store) {
  if (activeUi) { closeModal(); activeUi = null; }
  const root = document.createElement('div');
  root.className = 'mm-profile-editor';
  root.innerHTML = `
    <div class="mm-pe-list">
      <div class="mm-pe-list-actions">
        <button type="button" class="btn btn-ghost" data-action="new">+ New</button>
        <button type="button" class="btn btn-ghost mm-pe-danger" data-action="delete" disabled>Delete</button>
      </div>
      <ul class="mm-pe-profiles"></ul>
    </div>
    <div class="mm-pe-form">
      <div class="mm-pe-empty">Select a profile (or create one) to edit.</div>
    </div>
    <div class="mm-pe-preview">
      <div class="mm-pe-preview-header">
        <label>Preview against
          <select data-field="sampleClient"></select>
        </label>
        <label>Script
          <select data-field="sampleScript">
            <option value="login">login</option><option value="start">start</option>
            <option value="stop">stop</option><option value="test">test</option>
            <option value="reboot">reboot</option>
          </select>
        </label>
      </div>
      <pre class="mm-pe-preview-body" data-field="previewBody"></pre>
    </div>
  `;
  openModal({ title: 'Profiles', contentEl: root });

  activeUi = { store, root, draft: null, draftKind: null /* 'edit' | 'new' */ };
  renderProfileList(activeUi);
  populateSampleSelectors(activeUi);
  wireShellHandlers(activeUi);
}

function renderProfileList(ui) {
  const list = ui.root.querySelector('.mm-pe-profiles');
  list.innerHTML = '';
  const names = Object.keys(ui.store.profiles || {}).sort();
  for (const n of names) {
    const li = document.createElement('li');
    li.textContent = ui.store.profiles[n].label || n;
    li.dataset.name = n;
    li.addEventListener('click', () => selectProfile(ui, n));
    if (ui.draft && ui.draftKind === 'edit' && ui.draft.name === n) li.classList.add('selected');
    list.appendChild(li);
  }
}

function populateSampleSelectors(ui) {
  const clientSel = ui.root.querySelector('[data-field="sampleClient"]');
  for (const c of ui.store.displays) {
    const opt = document.createElement('option');
    opt.value = c.clientKey || c.id;
    opt.textContent = c.friendlyName || c.clientKey || c.id;
    clientSel.appendChild(opt);
  }
}

function wireShellHandlers(ui) {
  ui.root.querySelector('[data-action="new"]').addEventListener('click', () => {
    ui.draftKind = 'new';
    ui.draft = { name: '', label: '', matchDeviceType: 'Tablet',
                 scripts: { login: '', start: '', stop: '', test: '', reboot: '' },
                 launch: { method: 'ssh-then-vnc', taps: [] },
                 webclip: { bundleId: '', title: '' },
                 ssh: { legacyCrypto: true, user: 'root', keyPath: '~/.ssh/mosaic_ipad' } };
    renderForm(ui);
    refreshPreview(ui);
  });
  ui.root.querySelector('[data-action="delete"]').addEventListener('click', async () => {
    if (!ui.draft || ui.draftKind !== 'edit') return;
    const name = ui.draft.name;
    if (!confirm(`Delete profile "${name}"?`)) return;
    try {
      await ui.store.deleteProfile(name);
      ui.draft = null; ui.draftKind = null;
      renderProfileList(ui);
      ui.root.querySelector('.mm-pe-form').innerHTML = '<div class="mm-pe-empty">Select a profile to edit.</div>';
      refreshPreview(ui);
    } catch (e) { /* toast via withRollback; if 409 with refs, server's error string surfaces */ }
  });
  // C5 wires the form's own change handlers; C6 wires preview onChange.
}

function selectProfile(ui, name) {
  const src = ui.store.profiles[name];
  if (!src) return;
  ui.draftKind = 'edit';
  ui.draft = JSON.parse(JSON.stringify(src));   // deep clone so edits don't leak
  renderProfileList(ui);
  renderForm(ui);
  refreshPreview(ui);
  ui.root.querySelector('[data-action="delete"]').disabled = false;
}

// Stubs that T-C5 + T-C6 will replace with real implementations.
function renderForm(ui) {
  const formHost = ui.root.querySelector('.mm-pe-form');
  formHost.innerHTML = `<div class="mm-pe-empty">Form coming in T-C5 — editing ${ui.draft ? (ui.draft.name || '(new)') : 'nothing'}</div>`;
}
function refreshPreview(ui) {
  const out = ui.root.querySelector('[data-field="previewBody"]');
  out.textContent = ui.draft ? '(preview comes in T-C6)' : '';
}
```

- [ ] **Step 2: Add 3-pane CSS to admin.html**

Append next to the modal CSS:

```css
.mm-profile-editor { display: grid; grid-template-columns: 200px 1fr 280px; gap: 10px; min-width: 900px; max-width: 1100px; min-height: 480px; }
.mm-pe-list { border-right: 1px solid rgba(255,255,255,0.08); padding-right: 8px; display: flex; flex-direction: column; gap: 6px; }
.mm-pe-list-actions { display: flex; gap: 4px; }
.mm-pe-profiles { list-style: none; margin: 0; padding: 0; overflow-y: auto; flex: 1; font-size: 12px; }
.mm-pe-profiles li { padding: 6px 8px; cursor: pointer; border-radius: 3px; }
.mm-pe-profiles li:hover { background: rgba(255,255,255,0.06); }
.mm-pe-profiles li.selected { background: rgba(120,170,240,0.18); }
.mm-pe-form { overflow-y: auto; padding-right: 8px; min-width: 0; }
.mm-pe-empty { color: var(--text-muted, #888); padding: 12px; font-size: 12px; }
.mm-pe-preview { border-left: 1px solid rgba(255,255,255,0.08); padding-left: 8px; display: flex; flex-direction: column; gap: 6px; }
.mm-pe-preview-header { display: flex; flex-direction: column; gap: 4px; font-size: 11px; }
.mm-pe-preview-body { flex: 1; background: rgba(0,0,0,0.3); padding: 8px; font-size: 11px; white-space: pre-wrap; word-break: break-all; overflow-y: auto; min-height: 200px; }
.mm-pe-danger { color: var(--err, #f88); }
.mm-pe-unresolved { color: var(--err, #f88); font-weight: bold; }
```

- [ ] **Step 3: MCP-verify**

Click ⚙ Profiles in toolbar → modal opens with 3-pane layout. Left pane shows existing profile (`ipad1-ios5`). Click it → form pane shows the placeholder. Click + New → form pane updates. Sample client dropdown populated with current displays.

- [ ] **Step 4: Commit**

```bash
git add js/timeline/modals/profile-editor.js admin.html
git commit -m "feat(timeline/modals): profile-editor shell (3-pane layout)

Modal opens with the existing profiles in the left list, an empty
form host in the center, and a preview pane on the right with
sample-client + sample-script selectors. Selecting a profile clones
it into a local draft. + New seeds a blank draft. Delete + confirm
calls store.deleteProfile (server returns 409+refs if assigned —
withRollback shows the server's error string).

Form pane (T-C5) + preview pane (T-C6) land next.

PR-4c T-C3."
```

---

### Task C4: Profile-editor form pane

**Files:**
- Modify: `js/timeline/modals/profile-editor.js` (replace `renderForm` stub)

- [ ] **Step 1: Implement `renderForm` with all fields**

Replace the `renderForm` function with:

```javascript
function renderForm(ui) {
  const formHost = ui.root.querySelector('.mm-pe-form');
  if (!ui.draft) { formHost.innerHTML = '<div class="mm-pe-empty">Select a profile to edit.</div>'; return; }
  const d = ui.draft;
  formHost.innerHTML = `
    <div class="mm-form-grid">
      <label>Name <input type="text" data-field="name" value="${escapeAttr(d.name)}" ${ui.draftKind === 'edit' ? 'disabled' : ''}></label>
      <label>Label <input type="text" data-field="label" value="${escapeAttr(d.label || '')}"></label>
      <label>Match device type
        <select data-field="matchDeviceType">
          ${['Tablet','Mobile','Desktop','Default'].map(t =>
            `<option value="${t}"${(d.matchDeviceType||'Tablet')===t?' selected':''}>${t}</option>`).join('')}
        </select>
      </label>
      <label>Launch method
        <select data-field="launchMethod">
          ${['shell','vnc-tap','ssh-then-vnc'].map(m =>
            `<option value="${m}"${(d.launch?.method||'ssh-then-vnc')===m?' selected':''}>${m}</option>`).join('')}
        </select>
      </label>
    </div>
    <details open><summary>Scripts</summary>
      ${['login','start','stop','test','reboot'].map(k => `
        <label class="mm-pe-script-row">
          <span>${k}</span>
          <textarea data-field="script-${k}" rows="3">${escapeText(d.scripts?.[k] || '')}</textarea>
        </label>
      `).join('')}
    </details>
    <details><summary>Launch config</summary>
      <div class="mm-form-grid">
        <label>VNC password <input type="text" data-field="vncPassword" value="${escapeAttr(d.launch?.vncPassword || '')}"></label>
        <label>Wake script <input type="text" data-field="wakeScript" value="${escapeAttr(d.launch?.wakeScript || '')}"></label>
        <label class="mm-form-row-wide" data-field="tapsRow">Taps (one fbX,fbY per line)
          <textarea data-field="taps" rows="2">${(d.launch?.taps || []).map(t => `${t.fbX},${t.fbY}`).join('\n')}</textarea>
        </label>
      </div>
    </details>
    <details><summary>Webclip</summary>
      <div class="mm-form-grid">
        <label>Bundle ID <input type="text" data-field="webclipBundleId" value="${escapeAttr(d.webclip?.bundleId || '')}"></label>
        <label>Title <input type="text" data-field="webclipTitle" value="${escapeAttr(d.webclip?.title || '')}"></label>
      </div>
    </details>
    <details><summary>SSH</summary>
      <div class="mm-form-grid">
        <label>User <input type="text" data-field="sshUser" value="${escapeAttr(d.ssh?.user || 'root')}"></label>
        <label>Key path <input type="text" data-field="sshKeyPath" value="${escapeAttr(d.ssh?.keyPath || '')}"></label>
        <label><input type="checkbox" data-field="sshLegacyCrypto"${(d.ssh?.legacyCrypto)?' checked':''}> Legacy crypto (iOS 5)</label>
      </div>
    </details>
    <div class="mm-form-actions">
      <button type="button" class="btn btn-ghost" data-action="cancel-form">Discard changes</button>
      <button type="button" class="btn btn-primary" data-action="save-form">Save</button>
    </div>
  `;
  // Show/hide taps row based on launch method.
  function updateLaunchVisibility() {
    const m = formHost.querySelector('[data-field="launchMethod"]').value;
    formHost.querySelector('[data-field="tapsRow"]').style.display = (m === 'shell') ? 'none' : '';
  }
  updateLaunchVisibility();
  // Wire change handlers — capture into draft on every input, refresh preview.
  formHost.addEventListener('input', () => { captureForm(ui); refreshPreview(ui); });
  formHost.addEventListener('change', () => { captureForm(ui); refreshPreview(ui); updateLaunchVisibility(); });
  formHost.querySelector('[data-action="cancel-form"]').addEventListener('click', () => {
    if (ui.draftKind === 'edit') selectProfile(ui, ui.draft.name);
    else { ui.draft = null; ui.draftKind = null; renderProfileList(ui); renderForm(ui); refreshPreview(ui); }
  });
  formHost.querySelector('[data-action="save-form"]').addEventListener('click', async () => {
    captureForm(ui);
    if (!ui.draft.name.trim()) { ui.store.toast('Name is required.', 'error'); return; }
    try {
      if (ui.draftKind === 'new') {
        await ui.store.createProfile(ui.draft);
        ui.draftKind = 'edit';
      } else {
        await ui.store.updateProfile(ui.draft.name, ui.draft);
      }
      renderProfileList(ui);
    } catch (_) { /* toast via withRollback */ }
  });
}

function captureForm(ui) {
  const f = (sel) => ui.root.querySelector(sel);
  if (!ui.draft) return;
  const d = ui.draft;
  if (ui.draftKind === 'new') d.name = f('[data-field="name"]').value.trim();
  d.label = f('[data-field="label"]').value;
  d.matchDeviceType = f('[data-field="matchDeviceType"]').value;
  d.scripts = d.scripts || {};
  for (const k of ['login','start','stop','test','reboot']) {
    d.scripts[k] = f(`[data-field="script-${k}"]`).value;
  }
  d.launch = d.launch || {};
  d.launch.method = f('[data-field="launchMethod"]').value;
  d.launch.vncPassword = f('[data-field="vncPassword"]').value || undefined;
  d.launch.wakeScript = f('[data-field="wakeScript"]').value || undefined;
  d.launch.taps = f('[data-field="taps"]').value.split('\n')
    .map(s => s.trim()).filter(Boolean)
    .map(s => { const [x, y] = s.split(',').map(n => Number(n.trim())); return { fbX: x, fbY: y }; })
    .filter(t => Number.isFinite(t.fbX) && Number.isFinite(t.fbY));
  d.webclip = d.webclip || {};
  d.webclip.bundleId = f('[data-field="webclipBundleId"]').value || undefined;
  d.webclip.title = f('[data-field="webclipTitle"]').value || undefined;
  d.ssh = d.ssh || {};
  d.ssh.user = f('[data-field="sshUser"]').value || 'root';
  d.ssh.keyPath = f('[data-field="sshKeyPath"]').value || undefined;
  d.ssh.legacyCrypto = !!f('[data-field="sshLegacyCrypto"]').checked;
}

function escapeText(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function escapeAttr(s) { return escapeText(s).replace(/"/g,'&quot;'); }
```

- [ ] **Step 2: Form CSS additions**

```css
.mm-pe-script-row { display: grid; grid-template-columns: 50px 1fr; gap: 6px; align-items: center; margin-bottom: 4px; font-size: 11px; }
.mm-pe-script-row textarea { font-family: monospace; font-size: 11px; padding: 4px; box-sizing: border-box; width: 100%; }
.mm-profile-editor details { margin-bottom: 8px; }
.mm-profile-editor summary { cursor: pointer; font-size: 12px; font-weight: 600; padding: 4px 0; }
.mm-form-row-wide { grid-column: 1 / -1; display: flex; flex-direction: column; gap: 4px; }
```

- [ ] **Step 3: MCP-verify**

Open profile editor, click `ipad1-ios5` → form populates with all five script textareas + launch + webclip + ssh sections. Edit the label → toggle launch method → save → list refreshes with new label.

- [ ] **Step 4: Commit**

```bash
git add js/timeline/modals/profile-editor.js admin.html
git commit -m "feat(timeline/modals): profile-editor form pane

Full editor for ScriptingProfile: name, label, matchDeviceType,
launch method, 5 script textareas, launch config (with method-aware
taps row), webclip, ssh. Edit captures into a local draft on every
change; Save calls createProfile / updateProfile via the store's
withRollback path.

The 'name' field is disabled when editing an existing profile (the
key is immutable — rename = delete + create).

PR-4c T-C4."
```

---

### Task C5: Profile-editor preview pane

**Files:**
- Modify: `js/timeline/modals/profile-editor.js` (replace `refreshPreview` stub)

- [ ] **Step 1: Implement template-variable preview**

Replace `refreshPreview` with:

```javascript
function refreshPreview(ui) {
  const out = ui.root.querySelector('[data-field="previewBody"]');
  if (!ui.draft) { out.textContent = ''; return; }
  const clientKey = ui.root.querySelector('[data-field="sampleClient"]').value;
  const scriptKey = ui.root.querySelector('[data-field="sampleScript"]').value;
  const client = ui.store.displays.find(d => (d.clientKey || d.id) === clientKey);
  const template = (ui.draft.scripts && ui.draft.scripts[scriptKey]) || '';
  const vars = buildPreviewVars(client, ui.draft);
  // Render the template with vars; mark unresolved {tokens} in red.
  const html = template.replace(/\{([a-zA-Z_]\w*)\}/g, (full, key) => {
    if (key in vars) return escapeText(String(vars[key]));
    return `<span class="mm-pe-unresolved">${escapeText(full)}</span>`;
  }).replace(/\n/g, '<br>');
  // Switch from textContent to innerHTML because we hand-build the
  // highlighted spans. escapeText is applied to user-supplied values
  // above so this is safe.
  out.innerHTML = html;
}

function buildPreviewVars(client, draft) {
  // Mirror of mosaicmesh.template_vars.SafeDict + build_vars (the
  // server-side substitution). Keep the keys in sync — see
  // mosaicmesh/template_vars.py for the canonical list.
  const v = {};
  if (client) {
    v.ip = client.ip || client.address || '';
    v.clientKey = client.clientKey || client.id || '';
    v.friendlyName = client.friendlyName || '';
    v.displayUrl = client.displayUrl || (window.location.origin + '/');
  }
  if (draft.webclip) {
    if (draft.webclip.bundleId) v.webclipBundleId = draft.webclip.bundleId;
    if (draft.webclip.title)    v.webclipTitle    = draft.webclip.title;
  }
  return v;
}

// Add the sample-client / sample-script change handlers (place inside
// openProfileEditor right after populateSampleSelectors):
function wirePreviewHandlers(ui) {
  ui.root.querySelector('[data-field="sampleClient"]').addEventListener('change', () => refreshPreview(ui));
  ui.root.querySelector('[data-field="sampleScript"]').addEventListener('change', () => refreshPreview(ui));
}
```

Update `openProfileEditor` to call `wirePreviewHandlers(activeUi)` after `populateSampleSelectors`.

- [ ] **Step 2: MCP-verify**

Open profile editor → select `ipad1-ios5` → preview pane shows the `login` script with `{webclipBundleId}` resolved. Switch the sample-script dropdown to `start` → preview re-renders with `{displayUrl}` resolved. Edit a script to include `{nonexistent}` → that token shows in red in the preview.

- [ ] **Step 3: Commit**

```bash
git add js/timeline/modals/profile-editor.js
git commit -m "feat(timeline/modals): profile-editor preview pane

Live-renders the currently-edited script through a template-variable
table built from the selected sample client + the profile's own
webclip fields. Unresolved {tokens} render in red so operators see
which variables aren't reachable at runtime.

Mirrors the server-side substitution surface from
mosaicmesh/template_vars.py — keep the var-name lists in sync.

PR-4c T-C5 (Phase C complete)."
```

---

### Task C6: Profile-editor e2e spec

**Files:**
- Create: `tests/e2e/test-profile-editor.spec.js`

- [ ] **Step 1: Write the spec**

```javascript
import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, cleanupE2eOrphans } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);

    // Open Profiles modal.
    await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Profiles'));
      btn.click();
    });
    await page.waitForSelector('.mm-profile-editor', { timeout: 5000 });

    // The default ipad1-ios5 profile should be in the list.
    const has = await page.evaluate(
      () => !!Array.from(document.querySelectorAll('.mm-pe-profiles li')).find(li => li.textContent.includes('iPad 1')));
    assert.ok(has, 'expected ipad1-ios5 in profile list');

    // Select it -> form populates -> change label -> save.
    await page.evaluate(() => {
      const li = Array.from(document.querySelectorAll('.mm-pe-profiles li')).find(l => l.textContent.includes('iPad 1'));
      li.click();
    });
    await page.waitForSelector('[data-field="label"]', { timeout: 5000 });
    const NEW_LABEL = '__e2e_label_' + Date.now();
    await page.evaluate((lbl) => {
      const root = document.querySelector('.mm-profile-editor');
      const inp = root.querySelector('[data-field="label"]');
      inp.value = lbl;
      inp.dispatchEvent(new Event('input', { bubbles: true }));
      root.querySelector('[data-action="save-form"]').click();
    }, NEW_LABEL);

    await page.waitForFunction(
      (lbl) => Alpine.store('mm').profiles['ipad1-ios5']?.label === lbl,
      NEW_LABEL, { timeout: 5000 });

    // Revert so we don't leave a noisy label on the server.
    await page.evaluate(async () => {
      const p = Alpine.store('mm').profiles['ipad1-ios5'];
      await Alpine.store('mm').updateProfile('ipad1-ios5', { ...p, label: 'iPad 1 — iOS 5.1.1' });
    });
    return 'pass';
  } finally { await browser.close(); }
}
```

- [ ] **Step 2: Run e2e suite**

```bash
node tests/e2e/run.js
```
Expected: 9 pass / 0 fail.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test-profile-editor.spec.js
git commit -m "test(e2e): profile editor modal opens + saves a label edit

Smokes the wiring: toolbar -> modal -> profile list -> form -> Save
-> store.profiles[name] updated. The script editor + launch config
edges are exercised at the unit level (store.createProfile /
updateProfile tests in T-C1).

Test reverts the label edit so a noisy '__e2e_label_*' string
doesn't linger on the dev server.

PR-4c T-C6 (Phase C complete)."
```

---

## Phase D: Calibration modal

Relocates the existing flow — no new server code, no behaviour change for the underlying ArUco pipeline. Mostly DOM-pluming + reusing the modal shell.

### Task D1: `modals/calibration.js` — real modal body

**Files:**
- Modify: `js/timeline/modals/calibration.js` (replace stub)

- [ ] **Step 1: Implement the modal**

```javascript
// js/timeline/modals/calibration.js
/**
 * Display calibration modal. Three steps stacked vertically:
 *   1. Pick a display group from a dropdown.
 *   2. Click 'Generate ArUco' — sends GENERATEARUCO websocket request
 *      so each device shows its unique marker.
 *   3. Upload a wall photo. POST /upload/calibrate, surface the result
 *      ("Detected N markers" or "Found 0 — check lighting").
 *
 * No new server code — the websocket request type already exists in
 * mosaicmesh.websocket.legacy.msg_response, and /upload/calibrate is
 * handled by mosaicmesh.api.media. The existing Displays page UI
 * stays for now; PR-6 (spec) deletes it.
 */
import { openModal, closeModal } from './modal-shell.js';

export function openCalibrationModal(store) {
  const root = document.createElement('div');
  root.className = 'mm-calibration';
  const groups = Array.from(new Set(store.displays.map(d => d.displayID).filter(Boolean))).sort();
  root.innerHTML = `
    <ol class="steps">
      <li><span class="num">1</span>
        <label>Display group
          <select data-field="group">
            ${groups.map(g => `<option value="${escapeAttr(g)}">${escapeAttr(g)}</option>`).join('')}
          </select>
        </label>
      </li>
      <li><span class="num">2</span>
        <button type="button" class="btn btn-primary" data-action="generate">Generate ArUco on selected group</button>
        <span class="mm-calibration-status" data-field="generateStatus"></span>
      </li>
      <li><span class="num">3</span>
        <label>Upload wall photo
          <input type="file" accept="image/*" data-field="photo">
        </label>
      </li>
    </ol>
    <div class="mm-calibration-result" data-field="result"></div>
    <div class="mm-form-actions">
      <button type="button" class="btn btn-ghost" data-action="close">Close</button>
    </div>
  `;
  openModal({ title: 'Display calibration', contentEl: root });

  root.querySelector('[data-action="close"]').addEventListener('click', () => closeModal());

  root.querySelector('[data-action="generate"]').addEventListener('click', () => {
    const group = root.querySelector('[data-field="group"]').value;
    if (!group) return;
    const status = root.querySelector('[data-field="generateStatus"]');
    status.textContent = 'Sending GENERATEARUCO…';
    // The existing SockJS plumbing exposes a sock global; reuse it so
    // we don't recreate a connection just for this one message.
    try {
      if (typeof window.sock !== 'undefined' && typeof window.generateMessage === 'function') {
        window.sock.send(window.generateMessage('SRV', 'GENERATEARUCO', { id: group }));
        status.textContent = `Markers requested for ${group}. Photograph and upload below.`;
      } else {
        // Fallback: REST-only path doesn't exist for this — surface the
        // mismatch instead of silently failing.
        status.textContent = 'SockJS not available; reload the page.';
      }
    } catch (e) {
      status.textContent = 'Failed to send: ' + (e?.message || e);
    }
  });

  root.querySelector('[data-field="photo"]').addEventListener('change', async (ev) => {
    const file = ev.target.files?.[0];
    if (!file) return;
    const out = root.querySelector('[data-field="result"]');
    out.textContent = 'Uploading + detecting…';
    const fd = new FormData();
    fd.append('file', file, file.name);
    try {
      const r = await fetch('/upload/calibrate', { method: 'POST', body: fd });
      const body = await r.json().catch(() => ({}));
      if (r.ok && body.success !== false) {
        const n = body.detected ?? body.markers ?? '?';
        out.textContent = `Detected ${n} markers.`;
        store.toast(`Calibration: detected ${n} markers.`, 'info');
      } else {
        out.textContent = body.error || 'Calibration failed.';
      }
    } catch (e) {
      out.textContent = 'Upload failed: ' + (e?.message || e);
    }
  });
}

function escapeAttr(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;'); }
```

- [ ] **Step 2: CSS for the modal**

```css
.mm-calibration .steps { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 10px; }
.mm-calibration .steps li { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.mm-calibration .steps .num { width: 22px; height: 22px; border-radius: 50%; background: var(--accent, #345); display: inline-flex; align-items: center; justify-content: center; font-weight: 600; font-size: 11px; flex-shrink: 0; }
.mm-calibration-status, .mm-calibration-result { font-size: 11px; color: var(--text-muted, #888); }
.mm-calibration-result { margin: 10px 0; padding: 8px; background: rgba(0,0,0,0.2); border-radius: 4px; }
```

- [ ] **Step 3: MCP-verify**

Click 🎯 Calibrate. Modal opens with the three-step list + group dropdown populated from current displays. Clicking Generate sets a status string (the actual ArUco generation may not visible in MCP without iPads but the message should send without error).

- [ ] **Step 4: Commit**

```bash
git add js/timeline/modals/calibration.js admin.html
git commit -m "feat(timeline/modals): calibration.js — relocate ArUco flow

Same three steps as the Displays page (group dropdown -> Generate
ArUco websocket -> upload photo -> result), now opened from the
toolbar 🎯 button. No new server code — reuses the existing
GENERATEARUCO websocket request + /upload/calibrate REST handler.

Failure cases: SockJS unavailable surfaces explicitly (rather than
silent no-op); upload failure shows the server's error string in the
result area and a toast.

PR-4c T-D1."
```

---

### Task D2: Calibration e2e spec

**Files:**
- Create: `tests/e2e/test-calibration-modal.spec.js`

- [ ] **Step 1: Smoke test the modal opens with correct structure**

```javascript
import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, cleanupE2eOrphans } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);

    // Open via toolbar button.
    await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Calibrate'));
      btn.click();
    });
    await page.waitForSelector('.mm-calibration', { timeout: 5000 });

    // Verify the dropdown has at least one group from store.displays.
    const groupCount = await page.evaluate(
      () => document.querySelectorAll('.mm-calibration [data-field="group"] option').length);
    assert.ok(groupCount >= 1, `expected ≥1 group in dropdown, got ${groupCount}`);

    // Verify the three steps + upload input are present.
    const steps = await page.evaluate(
      () => document.querySelectorAll('.mm-calibration .steps li').length);
    assert.equal(steps, 3);
    const hasUpload = await page.evaluate(
      () => !!document.querySelector('.mm-calibration [data-field="photo"]'));
    assert.ok(hasUpload, 'expected file input in modal');

    return 'pass';
  } finally { await browser.close(); }
}
```

- [ ] **Step 2: Run e2e suite**

```bash
node tests/e2e/run.js
```
Expected: 10 pass / 0 fail.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test-calibration-modal.spec.js
git commit -m "test(e2e): calibration modal opens with correct structure

Smokes the toolbar 🎯 wiring: button -> modal -> 3-step list + group
dropdown populated from store.displays + photo upload input. Does
NOT exercise the full ArUco round-trip (would need a real wall
photo + iPads in the test fixture); that path stays manual per the
spec's Section 11 smoke checklist.

PR-4c T-D2 (Phase D complete)."
```

---

## Phase E: Final wiring

### Task E1: CLAUDE.md updates

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add modal layout to the Layout section**

In `CLAUDE.md`, find the `js/timeline/` bullet and append:

```markdown
- `js/timeline/modals/` — admin-side modal components. `modal-shell.js` is the focus-trapped, Esc/click-outside-dismissable scaffold every other modal mounts into via `openModal({title, contentEl})`. One modal at a time — opening a new one closes the current. The four modal bodies are `recurrence-editor.js` (replaces PR-4b's inline popover; full schedule fields + next-N preview), `playlist-editor.js` (per-item form), `profile-editor.js` (3-pane: list + form + template-var preview), `calibration.js` (relocates the existing ArUco generate+upload flow). Modals talk to the store directly; the store handles optimistic + rollback + 412 refetch.
- `js/timeline/context-menu.js` — right-click `<ul>` rendered into `#mmContextMenu`. Items: Edit schedule / Edit playlist items / Duplicate / Delete. Position clamps to viewport.
- `js/timeline/util/refetch-merge.js` — 412 conflict resolver. When `withRollback`'s apiFn throws `ApiError(status=412)`, this fetches the fresh entity, replaces the store slice, toasts *"X was updated by another admin"*. Wired from `util/optimistic.js` via `opts.conflictKind` + `opts.conflictId`.
```

- [ ] **Step 2: Add the conventions**

In the Conventions section, append:

```markdown
- **Admin timeline 412 conflict resolution is refetch+merge, not bare rollback.** `util/optimistic.js`'s `withRollback` catches `ApiError(status=412)` and routes through `util/refetch-merge.js` to update the store to the server's version + toast a *"updated by another admin"* message. Callers opt in via `opts.conflictKind` + `opts.conflictId`. Delete paths skip this (404 on delete isn't a conflict).
- **Admin timeline modals share `modals/modal-shell.js`.** Every modal in `js/timeline/modals/` calls `openModal({title, contentEl})` and supplies its own content + Save handler. The shell owns focus trap, Esc, click-outside, and ARIA. One modal at a time — opening a new modal closes the current.
- **Right-click on a `.mm-clip` opens the context menu in `js/timeline/context-menu.js`**, NOT a `confirm()` prompt. The four items (Edit schedule / Edit playlist items / Duplicate / Delete) route through `openRecurrenceEditor`, `openPlaylistEditor`, `store.createSchedule`, and `store.deleteSchedule` respectively.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude-md): document PR-4c modals + 412 refetch convention

Layout: js/timeline/modals/ (shell + four modal bodies),
js/timeline/context-menu.js, js/timeline/util/refetch-merge.js.

Conventions: 412 conflict resolution is refetch+merge not bare
rollback; modals share modal-shell.js; right-click goes through
context-menu.js not confirm().

PR-4c T-E1."
```

---

### Task E2: Final suite + open PR

- [ ] **Step 1: Run the full test matrix**

```bash
python pytest_runner.py --js
python pytest_runner.py --e2e
```
Expected:
- JS: 68+ pass / 0 fail (PR-4b's 66 + at least 2 new from refetch-merge tests)
- e2e: 10 pass / 0 fail (PR-4b's 4 + 6 new)

- [ ] **Step 2: Push + open PR**

```bash
git push -u origin feature/pr4c-timeline-modals-polish
gh pr create --base feature/pr4b-timeline-interactivity --head feature/pr4c-timeline-modals-polish \
  --title "PR-4c: Admin timeline modals + polish" \
  --body "$(cat <<'EOF'
## Summary

Closes the spec's modal/menu chapter on top of PR-4b's interactivity layer. Stacks on **#7**.

- **Phase A — schedule editing polish.** Full recurrence modal (replaces T13 popover) with dtstart + startTime/endTime + priority + next-N preview. Right-click context menu (Edit schedule / Edit playlist items / Duplicate / Delete). 412 conflict resolution UX — `util/refetch-merge.js` pulls the server's version + toasts *"updated by another admin"* instead of bare rollback.
- **Phase B — playlist-item editor modal.** Per-item form (playmode, backgroundColor, duration override) opened from drilled-in row clicks or context-menu Edit playlist items.
- **Phase C — profile-editor modal (3-pane).** List + form + live template-variable preview. Wired to existing `/api/profiles` CRUD.
- **Phase D — calibration modal.** Relocates the existing ArUco generate+upload+detect flow into a toolbar-launched modal. No new server code.
- **Phase E — docs + suite.** CLAUDE.md updated; full unit + e2e suite green.

## Test plan

- [x] `python pytest_runner.py --js` — N pass / 0 fail
- [x] `python pytest_runner.py --e2e` — 10 pass / 0 fail (PR-4b's 4 + 6 new)
- [ ] Manual: Alt+click clip → recurrence modal → switch freq → preview updates → Save. Right-click clip → menu. Drill-in → click item → editor. Toolbar ⚙ → profile editor. Toolbar 🎯 → calibration modal.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Notes for the implementing engineer

1. **Modal shell + context menu are foundational.** Tasks A3 + A5 must land before the modals that depend on them. The plan orders them correctly — don't reshuffle.

2. **The 412 refetch UX (A1-A2) needs server cooperation.** Make sure the `/api/schedules` + `/api/playlists` endpoints still return `_serverVersion` on their GET-by-id responses (they do, per PR-2). If a future server change drops the field, `refetch-merge.js` will silently install a stale-on-arrival entity.

3. **The Profile editor lives in `js/timeline/modals/`** even though its behaviour spans store + API + UI. Don't pull the form into Alpine components — the modal is mounted/unmounted imperatively and Alpine's lifecycle doesn't help here. PR-4b T13's recurrence popover used the same imperative pattern; we're extending it.

4. **Calibration is a relocation, not a rewrite.** The existing `data-route="displays"` calibration UI in `admin.html` stays in place. PR-6 in the spec's rollout will delete it. Don't preemptively remove it from this PR.

5. **Synthetic events in e2e specs use the same pattern as PR-4b T14.** `dispatchEvent` with `bubbles:true` on the right element bypasses Playwright's mouse-event flakiness with reactive renderers. See `tests/e2e/helpers.js`'s `syntheticDrag` for the canonical example.

6. **No new server code.** Every endpoint this PR talks to was landed in PR-2 (REST surface) or PR-3 (profiles). If you find yourself wanting a new server route, stop — the spec deliberately put all CRUD ahead of the UI.

7. **Don't drop the iPad-1 client compatibility.** Only `admin.html` + `js/timeline/` change in this PR. The display clients (`index.html`, `js/mosiacmesh.js`, `js/GoTime.js`) stay ES5 / jQuery 1.x.

## Known gaps — deferred from this plan

These spec items are deliberately NOT covered here, to keep the PR scoped. None of them block the modals from working:

- **`Del` on a drilled-in sub-clip removes a playlist item** (spec line 358). The drag-to-add path lands in PR-4b T11; the reverse (delete) is a small follow-up — add a `keydown` listener inside the drill-in row that calls `store.updatePlaylist` with the item removed.
- **Track-header click → per-client profile override popover** (spec line 362). `store.assignProfileToClient` is wired in T-C1; the trigger UI (popover anchored under the track header) is a separate small task. The Profile editor modal handles the WHAT — the per-display assignment popover is the WHERE.
- **Confirmation modal on fleet-wide actions affecting >3 devices** (spec line 363). Fleet actions live in the existing toolbar pieces; the >3-device confirmation is a small one-off that doesn't share infrastructure with the modal shell.
- **Schedule overlap diagonal-stripe overlay** (spec line 367). PR-4a's `detectConflicts` already returns the overlap ranges; the visual stripe overlay is rendered in `clip.js`'s `renderStripes` but only on the lower-priority clip and only in Day view — Week view doesn't have it. Polish, not blocking.

Each is a single-task follow-up PR if/when the operator workflow needs it.
