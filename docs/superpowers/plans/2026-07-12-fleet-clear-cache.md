# Fleet "Clear cache" for a display group Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Fleet-management "Clear cache" button that wipes cached render segments on every device in a display group and clears the server's per-client `cachedSegments` record, uniformly across both cache backends.

**Architecture:** A new `backend.clear()` method on the `mmCache` abstraction (Modern → `caches.delete('mm-seg')`; mmvideo → a new native `mmcache://clearall` wipe), a `CLEAR_CACHE {displayID}` SockJS handler mirroring `RELOAD`, a client dispatch that calls `mmCache.clear()`, a generic `confirm-modal` on `modal-shell`, and a `clearCache()` button in the Fleet component. The mmvideo path needs a tweak rebuild + staged fleet redeploy.

**Tech Stack:** ES5 browser JS (iPad-1 / iOS 5.1) + Node 20 `node --test`; Python 3 aiohttp/SockJS + pytest; admin-side Alpine.js ESM; iOS-5 MobileSubstrate tweak (Foundation-only ObjC via `objc_msgSend`, theos build).

## Global Constraints

- **ES5 only** in `js/mmCache.js`, `js/mmCacheBackendModern.js`, `js/mmCacheBackendMmvideo.js`, `index.html`: no `let`/`const`, arrow functions, template literals, `class`, `Promise` literal syntax (the Modern backend uses the host `Promise` that the SW environment provides — keep `.then`/`['catch']`), `fetch`. `js/timeline/fleet/fleet-view.js` + `js/timeline/modals/confirm-modal.js` are admin ESM and MAY use modern JS.
- **Tweak** (`tweak/mmcache/Tweak.x`): Foundation only, runtime messaging via `objc_msgSend`, **NO Blocks**, **NO ObjC classes** (a static ObjC class SIGKILLs the load), mirror the existing `fetch`/`evict` handling.
- **Server record clearing is mandatory** alongside the broadcast: a `CLEAR_CACHE` must empty each targeted client's `cachedSegments` so `_resolve_media_url` stops routing to a now-deleted localhost seg.
- **No re-pull trigger, no stop-playback, no clearing of server-side rendered assets** (`media/<key>/videos/seg_*`).
- **Run tests via runners:** JS `node --test tests/unit/js/<file>` or `python pytest_runner.py --js`; Python `python -m pytest tests/unit/<file> -c tests/pytest.ini -v` or `python pytest_runner.py --unit`. On Windows `python` may need `C:/Users/jtubb.SOLUTIONS/AppData/Local/Programs/Python/Python314/python.exe`; `node`/`git` on PATH.
- **Deploy:** admin JS + server ship immediately; the `mmcache://clearall` tweak needs a rebuild + STAGED fleet redeploy (single/small-batch, never a burst — [[fleet-ssh-no-burst]]). Old-tweak devices treat `mmcache://clearall` as a no-op (safe).
- **`mmCache.state` returns** `'none'|'pending'|'cached'|'failed'`; the backend interface is `fetchToCache`/`localSrc`/`evict`/`has`/`size` (+ new `clear`).

---

### Task 1: `mmCache.clear()` + `backend.clear()` on both backends

**Files:**
- Modify: `js/mmCache.js` (add `clear`)
- Modify: `js/mmCacheBackendModern.js` (add `backend.clear`)
- Modify: `js/mmCacheBackendMmvideo.js` (add `backend.clear`)
- Test: `tests/unit/js/mmcache.test.js`, `tests/unit/js/mmcache-backend-modern.test.js`, `tests/unit/js/mmcache-backend.test.js` (extend each)

**Interfaces:**
- Consumes: existing `mmCache.backend`, `mmCache._tokens`, `mmCache._order`; the backends' `_present`; test loaders `loadMmCache()`/`mockBackend()` (`_mmcache_load.js`), `loadModern()`, `loadBackend()`.
- Produces: `mmCache.clear(onDone?, onFail?)` → resets `_tokens={}`/`_order=[]`, delegates to `backend.clear` when present. `backend.clear(onDone, onFail)` on both backends. Task 5's client dispatch calls `mmCache.clear()` (no args).

- [ ] **Step 1: Write the failing tests**

In `tests/unit/js/mmcache.test.js`, append:

```js
test('clear: delegates to backend.clear and resets token bookkeeping', function () {
  const mmCache = loadMmCache();
  let cleared = false;
  const b = mockBackend();
  b.clear = function (onDone) { cleared = true; if (onDone) onDone(); };
  mmCache.registerBackend(b);
  mmCache._recordToken('seg_T1_0', 'G1');           // seed state
  let doneCalled = false;
  mmCache.clear(function () { doneCalled = true; });
  assert.strictEqual(cleared, true);
  assert.strictEqual(doneCalled, true);
  assert.deepStrictEqual(mmCache._order, []);
  assert.deepStrictEqual(mmCache._tokens, {});
});

test('clear: no backend (or backend without clear) just resets + calls onDone', function () {
  const mmCache = loadMmCache();
  mmCache._recordToken('seg_T1_0', 'G1');
  let doneCalled = false;
  mmCache.clear(function () { doneCalled = true; });   // backend is null
  assert.strictEqual(doneCalled, true);
  assert.deepStrictEqual(mmCache._order, []);
});
```

In `tests/unit/js/mmcache-backend-modern.test.js`, extend the `caches` mock inside `loadModern()` to add a top-level `delete`, then append a test. Change the `const caches = { open: ... }` object to also include:

```js
    'delete': function (name) { delete store[name]; return Promise.resolve(true); },
```

Then append:

```js
test('modern backend: clear() deletes the whole named cache + resets _present', async function () {
  const w = loadModern();
  const b = w._mmCacheBackendModern;
  await new Promise(function (res, rej) {
    b.fetchToCache('http://s/seg_a_0.mp4', 'T1', function () { res(); }, function (t, r) { rej(new Error(r)); });
  });
  assert.strictEqual(b.has('T1'), true);
  await new Promise(function (res, rej) { b.clear(function () { res(); }, function (r) { rej(new Error(r)); }); });
  assert.strictEqual(b.has('T1'), false);          // _present reset
});
```

In `tests/unit/js/mmcache-backend.test.js`, append:

```js
test('mmvideo backend: clear() navigates mmcache://clearall + resets _present', function () {
  const { w, navs } = loadBackend();
  const b = w._mmCacheBackendMmvideo;
  b.fetchToCache('http://c/seg-a.mp4', 'T1', function () {}, function () {});
  w.__mmCacheDone('T1', 999);
  assert.strictEqual(b.has('T1'), true);
  let done = false;
  b.clear(function () { done = true; }, function () {});
  assert.ok(navs.indexOf('mmcache://clearall') !== -1);
  assert.strictEqual(done, true);
  assert.strictEqual(b.has('T1'), false);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `node --test tests/unit/js/mmcache.test.js tests/unit/js/mmcache-backend-modern.test.js tests/unit/js/mmcache-backend.test.js`
Expected: FAIL — `mmCache.clear`, `backend.clear` are undefined (`TypeError: ... is not a function`).

- [ ] **Step 3: Add `mmCache.clear` (`js/mmCache.js`)**

Immediately after the `mmCache.localSrc = ...` function (before `mmCache.onAck = null;`), insert:

```js
  // Clear the ENTIRE device cache: delegate to the backend's complete wipe (Modern:
  // caches.delete; mmvideo: native mmcache://clearall), then reset the coordinator's
  // token bookkeeping so mmCache.state() reports 'none' for every prior token. A backend
  // without clear() (old build) still resets the JS state. Fire-and-forget-friendly.
  mmCache.clear = function (onDone, onFail) {
    var b = mmCache.backend;
    function done() { mmCache._tokens = {}; mmCache._order = []; if (onDone) { onDone(); } }
    if (!b || !b.clear) { done(); return; }
    b.clear(done, function (reason) { if (onFail) { onFail(reason); } });
  };
```

- [ ] **Step 4: Add `backend.clear` (Modern, `js/mmCacheBackendModern.js`)**

Inside the `backend` object, after the `evict` method, add:

```js
    clear: function (onDone, onFail) {
      var cs = _caches();
      if (!cs) { if (onFail) { onFail('no-cache-api'); } return; }
      cs['delete'](CACHE_NAME).then(function () { _present = {}; if (onDone) { onDone(); } })
        ['catch'](function () { if (onFail) { onFail('delete-failed'); } });
    },
```

- [ ] **Step 5: Add `backend.clear` (mmvideo, `js/mmCacheBackendMmvideo.js`)**

Inside the `backend` object, after the `evict` method, add:

```js
    clear: function (onDone, onFail) {
      _present = {};
      _nav('mmcache://clearall');       // native wipes the MosaicMeshCache dir; fire-and-forget
      if (onDone) { onDone(); }
    },
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `node --test tests/unit/js/mmcache.test.js tests/unit/js/mmcache-backend-modern.test.js tests/unit/js/mmcache-backend.test.js`
Expected: PASS (the 4 new tests + all existing).

- [ ] **Step 7: Run the full JS suite (no regression)**

Run: `python pytest_runner.py --js`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add js/mmCache.js js/mmCacheBackendModern.js js/mmCacheBackendMmvideo.js tests/unit/js/mmcache.test.js tests/unit/js/mmcache-backend-modern.test.js tests/unit/js/mmcache-backend.test.js
git commit -m "feat(cache): mmCache.clear() + backend.clear() (Modern caches.delete / mmvideo clearall)

Uniform whole-cache wipe across both backends: Modern deletes the named
Cache API cache; mmvideo navs mmcache://clearall (native wipe, Task 4).
mmCache.clear resets _tokens/_order so state() reports 'none'.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PVeEo4Mcq6YhcjArDRikoX"
```

---

### Task 2: Generic `confirm-modal.js` on `modal-shell`

**Files:**
- Create: `js/timeline/modals/confirm-modal.js`
- Modify: `tests/unit/js/test_timeline_smoke.js` (add the new module to `MODULES`)

**Interfaces:**
- Consumes: `openModal({title, contentEl})` / `closeModal()` from `js/timeline/modals/modal-shell.js`.
- Produces: `confirmModal({ title, message, confirmLabel, danger, onConfirm })` — builds a message `<p>` + a `.mm-form-actions` row (Cancel `btn btn-ghost` → `closeModal()`; Confirm `btn btn-primary` [+ `mm-fleet-confirm-danger` when `danger`] → `closeModal()` then `onConfirm()`), then `openModal({title, contentEl})`. Task 5 imports it.

> **Testing convention:** this is a thin DOM builder. Per the codebase's own rule (`test_fleet_confirm.js`: "Modal UI is browser-driven; verified by Playwright" — `openModal` needs `#mmModalHost` + `querySelector` + `focus`, impractical to node-stub), the automated gate is the **module-load smoke** (`test_timeline_smoke.js`, which fail-closes on any syntax/missing-import error) plus the on-wall Fleet-button sign-off (Task 7). Do NOT add a fragile DOM-shim unit test or a production test-seam. A follow-up Playwright spec (like `test-fleet-scope.spec.js`) may cover the click interaction later.

- [ ] **Step 1: Add the new module to the smoke list (the failing test)**

In `tests/unit/js/test_timeline_smoke.js`, add to the `MODULES` array (near the other `js/timeline/modals/` entries if present, else anywhere in the list):

```js
  'js/timeline/modals/confirm-modal.js',
```

- [ ] **Step 2: Run the smoke to verify it fails**

Run: `node --test tests/unit/js/test_timeline_smoke.js`
Expected: FAIL — the module doesn't exist yet (`ERR_MODULE_NOT_FOUND` for `confirm-modal.js`).

- [ ] **Step 3: Create `js/timeline/modals/confirm-modal.js`**

```js
/**
 * Generic confirm modal on modal-shell. fleet-confirm.js is RUN_SCRIPT-bound, so
 * destructive fleet actions (e.g. Clear cache) use this parameterized confirm instead.
 *   confirmModal({ title, message, confirmLabel, danger, onConfirm })
 * Cancel -> closeModal(); Confirm -> closeModal() then onConfirm().
 */
import { openModal, closeModal } from './modal-shell.js';

export function confirmModal({ title, message, confirmLabel, danger, onConfirm }) {
  const root = document.createElement('div');
  root.className = 'mm-confirm-modal';

  const msg = document.createElement('p');
  msg.className = 'mm-confirm-modal-msg';
  msg.textContent = message || '';
  root.appendChild(msg);

  const actions = document.createElement('div');
  actions.className = 'mm-form-actions';

  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'btn btn-ghost';
  cancel.textContent = 'Cancel';
  cancel.addEventListener('click', () => closeModal());

  const confirm = document.createElement('button');
  confirm.type = 'button';
  confirm.className = danger ? 'btn btn-primary mm-fleet-confirm-danger' : 'btn btn-primary';
  confirm.textContent = confirmLabel || 'Confirm';
  confirm.addEventListener('click', () => { closeModal(); if (onConfirm) onConfirm(); });

  actions.appendChild(cancel);
  actions.appendChild(confirm);
  root.appendChild(actions);

  openModal({ title: title || 'Confirm', contentEl: root });
}
```

- [ ] **Step 4: Run the smoke to verify it passes**

Run: `node --test tests/unit/js/test_timeline_smoke.js`
Expected: PASS — the module loads with no syntax/import error.

- [ ] **Step 5: Run the full JS suite**

Run: `python pytest_runner.py --js`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add js/timeline/modals/confirm-modal.js tests/unit/js/test_timeline_smoke.js
git commit -m "feat(modals): generic confirmModal on modal-shell

fleet-confirm is RUN_SCRIPT-bound; destructive actions like Clear cache
need a parameterized confirm. Cancel->closeModal; Confirm->closeModal+onConfirm.
Covered by the module-load smoke + on-wall sign-off (modal DOM is
Playwright-verified per the fleet-confirm convention).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PVeEo4Mcq6YhcjArDRikoX"
```

---

### Task 3: `CLEAR_CACHE` server handler + client dispatch

**Files:**
- Modify: `mosaicmesh/websocket/legacy.py` (add a `CLEAR_CACHE` branch)
- Modify: `index.html` (add a `CLEAR_CACHE` client-dispatch branch)
- Test: `tests/unit/test_clear_cache_msg.py` (new; mirrors `test_cache_pull_msg.py`)

**Interfaces:**
- Consumes: `server.settings.clients` (dict of `client_key -> Client` with `.displayID`, `.cachedSegments`); `broadcast_to_client(key, msg)`; the `msg_response` dispatch shape `{SRC, DEST, REQUEST, PAYLOAD}`.
- Produces: server handles `REQUEST=="CLEAR_CACHE"` with `PAYLOAD={displayID}` (also `{clientKey}` / none→all), broadcasting `{REQUEST:"CLEAR_CACHE", PAYLOAD:"NONE"}` per targeted client + emptying each `client.cachedSegments`, responding `{status:"SUCCESS", count:N}`. Client (index.html) on `CLEAR_CACHE` calls `mmCache.clear()`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_clear_cache_msg.py` (mirrors `test_legacy_run_script.py` exactly — `server.msg_response(msg, session)` takes a session arg and returns a **jsonpickle-encoded string**; the handler calls the bare `broadcast_to_client` imported into `legacy`, so patch it there):

```python
"""Unit tests for the CLEAR_CACHE handler in mosaicmesh/websocket/legacy.py."""
import sys, argparse, jsonpickle
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

_orig = argparse.ArgumentParser.parse_args
argparse.ArgumentParser.parse_args = lambda self, *a, **k: argparse.Namespace(Port=3000, Verbose=False)
try:
    import server
    import mosaicmesh.websocket.legacy as legacy
finally:
    argparse.ArgumentParser.parse_args = _orig


def _sess():
    s = MagicMock(); s.id = "s"; s.request = MagicMock()
    s.request.remote = "127.0.0.1"; s.request.headers = {"User-Agent": "T"}
    return s


def _three_clients():
    server.settings = server.Settings()
    for k, g, segs in (("a", "G", {"T1_0", "T2_0"}), ("b", "G", {"T1_0"}), ("c", "Other", {"Z_0"})):
        cl = server.Client(); cl.displayID = g; cl.cachedSegments = set(segs)
        server.settings.clients[k] = cl


def _dispatch(payload):
    sent = []
    with patch.object(legacy, "broadcast_to_client",
                      lambda key, msg: sent.append((key, msg.get("REQUEST")))):
        ret = server.msg_response(
            {"SRC": "admin", "DEST": "SRV", "REQUEST": "CLEAR_CACHE", "PAYLOAD": payload}, _sess())
    return sent, jsonpickle.decode(ret)["PAYLOAD"]


def test_clear_cache_group_broadcasts_and_clears():
    _three_clients()
    sent, payload = _dispatch({"displayID": "G"})
    assert set(k for k, _ in sent) == {"a", "b"}                    # only group G, not "c"
    assert all(r == "CLEAR_CACHE" for _, r in sent)
    assert server.settings.clients["a"].cachedSegments == set()
    assert server.settings.clients["b"].cachedSegments == set()
    assert server.settings.clients["c"].cachedSegments == {"Z_0"}   # untouched
    assert payload["count"] == 2


def test_clear_cache_single_client():
    _three_clients()
    sent, payload = _dispatch({"clientKey": "a"})
    assert set(k for k, _ in sent) == {"a"}
    assert server.settings.clients["a"].cachedSegments == set()
    assert payload["count"] == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/test_clear_cache_msg.py -c tests/pytest.ini -v`
Expected: FAIL — no `CLEAR_CACHE` branch, so `resp["PAYLOAD"]` isn't the SUCCESS shape (or the branch falls through to the default).

- [ ] **Step 3: Add the `CLEAR_CACHE` handler (`mosaicmesh/websocket/legacy.py`)**

Immediately after the `RELOAD` branch (ends ~line 560, `response["PAYLOAD"] = "SUCCESS"`), add:

```python
    elif(msg["REQUEST"] == "CLEAR_CACHE"):
        # Admin command: wipe the cached render segments on a group's devices AND clear
        # the server's per-client cachedSegments record (so _resolve_media_url stops
        # routing to a now-deleted localhost seg). Scopes mirror RELOAD:
        #   PAYLOAD.clientKey -> one device; PAYLOAD.displayID -> that group; else -> all.
        payload = msg.get("PAYLOAD") or {}
        client_key = payload.get("clientKey") if isinstance(payload, dict) else None
        display_id = payload.get("displayID") if isinstance(payload, dict) else None
        if client_key:
            keys = [client_key] if client_key in server.settings.clients else []
        elif display_id:
            keys = [k for k, c in server.settings.clients.items()
                    if getattr(c, "displayID", None) == display_id]
        else:
            keys = list(server.settings.clients.keys())
        for k in keys:
            broadcast_to_client(k, {"REQUEST": "CLEAR_CACHE", "PAYLOAD": "NONE"})
            c = server.settings.clients.get(k)
            cs = getattr(c, "cachedSegments", None) if c is not None else None
            if isinstance(cs, set):
                cs.clear()
            elif c is not None:
                c.cachedSegments = set()
        logging.warning("CLEAR_CACHE -> %d device(s)", len(keys))
        response["PAYLOAD"] = {"status": "SUCCESS", "count": len(keys)}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/test_clear_cache_msg.py -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Add the client dispatch (`index.html`)**

Find the `CLEAR_CACHE`-adjacent dispatch area — the `else if (data_obj.REQUEST == "STOP")` / `"PAUSE"` / `'PRECACHE'` chain (around line 1786–1807). After the `PRECACHE` branch's closing `}` (line ~1807), add:

```js
				else if (data_obj.REQUEST === 'CLEAR_CACHE' && window.mmCache) {
					// Admin cleared this group's cache: wipe the local cache (backend.clear)
					// + reset mmCache bookkeeping. Segments re-pull on next play (auto-render
					// + reconcile + arm-recache). Best-effort log for the server.
					mmCache.clear();
					if (sock && typeof SockJS !== 'undefined' && sock.readyState === SockJS.OPEN) {
						sock.send(generateMessage('SRV', 'CLIENTLOG', { msg: 'mmcache-cleared' }));
					}
				}
```

- [ ] **Step 6: Verify the client branch + full unit suite (no regression)**

Run:
```bash
grep -n "REQUEST === 'CLEAR_CACHE'" index.html
python pytest_runner.py --unit
```
Expected: the grep shows the new branch; the unit suite passes (nothing else touches these handlers).

- [ ] **Step 7: Commit**

```bash
git add mosaicmesh/websocket/legacy.py index.html tests/unit/test_clear_cache_msg.py
git commit -m "feat(cache): CLEAR_CACHE handler (group scope) + client dispatch

Server mirrors RELOAD scope: broadcast CLEAR_CACHE to the group's clients
+ empty each client's cachedSegments (so serves stop routing to deleted
localhost segs). Client runs mmCache.clear(). Responds {count}.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PVeEo4Mcq6YhcjArDRikoX"
```

---

### Task 4: `mmcache://clearall` native handler (`tweak/mmcache/Tweak.x`)

**Files:**
- Modify: `tweak/mmcache/Tweak.x` (add a `clearall` op branch)

**Interfaces:**
- Consumes: `handle_mmcache(const char *url)` parses `mmcache://<op>?...`; `MM_CACHE_DIR`, `nsstr()`, `objc_msgSend`, `mmclog()`.
- Produces: a `mmcache://clearall` URL (no token) wipes the `MM_CACHE_DIR` contents. The mmvideo backend's `clear()` (Task 1) navs this. Deployed in Task 6.

> No node/pytest coverage (native ObjC). The gate here is a clean theos build (Task 6 Step 1 also builds); Step 3 is a source-review-level change matching the existing `fetch`/`evict` idiom.

- [ ] **Step 1: Add the `clearall` branch BEFORE the token-safety gate**

In `handle_mmcache` (`tweak/mmcache/Tweak.x`), the current flow parses `op`, computes `query`, then parses `token` and rejects unsafe tokens (`if (!token_is_safe(token)) { ... return; }`). Because `clearall` carries **no token**, it must be handled BEFORE that gate. Insert it right after the `query` line (`const char *query = (*p == '?') ? p + 1 : "";`) and BEFORE the `char token[128]` block:

```c
    if (strncmp(op, "clearall", 8) == 0) {
        /* Wipe the whole cache dir (no token). Remove the dir, then recreate it empty so
           lighttpd on :8080 still has a docroot. Foundation-only, runtime messaging. */
        id fm = ((id (*)(id, SEL))objc_msgSend)(
            (id)objc_getClass("NSFileManager"), sel_registerName("defaultManager"));
        ((int (*)(id, SEL, id, id *))objc_msgSend)(
            fm, sel_registerName("removeItemAtPath:error:"), nsstr(MM_CACHE_DIR), (id *)0);
        ((int (*)(id, SEL, id, int, id, id))objc_msgSend)(
            fm, sel_registerName("createDirectoryAtPath:withIntermediateDirectories:attributes:error:"),
            nsstr(MM_CACHE_DIR), 1, (id)0, (id)0);
        mmclog("[mmcache] clearall -> wiped %s\n", MM_CACHE_DIR);
        return;
    }
```

Leave the existing `token` parse + `token_is_safe` gate + `fetch`/`evict` branches unchanged below it.

- [ ] **Step 2: Sanity-check the edit locally (no build here)**

Run:
```bash
grep -n "clearall" tweak/mmcache/Tweak.x
```
Expected: the new branch is present, positioned before `char token[128]`. (The actual theos build + on-device load happens in Task 6 — this task's deliverable is the source change; committing it lets Task 6 build from a clean tree.)

- [ ] **Step 3: Commit**

```bash
git add tweak/mmcache/Tweak.x
git commit -m "feat(tweak): mmcache://clearall wipes the MosaicMeshCache dir

Adds a no-token clearall op (before the token-safety gate) that removes +
recreates the cache dir. Backs the mmvideo backend.clear(). Build + staged
fleet redeploy in the deploy task.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PVeEo4Mcq6YhcjArDRikoX"
```

---

### Task 5: Fleet "Clear cache" button

**Files:**
- Modify: `js/timeline/fleet/fleet-view.js` (import `confirmModal`; add `clearCache()`)
- Modify: `admin.html` (add the button next to Reload, ~line 1051)

**Interfaces:**
- Consumes: `confirmModal({title, message, confirmLabel, danger, onConfirm})` (Task 2); `this.selectedGroupId`; `this.$store.mm` (`displays`, `toast`); `window.sock` + `window.generateMessage` (the `RELOAD` wrapper pattern at `fleet-view.js:72–86`).
- Produces: a `clearCache()` method + a `↯ Clear cache` button firing `CLEAR_CACHE {displayID}` (Task 3's server handler).

- [ ] **Step 1: Add the `confirmModal` import (`js/timeline/fleet/fleet-view.js`)**

After the existing modal imports (near line 13, `import { fireFleetAction } from '../modals/fleet-confirm.js';`), add:

```js
import { confirmModal } from '../modals/confirm-modal.js';
```

- [ ] **Step 2: Add the `clearCache()` method**

Immediately after `reloadGroup()` (ends ~line 86) and before `calibrate()`, add:

```js
    clearCache() {
      const id = this.selectedGroupId;
      if (!id) return;
      const count = (this.$store.mm.displays || []).filter(d => d.displayID === id).length;
      confirmModal({
        title: `Clear cache (group "${id}")`,
        message: `Clear cached video on ${count} device${count === 1 ? '' : 's'} in "${id}"? They'll re-pull on next play.`,
        confirmLabel: `Clear ${count} device${count === 1 ? '' : 's'}`,
        danger: true,
        onConfirm: () => {
          if (typeof window.sock === 'undefined' || typeof window.generateMessage !== 'function') {
            this.$store.mm.toast('SockJS not available; reload the page.', 'error');
            return;
          }
          try {
            window.sock.send(window.generateMessage('SRV', 'CLEAR_CACHE', { displayID: id }));
            this.$store.mm.toast(`Cache clear sent to "${id}" (${count} device${count === 1 ? '' : 's'}).`, 'info');
          } catch (e) {
            this.$store.mm.toast(`Failed to send clear: ${e?.message || e}`, 'error');
          }
        },
      });
    },
```

- [ ] **Step 3: Add the button (`admin.html`, next to Reload ~line 1051)**

Immediately after the Reload button line:
```html
                      <button class="btn" @click="reloadGroup()" title="Reload the display page on every device in this group">↻ Reload</button>
```
add:
```html
                      <button class="btn" @click="clearCache()" title="Wipe cached video on every device in this group; they re-pull on next play">↯ Clear cache</button>
```

- [ ] **Step 4: Verify wiring + full JS suite**

Run:
```bash
grep -n "clearCache" js/timeline/fleet/fleet-view.js admin.html
python pytest_runner.py --js
```
Expected: `clearCache` appears in both files (method + button); JS suite green (this task adds no new test — the method is a thin SockJS wrapper like `reloadGroup`, which is itself untested; its behavior is covered by the on-wall sign-off. If `fleet-view` has an existing node test that constructs the component, extend it to assert `clearCache` sends the right frame with a mocked `window.sock`).

- [ ] **Step 5: Commit**

```bash
git add js/timeline/fleet/fleet-view.js admin.html
git commit -m "feat(fleet): Clear cache button (group-scoped CLEAR_CACHE + confirm)

Sibling of reloadGroup: confirmModal gate then CLEAR_CACHE {displayID}
over SockJS. Placed next to Reload in the group-actions row.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PVeEo4Mcq6YhcjArDRikoX"
```

---

### Task 6: Build the tweak + staged fleet redeploy (operational; gated on Tasks 1–5)

**Files:** none (operational). Uses `tweak/mmcache/build.sh`, single-device SSH (the `cat >`-over-ssh pattern; scp is absent on iOS-5 — [[device-automation-tooling]]).

**Interfaces:** Consumes the committed `Tweak.x` (Task 4). Produces the rebuilt `mmcache.dylib` deployed to the fleet's `/Library/MobileSubstrate/DynamicLibraries/mmcache.dylib`.

This is a **manual, human-in-the-loop** task (native build + physical fleet). The subagent executing this plan should STOP after Task 5 and hand back to the operator/controller for Tasks 6–7.

- [ ] **Step 1: Build the dylib**

Run: `bash tweak/mmcache/build.sh`
Expected: `make` succeeds; the trailer prints `CLEAN (plain ObjC)`, `NONE` (no ObjC classes), a small undefined-symbol list (libSystem/ObjC runtime only), and `DYLIB=<path> (<bytes> bytes)`. If the theos toolchain isn't reachable from this shell, report BLOCKED with the build error (the build runs under the Linux/theos env, `$HOME/theos`).

- [ ] **Step 2: Copy the built dylib into the repo tweak dir**

Copy the built `~/mmcache/*.dylib` over `tweak/mmcache/mmcache.dylib` (the fleet-deploy source of truth), then `git add tweak/mmcache/mmcache.dylib && git commit -m "build(tweak): rebuild mmcache.dylib with clearall"` (binary artifact; committed like the existing one).

- [ ] **Step 3: Deploy to ONE pilot device (staged)**

Push the dylib to one device and respring, using the cat-over-ssh pattern (scp is absent):
```bash
KEY=~/.ssh/mosaic_ipad
OPTS="-o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes"
IP=<pilot-ip>
ssh -i "$KEY" $OPTS root@$IP "cat > /Library/MobileSubstrate/DynamicLibraries/mmcache.dylib" < tweak/mmcache/mmcache.dylib
ssh -i "$KEY" $OPTS root@$IP "killall SpringBoard"   # respring so the new dylib loads
```
Wait for the webclip to relaunch + reconnect (verify via the access-log UA = `Web/1.0 CFNetwork`, NOT Safari — [[onwall-signoff-needs-webclip-not-safari]]).

- [ ] **Step 4: Smoke-test the pilot**

From the admin console (or `tools/`), send `CLEAR_CACHE {displayID}` to the pilot's group (or a single-client `CLEAR_CACHE`), then SSH-`ls` the pilot's `/var/mobile/Media/MosaicMeshCache/` — expect it emptied, and `/var/mobile/mmcache.log` shows `clearall -> wiped`. If the pilot misbehaves, STOP and do not roll out.

- [ ] **Step 5: Roll out to the rest of the fleet (paced, small batches)**

Repeat Step 3 for the remaining devices in **small sequential batches with pacing** (never a burst — a parallel SSH sweep trips the AP flood-protection, [[fleet-ssh-no-burst]]). Respring each. Verify each reconnects on the webclip.

- [ ] **Step 6: Commit note**

No code commit here beyond Step 2's dylib. Record completion in the progress ledger.

---

### Task 7: On-wall sign-off (operational; gated on Task 6)

**Files:** none. Uses the admin "Clear cache" button (or a `tools/` SockJS wrapper) + single-device SSH.

**Interfaces:** the deployed feature end-to-end.

Manual, human-in-the-loop. Validates the whole feature on the wall.

- [ ] **Step 1: Seed cache**

Put a display group on a cached video playlist (e.g. `PullTest3`) and let its devices cache the segments (confirm via `GET /api/discovery/devices` `cachedSegments`, or SSH-`ls` a device's `MosaicMeshCache`).

- [ ] **Step 2: Click "Clear cache" for the group + confirm**

In Fleet, select the group, click **↯ Clear cache**, confirm the dialog.

- [ ] **Step 3: Verify the wipe**

- On a device: SSH-`ls /var/mobile/Media/MosaicMeshCache/` → emptied; `/var/mobile/mmcache.log` shows `clearall`.
- Server record: `GET /api/discovery/devices` → the group's `cachedSegments` are empty.

- [ ] **Step 4: Verify re-pull + play**

PLAY the group's cached playlist again. Confirm devices re-pull and play — metric: `verr` clear AND `rs>=2` AND `ct` advancing across two CLIENTLOG snapshots (NOT `elapsed` — [[wall-verr3-is-mmvideo-not-cache]]). The arm-recache poll ([[wall-verr3-is-mmvideo-not-cache]]) may drive the first re-arm; that's expected. Verify in the webclip, not Safari ([[onwall-signoff-needs-webclip-not-safari]]).

- [ ] **Step 5: Record the sign-off** in the progress ledger.

---

## Deploy note (not a task step)

Admin JS + server (Tasks 1–3, 5) reach devices on their next served-file load/reload; a server restart picks up the Python. The `mmcache://clearall` tweak (Task 4) requires the staged dylib redeploy (Task 6) before the mmvideo on-disk wipe engages; until then `clear()` is a safe no-op on old-tweak devices (server record still clears; Modern clients fully functional).
