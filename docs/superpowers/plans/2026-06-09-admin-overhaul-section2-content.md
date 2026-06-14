# Admin Overhaul Section 2 (Content) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill in the Content tab with a unified content library + a rebuilt vertical-list playlist editor that finally lets an operator add an animation to a playlist, and migrate the animations registry into one shared ES5 module.

**Architecture:** A new ES5 `js/animations.js` (no module syntax, sets `window.MM_ANIMATIONS`) becomes the single animation source — loaded by `index.html`, `admin.html`, and Node tests via a global bridge — retiring the catalog + test mirror. The admin merges `/api/media` + that registry client-side into "content items"; a new `mmContent` component renders Library | Playlists (with upload/delete); and `playlist-editor.js` is rewritten into a vertical list whose **+ Add content** picker maps an animation pick to a `{file, playmode:'SCRIPT'}` item. No server changes.

**Tech Stack:** Alpine 3.x + ES modules (admin, no build step), ES5 (`index.html` + `js/animations.js`), `node --test`, pytest (`-c tests/pytest.ini`), Playwright (`tests/e2e/run.js`).

---

## Conventions

- Tests: `node --test tests/unit/js/<f>.js`; full JS `python pytest_runner.py --js`; e2e `node tests/e2e/run.js <substr>` (server on `:3000`); pytest `python -m pytest <p> -c tests/pytest.ini -v`.
- **No build step.** `admin.html` + `js/timeline/*` are modern JS. **`index.html` + `js/animations.js` are ES5 ONLY** (var/function, no let/const/arrow/template-literals/class) — they run on a 1st-gen iPad (Safari 5.1).
- One commit per task; message ends with:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```
- Branch: `feature/admin-ui-overhaul` (current — has Section 1).

## File structure

| File | Responsibility | C/M/D |
|------|----------------|-------|
| `js/animations.js` | ES5 shared registry `window.MM_ANIMATIONS=[{key,label,description,draw}]` | Create |
| `index.html` | load `js/animations.js`; rebuild `animations` map from it | Modify |
| `admin.html` | load `js/animations.js`; Content view; remove Schedule media-bin; CSS | Modify |
| `js/timeline/content/content-items.js` | `buildContentItems` + `contentItemToPlaylistItem` (pure) | Create |
| `js/timeline/content/content-view.js` | `mmContent` Alpine component (Library \| Playlists) | Create |
| `js/timeline/modals/playlist-editor.js` | Rewrite: vertical list + inline unified picker | Modify (rewrite) |
| `js/timeline/store.js` | `contentItems` getter | Modify |
| `js/timeline/index.js` | register `mmContent`; drop `mmMediaBin` | Modify |
| `js/timeline/animations-catalog.js` | Retire | Delete |
| `js/timeline/bin/media-bin.js` | Retire (Schedule media-bin removed) | Delete |
| `tests/unit/js/_animations_mirror.js` | Retire | Delete |
| `tests/unit/js/test_animations_registry_sync.js` | Retire | Delete |
| `tests/unit/js/test_animations_catalog.js` | Rework → shared-module well-formed | Modify |
| `tests/unit/js/test_animations_{lissajous,phyllotaxis,wireframe}.js` | Re-point to `js/animations.js` | Modify |
| `tests/unit/js/test_content_items.js` | `buildContentItems`/`contentItemToPlaylistItem` | Create |
| `tests/unit/js/test_timeline_smoke.js` | Add new modules; drop deleted | Modify |
| `tests/e2e/test-content-tab.spec.js` | Library/upload/delete/add-animation/editor e2e | Create |

---

## Phase A — Shared animations module

### Task 1: Create `js/animations.js`

**Files:** Create `js/animations.js`; Test `tests/unit/js/test_animations_module.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_animations_module.js`:

```js
/**
 * The shared animations module: one ES5 source of truth. Importing it for
 * side-effect sets globalThis.MM_ANIMATIONS. Each entry is self-describing.
 */
import { test } from 'node:test';
import assert from 'node:assert';

test('importing js/animations.js populates MM_ANIMATIONS with well-formed entries', async () => {
  await import('../../../js/animations.js');
  const list = globalThis.MM_ANIMATIONS;
  assert.ok(Array.isArray(list), 'MM_ANIMATIONS should be an array');
  const keys = list.map((a) => a.key);
  for (const k of ['bouncingBalls', 'lissajous', 'phyllotaxis', 'wireframeCube']) {
    assert.ok(keys.includes(k), `missing animation "${k}"`);
  }
  for (const a of list) {
    assert.equal(typeof a.key, 'string');
    assert.equal(typeof a.label, 'string');
    assert.equal(typeof a.description, 'string');
    assert.equal(typeof a.draw, 'function');
  }
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --test tests/unit/js/test_animations_module.js`
Expected: FAIL — cannot find module `js/animations.js`.

- [ ] **Step 3: Create the module**

Create `js/animations.js`. The four `draw` bodies are moved **verbatim** from `index.html`'s current `var animations` block (index.html:426-499) — same `(ctx, tMs, w, h)` math. ES5 only; no `import`/`export`:

```js
/* js/animations.js — ES5, NO module syntax (so this same file is a valid
 * classic <script> for the iPad-1 display client AND a side-effect ESM import
 * for the admin + Node tests). Single source of truth for SCRIPT animations.
 * Each entry is self-describing; draw(ctx, tMs, w, h) is a PURE function of
 * elapsed time + canvas size, so every display draws the same frame. */
(function (root) {
  var animations = [
    {
      key: 'bouncingBalls',
      label: 'Bouncing balls',
      description: 'Four balls drifting around the screen.',
      draw: function (ctx, tMs, w, h) {
        var colors = ['#e74c3c', '#27ae60', '#2980b9', '#f1c40f'];
        var r = Math.max(12, Math.min(w, h) * 0.06), n = 4, i;
        for (i = 0; i < n; i++) {
          var px = (Math.sin(tMs / (900 + i * 220) + i) + 1) / 2;
          var py = (Math.sin(tMs / (700 + i * 180) + i * 1.7) + 1) / 2;
          ctx.fillStyle = colors[i % colors.length];
          ctx.beginPath();
          ctx.arc(r + px * (w - 2 * r), r + py * (h - 2 * r), r, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    },
    {
      key: 'lissajous',
      label: 'Lissajous curve',
      description: 'A single morphing parametric curve that breathes over time.',
      draw: function (ctx, tMs, w, h) {
        var N = 300, i;
        var a = 3 + 2 * Math.sin(tMs / 8000);
        var b = 4 + 2 * Math.sin(tMs / 11000);
        var phi = tMs / 3000;
        var cx = w / 2, cy = h / 2, ax = w * 0.35, ay = h * 0.35;
        ctx.strokeStyle = 'hsl(' + ((tMs / 40) % 360) + ', 70%, 60%)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        for (i = 0; i <= N; i++) {
          var s = (i / N) * Math.PI * 2;
          var x = cx + ax * Math.sin(a * s + phi);
          var y = cy + ay * Math.sin(b * s);
          if (i === 0) { ctx.moveTo(x, y); } else { ctx.lineTo(x, y); }
        }
        ctx.stroke();
      }
    },
    {
      key: 'phyllotaxis',
      label: 'Phyllotaxis spiral',
      description: 'A rotating golden-angle sunflower-seed spiral.',
      draw: function (ctx, tMs, w, h) {
        var N = 600, i;
        var GOLDEN = 137.508 * Math.PI / 180;
        var c = (Math.min(w, h) / (2 * Math.sqrt(N))) * 0.92;
        var cx = w / 2, cy = h / 2;
        var rot = tMs / 4000;
        for (i = 0; i < N; i++) {
          var theta = i * GOLDEN + rot;
          var r = c * Math.sqrt(i);
          var x = cx + r * Math.cos(theta);
          var y = cy + r * Math.sin(theta);
          var dotR = 3 + 2 * Math.sin(tMs / 1500 + i * 0.02);
          ctx.fillStyle = 'hsl(' + ((i / N) * 360) + ', 80%, 60%)';
          ctx.beginPath();
          ctx.arc(x, y, dotR, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    },
    {
      key: 'wireframeCube',
      label: 'Wireframe cube',
      description: 'A spinning 3D wireframe cube.',
      draw: function (ctx, tMs, w, h) {
        var V = [[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]];
        var E = [[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]];
        var ax = tMs / 2500, ay = tMs / 3700, az = tMs / 5300;
        var cosx = Math.cos(ax), sinx = Math.sin(ax);
        var cosy = Math.cos(ay), siny = Math.sin(ay);
        var cosz = Math.cos(az), sinz = Math.sin(az);
        var s = Math.min(w, h) / 4, persp = 0.5, cx = w / 2, cy = h / 2;
        var proj = [], i;
        for (i = 0; i < V.length; i++) {
          var x = V[i][0], y = V[i][1], z = V[i][2];
          var y1 = y * cosx - z * sinx, z1 = y * sinx + z * cosx;
          var x2 = x * cosy + z1 * siny, z2 = -x * siny + z1 * cosy;
          var x3 = x2 * cosz - y1 * sinz, y3 = x2 * sinz + y1 * cosz;
          var d = 1 + z2 * persp;
          proj.push([cx + s * x3 / d, cy + s * y3 / d]);
        }
        ctx.strokeStyle = 'hsl(' + ((tMs / 30) % 360) + ', 80%, 60%)';
        ctx.lineWidth = 3;
        ctx.beginPath();
        for (i = 0; i < E.length; i++) {
          var p0 = proj[E[i][0]], p1 = proj[E[i][1]];
          ctx.moveTo(p0[0], p0[1]);
          ctx.lineTo(p1[0], p1[1]);
        }
        ctx.stroke();
      }
    }
  ];
  root.MM_ANIMATIONS = animations;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
```

ES5 check: only `var`/`function`, no arrow/let/const/template-literals. (The draw bodies are byte-identical to index.html's current ones.)

- [ ] **Step 4: Run it to verify it passes**

Run: `node --test tests/unit/js/test_animations_module.js`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add js/animations.js tests/unit/js/test_animations_module.js
git commit -m "feat(animations): shared ES5 animations module (js/animations.js)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Wire `index.html` to the shared module (iPad-1 path)

**Files:** Modify `index.html`

This is the iPad-1-sensitive change. Verified by the existing SCRIPT e2e (`test-script-animations.spec.js`), which drives the real `index.html` SCRIPT path.

- [ ] **Step 1: Load the module + rebuild the map**

In `index.html`, add a `<script src="/js/animations.js"></script>` tag BEFORE the inline `<script>` block that defines the display client (so `window.MM_ANIMATIONS` exists when that block runs). Place it next to the other `<script src>` tags (jquery/sockjs/GoTime) or just before the main inline script.

Then REPLACE the entire inline `var animations = { ... };` block (index.html:425-500) with:

```js
	// Animations now live in the shared ES5 module js/animations.js (loaded above
	// via <script>), so the admin + tests use the exact same code. Rebuild the
	// name->draw map runScriptLoop/showItem already consume (signature unchanged).
	var animations = {};
	(function () {
		var list = (typeof window !== 'undefined' && window.MM_ANIMATIONS) ? window.MM_ANIMATIONS : [];
		for (var i = 0; i < list.length; i++) { animations[list[i].key] = list[i].draw; }
	})();
```

ES5 only. `runScriptLoop` (index.html ~517) still calls `animations[name](ctx, tMs, w, h)` unchanged; the `if (animations[name])` guard still covers a missing/failed module (blank canvas, no crash).

- [ ] **Step 2: Verify the iPad-1 SCRIPT path still works**

The dev server is on `:3000`. Restart it if needed so it serves the new `index.html` + `js/animations.js` (the static handler reads from disk; if the implementer cannot restart, note it — but `index.html`/`js` are served fresh per request via the file cache, mtime-keyed, so a restart usually isn't required).

Run: `node tests/e2e/run.js script-animations`
Expected: PASS — the SCRIPT smoke renders all three batch-1 animations through the real `index.html` path now sourced from `js/animations.js`.

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "refactor(animations): index.html sources animations from js/animations.js

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Retire the 3-way split (tests)

**Files:** Delete `tests/unit/js/_animations_mirror.js`, `tests/unit/js/test_animations_registry_sync.js`; Modify `tests/unit/js/test_animations_{lissajous,phyllotaxis,wireframe}.js`, `tests/unit/js/test_animations_catalog.js`

- [ ] **Step 1: Re-point the determinism tests**

In each of `test_animations_lissajous.js`, `test_animations_phyllotaxis.js`, `test_animations_wireframe.js`, replace the mirror import with the shared module. The current head is:
```js
import { mirror } from './_animations_mirror.js';
import { makeRecordingCtx } from './_canvas_stub.js';
```
Replace with:
```js
import { makeRecordingCtx } from './_canvas_stub.js';
await import('../../../js/animations.js');
const byKey = Object.fromEntries(globalThis.MM_ANIMATIONS.map((a) => [a.key, a.draw]));
```
Then replace every `mirror.lissajous(` (resp. `phyllotaxis`/`wireframeCube`) with `byKey.lissajous(` etc. (top-level `await import` is valid in these ESM test files; `node --test` supports it.) Keep all assertions identical — they now exercise the shipped code.

- [ ] **Step 2: Rework the catalog test**

Replace `tests/unit/js/test_animations_catalog.js` contents with a well-formedness check (the catalog concept is gone; the module is the source):
```js
import { test } from 'node:test';
import assert from 'node:assert';

test('MM_ANIMATIONS entries are well-formed + the four keys exist', async () => {
  await import('../../../js/animations.js');
  const list = globalThis.MM_ANIMATIONS;
  const keys = list.map((a) => a.key);
  for (const k of ['bouncingBalls', 'lissajous', 'phyllotaxis', 'wireframeCube']) {
    assert.ok(keys.includes(k), `missing "${k}"`);
  }
  for (const a of list) {
    assert.equal(typeof a.key, 'string');
    assert.equal(typeof a.label, 'string');
    assert.equal(typeof a.description, 'string');
    assert.equal(typeof a.draw, 'function');
  }
});
```
(This overlaps `test_animations_module.js` from Task 1 — that's fine; if you prefer, DELETE `test_animations_catalog.js` entirely since Task 1 covers it. Either is acceptable; deleting is tidier. If you delete it, remove it from the smoke list too.)

- [ ] **Step 3: Delete the retired files**

```bash
git rm tests/unit/js/_animations_mirror.js tests/unit/js/test_animations_registry_sync.js
```
(If you chose to delete `test_animations_catalog.js` in Step 2, `git rm` it too.)

- [ ] **Step 4: Run the full JS suite**

Run: `python pytest_runner.py --js`
Expected: all pass — determinism tests green against the shared module; no missing-import errors. (The smoke `MODULES` list still references `animations-catalog.js` at this point — that's deleted in Task 7; if the smoke fails on it now, you may remove that one line here too, but `animations-catalog.js` still exists until Task 7, so the smoke should still pass.)

- [ ] **Step 5: Commit**

```bash
git add -A tests/unit/js/
git commit -m "test(animations): retire mirror + sync; point determinism at shared module

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase B — Content data + library

### Task 4: `content-items.js` (merge + the trigger-fix helper)

**Files:** Create `js/timeline/content/content-items.js`; Test `tests/unit/js/test_content_items.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_content_items.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert';
import { buildContentItems, contentItemToPlaylistItem } from '../../../js/timeline/content/content-items.js';

const media = {
  images: ['/media/server/images/logo.png'],
  videos: ['/media/server/videos/promo.mp4'],
  videoDurations: { '/media/server/videos/promo.mp4': 30 },
};
const animations = [{ key: 'lissajous', label: 'Lissajous curve', description: 'x' }];

test('buildContentItems merges media + animations with correct kinds/refs', () => {
  const items = buildContentItems({ media, animations });
  const byRef = Object.fromEntries(items.map((i) => [i.ref, i]));
  assert.equal(byRef['/media/server/images/logo.png'].kind, 'image');
  assert.equal(byRef['/media/server/images/logo.png'].name, 'logo.png');
  assert.equal(byRef['/media/server/videos/promo.mp4'].kind, 'video');
  assert.equal(byRef['/media/server/videos/promo.mp4'].duration, 30);
  assert.equal(byRef['lissajous'].kind, 'animation');
  assert.equal(byRef['lissajous'].label, 'Lissajous curve');
});

test('buildContentItems tolerates empty inputs', () => {
  assert.deepEqual(buildContentItems({}), []);
});

test('contentItemToPlaylistItem: animation -> SCRIPT (the trigger fix)', () => {
  const it = contentItemToPlaylistItem({ kind: 'animation', ref: 'lissajous', name: 'lissajous' });
  assert.equal(it.file, 'lissajous');
  assert.equal(it.playmode, 'SCRIPT');
  assert.equal(it.duration, 20);
});

test('contentItemToPlaylistItem: media -> loop', () => {
  const it = contentItemToPlaylistItem({ kind: 'video', ref: '/media/server/videos/promo.mp4', duration: 30 });
  assert.equal(it.file, '/media/server/videos/promo.mp4');
  assert.equal(it.playmode, 'loop');
  assert.equal(it.duration, 30);
  const img = contentItemToPlaylistItem({ kind: 'image', ref: '/media/server/images/logo.png' });
  assert.equal(img.playmode, 'loop');
  assert.equal(img.duration, undefined);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --test tests/unit/js/test_content_items.js`
Expected: FAIL — cannot find module.

- [ ] **Step 3: Implement**

Create `js/timeline/content/content-items.js`:

```js
/**
 * Unified content model. Merges media (/api/media) + the animations registry
 * (window.MM_ANIMATIONS) into one list of content items, and maps a picked
 * content item to a playlist item. Pure + DOM-free (testable).
 */
function basename(p) { return String(p || '').split('/').pop() || ''; }

export function buildContentItems({ media = {}, animations = [] } = {}) {
  const out = [];
  for (const url of media.images || []) {
    out.push({ kind: 'image', ref: url, name: basename(url) });
  }
  for (const url of media.videos || []) {
    out.push({ kind: 'video', ref: url, name: basename(url), duration: (media.videoDurations || {})[url] });
  }
  for (const a of animations || []) {
    out.push({ kind: 'animation', ref: a.key, name: a.key, label: a.label });
  }
  return out;
}

// The trigger fix: an animation pick becomes a SCRIPT item automatically; the
// operator never touches play-mode for an animation. Media picks default to loop.
const ANIMATION_DEFAULT_DURATION_S = 20;
export function contentItemToPlaylistItem(ci) {
  if (ci.kind === 'animation') {
    return { file: ci.ref, playmode: 'SCRIPT', duration: ANIMATION_DEFAULT_DURATION_S };
  }
  return { file: ci.ref, playmode: 'loop', duration: ci.duration == null ? undefined : ci.duration };
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `node --test tests/unit/js/test_content_items.js`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add js/timeline/content/content-items.js tests/unit/js/test_content_items.js
git commit -m "feat(content): buildContentItems + contentItemToPlaylistItem (trigger fix)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `store.contentItems` getter

**Files:** Modify `js/timeline/store.js`; Test `tests/unit/js/test_store_content.js`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_store_content.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert';
import { makeStore } from '../../../js/timeline/store.js';

test('contentItems getter merges store.media + MM_ANIMATIONS', () => {
  globalThis.MM_ANIMATIONS = [{ key: 'lissajous', label: 'Lissajous curve', description: 'x' }];
  const s = makeStore();
  s.media = { images: ['/media/server/images/logo.png'], videos: [], videoDurations: {} };
  const items = s.contentItems;
  const kinds = items.map((i) => i.kind).sort();
  assert.deepEqual(kinds, ['animation', 'image']);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `node --test tests/unit/js/test_store_content.js`
Expected: FAIL — `contentItems` undefined.

- [ ] **Step 3: Implement**

In `js/timeline/store.js`, add the import near the top:
```js
import { buildContentItems } from './content/content-items.js';
```
Add the getter to the object returned by `makeStore()` (next to the `nowCards` getter from Section 1):
```js
    get contentItems() {
      const anims = (typeof window !== 'undefined' && window.MM_ANIMATIONS)
        ? window.MM_ANIMATIONS
        : (typeof globalThis !== 'undefined' && globalThis.MM_ANIMATIONS) || [];
      return buildContentItems({ media: this.media, animations: anims });
    },
```

- [ ] **Step 4: Run it to verify it passes**

Run: `node --test tests/unit/js/test_store_content.js`
Expected: PASS. Then `python pytest_runner.py --js` → all pass.

- [ ] **Step 5: Commit**

```bash
git add js/timeline/store.js tests/unit/js/test_store_content.js
git commit -m "feat(content): store.contentItems getter (media + animations)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Content tab — `mmContent` view (Library | Playlists)

**Files:** Create `js/timeline/content/content-view.js`; Modify `admin.html`, `js/timeline/index.js`

Verified by the e2e in Task 9 + page load. This is a UI build — follow the Section-1 `mmContent`-style component pattern.

- [ ] **Step 1: Load the shared module in admin.html**

In `admin.html`, add `<script src="/js/animations.js"></script>` alongside the other `<script src>` tags (so `window.MM_ANIMATIONS` is set before Alpine/store run).

- [ ] **Step 2: Create the component**

Create `js/timeline/content/content-view.js`:

```js
/**
 * mmContent — the Content tab. Two sub-views: Library (the unified content
 * grid + upload + delete) and Playlists (list/create/delete). Reads
 * store.contentItems; opens the playlist editor.
 */
import { api } from '../api.js';
import { openPlaylistEditor } from '../modals/playlist-editor.js';

export function mmContentComponent() {
  return {
    subview: 'library',       // 'library' | 'playlists'
    filter: 'all',            // 'all' | 'image' | 'video' | 'animation'
    get items() {
      const all = this.$store.mm.contentItems;
      return this.filter === 'all' ? all : all.filter((i) => i.kind === this.filter);
    },
    get playlists() {
      return Object.values(this.$store.mm.playlists || {}).sort((a, b) => a.name.localeCompare(b.name));
    },
    iconFor(kind) { return kind === 'image' ? '▦' : kind === 'video' ? '▶' : '✦'; },

    async uploadFiles(ev) {
      const files = Array.from(ev.target.files || []);
      let ok = 0, fail = 0;
      for (const f of files) { try { await api.uploadMedia(f); ok += 1; } catch (_) { fail += 1; } }
      try { this.$store.mm.media = await api.listMedia(); } catch (_) {}
      this.$store.mm.toast(fail ? `${ok} uploaded, ${fail} failed` : `Uploaded ${ok} file${ok === 1 ? '' : 's'}`, fail ? 'error' : 'info');
      ev.target.value = '';
    },
    async removeItem(it) {
      if (it.kind === 'animation') return;       // animations are code, not deletable
      if (!confirm(`Delete ${it.name}?`)) return;
      try { await this.$store.mm.deleteMedia(it.ref); }
      catch (_) { /* store.deleteMedia toasts 409 refs */ }
    },

    newPlaylist() {
      const name = (prompt('New playlist name:') || '').trim();
      if (!name) return;
      this.$store.mm.createPlaylist(name).catch(() => {});
    },
    async deletePlaylist(name) {
      if (!confirm(`Delete playlist "${name}"?`)) return;
      try { await this.$store.mm.deletePlaylist(name); } catch (_) {}
    },
    edit(name) { openPlaylistEditor(this.$store.mm, name); },
  };
}
```

NOTE: confirm `api.uploadMedia`, `api.listMedia`, `store.createPlaylist`, `store.deletePlaylist`, `store.deleteMedia` exist with these signatures (they're used by the current media-bin/playlist-bin/store). If `store.createPlaylist` doesn't exist, check store.js for the actual create method name and use it (the bins + Section-1 store have create/delete playlist mutators). Report the actual names used.

- [ ] **Step 3: Register + mount**

In `js/timeline/index.js`: add `import { mmContentComponent } from './content/content-view.js';` and register `Alpine.data('mmContent', mmContentComponent);` (next to the other `Alpine.data(...)` calls).

In `admin.html`, replace the Content placeholder section:
```html
<section class="section" data-route="content" x-show="$store.mm.activeTab==='content'">
  <div class="placeholder-tab">Content — coming soon.</div>
</section>
```
with the Library/Playlists view:
```html
<section class="section" data-route="content" x-show="$store.mm.activeTab==='content'" x-data="mmContent">
  <div class="mm-content-subtabs">
    <button :class="{on: subview==='library'}" @click="subview='library'">Library</button>
    <button :class="{on: subview==='playlists'}" @click="subview='playlists'">Playlists</button>
  </div>

  <!-- Library -->
  <div x-show="subview==='library'">
    <div class="mm-content-toolbar">
      <div class="mm-content-filters">
        <button :class="{on: filter==='all'}" @click="filter='all'">All</button>
        <button :class="{on: filter==='image'}" @click="filter='image'">Images</button>
        <button :class="{on: filter==='video'}" @click="filter='video'">Videos</button>
        <button :class="{on: filter==='animation'}" @click="filter='animation'">Animations</button>
      </div>
      <label class="btn btn-primary mm-upload-label">+ Upload
        <input type="file" accept="image/*,video/*" multiple style="display:none" @change="uploadFiles($event)">
      </label>
    </div>
    <div class="mm-content-grid">
      <template x-for="it in items" :key="it.kind + ':' + it.ref">
        <div class="mm-content-tile" :class="'kind-' + it.kind">
          <span class="mm-tile-ic" x-text="iconFor(it.kind)"></span>
          <span class="mm-tile-name" x-text="it.name"></span>
          <button class="mm-tile-del" x-show="it.kind!=='animation'" @click="removeItem(it)" title="Delete">×</button>
        </div>
      </template>
      <p class="mm-content-empty" x-show="items.length===0">Nothing here yet.</p>
    </div>
  </div>

  <!-- Playlists -->
  <div x-show="subview==='playlists'">
    <div class="mm-content-toolbar">
      <button class="btn btn-primary" @click="newPlaylist()">+ New playlist</button>
    </div>
    <ul class="mm-playlist-list">
      <template x-for="p in playlists" :key="p.name">
        <li class="mm-playlist-row">
          <button class="mm-playlist-name" @click="edit(p.name)" x-text="p.name"></button>
          <span class="mm-playlist-meta" x-text="(p.items?.length || 0) + ' items'"></span>
          <button class="mm-playlist-del" @click="deletePlaylist(p.name)" title="Delete">×</button>
        </li>
      </template>
      <p class="mm-content-empty" x-show="playlists.length===0">No playlists yet.</p>
    </ul>
  </div>
</section>
```

- [ ] **Step 4: Add CSS**

In `admin.html`'s `<style>`, add (using the consolidated tokens):
```css
.mm-content-subtabs { display:flex; gap:4px; border-bottom:1px solid var(--border); margin-bottom:12px; }
.mm-content-subtabs button { background:none; border:none; color:var(--text-muted); padding:8px 14px; border-bottom:2px solid transparent; cursor:pointer; }
.mm-content-subtabs button.on { color:var(--text); font-weight:600; border-bottom-color:var(--accent); }
.mm-content-toolbar { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:12px; flex-wrap:wrap; }
.mm-content-filters { display:flex; gap:6px; flex-wrap:wrap; }
.mm-content-filters button { font-size:12px; padding:3px 10px; border-radius:999px; border:1px solid var(--border); background:none; color:var(--text-muted); cursor:pointer; }
.mm-content-filters button.on { background:var(--accent); color:#fff; border-color:var(--accent); }
.mm-content-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(120px,1fr)); gap:10px; }
.mm-content-tile { position:relative; background:var(--surface-2); border:1px solid var(--border); border-radius:8px; padding:14px 8px; text-align:center; }
.mm-content-tile.kind-animation { border-color:#9a7bff; }
.mm-tile-ic { display:block; font-size:22px; margin-bottom:6px; }
.mm-tile-name { font-size:11px; word-break:break-all; }
.mm-tile-del { position:absolute; top:4px; right:4px; border:none; background:rgba(0,0,0,.4); color:#fff; border-radius:50%; width:18px; height:18px; line-height:16px; cursor:pointer; opacity:0; }
.mm-content-tile:hover .mm-tile-del { opacity:1; }
.mm-content-empty { color:var(--text-muted); padding:24px; text-align:center; }
.mm-playlist-list { list-style:none; padding:0; margin:0; }
.mm-playlist-row { display:flex; align-items:center; gap:10px; padding:8px; border:1px solid var(--border); border-radius:6px; margin-bottom:6px; }
.mm-playlist-name { flex:1; text-align:left; background:none; border:none; color:var(--text); font-size:14px; cursor:pointer; }
.mm-playlist-meta { font-size:11px; color:var(--text-muted); }
.mm-playlist-del { border:none; background:none; color:var(--text-muted); cursor:pointer; font-size:16px; }
```

- [ ] **Step 5: Verify**

Run: `python pytest_runner.py --js` → pass (smoke includes new modules once added in Task 9; for now confirm content-view imports cleanly by adding it to the smoke MODULES list — or defer that to Task 9 and just load the page). Open `http://localhost:3000/admin.html#content`: Library shows media + animations (✦ tiles), filter chips work, Playlists lists playlists, clicking a playlist opens the editor (the OLD ribbon editor until Task 7 — that's fine, it still works). Report what you saw.

- [ ] **Step 6: Commit**

```bash
git add js/timeline/content/content-view.js js/timeline/index.js admin.html
git commit -m "feat(content): Content tab — unified Library + Playlists (mmContent)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase C — Editor rewrite + cleanup

### Task 7: Rewrite the playlist editor (vertical list + inline picker)

**Files:** Modify (rewrite) `js/timeline/modals/playlist-editor.js`; Delete `js/timeline/animations-catalog.js`

The current file is a ~591-line ribbon editor. Rewrite it as a vertical list with an inline content picker. Keep the public entry point `openPlaylistEditor(store, playlistName, initialIndex)` and the save path (`store.updatePlaylist(name, {items, loop})`). Verified by the e2e in Task 9.

- [ ] **Step 1: Read the current file**

Read `js/timeline/modals/playlist-editor.js` fully to preserve: the `openPlaylistEditor` signature + its two callers (context menu, drill-in double-click — keep `attachPlaylistEditor(store)` if it wires those listeners), the `modal-shell.js` `openModal({title, contentEl})` usage, the `draft = {items, loop}` working-copy pattern, `effectiveDuration`/`maxDuration` helpers (reuse), and `store.updatePlaylist`. Note what to drop: the ribbon render, `PX_PER_SECOND`, right-edge resize, `pickerEntries` (media-only), the PR#31 `playmode`/`file-anim` SCRIPT fields, the `ANIMATIONS` import from `animations-catalog.js`.

- [ ] **Step 2: Rewrite as a vertical list + inline picker**

Replace the file. Import the content helpers + keep the modal shell + drag-reorder. The structure (provide complete code; reuse `effectiveDuration`/`maxDuration`/`basename` from the old file verbatim):

```js
// js/timeline/modals/playlist-editor.js
import { openModal, closeModal } from './modal-shell.js';
import { buildContentItems, contentItemToPlaylistItem } from '../content/content-items.js';

const DEFAULT_DURATION_S = 10;
function basename(p) { return String(p || '').split('/').pop() || ''; }
function asObject(it) { return (typeof it === 'string') ? { file: it } : { ...it }; }
function isAnim(it) { return it.playmode === 'SCRIPT'; }
function maxDuration(item, store) {
  const probed = store.media?.videoDurations?.[item.file];
  return (probed != null && Number.isFinite(probed) && probed > 0) ? probed : null;
}

// Wire the drill-in entry points (preserve from the old file).
export function attachPlaylistEditor(store) {
  document.addEventListener('dblclick', (ev) => {
    const item = ev.target.closest && ev.target.closest('.mm-drillin-item');
    if (!item) return;
    const row = item.closest('.mm-drillin-row'); if (!row) return;
    ev.preventDefault(); ev.stopPropagation();
    openPlaylistEditor(store, row.dataset.playlistName, Number(item.dataset.itemIndex || 0));
  }, true);
}

export function openPlaylistEditor(store, playlistName, initialIndex = 0) {
  const pl = store.playlists[playlistName];
  if (!pl) return;
  const draft = { items: (pl.items || []).map(asObject), loop: !!pl.loop };
  let selectedIdx = draft.items.length ? Math.min(Math.max(0, initialIndex), draft.items.length - 1) : -1;
  let pickerOpen = false;
  let pickerFilter = 'all';

  const root = document.createElement('div');
  root.className = 'mm-ple';
  function render() {
    root.innerHTML = '';
    // header: loop toggle + summary
    const head = document.createElement('div'); head.className = 'mm-ple-head';
    const loopLbl = document.createElement('label');
    loopLbl.innerHTML = '<input type="checkbox"> Loop playlist';
    const loopCb = loopLbl.querySelector('input'); loopCb.checked = draft.loop;
    loopCb.addEventListener('change', () => { draft.loop = loopCb.checked; });
    head.appendChild(loopLbl);
    const sum = document.createElement('span'); sum.className = 'mm-ple-sum';
    sum.textContent = `${draft.items.length} item${draft.items.length === 1 ? '' : 's'}`;
    head.appendChild(sum);
    root.appendChild(head);

    // vertical item list (grip-drag reorder, × remove, click to select)
    const list = document.createElement('ul'); list.className = 'mm-ple-list';
    draft.items.forEach((it, idx) => {
      const li = document.createElement('li');
      li.className = 'mm-ple-row' + (idx === selectedIdx ? ' sel' : '');
      li.draggable = true;
      li.dataset.idx = String(idx);
      const kind = isAnim(it) ? 'animation' : (/\.(mp4|webm|mov)$/i.test(it.file) ? 'video' : 'image');
      li.innerHTML =
        '<span class="mm-ple-grip">⠿</span>' +
        '<span class="mm-ple-ic">' + (kind === 'image' ? '▦' : kind === 'video' ? '▶' : '✦') + '</span>' +
        '<span class="mm-ple-nm"></span><span class="mm-ple-dur"></span>' +
        '<button class="mm-ple-del" title="Remove">×</button>';
      li.querySelector('.mm-ple-nm').textContent = basename(it.file);
      li.querySelector('.mm-ple-dur').textContent = (it.duration != null ? it.duration + 's' : 'auto');
      li.addEventListener('click', (e) => { if (e.target.closest('.mm-ple-del')) return; selectedIdx = idx; render(); });
      li.querySelector('.mm-ple-del').addEventListener('click', () => { draft.items.splice(idx, 1); selectedIdx = Math.min(selectedIdx, draft.items.length - 1); render(); });
      // drag-reorder
      li.addEventListener('dragstart', (e) => { e.dataTransfer.setData('text/plain', String(idx)); });
      li.addEventListener('dragover', (e) => e.preventDefault());
      li.addEventListener('drop', (e) => {
        e.preventDefault();
        const from = Number(e.dataTransfer.getData('text/plain'));
        const to = idx; if (from === to) return;
        const [moved] = draft.items.splice(from, 1); draft.items.splice(to, 0, moved);
        selectedIdx = to; render();
      });
      list.appendChild(li);
    });
    root.appendChild(list);

    // selected-item settings
    if (selectedIdx >= 0) {
      const it = draft.items[selectedIdx];
      const box = document.createElement('div'); box.className = 'mm-ple-settings';
      // duration
      const durWrap = document.createElement('label'); durWrap.textContent = 'Duration (s) ';
      const dur = document.createElement('input'); dur.type = 'number'; dur.min = '0.1'; dur.step = '0.1';
      dur.value = it.duration != null ? String(it.duration) : '';
      const cap = maxDuration(it, store); if (cap != null) dur.max = String(cap);
      dur.addEventListener('input', () => {
        const v = dur.value.trim();
        if (!v) { delete it.duration; render(); return; }
        let n = Number(v); if (!Number.isFinite(n) || n <= 0) return;
        if (cap != null && n > cap) n = cap;
        it.duration = n; render();
      });
      durWrap.appendChild(dur); box.appendChild(durWrap);
      // backgroundColor
      const bgWrap = document.createElement('label'); bgWrap.textContent = 'Background ';
      const bg = document.createElement('input'); bg.type = 'text'; bg.placeholder = '#000000';
      bg.value = it.backgroundColor || '';
      bg.addEventListener('input', () => { const v = bg.value.trim(); if (v) it.backgroundColor = v; else delete it.backgroundColor; });
      bgWrap.appendChild(bg); box.appendChild(bgWrap);
      // play mode — media only (animations are implicitly SCRIPT)
      if (!isAnim(it)) {
        const pmWrap = document.createElement('label'); pmWrap.textContent = 'Play mode ';
        const pm = document.createElement('select');
        pm.innerHTML = '<option value="loop">Loop</option><option value="once">Play once</option>';
        pm.value = (it.playmode === 'once') ? 'once' : 'loop';
        pm.addEventListener('change', () => { it.playmode = pm.value; });
        pmWrap.appendChild(pm); box.appendChild(pmWrap);
      }
      root.appendChild(box);
    }

    // + Add content (inline picker panel — NOT a second modal)
    const addBtn = document.createElement('button');
    addBtn.className = 'btn mm-ple-add';
    addBtn.textContent = pickerOpen ? '✕ Close picker' : '+ Add content';
    addBtn.addEventListener('click', () => { pickerOpen = !pickerOpen; render(); });
    root.appendChild(addBtn);

    if (pickerOpen) {
      const panel = document.createElement('div'); panel.className = 'mm-ple-picker';
      const chips = document.createElement('div'); chips.className = 'mm-ple-picker-filters';
      ['all', 'image', 'video', 'animation'].forEach((f) => {
        const b = document.createElement('button');
        b.textContent = f === 'all' ? 'All' : f[0].toUpperCase() + f.slice(1) + 's';
        if (pickerFilter === f) b.className = 'on';
        b.addEventListener('click', () => { pickerFilter = f; render(); });
        chips.appendChild(b);
      });
      panel.appendChild(chips);
      const grid = document.createElement('div'); grid.className = 'mm-ple-picker-grid';
      const anims = (typeof window !== 'undefined' && window.MM_ANIMATIONS) || [];
      let ci = buildContentItems({ media: store.media, animations: anims });
      if (pickerFilter !== 'all') ci = ci.filter((c) => c.kind === pickerFilter);
      ci.forEach((c) => {
        const t = document.createElement('button'); t.className = 'mm-ple-picktile kind-' + c.kind;
        t.innerHTML = '<span>' + (c.kind === 'image' ? '▦' : c.kind === 'video' ? '▶' : '✦') + '</span>';
        const nm = document.createElement('span'); nm.textContent = c.name; t.appendChild(nm);
        t.addEventListener('click', () => {
          const item = contentItemToPlaylistItem(c);
          if (item.duration == null) delete item.duration;
          draft.items.push(item);
          selectedIdx = draft.items.length - 1;
          render();
        });
        grid.appendChild(t);
      });
      panel.appendChild(grid);
      root.appendChild(panel);
    }

    // actions
    const actions = document.createElement('div'); actions.className = 'mm-form-actions';
    const cancel = document.createElement('button'); cancel.className = 'btn btn-ghost'; cancel.textContent = 'Cancel';
    cancel.addEventListener('click', () => closeModal());
    const save = document.createElement('button'); save.className = 'btn btn-primary'; save.textContent = 'Save';
    save.addEventListener('click', () => {
      store.updatePlaylist(playlistName, { items: draft.items, loop: draft.loop })
        .then(() => closeModal()).catch(() => {/* store toasts 412 */});
    });
    actions.appendChild(cancel); actions.appendChild(save);
    root.appendChild(actions);
  }

  render();
  openModal({ title: `Edit playlist: ${playlistName}`, contentEl: root });
}
```

(This preserves reorder, per-item settings, save, and the inline picker. The duration default for a media item without a probed length stays "auto" — `effectiveDuration` on the display side already falls back; an item with no `duration` is valid.)

- [ ] **Step 3: Delete the catalog (last importer gone)**

```bash
git rm js/timeline/animations-catalog.js
```
Grep to confirm nothing else imports it: `grep -rn "animations-catalog" js/ tests/` → only the now-rewritten editor referenced it; should be zero hits after the rewrite. Fix any straggler.

- [ ] **Step 4: Add the editor CSS**

In `admin.html`'s `<style>`, add:
```css
.mm-ple-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }
.mm-ple-list { list-style:none; padding:0; margin:0 0 12px; }
.mm-ple-row { display:flex; align-items:center; gap:8px; padding:8px; background:var(--surface-2); border:1px solid var(--border); border-radius:6px; margin-bottom:6px; cursor:pointer; }
.mm-ple-row.sel { border-color:var(--accent); }
.mm-ple-grip { cursor:grab; color:var(--text-muted); }
.mm-ple-ic { width:18px; text-align:center; }
.mm-ple-nm { flex:1; font-size:13px; }
.mm-ple-dur { font-size:11px; color:var(--text-muted); }
.mm-ple-del { border:none; background:none; color:var(--text-muted); cursor:pointer; font-size:15px; }
.mm-ple-settings { display:flex; gap:12px; flex-wrap:wrap; padding:10px; background:var(--surface); border:1px solid var(--border); border-radius:6px; margin-bottom:10px; }
.mm-ple-settings label { font-size:12px; display:flex; flex-direction:column; gap:3px; }
.mm-ple-add { width:100%; border:1px dashed var(--accent); color:var(--accent); background:none; border-radius:6px; padding:9px; }
.mm-ple-picker { border:1px solid var(--border); border-radius:6px; padding:10px; margin-top:8px; }
.mm-ple-picker-filters { display:flex; gap:6px; margin-bottom:8px; flex-wrap:wrap; }
.mm-ple-picker-filters button { font-size:11px; padding:2px 9px; border-radius:999px; border:1px solid var(--border); background:none; color:var(--text-muted); cursor:pointer; }
.mm-ple-picker-filters button.on { background:var(--accent); color:#fff; border-color:var(--accent); }
.mm-ple-picker-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(90px,1fr)); gap:8px; max-height:240px; overflow-y:auto; }
.mm-ple-picktile { display:flex; flex-direction:column; gap:4px; align-items:center; font-size:10px; padding:8px 4px; border:1px solid var(--border); border-radius:6px; background:var(--surface-2); color:var(--text); cursor:pointer; }
.mm-ple-picktile.kind-animation { border-color:#9a7bff; }
.mm-ple-picktile span:first-child { font-size:18px; }
```

- [ ] **Step 5: Verify**

Run: `python pytest_runner.py --js` → pass (the smoke loads playlist-editor.js — confirm it imports with the new content-items import; if the smoke MODULES still lists `animations-catalog.js`, remove that line, since the file is deleted).
Open `http://localhost:3000/admin.html`: from Content > Playlists open a playlist → vertical list; + Add content → inline picker with filters → pick `lissajous` → it appears as an item; reorder by dragging the grip; edit a duration; Save; reopen → persisted. Also confirm the timeline drill-in double-click still opens this editor. Report observations.

- [ ] **Step 6: Commit**

```bash
git add js/timeline/modals/playlist-editor.js admin.html
git rm js/timeline/animations-catalog.js
git commit -m "feat(content): vertical-list playlist editor + inline content picker

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Remove the media-bin from the Schedule view

**Files:** Modify `admin.html`, `js/timeline/index.js`; Delete `js/timeline/bin/media-bin.js`

- [ ] **Step 1: Remove the markup + registration**

In `admin.html`, find the media-bin markup inside the Schedule (`data-route="schedule"`) section — the `x-data="mmMediaBin"` element (search/upload list with the delete × and the upload `<input id="mmUploadInput">`/button). Remove that media-bin block. KEEP the playlist-bin (`x-data="mmPlaylistBin"`). If the upload `<input>`/button lived in the media-bin and is now only needed in Content, it already moved to the Content view (Task 6) — remove the Schedule copy.

In `js/timeline/index.js`, remove the `Alpine.data('mmMediaBin', ...)` registration + its import.

- [ ] **Step 2: Delete the component + drop from smoke**

```bash
git rm js/timeline/bin/media-bin.js
```
In `tests/unit/js/test_timeline_smoke.js`, remove `'js/timeline/bin/media-bin.js'` from `MODULES` and add `'js/animations.js'`, `'js/timeline/content/content-items.js'`, `'js/timeline/content/content-view.js'`. Also remove `'js/timeline/animations-catalog.js'` if still listed (deleted in Task 7).

Grep: `grep -rn "media-bin\|mmMediaBin" js/ admin.html` → zero hits after removal (the upload.js helper, if it targeted `#mmUploadBtn` in the bin, is now unused — if `js/timeline/upload.js` is no longer imported/used anywhere, leave it for now OR remove its registration; report what you find).

- [ ] **Step 3: Verify**

Run: `python pytest_runner.py --js` → pass (smoke updated).
Open `http://localhost:3000/admin.html#schedule`: the timeline renders, the playlist-bin is present (drag a playlist onto a track still works), and the media-bin is gone. No console errors. `#content` Library still has upload/delete.

- [ ] **Step 4: Commit**

```bash
git add admin.html js/timeline/index.js tests/unit/js/test_timeline_smoke.js
git rm js/timeline/bin/media-bin.js
git commit -m "refactor(content): remove media-bin from Schedule (Content is sole media home)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase D — End-to-end + final

### Task 9: Playwright — Content tab e2e

**Files:** Create `tests/e2e/test-content-tab.spec.js`

- [ ] **Step 1: Read the harness**

Read `tests/e2e/run.js`, `tests/e2e/test-shell-nav.spec.js` (Section 1's spec — same patterns), and `tests/e2e/helpers.js`. Match the export shape + `chromium.launch()` + `cleanupE2eOrphans` usage.

- [ ] **Step 2: Write the spec**

Create `tests/e2e/test-content-tab.spec.js`. Cover (light but real; create + clean up a `__e2e_`-prefixed playlist):
1. Navigate to `${baseURL}/admin.html#content`; wait `Alpine.store('mm').hydrated`. Library shows tiles; assert ≥1 `.mm-content-tile.kind-animation` exists (animations appear in the library — the unification).
2. Filter chips: click Animations → only `.kind-animation` tiles; click Images → only image tiles.
3. **Add-animation-to-a-playlist (THE TRIGGER, finally e2e):** create a `__e2e_content` playlist via `POST /api/playlists` (or the UI New-playlist), open it from Content > Playlists, click **+ Add content**, filter Animations, click the `lissajous` tile, Save. Reload, reopen the playlist editor, assert an item with `lissajous` exists. Then verify via REST: `GET /api/playlists` shows the `__e2e_content` playlist has an item `{file:'lissajous', playmode:'SCRIPT'}`. Clean up (DELETE the playlist).
4. Editor reorder: with ≥2 items, drag row 2's grip above row 1 (or assert the reorder handler exists + a programmatic reorder persists) + Save persists order.
5. (Optional, if easy) upload a tiny file → appears; delete it (and assert a media item with playlist refs returns the 409 toast path).

Keep resilient (waitForFunction, not sleeps). Loosen pixel-exact assertions, never behavior.

- [ ] **Step 3: Run it**

Run: `node tests/e2e/run.js content-tab`
Expected: PASS. If the e2e env is unavailable, write the spec cleanly, commit, report DONE_WITH_CONCERNS.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test-content-tab.spec.js
git commit -m "test(e2e): Content tab — library, filters, add-animation-to-playlist, editor

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Final review + PR

- [ ] **Step 1: Run all suites**

```bash
python pytest_runner.py --js
node tests/e2e/run.js content-tab
node tests/e2e/run.js script-animations   # iPad-1 SCRIPT path still works post-migration
python -m pytest tests/unit/test_api_media.py tests/unit/test_api_endpoints.py -c tests/pytest.ini
```
Expected: JS green; both e2e green; media/playlists API tests green (no server change). Pre-existing `--unit` asyncio failures are unrelated — confirm none added.

- [ ] **Step 2: Code review**

Use superpowers:requesting-code-review over the section. Focus: the shared-module global bridge (works as `<script>` + ESM import + the determinism tests run the real code), the trigger fix (`contentItemToPlaylistItem` animation→SCRIPT), no server change, the editor rewrite preserves save/reorder/entry-points, the catalog/mirror retirement left no dangling imports.

- [ ] **Step 3: Manual smoke + flag iPad-1**

Confirm on `:3000`: Content Library (media + ✦ animations, filters, upload, delete), Playlists, the editor add-animation flow, the Schedule view's media-bin gone + playlist-bin intact. Note in the PR: **the iPad-1 hardware sign-off (pending animations Task 9) should be re-run** — the SCRIPT code path now sources from `js/animations.js` (math unchanged, code path moved).

- [ ] **Step 4: Finish the branch**

Use superpowers:finishing-a-development-branch. PR summary: unified content library + shared animations module + rebuilt editor (trigger fixed), no server change, Schedule media-bin removed; Sections 3–4 (Schedule/Fleet) remain.

---

## Self-Review

**1. Spec coverage:**

| Spec item | Task |
|---|---|
| Shared `js/animations.js` (ES5, global bridge) | 1, 2 |
| Retire catalog + mirror + sync; re-point determinism | 3, 7 (catalog delete) |
| `buildContentItems` + `contentItemToPlaylistItem` (trigger fix) | 4 |
| `store.contentItems` | 5 |
| Content tab Library \| Playlists + upload/delete | 6 |
| Vertical-list editor + inline picker (animation→SCRIPT) | 7 |
| Remove Schedule media-bin, keep playlist-bin | 8 |
| Tests: node + e2e (add-animation e2e) + pytest guard | 4,5,9,10 |
| No server change | (none touch server.py / mosaicmesh) |
| `bouncingBalls` label/description | 1 |

No gaps.

**2. Placeholder scan:** No TBD/vague steps. Large UI files (content-view, playlist-editor) ship complete code; the editor rewrite is a full module. Task 6/7 verification is page-load + the Task 9 e2e (UI not unit-testable). The two open-question decisions (omit rename, inline picker) are baked in. `api`/`store` method names are flagged for confirmation in Task 6 (createPlaylist/deletePlaylist/uploadMedia/listMedia/deleteMedia) — the implementer verifies against store.js/api.js and reports actuals.

**3. Type/name consistency:**
- `buildContentItems` → `{kind, ref, name, label?, duration?}` consistent across Tasks 4, 5, 6, 7.
- `contentItemToPlaylistItem` → `{file, playmode, duration}` consistent (Task 4 def, Task 7 use).
- `window.MM_ANIMATIONS` entry shape `{key,label,description,draw}` consistent across Tasks 1, 3, 5, 7.
- `openPlaylistEditor(store, name, idx)` signature preserved (Task 7) for both callers.
- `store.updatePlaylist(name, {items, loop})` unchanged (Task 7).

No inconsistencies. One flagged verification: the exact `store`/`api` mutator names (Task 6 Step 2 note) — confirmed by the implementer against the real store.js/api.js before use.
