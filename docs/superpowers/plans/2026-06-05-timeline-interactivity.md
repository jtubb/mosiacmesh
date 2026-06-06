# Admin Timeline (Interactivity) Implementation Plan — PR-4b

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the full interaction layer to the read-only timeline shipped in PR-4a — drag playlists onto tracks to create schedules, drag clip bodies/edges to move/resize, double-click to drill in and edit playlist items, upload media, delete via key/menu, with optimistic-local + server-confirm + rollback per spec §8.

**Architecture:** PR-4a left the store's mutation methods (`createSchedule`, `updateSchedule`, `deleteSchedule`, `updatePlaylist`) as stubs that throw `'not implemented in PR-4a'`. PR-4b implements them, extends `api.js` with POST/PUT/DELETE wrappers including `If-Match` concurrency, and wires native HTML5 drag-and-drop + pointer events on the existing clip/track/bin DOM. Every mutation snapshots the relevant store slice before issuing the request; on non-2xx, the snapshot restores and a toast surfaces the server's `error` string.

**Tech Stack:** Same as PR-4a — Alpine.js 3.x, native ES modules, no build step. **New dev dep**: `playwright` (lower-level package, used directly inside Node's `--test` runner — no separate test framework). Adds `package.json` + `node_modules/` (gitignored) for browser integration tests only. Production code unchanged in deps.

**Stacks on:** `feature/pr4a-timeline-readonly` (PR #6). Merge after PR-4a lands.

**Spec reference:** `docs/superpowers/specs/2026-06-04-admin-timeline-redesign-design.md` sections 8 (data flow — edits), 9 (interaction model), 10 (error handling).

---

## File Structure

| File | Action | Responsibility after this PR |
|---|---|---|
| `js/timeline/api.js` | **Modify** | Adds `postJson` / `putJson` / `deleteReq` / `uploadFile` helpers + `createSchedule`/`updateSchedule`/`deleteSchedule`/`updatePlaylist`/`assignProfile`/`uploadMedia` methods. PR-4a's GET-only API gains the full CRUD surface. |
| `js/timeline/store.js` | **Modify** | The four stubbed mutation methods (`createSchedule` / `updateSchedule` / `deleteSchedule` / `updatePlaylist`) become real implementations following the optimistic-local + rollback pattern from spec §8. Adds `selection` state for click-selection. Adds `toast(msg, kind)` UI helper. Adds `drillIn` state for double-click drill-in. |
| `js/timeline/util/snap.js` | **Create** | Pure helpers: `pxToHour(pxFromGridLeft, gridWidth)`, `hourToTime(hour)`, `snapTo15min(hour)`, `isoTimeAdd(date, hour)`. Used by drag handlers — pure functions, Node-testable. |
| `js/timeline/util/optimistic.js` | **Create** | `withRollback(store, snapshotKeys, mutationFn, apiFn)` — generic wrapper that snapshots given keys from the store, runs mutationFn locally, then awaits apiFn; on success, applies the server-returned values; on error, restores the snapshot and emits a toast. Pure-function tested. |
| `js/timeline/drag/playlist-to-track.js` | **Create** | Drag from `mm-bin-item` (in playlist-bin) onto a Day-view track row → POST schedule at the drop time. |
| `js/timeline/drag/clip-move.js` | **Create** | Drag clip body to move within same track or cross-track → PUT schedule with new startTime/endTime (+ optional new displayID). |
| `js/timeline/drag/clip-resize.js` | **Create** | Drag the left or right edge of a clip to resize → PUT schedule with new startTime or endTime. Handles use `cursor: ew-resize`. |
| `js/timeline/drag/media-to-clip.js` | **Create** | Drag from `mm-bin-item` (in media-bin) onto a drilled-in clip's items sub-track → PUT playlist with appended item. |
| `js/timeline/drag/dragstate.js` | **Create** | Tiny module owning a single `dragState` ref shared across all drag handlers (kind: 'playlist'|'clip-move'|'clip-resize-left'|'clip-resize-right'|'media', source payload, etc.). Centralizes the conventional HTML5 `dataTransfer.setData` shape so handlers stay in lockstep. |
| `js/timeline/select.js` | **Create** | Click-selection model: single-click on `.mm-clip` sets `store.selection = scheduleId`, click on empty grid clears, Shift-click adds to a multi-select set. Wires up `Delete` key to call `deleteSelection()` (single or multi). |
| `js/timeline/drill-in.js` | **Create** | Double-click `.mm-clip` → expand the parent track row to show the playlist's items as a sub-track (one per item, positioned by cumulative duration). Second double-click collapses. |
| `js/timeline/upload.js` | **Create** | `+ Upload` button handler on the media bin. Opens a hidden `<input type="file" multiple>`, POSTs each file to `/upload/image` or `/upload/video` based on extension, then re-hydrates `store.media`. |
| `js/timeline/recurrence-popover.js` | **Create** | Inline popover anchored under a clicked clip: freq dropdown (DAILY/WEEKLY/MONTHLY/YEARLY), interval input, byweekday checkboxes (when WEEKLY), end-type radio (never/until/count) with conditional inputs. PUT on Save. Read-only mode shows current values. The full modal-shaped editor lands in PR-4c. |
| `js/timeline/timeline/timeline.js` | **Modify** | Each clip block gains `draggable="true"` + `data-schedule-id` (existing) + resize handle children. Each track row gains drop-zone class + handlers for incoming playlists. The drilled-in sub-track HTML emitter is added here. |
| `js/timeline/index.js` | **Modify** | Wires the new handlers into `bootstrap()`: imports `select`, `drag/*`, `drill-in`, `upload`, `recurrence-popover` and calls their `attach(store)` setup function once the DOM is ready (post-hydrate). |
| `js/timeline/store.js` toast state | (in store.js modify) | `store.toasts = []` + `store.toast(msg, kind)` + `store.dismissToast(id)`. Auto-dismiss after 4s. UI rendered by a small `mmToast` Alpine component. |
| `js/timeline/timeline/toast.js` | **Create** | `mmToast` Alpine component (small fixed-position toast stack — neutral/error styling, click-to-dismiss). |
| `admin.html` | **Modify** | Adds a `<div x-data="mmToast" ...>` toast container near the section root, the hidden `<input type="file">` for uploads, drag/drop CSS (cursor changes, drop-zone highlight, drilled-in sub-track styling, popover positioning). |
| `tests/unit/js/test_snap.js` | **Create** | Node `--test` for `util/snap.js` (pxToHour, snapTo15min, hourToTime, isoTimeAdd). |
| `tests/unit/js/test_optimistic.js` | **Create** | Node `--test` for `util/optimistic.js` (rollback on error, snapshot restore, success path applies returned values). |
| `tests/unit/js/test_store_mutations.js` | **Create** | Node `--test` for store mutation methods (mocks `api`, asserts optimistic-then-rollback / optimistic-then-confirm). |
| `tests/unit/js/test_api_mutations.js` | **Create** | Node `--test` for the new api.js POST/PUT/DELETE methods (mocks `fetch`, asserts request shape including `If-Match`, JSON body, etc.). |
| `tests/unit/js/test_timeline_smoke.js` | **Modify** | Adds the new modules to the MODULES list. |
| `package.json` | **Create** | dev-dep `playwright@^1.49.0`. No prod deps. `"private": true`. |
| `.gitignore` | **Modify** | Add `node_modules/`. |
| `tests/e2e/run.js` | **Create** | Entry point — launches chromium, opens `http://localhost:3000/admin#timeline`, dispatches each spec file in sequence. Reports pass/fail. Exits non-zero on any failure. |
| `tests/e2e/helpers.js` | **Create** | `cleanupSchedule(page, name)`, `cleanupPlaylist(page, name)`, `waitForHydrated(page)`. |
| `tests/e2e/test-create-schedule.spec.js` | **Create** | Drag the `Morning` playlist onto the `Tablet` track at hour 14 → verify schedule appears + cleanup. |
| `tests/e2e/test-clip-move.spec.js` | **Create** | Create a schedule programmatically; drag its clip from 09:00 → 14:00; verify PUT issued + new position; cleanup. |
| `tests/e2e/test-clip-delete.spec.js` | **Create** | Create a schedule; click + Delete key; verify schedule removed; (no cleanup needed). |
| `tests/e2e/test-drill-in.spec.js` | **Create** | Double-click an existing schedule's clip → verify sub-track renders with playlist items. |
| `pytest_runner.py` | **Modify** | New `--e2e` flag: chains `node tests/e2e/run.js` after a server-up check. Skips gracefully with a helpful message if `node_modules` is missing. |
| `CLAUDE.md` | **Modify** | Adds the `tests/e2e/` block to the Test Status section + the `package.json`/`node_modules/` story to Conventions. |

---

## Conventions for this PR (carry-forward from PR-4a + new)

1. **Optimistic-local + server-confirm + rollback.** Every mutation method follows the same shape: snapshot the relevant slice → apply local mutation → fire the REST call → on success, replace local with server's returned value (`_serverVersion` bumped) → on error, restore the snapshot + show a toast with the server's `error` string. The `util/optimistic.js` wrapper makes this consistent.
2. **If-Match required on PUT.** Every PUT sends `If-Match: <_serverVersion>`. Server returns 412 with `{currentVersion}` if stale; the rollback path refetches the resource and toasts `"<name> updated by another admin."` Last-write-wins for v1.
3. **HTML5 drag-and-drop, not pointer events.** Uses `draggable="true"` + `dragstart/dragover/drop` for the playlist→track and media→clip flows. Pointer-events fallback is only for the clip-move and clip-resize (those need precise position tracking that drag events don't give cleanly).
4. **Always go through `Alpine.store('mm')`** — PR-4a's lesson. Never call methods on the raw `makeStore()` reference; always retrieve the proxy first so reactivity fires.
5. **Read-only safety net.** The mutation stubs from PR-4a get replaced with real implementations — no other call sites should depend on the throwing behavior.
6. **Browser integration tests live in `tests/e2e/`.** They use `playwright` directly (no separate test runner) and assume the dev server is running on `:3000`. Each spec creates + cleans up its own data.
7. **No production deps added.** `playwright` is `devDependencies` only. Production install (`pip install -r requirements.txt` + the static files) remains unchanged.

---

## Task 1: Extend `js/timeline/api.js` with POST/PUT/DELETE + upload

Adds the mutation surface: every PR-2 endpoint that PR-4a's GET methods called now has a write counterpart.

**Files:**
- Modify: `js/timeline/api.js`
- Create: `tests/unit/js/test_api_mutations.js`
- Modify: `tests/unit/js/test_timeline_smoke.js` (already includes api.js; no change)

### Step 1.1: Write the failing test

Create `tests/unit/js/test_api_mutations.js`:

```javascript
/**
 * Unit tests for js/timeline/api.js mutation methods. Mocks global
 * fetch and asserts request shape: method, URL, headers (especially
 * If-Match), JSON body.
 */
import { test, describe, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const apiUrl = pathToFileURL(path.join(__dirname, '../../../js/timeline/api.js')).href;

let fetchCalls;
let origFetch;
function installFakeFetch(responses) {
  origFetch = globalThis.fetch;
  fetchCalls = [];
  let i = 0;
  globalThis.fetch = async (url, opts) => {
    const r = responses[i++] ?? responses[responses.length - 1];
    fetchCalls.push({ url, opts });
    return {
      ok: r.status >= 200 && r.status < 300,
      status: r.status,
      statusText: r.statusText || '',
      text: async () => typeof r.body === 'string' ? r.body : JSON.stringify(r.body),
    };
  };
}
function restoreFetch() {
  if (origFetch) globalThis.fetch = origFetch;
}

describe('api.createSchedule', () => {
  beforeEach(() => installFakeFetch([{ status: 201, body: { success: true, schedule: { id: 'sch_new', _serverVersion: 1 } } }]));
  afterEach(restoreFetch);

  test('POSTs to /api/schedules with JSON body', async () => {
    const { api } = await import(apiUrl + '?t=' + Date.now());
    const out = await api.createSchedule({ playlistName: 'P', displayID: 'D', startTime: '09:00', endTime: '17:00' });
    assert.equal(fetchCalls.length, 1);
    const c = fetchCalls[0];
    assert.equal(c.url, '/api/schedules');
    assert.equal(c.opts.method, 'POST');
    assert.equal(c.opts.headers['Content-Type'], 'application/json');
    const body = JSON.parse(c.opts.body);
    assert.equal(body.playlistName, 'P');
    assert.equal(body.displayID, 'D');
    assert.equal(out.id, 'sch_new');
  });
});

describe('api.updateSchedule', () => {
  beforeEach(() => installFakeFetch([{ status: 200, body: { success: true, schedule: { id: 'sch_1', _serverVersion: 7 } } }]));
  afterEach(restoreFetch);

  test('PUTs to /api/schedules/{id} with If-Match header', async () => {
    const { api } = await import(apiUrl + '?t=' + Date.now() + '_b');
    await api.updateSchedule('sch_1', { startTime: '10:00' }, 6);
    const c = fetchCalls[0];
    assert.equal(c.url, '/api/schedules/sch_1');
    assert.equal(c.opts.method, 'PUT');
    assert.equal(c.opts.headers['If-Match'], '6');
  });
});

describe('api.deleteSchedule', () => {
  beforeEach(() => installFakeFetch([{ status: 204, body: '' }]));
  afterEach(restoreFetch);

  test('DELETEs to /api/schedules/{id}', async () => {
    const { api } = await import(apiUrl + '?t=' + Date.now() + '_c');
    await api.deleteSchedule('sch_1');
    const c = fetchCalls[0];
    assert.equal(c.url, '/api/schedules/sch_1');
    assert.equal(c.opts.method, 'DELETE');
  });
});

describe('api.updatePlaylist', () => {
  beforeEach(() => installFakeFetch([{ status: 200, body: { success: true, playlist: { name: 'P', _serverVersion: 3 } } }]));
  afterEach(restoreFetch);

  test('PUTs to /api/playlists/{name} with If-Match', async () => {
    const { api } = await import(apiUrl + '?t=' + Date.now() + '_d');
    await api.updatePlaylist('P', { items: [{ file: '/m/a.mp4' }] }, 2);
    const c = fetchCalls[0];
    assert.equal(c.url, '/api/playlists/P');
    assert.equal(c.opts.method, 'PUT');
    assert.equal(c.opts.headers['If-Match'], '2');
  });
});

describe('api throws ApiError on non-2xx', () => {
  beforeEach(() => installFakeFetch([{ status: 412, body: { success: false, error: 'stale', currentVersion: 9 } }]));
  afterEach(restoreFetch);

  test('412 PUT throws ApiError with status + body', async () => {
    const mod = await import(apiUrl + '?t=' + Date.now() + '_e');
    let thrown = null;
    try {
      await mod.api.updateSchedule('sch_1', { startTime: '11:00' }, 5);
    } catch (e) { thrown = e; }
    assert.ok(thrown, 'expected ApiError to be thrown');
    assert.equal(thrown.status, 412);
    assert.equal(thrown.body.currentVersion, 9);
  });
});
```

Run: `node --test tests/unit/js/test_api_mutations.js` → expect FAIL (`api.createSchedule is not a function`).

### Step 1.2: Add the mutation methods to `js/timeline/api.js`

Replace the contents of `js/timeline/api.js` with:

```javascript
/**
 * Async wrappers over the PR-2 REST endpoints.
 *
 * GET methods land in PR-4a (listPlaylists, listSchedules, listProfiles,
 * listMedia, listDevices). PR-4b extends with POST/PUT/DELETE for the
 * create/edit/delete flows + a multipart `uploadMedia` for the media
 * bin's + Upload button.
 *
 * Every method returns the parsed JSON body on success, or throws an
 * ApiError on non-2xx. The thrown error has `.status` and `.body`
 * fields so callers can render a precise toast with the server's
 * `error` string and (for 412 stale) the `currentVersion` for resync.
 */

class ApiError extends Error {
  constructor(message, { status, body }) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function parseJsonOrText(resp) {
  const text = await resp.text();
  try { return text ? JSON.parse(text) : null; } catch (_) { return text; }
}

async function getJson(url) {
  const resp = await fetch(url, {
    method: 'GET',
    headers: { 'Accept': 'application/json' },
    credentials: 'same-origin',
  });
  const body = await parseJsonOrText(resp);
  if (!resp.ok) throw new ApiError(`GET ${url} -> ${resp.status} ${resp.statusText}`, { status: resp.status, body });
  return body;
}

async function postJson(url, body) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(body),
  });
  const respBody = await parseJsonOrText(resp);
  if (!resp.ok) throw new ApiError(`POST ${url} -> ${resp.status} ${resp.statusText}`, { status: resp.status, body: respBody });
  return respBody;
}

async function putJson(url, body, ifMatch) {
  const headers = {
    'Accept': 'application/json',
    'Content-Type': 'application/json',
  };
  if (ifMatch != null) headers['If-Match'] = String(ifMatch);
  const resp = await fetch(url, {
    method: 'PUT',
    headers,
    credentials: 'same-origin',
    body: JSON.stringify(body),
  });
  const respBody = await parseJsonOrText(resp);
  if (!resp.ok) throw new ApiError(`PUT ${url} -> ${resp.status} ${resp.statusText}`, { status: resp.status, body: respBody });
  return respBody;
}

async function deleteReq(url) {
  const resp = await fetch(url, {
    method: 'DELETE',
    headers: { 'Accept': 'application/json' },
    credentials: 'same-origin',
  });
  if (!resp.ok && resp.status !== 204) {
    const body = await parseJsonOrText(resp);
    throw new ApiError(`DELETE ${url} -> ${resp.status} ${resp.statusText}`, { status: resp.status, body });
  }
  return null;
}

async function uploadFile(url, file) {
  // Mirrors the legacy upload_handler — single-field multipart with the
  // file under any field name. Server reads via reader.next() so the
  // field name doesn't matter.
  const form = new FormData();
  form.append('file', file, file.name);
  const resp = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    body: form,
  });
  const body = await parseJsonOrText(resp);
  if (!resp.ok) throw new ApiError(`UPLOAD ${url} -> ${resp.status} ${resp.statusText}`, { status: resp.status, body });
  return body;
}

export const api = {
  // ---- Read (PR-4a) ----
  async listPlaylists()  { const b = await getJson('/api/playlists');           return b?.playlists ?? []; },
  async listSchedules()  { const b = await getJson('/api/schedules');           return b?.schedules ?? []; },
  async listProfiles()   { const b = await getJson('/api/profiles');            return b?.profiles ?? []; },
  async listMedia()      { return await getJson('/api/media'); },
  async listDevices()    { return await getJson('/api/discovery/devices'); },

  // ---- Schedules ----
  /** POST /api/schedules — body must include playlistName + displayID. Returns the created schedule. */
  async createSchedule(partial) {
    const b = await postJson('/api/schedules', partial);
    return b?.schedule;
  },
  /** PUT /api/schedules/{id} — partial patch + If-Match. Returns the updated schedule (new _serverVersion). */
  async updateSchedule(id, patch, ifMatch) {
    const b = await putJson(`/api/schedules/${encodeURIComponent(id)}`, patch, ifMatch);
    return b?.schedule;
  },
  /** DELETE /api/schedules/{id} — 204 on success. */
  async deleteSchedule(id) {
    return await deleteReq(`/api/schedules/${encodeURIComponent(id)}`);
  },

  // ---- Playlists ----
  /** POST /api/playlists — body must include name. */
  async createPlaylist(partial) {
    const b = await postJson('/api/playlists', partial);
    return b?.playlist;
  },
  /** PUT /api/playlists/{name} — partial patch + If-Match. */
  async updatePlaylist(name, patch, ifMatch) {
    const b = await putJson(`/api/playlists/${encodeURIComponent(name)}`, patch, ifMatch);
    return b?.playlist;
  },
  /** DELETE /api/playlists/{name} — 204 or 409+refs. */
  async deletePlaylist(name) {
    return await deleteReq(`/api/playlists/${encodeURIComponent(name)}`);
  },

  // ---- Profiles ----
  async assignProfile(clientKey, profileName) {
    const b = await postJson(`/api/clients/${encodeURIComponent(clientKey)}/profile`, { profileName });
    return b;
  },

  // ---- Media ----
  /** POST /upload/image or /upload/video based on extension. Returns server's response. */
  async uploadMedia(file) {
    const ext = (file.name.split('.').pop() || '').toLowerCase();
    const isVideo = ['mp4', 'mov', 'mkv', 'webm', 'avi'].includes(ext);
    const dest = isVideo ? 'video' : 'image';
    return await uploadFile(`/upload/${dest}`, file);
  },
};

export { ApiError };
```

### Step 1.3: Run the api-mutation tests — expect PASS

```bash
node --test tests/unit/js/test_api_mutations.js
```

Expected: 5/5 pass.

### Step 1.4: Run all JS tests — confirm no regressions

```bash
cmd //c "tests\unit\js\run_js_tests.bat"
```

Expected: all green (33 from PR-4a + 5 new = 38).

### Step 1.5: Commit

```bash
git add js/timeline/api.js tests/unit/js/test_api_mutations.js
git commit -m "feat(timeline/api): POST/PUT/DELETE + upload wrappers

Extends PR-4a's GET-only api.js with the full CRUD surface needed by
PR-4b's interactive flows:

  createSchedule(partial)               POST /api/schedules
  updateSchedule(id, patch, ifMatch)    PUT  /api/schedules/{id}  + If-Match
  deleteSchedule(id)                    DELETE /api/schedules/{id}
  createPlaylist(partial)               POST /api/playlists
  updatePlaylist(name, patch, ifMatch)  PUT  /api/playlists/{name} + If-Match
  deletePlaylist(name)                  DELETE /api/playlists/{name}
  assignProfile(clientKey, profileName) POST /api/clients/{key}/profile
  uploadMedia(file)                     POST /upload/{image|video} multipart

Every mutating wrapper throws ApiError(.status, .body) on non-2xx so the
caller can render a precise toast — including 412 stale's currentVersion
for resync.

5 new tests pin the request shape (method, URL, JSON body, If-Match
header, ApiError on non-2xx).

Part of PR-4b of the admin-timeline-redesign spec."
```

---

## Task 2: Optimistic-local + rollback helper

Pure-function wrapper that consolidates the "snapshot → mutate locally → call API → on failure restore" pattern so each store mutation method stays small.

**Files:**
- Create: `js/timeline/util/optimistic.js`
- Create: `tests/unit/js/test_optimistic.js`
- Modify: `tests/unit/js/test_timeline_smoke.js`

### Step 2.1: Write the failing test

Create `tests/unit/js/test_optimistic.js`:

```javascript
import { test, describe } from 'node:test';
import assert from 'node:assert';
import { withRollback } from '../../../js/timeline/util/optimistic.js';

function makeFakeStore() {
  return {
    schedules: [{ id: 'a', startTime: '09:00' }],
    toasts: [],
    toast(msg, kind) { this.toasts.push({ msg, kind }); },
  };
}

describe('withRollback', () => {
  test('success path: applies local mutation, returns API result', async () => {
    const store = makeFakeStore();
    const result = await withRollback(store, ['schedules'],
      () => { store.schedules[0].startTime = '10:00'; },
      async () => ({ id: 'a', startTime: '10:00', _serverVersion: 2 }),
    );
    assert.equal(store.schedules[0].startTime, '10:00');
    assert.equal(result.startTime, '10:00');
    assert.equal(store.toasts.length, 0);
  });

  test('failure path: restores snapshot + toasts the error', async () => {
    const store = makeFakeStore();
    const err = Object.assign(new Error('PUT failed'), { status: 412, body: { error: 'stale' } });
    let thrown = null;
    try {
      await withRollback(store, ['schedules'],
        () => { store.schedules[0].startTime = '10:00'; },
        async () => { throw err; },
      );
    } catch (e) { thrown = e; }
    assert.equal(store.schedules[0].startTime, '09:00', 'snapshot restored');
    assert.ok(thrown, 'rethrows so caller can react');
    assert.equal(store.toasts.length, 1);
    assert.equal(store.toasts[0].kind, 'error');
  });

  test('preserves multiple snapshot keys', async () => {
    const store = makeFakeStore();
    store.playlists = { P: { name: 'P', items: [] } };
    await withRollback(store, ['schedules', 'playlists'],
      () => { store.schedules[0].startTime = '10:00'; store.playlists.P.items.push('x'); },
      async () => { throw new Error('boom'); },
    ).catch(() => {});
    assert.equal(store.schedules[0].startTime, '09:00');
    assert.deepEqual(store.playlists.P.items, []);
  });
});
```

Run: expect FAIL — module missing.

### Step 2.2: Add to smoke

Append `'js/timeline/util/optimistic.js'` to `MODULES` in `tests/unit/js/test_timeline_smoke.js`.

### Step 2.3: Implement `js/timeline/util/optimistic.js`

```javascript
/**
 * Optimistic-local + server-confirm + rollback wrapper for store
 * mutations. The standard shape for every PR-4b mutation method:
 *
 *   await withRollback(this, ['schedules'], 
 *     () => { this.schedules.push(temp); },        // local mutation
 *     async () => await api.createSchedule(body),  // server call
 *   );
 *
 * - Snapshots a deep-clone of each named store slice BEFORE the local
 *   mutation runs.
 * - Runs the mutation locally so the UI updates immediately.
 * - Awaits the API call. On success, returns its result.
 * - On error, restores every snapshotted slice and emits a toast with
 *   the server's `error` string (falling back to `e.message`).
 * - Re-throws so the caller can chain extra cleanup (e.g. removing an
 *   ephemeral placeholder by id) — but the snapshot restoration has
 *   already happened.
 *
 * Pure function aside from the `store.toast(...)` call. Testable in
 * Node without DOM.
 */

function deepClone(value) {
  if (value === null || typeof value !== 'object') return value;
  if (Array.isArray(value)) return value.map(deepClone);
  const out = {};
  for (const k of Object.keys(value)) out[k] = deepClone(value[k]);
  return out;
}

export async function withRollback(store, snapshotKeys, mutationFn, apiFn) {
  const snapshot = {};
  for (const k of snapshotKeys) snapshot[k] = deepClone(store[k]);
  try {
    mutationFn();
    return await apiFn();
  } catch (e) {
    for (const k of snapshotKeys) store[k] = snapshot[k];
    const errMsg = (e && e.body && e.body.error) || (e && e.message) || String(e);
    if (typeof store.toast === 'function') store.toast(errMsg, 'error');
    throw e;
  }
}
```

Run `node --test tests/unit/js/test_optimistic.js` → 3 PASS.

### Step 2.4: Commit

```bash
git add js/timeline/util/optimistic.js tests/unit/js/test_optimistic.js tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline/util): optimistic.js — snapshot + rollback wrapper

withRollback(store, keys, mutationFn, apiFn) is the canonical shape
every PR-4b store mutation uses. Snapshots the named slices, runs
the local mutation (UI updates immediately), then awaits the API;
on failure restores the snapshot and emits an error toast with the
server's error string.

3 tests cover success, failure-with-restore, and multi-key snapshot.

Part of PR-4b of the admin-timeline-redesign spec."
```

---

## Task 3: Store mutation implementations + toast state

Replaces the four `throw 'not implemented in PR-4a'` stubs with real implementations using `withRollback`. Adds the toast state Alpine will render below.

**Files:**
- Modify: `js/timeline/store.js`
- Create: `tests/unit/js/test_store_mutations.js`

### Step 3.1: Write the failing test

Create `tests/unit/js/test_store_mutations.js`:

```javascript
import { test, describe, beforeEach } from 'node:test';
import assert from 'node:assert';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const storeUrl = pathToFileURL(path.join(here, '../../../js/timeline/store.js')).href;

// Replace the imported api with mocks for each test (cache-bust via ?t).
async function loadStoreWithMockApi(mockApi) {
  // The store imports './api.js' — to mock it without DI, we shadow the
  // global fetch. The api wrapper uses fetch; mocking fetch is enough.
  globalThis._mockApi = mockApi;
  const mod = await import(storeUrl + '?t=' + Date.now() + Math.random());
  return mod.makeStore();
}

function installFetch(handlers) {
  // handlers: {METHOD url -> async (req) => Response-like}
  globalThis.fetch = async (url, opts) => {
    const m = (opts && opts.method) || 'GET';
    const key = `${m} ${url}`;
    const handler = handlers[key];
    if (!handler) throw new Error('unhandled fetch: ' + key);
    const result = await handler(opts);
    return {
      ok: result.status >= 200 && result.status < 300,
      status: result.status,
      statusText: result.statusText || '',
      text: async () => typeof result.body === 'string' ? result.body : JSON.stringify(result.body ?? null),
    };
  };
}

describe('store.createSchedule', () => {
  test('optimistic: appends placeholder, confirms with server id', async () => {
    const store = await loadStoreWithMockApi();
    store.toasts = []; store.toast = function(m, k) { this.toasts.push({m,k}); };
    store.playlists = { P: { name: 'P', items: [], _serverVersion: 1 } };
    store.displays = [{ displayID: 'D' }];
    store.schedules = [];
    installFetch({
      'POST /api/schedules': async () => ({ status: 201, body: { success: true, schedule: { id: 'sch_server', playlistName: 'P', displayID: 'D', startTime: '09:00', endTime: '17:00', _serverVersion: 1 } } }),
    });
    await store.createSchedule({ playlistName: 'P', displayID: 'D', startTime: '09:00', endTime: '17:00' });
    assert.equal(store.schedules.length, 1);
    assert.equal(store.schedules[0].id, 'sch_server');
    assert.equal(store.toasts.length, 0);
  });

  test('failure: rolls back, toasts error', async () => {
    const store = await loadStoreWithMockApi();
    store.toasts = []; store.toast = function(m, k) { this.toasts.push({m,k}); };
    store.playlists = { P: { name: 'P', items: [], _serverVersion: 1 } };
    store.displays = [{ displayID: 'D' }];
    store.schedules = [];
    installFetch({
      'POST /api/schedules': async () => ({ status: 400, body: { success: false, error: "playlist 'Ghost' not found" } }),
    });
    let thrown = null;
    try {
      await store.createSchedule({ playlistName: 'Ghost', displayID: 'D' });
    } catch (e) { thrown = e; }
    assert.equal(store.schedules.length, 0, 'placeholder removed on failure');
    assert.ok(thrown);
    assert.equal(store.toasts.length, 1);
    assert.equal(store.toasts[0].kind, 'error');
    assert.match(store.toasts[0].m, /Ghost/);
  });
});

describe('store.updateSchedule', () => {
  test('PUT with If-Match, replaces with server returned object', async () => {
    const store = await loadStoreWithMockApi();
    store.toasts = []; store.toast = function(m, k) { this.toasts.push({m,k}); };
    store.schedules = [{ id: 'sch_1', playlistName: 'P', displayID: 'D', startTime: '09:00', endTime: '17:00', _serverVersion: 5 }];
    installFetch({
      'PUT /api/schedules/sch_1': async () => ({ status: 200, body: { success: true, schedule: { id: 'sch_1', playlistName: 'P', displayID: 'D', startTime: '10:00', endTime: '17:00', _serverVersion: 6 } } }),
    });
    await store.updateSchedule('sch_1', { startTime: '10:00' });
    assert.equal(store.schedules[0].startTime, '10:00');
    assert.equal(store.schedules[0]._serverVersion, 6);
  });

  test('412 stale: rolls back, toasts conflict', async () => {
    const store = await loadStoreWithMockApi();
    store.toasts = []; store.toast = function(m, k) { this.toasts.push({m,k}); };
    store.schedules = [{ id: 'sch_1', startTime: '09:00', _serverVersion: 5 }];
    installFetch({
      'PUT /api/schedules/sch_1': async () => ({ status: 412, body: { success: false, error: 'schedule was modified by another writer', currentVersion: 7 } }),
    });
    let thrown = null;
    try {
      await store.updateSchedule('sch_1', { startTime: '10:00' });
    } catch (e) { thrown = e; }
    assert.equal(store.schedules[0].startTime, '09:00');
    assert.equal(store.schedules[0]._serverVersion, 5);
    assert.ok(thrown);
    assert.equal(thrown.status, 412);
    assert.equal(store.toasts.length, 1);
  });
});

describe('store.deleteSchedule', () => {
  test('removes from local + DELETE', async () => {
    const store = await loadStoreWithMockApi();
    store.toasts = []; store.toast = function(m, k) { this.toasts.push({m,k}); };
    store.schedules = [{ id: 'sch_1' }, { id: 'sch_2' }];
    installFetch({
      'DELETE /api/schedules/sch_1': async () => ({ status: 204, body: '' }),
    });
    await store.deleteSchedule('sch_1');
    assert.deepEqual(store.schedules.map(s => s.id), ['sch_2']);
  });
});

describe('store.toast + dismissToast', () => {
  test('toast appends; dismissToast removes by id', async () => {
    const store = await loadStoreWithMockApi();
    store.toasts = [];
    const id = store.toast('hello', 'info');
    assert.equal(store.toasts.length, 1);
    store.dismissToast(id);
    assert.equal(store.toasts.length, 0);
  });
});
```

Run: expect FAIL on first test (store still throws 'not implemented').

### Step 3.2: Implement the store mutations

Replace the stubs at the bottom of `js/timeline/store.js` (the four `throw new Error(...)` methods) with this block. **Keep everything else above unchanged**:

```javascript
    // ---- Toast state ----
    toasts: [],
    _nextToastId: 1,
    toast(msg, kind = 'info') {
      const id = this._nextToastId++;
      this.toasts.push({ id, msg: String(msg), kind });
      // Auto-dismiss after 4s for info; errors stay until clicked.
      if (kind !== 'error' && typeof setTimeout === 'function') {
        setTimeout(() => this.dismissToast(id), 4000);
      }
      return id;
    },
    dismissToast(id) {
      this.toasts = this.toasts.filter(t => t.id !== id);
    },

    // ---- Selection state ----
    selection: new Set(),    // scheduleIds (multi-select via Shift-click)
    selectClip(id, multi = false) {
      if (!multi) this.selection = new Set([id]);
      else { const s = new Set(this.selection); s.has(id) ? s.delete(id) : s.add(id); this.selection = s; }
    },
    clearSelection() { this.selection = new Set(); },

    // ---- Drill-in state ----
    drilledIn: null,   // scheduleId | null
    drillInto(id) { this.drilledIn = (this.drilledIn === id) ? null : id; },

    // ---- Mutations ----
    /**
     * POST a new schedule. Optimistic: append a placeholder with a temp
     * id; on success, swap in the server's authoritative id +
     * _serverVersion; on failure, roll back the array.
     */
    async createSchedule(partial) {
      const { withRollback } = await import('./util/optimistic.js');
      const { api } = await import('./api.js');
      const tempId = '__pending_' + Math.random().toString(36).slice(2);
      const placeholder = { id: tempId, _serverVersion: 0, ...partial };
      await withRollback(this, ['schedules'],
        () => { this.schedules.push(placeholder); },
        async () => {
          const created = await api.createSchedule(partial);
          // Swap the placeholder for the server's authoritative copy.
          const idx = this.schedules.findIndex(s => s.id === tempId);
          if (idx >= 0) this.schedules[idx] = created;
          else this.schedules.push(created);
          return created;
        },
      );
    },

    /**
     * PUT a partial patch with If-Match. Optimistic: apply the patch
     * locally; on success, replace with the server's returned object
     * (carrying the new _serverVersion); on failure (412 stale or
     * other), restore the pre-patch snapshot.
     */
    async updateSchedule(id, patch) {
      const { withRollback } = await import('./util/optimistic.js');
      const { api } = await import('./api.js');
      const cur = this.schedules.find(s => s.id === id);
      if (!cur) throw new Error(`updateSchedule: schedule '${id}' not found`);
      const ifMatch = cur._serverVersion;
      await withRollback(this, ['schedules'],
        () => { Object.assign(cur, patch); },
        async () => {
          const updated = await api.updateSchedule(id, patch, ifMatch);
          const idx = this.schedules.findIndex(s => s.id === id);
          if (idx >= 0) this.schedules[idx] = updated;
          return updated;
        },
      );
    },

    /**
     * DELETE a schedule. Optimistic: remove from local; on failure,
     * restore. 204 success; 4xx errors surface in the toast.
     */
    async deleteSchedule(id) {
      const { withRollback } = await import('./util/optimistic.js');
      const { api } = await import('./api.js');
      await withRollback(this, ['schedules'],
        () => { this.schedules = this.schedules.filter(s => s.id !== id); },
        async () => { await api.deleteSchedule(id); },
      );
    },

    /**
     * PUT a partial playlist patch with If-Match. Same rollback shape
     * as updateSchedule but the slice is `playlists` (a dict, not list).
     */
    async updatePlaylist(name, patch) {
      const { withRollback } = await import('./util/optimistic.js');
      const { api } = await import('./api.js');
      const cur = this.playlists[name];
      if (!cur) throw new Error(`updatePlaylist: playlist '${name}' not found`);
      const ifMatch = cur._serverVersion;
      await withRollback(this, ['playlists'],
        () => { this.playlists[name] = { ...cur, ...patch }; },
        async () => {
          const updated = await api.updatePlaylist(name, patch, ifMatch);
          this.playlists[name] = updated;
          return updated;
        },
      );
    },
```

The dynamic `import('./util/optimistic.js')` inside each method (rather than a top-of-file import) is intentional: it lets the Node-test cache-bust the module load via `import(storeUrl + '?t=' + ...)` without holding stale references to util/optimistic.

### Step 3.3: Run the store-mutation tests — expect PASS

```bash
node --test tests/unit/js/test_store_mutations.js
```

Expected: 6/6 pass.

### Step 3.4: Run all JS tests — confirm no regressions

```bash
cmd //c "tests\unit\js\run_js_tests.bat"
```

Expected: 33 (PR-4a) + 5 (api mutations) + 3 (optimistic) + 6 (store mutations) = 47 green.

### Step 3.5: Commit

```bash
git add js/timeline/store.js tests/unit/js/test_store_mutations.js
git commit -m "feat(timeline/store): real createSchedule/updateSchedule/deleteSchedule/updatePlaylist

Replaces PR-4a's four 'not implemented' stubs with implementations
that follow the spec §8 optimistic-local + server-confirm + rollback
pattern via util/optimistic.withRollback.

Also adds:
- toast(msg, kind) + dismissToast(id) + this.toasts ring
  (errors stick, info auto-dismisses after 4s)
- selection: Set of scheduleIds for click-select (single/multi)
- drilledIn: scheduleId | null for the double-click drill-in

Dynamic import('./util/optimistic.js') inside each method makes the
Node tests' cache-busting (?t=...) work cleanly.

6 new store-mutation tests cover create/update/delete success + 412
rollback + toast lifecycle.

Part of PR-4b of the admin-timeline-redesign spec."
```

---

## Task 4: Pure geometry helpers + tests

Pixels-to-time conversions and snap-to-15-min used by every drag handler.

**Files:**
- Create: `js/timeline/util/snap.js`
- Create: `tests/unit/js/test_snap.js`
- Modify: `tests/unit/js/test_timeline_smoke.js`

### Step 4.1: Failing tests

Create `tests/unit/js/test_snap.js`:

```javascript
import { test, describe } from 'node:test';
import assert from 'node:assert';
import { pxToHour, hourToHHMM, snapTo15min, isoDateAddHour } from '../../../js/timeline/util/snap.js';

describe('pxToHour', () => {
  test('0px -> 0h, full width -> 24h', () => {
    assert.equal(pxToHour(0, 240), 0);
    assert.equal(pxToHour(240, 240), 24);
    assert.equal(pxToHour(120, 240), 12);
  });
  test('clips to [0, 24]', () => {
    assert.equal(pxToHour(-10, 240), 0);
    assert.equal(pxToHour(500, 240), 24);
  });
});

describe('snapTo15min', () => {
  test('rounds to nearest 0.25', () => {
    assert.equal(snapTo15min(9.0),  9.0);
    assert.equal(snapTo15min(9.07), 9.0);
    assert.equal(snapTo15min(9.13), 9.25);
    assert.equal(snapTo15min(9.4),  9.5);
    assert.equal(snapTo15min(9.7),  9.75);
    assert.equal(snapTo15min(9.9), 10.0);
  });
});

describe('hourToHHMM', () => {
  test('integer hours and quarter hours', () => {
    assert.equal(hourToHHMM(0),    '00:00');
    assert.equal(hourToHHMM(9.25), '09:15');
    assert.equal(hourToHHMM(13.5), '13:30');
    assert.equal(hourToHHMM(13.75),'13:45');
    assert.equal(hourToHHMM(24),   '23:59');  // clamp end of day
  });
});

describe('isoDateAddHour', () => {
  test('returns same date for in-range hour', () => {
    assert.equal(isoDateAddHour('2026-06-05', 9), '2026-06-05');
  });
  test('rolls to next date when hour >= 24', () => {
    assert.equal(isoDateAddHour('2026-06-05', 25), '2026-06-06');
  });
});
```

Run: expect FAIL — module missing.

### Step 4.2: Implement `js/timeline/util/snap.js`

```javascript
/**
 * Pixel-to-time conversion + snap-to-grid helpers for the timeline's
 * drag-and-drop handlers. Pure functions, Node-testable, no DOM.
 *
 * Convention: hours are floats 0.0..24.0 where 24.0 is end-of-day
 * (and clamped to '23:59' in HH:MM rendering — the schedule rep
 * doesn't have a 24:00 form). Sub-hour precision is snapped to 15 min.
 */

export function pxToHour(pxFromGridLeft, gridWidthPx) {
  if (!gridWidthPx || gridWidthPx <= 0) return 0;
  const frac = pxFromGridLeft / gridWidthPx;
  if (frac <= 0) return 0;
  if (frac >= 1) return 24;
  return frac * 24;
}

export function snapTo15min(hour) {
  return Math.round(hour * 4) / 4;
}

export function hourToHHMM(hour) {
  if (hour >= 24) return '23:59';
  if (hour < 0) hour = 0;
  const h = Math.floor(hour);
  const m = Math.round((hour - h) * 60);
  return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}`;
}

export function isoDateAddHour(isoDate, hour) {
  // Returns YYYY-MM-DD of (isoDate's midnight UTC + hour). Used when
  // a clip drag crosses midnight.
  const [y, m, d] = isoDate.split('-').map(Number);
  const baseMs = Date.UTC(y, m - 1, d);
  const target = baseMs + Math.floor(hour) * 3600_000;
  const td = new Date(target);
  return `${td.getUTCFullYear()}-${String(td.getUTCMonth()+1).padStart(2,'0')}-${String(td.getUTCDate()).padStart(2,'0')}`;
}
```

Run `node --test tests/unit/js/test_snap.js` → expect 4 PASS.

### Step 4.3: Add to smoke + commit

Append `'js/timeline/util/snap.js'` to `MODULES`.

```bash
git add js/timeline/util/snap.js tests/unit/js/test_snap.js tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline/util): snap.js — px↔hour + 15-min snap

Pure helpers used by every drag handler. pxToHour clamps to [0,24];
snapTo15min rounds to nearest 0.25h; hourToHHMM clamps 24.0 → '23:59'
(schedule rep doesn't have 24:00); isoDateAddHour handles midnight
rollover for cross-day drags.

Part of PR-4b of the admin-timeline-redesign spec."
```

---

## Task 5: Toast component + UI wiring

The store now collects toasts in `this.toasts` but nothing renders them. Add the small Alpine component + admin.html anchoring.

**Files:**
- Create: `js/timeline/timeline/toast.js`
- Modify: `js/timeline/index.js` (register mmToast)
- Modify: `admin.html` (toast container in the timeline section + CSS)

### Step 5.1: Implement `js/timeline/timeline/toast.js`

```javascript
/**
 * Bottom-right toast stack. Reads from $store.mm.toasts (added in
 * Task 3). Error toasts stay until clicked; info auto-dismisses
 * after 4s via store.toast()'s internal setTimeout.
 *
 * Click anywhere on a toast to dismiss it manually.
 */
export function mmToastComponent() {
  return {
    get items() { return this.$store.mm.toasts; },
    dismiss(id) { this.$store.mm.dismissToast(id); },
  };
}
```

### Step 5.2: Register in index.js + add HTML/CSS

Update `js/timeline/index.js` to import and register `mmToastComponent`. Add `Alpine.data('mmToast', mmToastComponent);` in the bootstrap.

In `admin.html`, find the existing `</section>` closing the timeline route. Just BEFORE that closing tag, insert:

```html
        <div x-data="mmToast" class="mm-toast-stack" x-show="items.length > 0">
          <template x-for="t in items" :key="t.id">
            <div class="mm-toast" :class="'mm-toast-' + t.kind" @click="dismiss(t.id)" x-text="t.msg"></div>
          </template>
        </div>
```

Add CSS (with the other .mm-* rules):

```css
.mm-toast-stack { position: fixed; right: 16px; bottom: 16px; display: flex; flex-direction: column; gap: 6px; z-index: 1000; max-width: 360px; }
.mm-toast { padding: 8px 12px; border-radius: 6px; background: var(--bg-elev, #2a2a2a); color: var(--text, #eee); cursor: pointer; box-shadow: 0 2px 12px rgba(0,0,0,0.5); font-size: 12px; }
.mm-toast-error { background: var(--err, #c33); }
.mm-toast-info  { background: var(--accent, #345); }
```

### Step 5.3: Commit

```bash
git add js/timeline/timeline/toast.js js/timeline/index.js admin.html tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): mmToast component — fixed-bottom-right toast stack

Renders store.toasts (added in Task 3). Error toasts stay until
clicked; info auto-dismisses after 4s. Click anywhere on a toast
to dismiss manually.

CSS uses existing theme custom-props so light/dark mode both work.

Part of PR-4b of the admin-timeline-redesign spec."
```

---

## Task 6: Drag-state shared module + playlist-to-track drop

The first interactive flow: drag a playlist from the left bin onto a Day-view track → creates a schedule at the drop time.

**Files:**
- Create: `js/timeline/drag/dragstate.js`
- Create: `js/timeline/drag/playlist-to-track.js`
- Modify: `js/timeline/timeline/timeline.js` (renderDay attaches `data-display-id` to each track row + a `mm-track-droparea` overlay)
- Modify: `js/timeline/bin/playlist-bin.js` (template adds `draggable="true"` + `@dragstart`)
- Modify: `js/timeline/index.js` (wire `attachPlaylistToTrack(store)`)

### Step 6.1: Implement `js/timeline/drag/dragstate.js`

```javascript
/**
 * Tiny shared module holding a single in-flight drag payload. Drag
 * handlers in this directory cooperate via this object — HTML5 drag
 * events fire dragstart on the source element and drop on the target,
 * with no payload accessible to handlers that don't share the
 * `dataTransfer` reference (cross-frame restrictions). We mirror the
 * payload here so multi-handler coordination is simple.
 */

let _current = null;

export function setDrag(payload) { _current = payload; }
export function getDrag() { return _current; }
export function clearDrag() { _current = null; }
```

### Step 6.2: Modify `js/timeline/bin/playlist-bin.js`

Add a `dragStart(playlistName)` method:

```javascript
import { setDrag } from '../drag/dragstate.js';

export function mmPlaylistBinComponent() {
  return {
    get list() {
      return Object.values(this.$store.mm.playlists || {})
        .sort((a, b) => a.name.localeCompare(b.name));
    },
    dragStart(name, ev) {
      // HTML5 drag — set a payload so the drop handler can pick it up
      // even across re-renders. The mirror in dragstate.js is what the
      // track-row drop handler actually reads (dataTransfer is opaque
      // in some browser contexts).
      ev.dataTransfer.setData('application/x-mm-playlist', name);
      ev.dataTransfer.effectAllowed = 'copy';
      setDrag({ kind: 'playlist', playlistName: name });
    },
  };
}
```

Then in `admin.html`'s playlist-bin template, update the `<li>` to:

```html
                <template x-for="p in list" :key="p.name">
                  <li class="mm-bin-item"
                      draggable="true"
                      @dragstart="dragStart(p.name, $event)"
                      x-text="p.name"></li>
                </template>
```

### Step 6.3: Implement `js/timeline/drag/playlist-to-track.js`

```javascript
/**
 * Drag a playlist from the left bin onto a Day-view track row →
 * createSchedule(playlistName, displayID, startTime, endTime).
 *
 * Each track row in Day view has `data-display-id` (added in
 * timeline.js's renderDay). The drop event reads the playlist name
 * from the drag payload + computes the drop hour from the X
 * coordinate within the track row's bounding rect.
 */
import { getDrag, clearDrag } from './dragstate.js';
import { pxToHour, snapTo15min, hourToHHMM } from '../util/snap.js';

const DEFAULT_DURATION_HR = 1;  // new schedules are 1h by default; resize after

export function attachPlaylistToTrack(store) {
  document.addEventListener('dragover', onDragOver, true);
  document.addEventListener('drop', onDrop, true);

  function onDragOver(ev) {
    const drag = getDrag();
    if (!drag || drag.kind !== 'playlist') return;
    // Allow drop only on track rows in the Day-view grid.
    const row = ev.target.closest('.mm-day-grid [data-display-id]');
    if (!row) return;
    ev.preventDefault();   // required to enable drop
    ev.dataTransfer.dropEffect = 'copy';
  }

  function onDrop(ev) {
    const drag = getDrag();
    if (!drag || drag.kind !== 'playlist') return;
    const row = ev.target.closest('.mm-day-grid [data-display-id]');
    if (!row) return;
    ev.preventDefault();
    const displayID = row.dataset.displayId;
    // Compute drop hour from X within the GRID (not the row — the row
    // is just the track header). The actual playlist area is the
    // rest of the row's grid columns.
    const grid = row.closest('.mm-day-grid');
    if (!grid) return;
    const gridRect = grid.getBoundingClientRect();
    const labelColPx = 110;  // matches grid-template-columns: 110px in timeline.js
    const usableLeft = gridRect.left + labelColPx;
    const usableWidth = gridRect.width - labelColPx;
    const startHr = snapTo15min(pxToHour(ev.clientX - usableLeft, usableWidth));
    const endHr   = Math.min(24, startHr + DEFAULT_DURATION_HR);
    const startTime = hourToHHMM(startHr);
    const endTime   = hourToHHMM(endHr);
    clearDrag();
    store.createSchedule({
      playlistName: drag.playlistName,
      displayID,
      startTime,
      endTime,
      freq: 'DAILY',
      dtstart: store.viewDate,
    }).catch(() => {/* toast already surfaced */});
  }
}
```

### Step 6.4: Tag track rows + wire into index.js

In `js/timeline/timeline/timeline.js`'s `renderDay`, find:

```javascript
        html += `<div class="mm-track-row" style="grid-column:1">${trackHeaderHtml({...})}</div>`;
```

Change to also tag the row with `data-display-id` AND add a hidden full-width drop overlay so the drop target spans the full track horizontally:

```javascript
        html += `<div class="mm-track-row" data-display-id="${escapeAttr(did)}" style="grid-column:1">${trackHeaderHtml({...})}</div>`;
        html += `<div class="mm-track-droparea" data-display-id="${escapeAttr(did)}" style="grid-column:2/26;"></div>`;
```

In `admin.html` CSS, add:

```css
.mm-track-droparea { min-height: 32px; }
.mm-track-droparea.mm-drag-target { background: rgba(80, 160, 255, 0.15); }
```

In `js/timeline/index.js`, add:

```javascript
import { attachPlaylistToTrack } from './drag/playlist-to-track.js';
// ... inside bootstrap(), after store.hydrate() chain:
attachPlaylistToTrack(store);
```

### Step 6.5: Smoke + commit

Add `'js/timeline/drag/dragstate.js'` and `'js/timeline/drag/playlist-to-track.js'` to `MODULES`. Run smoke — must pass.

```bash
git add js/timeline/drag/ js/timeline/timeline/timeline.js js/timeline/bin/playlist-bin.js js/timeline/index.js admin.html tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): drag playlist from bin onto track creates schedule

Drag-and-drop wiring with HTML5 events. Track rows in Day view gain
data-display-id. Playlist bin entries are draggable; their dragstart
sets a payload via dragstate.js. Global dragover/drop listeners pick
the right track and compute the drop hour from X coords + grid width
+ 15-min snap.

Default new-schedule duration is 1h. Operator resizes via clip-edge
drag (Task 8).

Part of PR-4b of the admin-timeline-redesign spec."
```

---

## Task 7: Clip-body drag (move within track + cross-track)

**Files:**
- Create: `js/timeline/drag/clip-move.js`
- Modify: `js/timeline/timeline/clip.js` (add `draggable="true"` to clip blocks)
- Modify: `js/timeline/index.js` (wire `attachClipMove(store)`)

### Step 7.1: Implement `js/timeline/drag/clip-move.js`

```javascript
/**
 * Drag a clip body to move it within the same track (changes
 * startTime/endTime preserving duration) or across tracks (also
 * changes displayID). Uses HTML5 drag events with a `mm-clip-drag`
 * payload kind. Cross-track requires the target to be a track-droparea
 * with a different displayID.
 */
import { setDrag, getDrag, clearDrag } from './dragstate.js';
import { pxToHour, snapTo15min, hourToHHMM } from '../util/snap.js';

export function attachClipMove(store) {
  document.addEventListener('dragstart', onDragStart, true);
  document.addEventListener('drop', onDrop, true);

  function onDragStart(ev) {
    const clip = ev.target.closest('.mm-clip');
    if (!clip) return;
    const id = clip.dataset.scheduleId;
    if (!id) return;
    const sched = store.schedules.find(s => s.id === id);
    if (!sched) return;
    ev.dataTransfer.effectAllowed = 'move';
    ev.dataTransfer.setData('application/x-mm-clip', id);
    // Record the clip's rect + the click offset so we can compute the
    // drop position as "where the LEFT edge would land".
    const r = clip.getBoundingClientRect();
    setDrag({
      kind: 'clip-move',
      scheduleId: id,
      offsetXInClip: ev.clientX - r.left,
      originalStartTime: sched.startTime,
      originalEndTime: sched.endTime,
      originalDisplayID: sched.displayID,
    });
  }

  function onDrop(ev) {
    const drag = getDrag();
    if (!drag || drag.kind !== 'clip-move') return;
    const droparea = ev.target.closest('.mm-track-droparea');
    if (!droparea) return;
    ev.preventDefault();
    const newDisplay = droparea.dataset.displayId;
    const grid = droparea.closest('.mm-day-grid');
    if (!grid) return;
    const gridRect = grid.getBoundingClientRect();
    const labelColPx = 110;
    const usableLeft = gridRect.left + labelColPx;
    const usableWidth = gridRect.width - labelColPx;
    // Drop X minus the offset = where the clip's LEFT edge lands.
    const startHr = snapTo15min(pxToHour((ev.clientX - drag.offsetXInClip) - usableLeft, usableWidth));
    const duration = hoursBetween(drag.originalStartTime, drag.originalEndTime);
    const endHr = Math.min(24, startHr + duration);
    const patch = {
      startTime: hourToHHMM(startHr),
      endTime: hourToHHMM(endHr),
    };
    if (newDisplay !== drag.originalDisplayID) patch.displayID = newDisplay;
    clearDrag();
    store.updateSchedule(drag.scheduleId, patch).catch(() => {});
  }
}

function hoursBetween(startHHMM, endHHMM) {
  const [sh, sm] = startHHMM.split(':').map(Number);
  const [eh, em] = endHHMM.split(':').map(Number);
  let h = (eh + em / 60) - (sh + sm / 60);
  if (h < 0) h += 24;  // cross-midnight
  return h;
}
```

### Step 7.2: Make clips draggable

In `js/timeline/timeline/clip.js`'s `clipDayHtml`, change the outer `<div class="mm-clip" ...>` to include `draggable="true"`:

```javascript
    <div class="mm-clip" draggable="true" data-schedule-id="${escapeAttr(placement.scheduleId)}"
         style="grid-column:${colStart} / ${colEnd}; margin-left:${leftPct}%; margin-right:${rightPct}%;">
```

### Step 7.3: Wire + commit

Add `import { attachClipMove }` to index.js + `attachClipMove(store)` in bootstrap. Add `'js/timeline/drag/clip-move.js'` to MODULES.

```bash
git add js/timeline/drag/clip-move.js js/timeline/timeline/clip.js js/timeline/index.js tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): drag clip body to move within/across tracks

Cross-track moves change displayID. Drop position computed from drop
X minus the original click offset (so the LEFT edge of the clip lands
where the operator dragged it, not the click point). 15-min snap.
Duration preserved.

Part of PR-4b of the admin-timeline-redesign spec."
```

---

## Task 8: Clip-edge resize

**Files:**
- Create: `js/timeline/drag/clip-resize.js`
- Modify: `js/timeline/timeline/clip.js` (add left + right edge handles)
- Modify: `admin.html` (CSS for resize handles)
- Modify: `js/timeline/index.js`

### Step 8.1: Implement `js/timeline/drag/clip-resize.js`

```javascript
/**
 * Drag the left or right edge of a clip to resize the schedule. Uses
 * pointer events (not HTML5 drag) because we need continuous position
 * tracking + visual feedback as the operator drags — the HTML5 drag
 * API hides the source mid-drag, which would obscure the resize.
 *
 * On pointerdown on a `.mm-clip-resize-handle`, capture the pointer
 * and track pointermove until pointerup. The clip's startTime or
 * endTime updates locally on every move (visual feedback); the PUT
 * fires once on pointerup.
 */
import { pxToHour, snapTo15min, hourToHHMM } from '../util/snap.js';

export function attachClipResize(store) {
  document.addEventListener('pointerdown', onPointerDown, true);

  function onPointerDown(ev) {
    const handle = ev.target.closest('.mm-clip-resize-handle');
    if (!handle) return;
    const clip = handle.closest('.mm-clip');
    if (!clip) return;
    const id = clip.dataset.scheduleId;
    const edge = handle.dataset.edge;   // 'left' or 'right'
    const sched = store.schedules.find(s => s.id === id);
    if (!sched) return;
    ev.preventDefault();
    handle.setPointerCapture(ev.pointerId);
    const grid = clip.closest('.mm-day-grid');
    const gridRect = grid.getBoundingClientRect();
    const labelColPx = 110;
    const usableLeft = gridRect.left + labelColPx;
    const usableWidth = gridRect.width - labelColPx;
    const origStart = sched.startTime;
    const origEnd   = sched.endTime;

    function onMove(mv) {
      const hr = snapTo15min(pxToHour(mv.clientX - usableLeft, usableWidth));
      if (edge === 'left') {
        sched.startTime = hourToHHMM(Math.min(hr, hhmmToHour(sched.endTime) - 0.25));
      } else {
        sched.endTime = hourToHHMM(Math.max(hr, hhmmToHour(sched.startTime) + 0.25));
      }
    }
    function onUp() {
      handle.removeEventListener('pointermove', onMove);
      handle.removeEventListener('pointerup', onUp);
      const patch = edge === 'left'
        ? { startTime: sched.startTime }
        : { endTime: sched.endTime };
      // Roll back the visual change before issuing PUT so withRollback
      // can reapply it cleanly on success/failure.
      sched.startTime = origStart;
      sched.endTime = origEnd;
      store.updateSchedule(id, patch).catch(() => {});
    }
    handle.addEventListener('pointermove', onMove);
    handle.addEventListener('pointerup', onUp);
  }
}

function hhmmToHour(hhmm) {
  const [h, m] = hhmm.split(':').map(Number);
  return h + m / 60;
}
```

### Step 8.2: Add resize handles to `clip.js`

In `clipDayHtml`'s return, BEFORE the closing `</div>`, insert:

```javascript
      <div class="mm-clip-resize-handle" data-edge="left"></div>
      <div class="mm-clip-resize-handle" data-edge="right"></div>
```

### Step 8.3: CSS for handles

Add to admin.html:

```css
.mm-clip { user-select: none; }
.mm-clip-resize-handle {
  position: absolute; top: 0; bottom: 0; width: 6px; cursor: ew-resize;
  background: transparent;
}
.mm-clip-resize-handle[data-edge="left"]  { left: 0;  }
.mm-clip-resize-handle[data-edge="right"] { right: 0; }
.mm-clip-resize-handle:hover { background: rgba(255,255,255,0.15); }
```

### Step 8.4: Wire + commit

Add to index.js + smoke MODULES.

```bash
git add js/timeline/drag/clip-resize.js js/timeline/timeline/clip.js js/timeline/index.js admin.html tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): clip-edge resize via pointer events

Pointer events (not HTML5 drag) for continuous-feedback resize.
6px-wide invisible handles on both edges; cursor: ew-resize on
hover. 15-min snap; can't shrink below 15 min. PUT fires on
pointerup with the corresponding startTime or endTime patch.

Part of PR-4b of the admin-timeline-redesign spec."
```

---

## Task 9: Click-selection + Delete key + right-click delete

**Files:**
- Create: `js/timeline/select.js`
- Modify: `js/timeline/timeline/clip.js` (add `.mm-clip-selected` class binding)
- Modify: `js/timeline/index.js`
- Modify: `admin.html` (selection outline CSS)

### Step 9.1: Implement `js/timeline/select.js`

```javascript
/**
 * Click selection + Del key + right-click → delete. Stores selection
 * as a Set<scheduleId> in store.selection (added in Task 3).
 *
 * Clicking on a .mm-clip selects it (Shift adds to multi). Clicking
 * empty grid clears. Del key calls deleteSchedule on each selected id.
 * Right-click on a clip opens a tiny "Delete" context option (Phase
 * 1 of context menus — full right-click menu lands later).
 */
export function attachSelection(store) {
  document.addEventListener('click', onClick, true);
  document.addEventListener('keydown', onKeyDown, true);
  document.addEventListener('contextmenu', onContextMenu, true);

  function onClick(ev) {
    const clip = ev.target.closest('.mm-clip');
    if (clip) {
      const id = clip.dataset.scheduleId;
      if (id) store.selectClip(id, ev.shiftKey);
      return;
    }
    // Click on empty timeline area clears selection
    if (ev.target.closest('.mm-day-grid, .mm-week-grid, .mm-month-grid')) {
      if (!ev.shiftKey) store.clearSelection();
    }
  }

  function onKeyDown(ev) {
    if (ev.key !== 'Delete' && ev.key !== 'Backspace') return;
    // Ignore when typing in an input
    const tag = (ev.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || ev.target.isContentEditable) return;
    if (store.selection.size === 0) return;
    ev.preventDefault();
    const ids = Array.from(store.selection);
    if (ids.length > 3 && !confirm(`Delete ${ids.length} schedules?`)) return;
    for (const id of ids) {
      store.deleteSchedule(id).catch(() => {});
    }
    store.clearSelection();
  }

  function onContextMenu(ev) {
    const clip = ev.target.closest('.mm-clip');
    if (!clip) return;
    ev.preventDefault();
    const id = clip.dataset.scheduleId;
    if (!id) return;
    if (!confirm('Delete this schedule?')) return;
    store.deleteSchedule(id).catch(() => {});
  }
}
```

### Step 9.2: Reflect selection visually on clips

In `clip.js`'s `clipDayHtml`, change the outer div to add a conditional class. Since clip.js is a pure render-helper (no Alpine reactivity), we have to re-render when selection changes. The simplest path: pass `selection` as an input and add the class. Update `timeline.js`'s `renderDay` to pass it down. For a minimal change here, add it as a CSS rule based on `data-selected` attr that we set imperatively in select.js.

Update `select.js`'s `onClick` to ALSO sync DOM `data-selected` attrs after a selection change:

```javascript
  function syncSelectionDom() {
    const sel = store.selection;
    for (const el of document.querySelectorAll('.mm-clip')) {
      const isSelected = sel.has(el.dataset.scheduleId);
      el.classList.toggle('mm-clip-selected', isSelected);
    }
  }
```

Call `syncSelectionDom()` at the end of `onClick`. Also call it once on `attachSelection` setup (in case selection was set programmatically before).

### Step 9.3: CSS

```css
.mm-clip { outline: 1px solid transparent; transition: outline 60ms; }
.mm-clip-selected { outline: 2px solid var(--accent, #6ad); }
```

### Step 9.4: Wire + commit

```bash
git add js/timeline/select.js js/timeline/index.js admin.html tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): click selection + Delete key + right-click delete

Single-click selects a clip; Shift-click toggles multi-select. Click
empty grid clears. Del key deletes selected (with confirm if >3).
Right-click on a clip opens a confirm-then-delete prompt — full
context menu is PR-4c.

Selection state lives in store.selection (Set<scheduleId>); DOM
sync via data-selected/.mm-clip-selected class for visual outline.

Part of PR-4b of the admin-timeline-redesign spec."
```

---

## Task 10: Double-click clip → drill in to show playlist items

**Files:**
- Create: `js/timeline/drill-in.js`
- Modify: `js/timeline/timeline/timeline.js` (render drilled-in sub-track when store.drilledIn matches a clip's schedule)
- Modify: `admin.html` (CSS for sub-track)

### Step 10.1: Implement drill-in handler

`js/timeline/drill-in.js`:

```javascript
/**
 * Double-click a clip → store.drillInto(scheduleId). The timeline.js
 * renderer then emits an inline sub-track underneath that track row
 * showing the playlist's items as separate sub-clips. Second
 * double-click collapses (drillInto toggles).
 */
export function attachDrillIn(store) {
  document.addEventListener('dblclick', (ev) => {
    const clip = ev.target.closest('.mm-clip');
    if (!clip) return;
    const id = clip.dataset.scheduleId;
    if (id) store.drillInto(id);
  });
}
```

### Step 10.2: Render sub-track in `timeline.js`

In `renderDay()`, after the clip loop for a given track, if `store.drilledIn` matches any placement's scheduleId on this track, emit a sub-track row showing the playlist's items. Replace the per-track loop body in `renderDay()`:

```javascript
        for (const p of placements) {
          const conflictRanges = conflicts
            .filter(c => c.loserId === p.scheduleId)
            .map(c => ({ overlapStartMs: c.overlapStartMs, overlapEndMs: c.overlapEndMs }));
          html += clipDayHtml({ placement: p, viewDateMs: win.startMs, conflictRanges });
        }
        // Drill-in sub-track for the currently-drilled schedule (if any)
        const drilled = this.$store.mm.drilledIn;
        if (drilled) {
          const drilledPlacement = placements.find(p => p.scheduleId === drilled);
          if (drilledPlacement) {
            const playlist = this.$store.mm.playlists[drilledPlacement.playlistName];
            html += this.renderDrillInRow(drilledPlacement, playlist, win.startMs);
          }
        }
```

Add the `renderDrillInRow` method to the component:

```javascript
    renderDrillInRow(placement, playlist, viewDateMs) {
      const items = (playlist && playlist.items) || [];
      if (items.length === 0) {
        return `<div class="mm-drillin-row" style="grid-column:2/26">
          <div class="mm-drillin-empty">No items in playlist '${this.escapeText(playlist?.name || '')}'.
            Drag media files here to add.</div>
        </div>`;
      }
      // Distribute items evenly across the clip's hour-range as a
      // visual approximation. Actual playback order is the items
      // array's order (no time-of-day inside a playlist).
      const startHr = (placement.startMs - viewDateMs) / (60*60*1000);
      const endHr   = (placement.endMs   - viewDateMs) / (60*60*1000);
      const width = endHr - startHr;
      const perItem = width / items.length;
      let html = `<div class="mm-drillin-row" style="grid-column:2/26" data-playlist-name="${this.escapeAttr(playlist.name)}" data-schedule-id="${this.escapeAttr(placement.scheduleId)}">`;
      for (let i = 0; i < items.length; i++) {
        const it = items[i];
        const file = (typeof it === 'string') ? it : (it.file || '');
        const itemStartHr = startHr + i * perItem;
        const itemEndHr = itemStartHr + perItem;
        const colStart = 1 + Math.floor((itemStartHr / 24) * 24) + 1;  // approx
        const colEnd   = 1 + Math.ceil((itemEndHr / 24) * 24)  + 1;
        const leftPct  = ((itemStartHr / 24) * 100);
        const widthPct = ((itemEndHr - itemStartHr) / 24) * 100;
        html += `<div class="mm-drillin-item" data-item-index="${i}" style="left:${leftPct}%; width:${widthPct}%;" title="${this.escapeAttr(file)}">${this.escapeText(basename(file))}</div>`;
      }
      html += `</div>`;
      return html;
    },
```

Add `basename` helper at the bottom of the file:

```javascript
function basename(p) { return String(p || '').split('/').pop() || ''; }
```

Add a method to expose escape helpers via `this`:

```javascript
    escapeText(s) { return escapeText(s); },
    escapeAttr(s) { return escapeAttr(s); },
```

### Step 10.3: CSS

```css
.mm-drillin-row { position: relative; min-height: 30px; background: rgba(255,255,255,0.04); border-left: 3px solid var(--accent, #6ad); padding: 4px 0; }
.mm-drillin-item { position: absolute; top: 4px; bottom: 4px; background: var(--bg-elev, #333); border-radius: 3px; padding: 0 4px; font-size: 11px; line-height: 22px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; box-sizing: border-box; }
.mm-drillin-empty { padding: 6px 8px; font-size: 12px; color: var(--text-muted); }
.mm-drillin-item.mm-drag-target { outline: 2px dashed var(--accent, #6ad); }
```

### Step 10.4: Wire + commit

```bash
git add js/timeline/drill-in.js js/timeline/timeline/timeline.js js/timeline/index.js admin.html tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): double-click clip → drill in to show playlist items

Sub-track renders BELOW the track row, sharing its grid columns.
Items distributed evenly across the clip's hour-range as a visual
approximation; actual order is the playlist's items array. Empty
playlists show an empty-state hint pointing toward drag-from-bin.

Drill state toggles: dblclick the same clip again collapses.

Part of PR-4b of the admin-timeline-redesign spec."
```

---

## Task 11: Drag media from bin → drilled clip extends playlist

**Files:**
- Create: `js/timeline/drag/media-to-clip.js`
- Modify: `js/timeline/bin/media-bin.js` (draggable + dragStart)
- Modify: `admin.html` (media-bin template adds draggable + handler)
- Modify: `js/timeline/index.js`

### Step 11.1: Implement

`js/timeline/bin/media-bin.js` — update to expose dragStart:

```javascript
import { setDrag } from '../drag/dragstate.js';

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
    dragStart(item, ev) {
      ev.dataTransfer.setData('application/x-mm-media', item.url);
      ev.dataTransfer.effectAllowed = 'copy';
      setDrag({ kind: 'media', file: item.url, duration: item.duration ?? null });
    },
  };
}

function basename(p) { return p.split('/').pop(); }
```

In `admin.html` media-bin template, update `<li>`:

```html
                <template x-for="it in filtered()" :key="it.url">
                  <li class="mm-bin-item"
                      draggable="true"
                      @dragstart="dragStart(it, $event)">
                    <span x-text="it.name"></span>
                    <span class="size" style="color:var(--text-muted)" x-text="it.duration ? ' ' + it.duration + 's' : ''"></span>
                  </li>
                </template>
```

`js/timeline/drag/media-to-clip.js`:

```javascript
import { getDrag, clearDrag } from './dragstate.js';

export function attachMediaToClip(store) {
  document.addEventListener('dragover', (ev) => {
    const drag = getDrag();
    if (!drag || drag.kind !== 'media') return;
    const sub = ev.target.closest('.mm-drillin-row');
    if (!sub) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'copy';
  }, true);

  document.addEventListener('drop', (ev) => {
    const drag = getDrag();
    if (!drag || drag.kind !== 'media') return;
    const sub = ev.target.closest('.mm-drillin-row');
    if (!sub) return;
    ev.preventDefault();
    const playlistName = sub.dataset.playlistName;
    if (!playlistName) return;
    const playlist = store.playlists[playlistName];
    if (!playlist) return;
    const newItems = [...(playlist.items || []), {
      file: drag.file,
      duration: drag.duration,
    }];
    clearDrag();
    store.updatePlaylist(playlistName, { items: newItems }).catch(() => {});
  }, true);
}
```

### Step 11.2: Wire + commit

Add to index.js + smoke MODULES.

```bash
git add js/timeline/drag/media-to-clip.js js/timeline/bin/media-bin.js js/timeline/index.js admin.html tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): drag media from bin onto drilled clip extends playlist

Media bin items become draggable; their dragstart sets a payload
with the file URL + optional duration. drillin-row drop appends the
file as a new playlist item (PUT /api/playlists/{name} with
If-Match), and the sub-track re-renders showing the new item
distribution.

Part of PR-4b of the admin-timeline-redesign spec."
```

---

## Task 12: Upload button (media bin + Upload)

**Files:**
- Create: `js/timeline/upload.js`
- Modify: `js/timeline/bin/media-bin.js` (add an `+ Upload` button binding)
- Modify: `admin.html` (button + hidden file input)
- Modify: `js/timeline/index.js`

### Step 12.1: Implement

`js/timeline/upload.js`:

```javascript
/**
 * Wire the + Upload button on the media bin to a hidden file input;
 * uploads each chosen file via api.uploadMedia, then re-hydrates
 * store.media so the new files appear in the bin.
 */
import { api } from './api.js';

export function attachUpload(store) {
  const btn = document.getElementById('mmUploadBtn');
  const input = document.getElementById('mmUploadInput');
  if (!btn || !input) return;
  btn.addEventListener('click', () => input.click());
  input.addEventListener('change', async () => {
    const files = Array.from(input.files || []);
    if (files.length === 0) return;
    let ok = 0, fail = 0;
    for (const f of files) {
      try { await api.uploadMedia(f); ok += 1; }
      catch (e) { console.warn('upload failed', f.name, e); fail += 1; }
    }
    // Refresh just the media list (cheap GET)
    try {
      store.media = await api.listMedia();
    } catch (e) { /* hydrate retry will catch it */ }
    if (fail === 0) store.toast(`Uploaded ${ok} file(s)`, 'info');
    else store.toast(`${ok} uploaded, ${fail} failed`, 'error');
    input.value = '';   // allow re-uploading the same file
  });
}
```

In `admin.html`, somewhere convenient (near body end, outside the timeline section), add the hidden input:

```html
  <input type="file" id="mmUploadInput" multiple accept="image/*,video/*" style="display:none">
```

In the media-bin template inside the timeline section, after the search input + list, add a button:

```html
              <button class="btn btn-ghost" id="mmUploadBtn" type="button" style="margin-top:6px;">+ Upload</button>
```

### Step 12.2: Wire + commit

Add to index.js + smoke MODULES.

```bash
git add js/timeline/upload.js admin.html js/timeline/index.js tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): + Upload button on media bin

Hidden multiple-file input + bin button that triggers api.uploadMedia
per file. Auto-routes to /upload/image or /upload/video by extension.
On done, re-fetches /api/media so the new files appear in the bin.
Toast summarizes success/failure count.

Part of PR-4b of the admin-timeline-redesign spec."
```

---

## Task 13: Recurrence inline popover (basic editor)

The simplified inline editor that lets operators tweak freq / byweekday / end without leaving the timeline. The full 3-pane modal is PR-4c.

**Files:**
- Create: `js/timeline/recurrence-popover.js`
- Modify: `admin.html` (popover container + CSS)
- Modify: `js/timeline/index.js`

### Step 13.1: Implement

```javascript
/**
 * Inline recurrence popover. Opens when an operator clicks on a clip
 * with the Alt/Option key held (a temporary keyboard shortcut until
 * PR-4c lands the right-click context menu). Renders a small form:
 *   - freq dropdown (DAILY/WEEKLY/MONTHLY/YEARLY)
 *   - interval input (every N periods)
 *   - byweekday checkboxes (visible only for WEEKLY)
 *   - end-type radio (never/until/count) + conditional inputs
 * Save fires a PUT via store.updateSchedule.
 */
export function attachRecurrencePopover(store) {
  const pop = document.getElementById('mmRecurrencePopover');
  if (!pop) return;
  let openForScheduleId = null;

  function open(scheduleId, anchorRect) {
    const s = store.schedules.find(x => x.id === scheduleId);
    if (!s) return;
    openForScheduleId = scheduleId;
    pop.style.display = 'block';
    pop.style.left = `${anchorRect.left}px`;
    pop.style.top  = `${anchorRect.bottom + 4}px`;
    pop.querySelector('[data-field="freq"]').value = s.freq || 'DAILY';
    pop.querySelector('[data-field="interval"]').value = s.interval || 1;
    pop.querySelectorAll('[data-field="byweekday"] input').forEach(cb => {
      cb.checked = (s.byweekday || []).includes(Number(cb.value));
    });
    const endType = (s.end && s.end.type) || 'never';
    pop.querySelectorAll('[data-field="endType"] input').forEach(r => {
      r.checked = (r.value === endType);
    });
    pop.querySelector('[data-field="untilDate"]').value = s.end?.untilDate || '';
    pop.querySelector('[data-field="count"]').value = s.end?.count || 1;
    updateConditionalVisibility();
  }

  function close() {
    pop.style.display = 'none';
    openForScheduleId = null;
  }

  function updateConditionalVisibility() {
    const freq = pop.querySelector('[data-field="freq"]').value;
    pop.querySelector('[data-field="byweekday"]').style.display = (freq === 'WEEKLY') ? '' : 'none';
    const endType = pop.querySelector('[data-field="endType"] input:checked')?.value || 'never';
    pop.querySelector('[data-field="untilRow"]').style.display = (endType === 'until') ? '' : 'none';
    pop.querySelector('[data-field="countRow"]').style.display = (endType === 'count') ? '' : 'none';
  }

  pop.querySelector('[data-field="freq"]').addEventListener('change', updateConditionalVisibility);
  pop.querySelectorAll('[data-field="endType"] input').forEach(r => r.addEventListener('change', updateConditionalVisibility));
  pop.querySelector('[data-action="cancel"]').addEventListener('click', close);
  pop.querySelector('[data-action="save"]').addEventListener('click', () => {
    if (!openForScheduleId) return;
    const freq = pop.querySelector('[data-field="freq"]').value;
    const interval = Math.max(1, parseInt(pop.querySelector('[data-field="interval"]').value, 10) || 1);
    const byweekday = freq === 'WEEKLY'
      ? Array.from(pop.querySelectorAll('[data-field="byweekday"] input:checked')).map(cb => Number(cb.value))
      : [];
    const endType = pop.querySelector('[data-field="endType"] input:checked').value;
    let end = { type: 'never' };
    if (endType === 'until') end = { type: 'until', untilDate: pop.querySelector('[data-field="untilDate"]').value };
    if (endType === 'count') end = { type: 'count', count: Math.max(1, parseInt(pop.querySelector('[data-field="count"]').value, 10) || 1) };
    const patch = { freq, interval, byweekday, end };
    store.updateSchedule(openForScheduleId, patch).then(close, close);
  });

  document.addEventListener('click', (ev) => {
    if (!ev.altKey) return;
    const clip = ev.target.closest('.mm-clip');
    if (!clip) return;
    ev.preventDefault();
    ev.stopPropagation();
    open(clip.dataset.scheduleId, clip.getBoundingClientRect());
  }, true);

  // Click outside the popover closes it.
  document.addEventListener('mousedown', (ev) => {
    if (pop.style.display === 'block' && !pop.contains(ev.target)) close();
  });
}
```

### Step 13.2: HTML + CSS

In `admin.html`, near body end (outside the timeline section):

```html
  <div id="mmRecurrencePopover" class="mm-popover" style="display:none">
    <div class="mm-popover-row">
      <label>Frequency
        <select data-field="freq">
          <option value="DAILY">Daily</option>
          <option value="WEEKLY">Weekly</option>
          <option value="MONTHLY">Monthly</option>
          <option value="YEARLY">Yearly</option>
        </select>
      </label>
      <label>Interval <input type="number" data-field="interval" min="1" value="1" style="width:4em"></label>
    </div>
    <div class="mm-popover-row" data-field="byweekday">
      <label><input type="checkbox" value="0"> Mon</label>
      <label><input type="checkbox" value="1"> Tue</label>
      <label><input type="checkbox" value="2"> Wed</label>
      <label><input type="checkbox" value="3"> Thu</label>
      <label><input type="checkbox" value="4"> Fri</label>
      <label><input type="checkbox" value="5"> Sat</label>
      <label><input type="checkbox" value="6"> Sun</label>
    </div>
    <div class="mm-popover-row" data-field="endType">
      <label><input type="radio" name="mmEndType" value="never" checked> Never</label>
      <label><input type="radio" name="mmEndType" value="until"> Until</label>
      <label><input type="radio" name="mmEndType" value="count"> N times</label>
    </div>
    <div class="mm-popover-row" data-field="untilRow" style="display:none">
      <label>Until <input type="date" data-field="untilDate"></label>
    </div>
    <div class="mm-popover-row" data-field="countRow" style="display:none">
      <label>Count <input type="number" data-field="count" min="1" value="1" style="width:5em"></label>
    </div>
    <div class="mm-popover-row" style="justify-content:flex-end">
      <button class="btn btn-ghost" data-action="cancel">Cancel</button>
      <button class="btn btn-primary" data-action="save">Save</button>
    </div>
  </div>
```

CSS:

```css
.mm-popover { position: fixed; z-index: 1100; background: var(--bg-elev, #2a2a2a); padding: 12px; border-radius: 8px; box-shadow: 0 4px 24px rgba(0,0,0,0.6); min-width: 320px; }
.mm-popover-row { display:flex; gap:8px; align-items:center; margin-bottom:8px; flex-wrap:wrap; }
.mm-popover-row label { font-size:12px; }
```

### Step 13.3: Wire + commit

Add to index.js + smoke MODULES.

```bash
git add js/timeline/recurrence-popover.js admin.html js/timeline/index.js tests/unit/js/test_timeline_smoke.js
git commit -m "feat(timeline): Alt+click clip → recurrence inline popover

Tiny inline form (freq, interval, byweekday for WEEKLY, end-type with
conditional until/count fields). Save calls store.updateSchedule with
a patch. Alt+click is the temporary trigger until PR-4c lands the
right-click context menu.

Part of PR-4b of the admin-timeline-redesign spec."
```

---

## Task 14: Playwright integration tests

The actual browser-driven smoke. Each spec creates + cleans up its own state so production data is unharmed.

**Files:**
- Create: `package.json`
- Modify: `.gitignore`
- Create: `tests/e2e/run.js`
- Create: `tests/e2e/helpers.js`
- Create: `tests/e2e/test-create-schedule.spec.js`
- Create: `tests/e2e/test-clip-move.spec.js`
- Create: `tests/e2e/test-clip-delete.spec.js`
- Create: `tests/e2e/test-drill-in.spec.js`

### Step 14.1: Create `package.json`

```json
{
  "name": "mosaicmesh-e2e",
  "private": true,
  "type": "module",
  "scripts": {
    "e2e": "node tests/e2e/run.js"
  },
  "devDependencies": {
    "playwright": "^1.49.0"
  }
}
```

Run `npm install` (operator step — adds ~150MB of chromium download). Add to `.gitignore`:

```
node_modules/
```

### Step 14.2: Create `tests/e2e/helpers.js`

```javascript
import { setTimeout as wait } from 'node:timers/promises';

export const BASE = process.env.MM_BASE_URL || 'http://localhost:3000';
export const TIMELINE = BASE + '/admin?nocache=' + Date.now() + '#timeline';

export async function waitForHydrated(page) {
  await page.waitForFunction(() => window.Alpine && Alpine.store('mm') && Alpine.store('mm').hydrated, null, { timeout: 10_000 });
}

export async function deleteScheduleByPlaylist(page, playlistName) {
  // Best-effort cleanup: find any schedule with this playlist name and DELETE.
  await page.evaluate(async (pn) => {
    const r = await fetch('/api/schedules');
    const j = await r.json();
    for (const s of (j.schedules || [])) {
      if (s.playlistName === pn) {
        await fetch('/api/schedules/' + encodeURIComponent(s.id), { method: 'DELETE' });
      }
    }
  }, playlistName);
}

export async function createTestPlaylist(page, name) {
  await page.evaluate(async (n) => {
    await fetch('/api/playlists', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: n, items: [{ file: '/media/server/videos/probe_test.mp4', duration: 30 }], loop: true }),
    });
  }, name);
}

export async function deletePlaylist(page, name) {
  await page.evaluate(async (n) => {
    await fetch('/api/playlists/' + encodeURIComponent(n), { method: 'DELETE' });
  }, name);
}
```

### Step 14.3: Create `tests/e2e/test-create-schedule.spec.js`

```javascript
import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, createTestPlaylist, deleteScheduleByPlaylist, deletePlaylist } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const TEST_PLAYLIST = '__e2e_create_' + Date.now();
    await page.goto(TIMELINE);
    await waitForHydrated(page);
    await createTestPlaylist(page, TEST_PLAYLIST);
    await page.reload(); await waitForHydrated(page);

    // Find the playlist's draggable <li> and the Tablet track's droparea
    const playlistLi = page.locator(`.mm-bin-item:has-text("${TEST_PLAYLIST}")`).first();
    const tabletDrop = page.locator('.mm-day-grid .mm-track-droparea[data-display-id="Tablet"]');
    await playlistLi.waitFor({ timeout: 5000 });
    await tabletDrop.waitFor({ timeout: 5000 });

    // dragTo positions the drop at the center by default; specify a
    // targetPosition to land at hour ~14.
    const rect = await tabletDrop.evaluate(el => el.getBoundingClientRect().toJSON());
    await playlistLi.dragTo(tabletDrop, {
      targetPosition: { x: rect.width * (14 / 24), y: rect.height / 2 },
    });

    // Verify schedule was created (poll the store)
    await page.waitForFunction((pn) => {
      const s = Alpine.store('mm').schedules.find(x => x.playlistName === pn);
      return s && s.startTime;
    }, TEST_PLAYLIST, { timeout: 5000 });
    const created = await page.evaluate((pn) => Alpine.store('mm').schedules.find(x => x.playlistName === pn), TEST_PLAYLIST);
    assert.ok(created, 'schedule was not created');
    assert.equal(created.displayID, 'Tablet');
    // startTime should land near 14:00 (15-min snap)
    assert.match(created.startTime, /^14:(00|15|30|45)$/, `expected ~14:xx, got ${created.startTime}`);

    // Cleanup
    await deleteScheduleByPlaylist(page, TEST_PLAYLIST);
    await deletePlaylist(page, TEST_PLAYLIST);
    return 'pass';
  } finally {
    await browser.close();
  }
}
```

### Step 14.4: Create `tests/e2e/test-clip-move.spec.js`

```javascript
import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, createTestPlaylist, deleteScheduleByPlaylist, deletePlaylist } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const PLAYLIST = '__e2e_move_' + Date.now();
    await page.goto(TIMELINE); await waitForHydrated(page);
    await createTestPlaylist(page, PLAYLIST);
    // Seed a schedule at 09:00-10:00 on Tablet
    await page.evaluate(async (pn) => {
      await fetch('/api/schedules', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ playlistName: pn, displayID: 'Tablet', freq: 'DAILY',
          dtstart: new Date().toISOString().slice(0,10),
          startTime: '09:00', endTime: '10:00' })
      });
    }, PLAYLIST);
    await page.reload(); await waitForHydrated(page);
    // Locate the seeded clip + drop area
    const clip = page.locator(`.mm-clip[data-schedule-id]`).first();
    await clip.waitFor({ timeout: 5000 });
    const tabletDrop = page.locator('.mm-day-grid .mm-track-droparea[data-display-id="Tablet"]');
    const rect = await tabletDrop.evaluate(el => el.getBoundingClientRect().toJSON());
    await clip.dragTo(tabletDrop, {
      targetPosition: { x: rect.width * (14 / 24), y: rect.height / 2 },
    });
    await page.waitForFunction((pn) => {
      const s = Alpine.store('mm').schedules.find(x => x.playlistName === pn);
      return s && s.startTime !== '09:00';
    }, PLAYLIST, { timeout: 5000 });
    const moved = await page.evaluate((pn) => Alpine.store('mm').schedules.find(x => x.playlistName === pn), PLAYLIST);
    assert.notEqual(moved.startTime, '09:00');
    assert.match(moved.startTime, /^1[34]:(00|15|30|45)$/);
    await deleteScheduleByPlaylist(page, PLAYLIST);
    await deletePlaylist(page, PLAYLIST);
    return 'pass';
  } finally { await browser.close(); }
}
```

### Step 14.5: Create `tests/e2e/test-clip-delete.spec.js`

```javascript
import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, createTestPlaylist, deletePlaylist } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const PL = '__e2e_del_' + Date.now();
    await page.goto(TIMELINE); await waitForHydrated(page);
    await createTestPlaylist(page, PL);
    await page.evaluate(async (pn) => {
      await fetch('/api/schedules', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ playlistName: pn, displayID: 'Tablet', freq: 'DAILY',
          dtstart: new Date().toISOString().slice(0,10),
          startTime: '15:00', endTime: '16:00' })
      });
    }, PL);
    await page.reload(); await waitForHydrated(page);
    // Click the clip
    const clip = page.locator(`.mm-clip[data-schedule-id]`).first();
    await clip.waitFor({ timeout: 5000 });
    await clip.click();
    // Press Delete + accept the confirm if it appears (single delete: no confirm)
    page.on('dialog', async d => await d.accept());
    await page.keyboard.press('Delete');
    await page.waitForFunction((pn) => !Alpine.store('mm').schedules.find(x => x.playlistName === pn), PL, { timeout: 5000 });
    await deletePlaylist(page, PL);
    return 'pass';
  } finally { await browser.close(); }
}
```

### Step 14.6: Create `tests/e2e/test-drill-in.spec.js`

```javascript
import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, createTestPlaylist, deleteScheduleByPlaylist, deletePlaylist } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const PL = '__e2e_drill_' + Date.now();
    await page.goto(TIMELINE); await waitForHydrated(page);
    await createTestPlaylist(page, PL);
    await page.evaluate(async (pn) => {
      await fetch('/api/schedules', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ playlistName: pn, displayID: 'Tablet', freq: 'DAILY',
          dtstart: new Date().toISOString().slice(0,10),
          startTime: '09:00', endTime: '12:00' })
      });
    }, PL);
    await page.reload(); await waitForHydrated(page);
    const clip = page.locator(`.mm-clip[data-schedule-id]`).first();
    await clip.waitFor({ timeout: 5000 });
    await clip.dblclick();
    // Sub-track should appear with at least one item
    const sub = page.locator('.mm-drillin-row .mm-drillin-item').first();
    await sub.waitFor({ timeout: 5000 });
    const itemText = await sub.innerText();
    assert.match(itemText, /probe_test/);
    await deleteScheduleByPlaylist(page, PL);
    await deletePlaylist(page, PL);
    return 'pass';
  } finally { await browser.close(); }
}
```

### Step 14.7: Create `tests/e2e/run.js`

```javascript
#!/usr/bin/env node
import { readdirSync, existsSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));

if (!existsSync(path.join(here, '..', '..', 'node_modules', 'playwright'))) {
  console.error('Playwright not installed. Run: npm install');
  process.exit(2);
}

const specs = readdirSync(here)
  .filter(f => f.endsWith('.spec.js'))
  .sort();

let pass = 0, fail = 0;
for (const spec of specs) {
  process.stdout.write(`▶ ${spec} ... `);
  const url = pathToFileURL(path.join(here, spec)).href;
  try {
    const mod = await import(url);
    const result = await mod.default();
    if (result === 'pass') { console.log('PASS'); pass++; }
    else { console.log('FAIL', result); fail++; }
  } catch (e) {
    console.log('FAIL\n   ', e.message);
    fail++;
  }
}
console.log(`\nTotal: ${pass} pass, ${fail} fail`);
process.exit(fail === 0 ? 0 : 1);
```

### Step 14.8: Commit

```bash
git add package.json .gitignore tests/e2e/
git commit -m "test(e2e): Playwright integration tests for PR-4b flows

Four spec files exercising the real browser:
- test-create-schedule: drag playlist onto track → POST /api/schedules
- test-clip-move:       drag clip body → PUT (new startTime)
- test-clip-delete:     click + Delete key → DELETE
- test-drill-in:        double-click → sub-track with playlist items

Uses playwright directly (not @playwright/test) — no extra test
runner needed. Each spec creates + cleans up its own state so the
shared dev server (settings.dat on disk) stays clean.

Adds package.json with playwright as a devDependency (operator runs
'npm install' once — ~150MB chromium download). .gitignore now
excludes node_modules/.

Production code unaffected. Run with: node tests/e2e/run.js

Part of PR-4b of the admin-timeline-redesign spec."
```

---

## Task 15: pytest_runner --e2e + CLAUDE.md + final smoke

**Files:**
- Modify: `pytest_runner.py`
- Modify: `CLAUDE.md`

### Step 15.1: Add `--e2e` flag to `pytest_runner.py`

In the existing argument handling, add `--e2e`. When set, run `node tests/e2e/run.js`. If `node_modules` doesn't exist, print a helpful message and skip.

```python
if '--e2e' in sys.argv:
    import subprocess
    if not os.path.isdir('node_modules/playwright'):
        print('Playwright not installed. To run e2e tests:')
        print('  npm install')
        print('  python pytest_runner.py --e2e')
        sys.exit(0)
    rc = subprocess.call(['node', 'tests/e2e/run.js'])
    sys.exit(rc)
```

(Match the existing style in pytest_runner.py — read it first.)

### Step 15.2: Update CLAUDE.md

In the Conventions section, append:

```markdown
- **Browser integration tests live in `tests/e2e/`.** Use `playwright` (the lower-level package, not `@playwright/test`) inside `node tests/e2e/run.js`. Requires `npm install` (adds `node_modules/`, gitignored, ~150MB chromium download). Each spec creates + cleans up its own state so the shared dev server's `settings.dat` stays clean. Run via `python pytest_runner.py --e2e` or `node tests/e2e/run.js`. Production code has no Node deps; this is dev-only.
```

In the Layout section, add:

```markdown
- `tests/e2e/` — Playwright spec files for browser-driven smoke. `run.js` dispatches them. Catches the layout/reactivity bugs that Node `--test` can't see (PR-4a learned this lesson the hard way).
```

### Step 15.3: Smoke + commit

Restart the dev server (the static handlers will pick up the new files automatically; no Python imports changed). Then run the full E2E suite:

```bash
npm install
python pytest_runner.py --e2e
```

Expected: 4/4 e2e specs pass.

Also run the Node unit suite — should still be green:

```bash
python pytest_runner.py --js
```

Expected: all green.

```bash
git add pytest_runner.py CLAUDE.md
git commit -m "docs(claude-md): document PR-4b interactivity + e2e tests

CLAUDE.md updated:
- Conventions: tests/e2e/ uses playwright (no separate runner), gated
  by npm install, dev-only (no prod deps).
- Layout: tests/e2e/ block added.

pytest_runner.py gains --e2e flag that chains 'node tests/e2e/run.js'
with a graceful skip if node_modules isn't installed.

Closes PR-4b (interactivity) of the admin-timeline-redesign spec."
```

---

## Self-Review Checklist (run before opening the PR)

- [ ] `python pytest_runner.py --js` — all green (33 from PR-4a + 14 new = 47)
- [ ] `python pytest_runner.py --unit` — same 13 pre-existing failures as PR-3 baseline (no Python regressions)
- [ ] `python pytest_runner.py --e2e` — 4/4 specs pass (after `npm install`)
- [ ] Manual browser smoke:
  - Drag the Morning playlist onto the Tablet track → schedule appears
  - Drag the new clip from 09:00 to 14:00 → it moves with 15-min snap
  - Drag the right edge to 17:00 → it stretches
  - Click the clip + Delete → it disappears
  - Double-click a clip → sub-track shows items
  - Drag a media file onto the sub-track → item appended; sub-track re-renders
  - Click + Upload → file dialog opens
  - Alt+click a clip → recurrence popover opens
  - Cause a 412 (open two browser tabs, edit same schedule in both) → toast appears with the conflict message
- [ ] `git log --oneline feature/pr4b-timeline-interactivity ^feature/pr4a-timeline-readonly` shows ~15 task commits

---

## Notes for the implementing engineer

1. **Optimistic-then-rollback applies to every mutation.** Use `withRollback` consistently — even for delete (the snapshot lets us reinsert the schedule if the DELETE fails). Don't shortcut.

2. **`Alpine.store('mm')` is always the proxy.** PR-4a's lesson: methods invoked on the raw `makeStore()` reference don't trigger reactivity. The dynamic `import('./util/optimistic.js')` inside each mutation method does NOT change this — it just lets Node tests cache-bust.

3. **HTML5 drag vs pointer events.** Use HTML5 drag for cross-element drops (playlist → track, media → clip). Use pointer events for in-place manipulations (clip-resize) where continuous visual feedback matters and the source element should stay visible.

4. **15-min snap is non-negotiable.** Shift-disabled-snap is a PR-4c feature.

5. **`If-Match` mandatory on PUT.** Server returns 412 with `{currentVersion}` on stale. The withRollback path catches the throw and the toast shows the server's `error` string. PR-4c can add a "refetch and merge" recovery.

6. **No new production dependencies.** Playwright is dev-only in `package.json`. Production install (`pip install -r requirements.txt`) is unchanged.

7. **Test isolation.** Every E2E spec creates + cleans up its own playlist/schedule. If a spec aborts mid-run (e.g. server crash), the next run's cleanup helpers tolerate already-deleted state (best-effort).

8. **iPad-1 clients unaffected.** PR-4b only touches `admin.html` + `js/timeline/`. The iPad display clients still load `index.html` + ES5 `js/mosiacmesh.js`.
