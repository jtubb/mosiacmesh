# Client-Pull Cache — Plan 3: Modern Backend + Legacy Retirement

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the modern-browser Cache-API/Service-Worker backend behind `mmCache.js`, and retire the legacy lighttpd + SSH segment-push + SSH cache-capability probe now that the client-pull path is proven end-to-end on the iPad-1 fleet.

**Architecture:** Two independent task groups. **A (modern backend):** a second `mmCache` backend (`js/mmCacheBackendModern.js`) driving the Cache API + a Service Worker (`js/sw.js`); the display client feature-detects and registers mmvideo (iOS-5) or modern (real browser). **B (retirement):** `cacheMode` becomes client-announced (the client already has `ANNOUNCE_CACHE_MODE`; it announces cache-capable when a backend registers), then the SSH probe + SSH push + lighttpd onboarding are deleted. The `http://127.0.0.1:8080/<name>` play URL stays (mmvideo maps it to `MosaicMeshCache/`); lighttpd is no longer needed to serve it.

**Tech Stack:** ES5-parseable client JS (must load on iPad-1) + `node --test`; Python 3 server; PowerShell onboarding.

## Global Constraints

- All client JS files must **PARSE on iPad-1/iOS-5** (ES5 syntax: `var`/`function`, no `let`/`const`/arrow/`class`/template literals). The modern backend's Cache-API calls (Promises) only *run* on a modern browser (guarded by feature-detect), but the file's SYNTAX must be ES5 so the iPad-1 can parse it without error.
- Backend interface (unchanged from Plan 1): `fetchToCache(url, token, onDone, onFail)`, `localSrc(token) -> string|null`, `evict(token)`, `has(token) -> bool`, `size(token) -> bytes`.
- `mmCache.registerBackend(b)` sets the ONE active backend. Feature-detect precedence: mmvideo (`window.__mmCacheReady`) → modern (`'serviceWorker' in navigator && 'caches' in window`) → none.
- **Retirement is FULL DELETION** (operator decision 2026-07-09): remove `_push_segment_to_cached_clients`, `_poll_push_progress`, `_probe_cache_capability`, `_maybe_fire_cache_probe`, `_is_probe_eligible`, the render.py push loops, and the lighttpd onboarding step + config. No coexistence flag.
- `cacheMode` value `"lighttpd-localhost"` is retained as the "cache-capable → serve 127.0.0.1:8080" marker `_per_client_items` checks (the name is now legacy — mmvideo, not lighttpd, serves it). It is set by the client's `ANNOUNCE_CACHE_MODE`, not the deleted probe.
- JS tests: `node --test tests/unit/js/<file>`. Python tests: `python -m pytest tests/unit/<file> -c tests/pytest.ini -v`. Branch `fix/mmvideo-vtfix2`.
- The client-pull path (Plans 1-2) is DONE + verified on-device: PRECACHE → mmvideo tweak pull → `MosaicMeshCache/seg_<token>_<i>.mp4` → plays local.

---

## File Structure

- **Create `js/mmCacheBackendModern.js`** — modern `mmCache` backend: Cache-API `fetchToCache`/`evict`, `localSrc` = the original URL (the SW serves it from cache), `has`/`size`. ES5 syntax; Cache-API at runtime.
- **Create `js/sw.js`** — Service Worker: on `fetch` for a cached segment URL, respond from `caches`.
- **Create `tests/unit/js/mmcache-backend-modern.test.js`** — node test with a mock `caches`.
- **Modify `js/mmCacheBackendMmvideo.js`** — announce cache-capable to the server when the backend registers (drives `cacheMode` post-probe-deletion).
- **Modify `index.html`** — feature-detect + register the right backend + register the SW on modern.
- **Modify `server.py`** — DELETE `_is_probe_eligible`, `_probe_cache_capability`, `_maybe_fire_cache_probe`, `_push_segment_to_cached_clients`, `_poll_push_progress`, and the probe call on REGISTER.
- **Modify `mosaicmesh/render.py`** — remove the seg/full push loops in `_encode_group` (the pull block stays).
- **Modify `tools/onboard_devices.ps1`** — remove the lighttpd package install + `lighttpd.conf` deploy step.
- **Modify tests** — delete/adjust tests referencing the removed push/probe functions.

---

## PART A — Modern Cache-API backend

## Task 1: `js/mmCacheBackendModern.js` — Cache-API backend adapter

**Files:**
- Create: `js/mmCacheBackendModern.js`
- Test: `tests/unit/js/mmcache-backend-modern.test.js`

**Interfaces:**
- Produces: `window._mmCacheBackendModern` (the backend), `window.__mmRegisterModernBackend()`.

- [ ] **Step 1: Write the failing test**

```js
// tests/unit/js/mmcache-backend-modern.test.js
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

function loadModern() {
  const code = fs.readFileSync(new URL('../../../js/mmCacheBackendModern.js', import.meta.url), 'utf8');
  // minimal Cache API mock
  const store = {};               // cacheName -> { url -> {bytes} }
  const caches = {
    open: function (name) {
      store[name] = store[name] || {};
      const c = store[name];
      return Promise.resolve({
        add: function (url) { c[url] = { bytes: 1234 }; return Promise.resolve(); },
        match: function (url) { return Promise.resolve(c[url] ? { _mm: c[url] } : undefined); },
        'delete': function (url) { delete c[url]; return Promise.resolve(true); }
      });
    }
  };
  const sandbox = { window: { caches: caches }, caches: caches };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox.window;
}

test('modern backend: fetchToCache adds to cache + localSrc returns the url', async function () {
  const w = loadModern();
  const b = w._mmCacheBackendModern;
  await new Promise(function (res, rej) {
    b.fetchToCache('http://s/seg_a_0.mp4', 'T1', function () { res(); }, function (t, r) { rej(new Error(r)); });
  });
  assert.strictEqual(b.has('T1'), true);
  assert.strictEqual(b.localSrc('T1'), 'http://s/seg_a_0.mp4');   // SW serves the same url from cache
  assert.strictEqual(b.localSrc('T2'), null);
  b.evict('T1');
  assert.strictEqual(b.has('T1'), false);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/mmcache-backend-modern.test.js`
Expected: FAIL — cannot read `js/mmCacheBackendModern.js`.

- [ ] **Step 3: Write the implementation**

```js
// js/mmCacheBackendModern.js — modern-browser mmCache backend (Cache API + a Service
// Worker registered separately). No native code. localSrc returns the ORIGINAL url — the
// SW intercepts the fetch and serves it from cache, so no file:// is needed. ES5 SYNTAX
// so the file parses on iPad-1 (its Cache-API methods only RUN on a modern browser, where
// this backend is the one registered; iOS-5 registers the mmvideo backend instead).
(function (root) {
  var CACHE_NAME = 'mm-seg';
  var _present = {};   // token -> url
  function _caches() { return (root.caches ? root.caches : (root.window && root.window.caches)); }

  var backend = {
    name: 'modern',
    fetchToCache: function (url, token, onDone, onFail) {
      var cs = _caches();
      if (!cs) { onFail(token, 'no-cache-api'); return; }
      cs.open(CACHE_NAME).then(function (c) {
        return c.add(url).then(function () {
          _present[token] = url;
          onDone(token);
        });
      })['catch'](function () { onFail(token, 'add-failed'); });
    },
    localSrc: function (token) { return _present.hasOwnProperty(token) ? _present[token] : null; },
    evict: function (token) {
      var url = _present[token]; delete _present[token];
      var cs = _caches();
      if (cs && url) { cs.open(CACHE_NAME).then(function (c) { c['delete'](url); })['catch'](function () {}); }
    },
    has: function (token) { return _present.hasOwnProperty(token); },
    size: function (token) { return _present.hasOwnProperty(token) ? 1 : 0; }
  };

  root._mmCacheBackendModern = backend;
  root.__mmRegisterModernBackend = function () {
    if (root.mmCache && root.mmCache.registerBackend) { root.mmCache.registerBackend(backend); }
  };
})(typeof window !== 'undefined' ? window : global);
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/mmcache-backend-modern.test.js`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add js/mmCacheBackendModern.js tests/unit/js/mmcache-backend-modern.test.js
git commit -m "feat(mmCache): modern Cache-API backend adapter"
```

---

## Task 2: `js/sw.js` — Service Worker (serve cached segments from cache)

**Files:**
- Create: `js/sw.js`

**Interfaces:** none (browser-registered). Serves any request whose URL is in the `mm-seg` cache from cache; else passes through to network.

**No node test** — a Service Worker needs a browser + registration; it's exercised by the on-modern manual check in Task 3's notes. Keep it tiny + correct.

- [ ] **Step 1: Write the Service Worker**

```js
// js/sw.js — MosaicMesh Service Worker: cache-first for segments pre-cached by
// mmCacheBackendModern (Cache API 'mm-seg'); everything else falls through to network.
var MM_CACHE = 'mm-seg';
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', function (event) {
  event.respondWith(
    caches.open(MM_CACHE).then(function (c) {
      return c.match(event.request).then(function (hit) {
        return hit || fetch(event.request);
      });
    })
  );
});
```

- [ ] **Step 2: Commit**

```bash
git add js/sw.js
git commit -m "feat(sw): MosaicMesh Service Worker cache-first for pre-cached segments"
```

---

## Task 3: index.html — feature-detect + register the right backend

**Files:**
- Modify: `index.html` (the backend-registration block added in Plan 2, ~line 1675-1687)

**Interfaces:**
- Consumes: `window.__mmCacheReady` (mmvideo), `window.__mmRegisterMmvideoBackend`, `window.__mmRegisterModernBackend`.

- [ ] **Step 1: Load the modern backend script** — where `js/mmCacheBackendMmvideo.js` is `<script src>`-included, add after it:

```html
<script src="js/mmCacheBackendModern.js"></script>
```

- [ ] **Step 2: Feature-detect + register + register the SW** — replace the existing registration block (the `if (window.__mmCacheReady && window.__mmRegisterMmvideoBackend) { ... }`) with:

```javascript
// Register the cache backend that fits this client: mmvideo (iOS-5 native bridge) if the
// mmcache tweak injected; else the modern Cache-API backend on a real browser. Neither ->
// mmCache stays backend-less (PRECACHE acks CACHE_FAILED -> streams centrally, safe).
if (window.__mmCacheReady && window.__mmRegisterMmvideoBackend) {
  window.__mmRegisterMmvideoBackend();
} else if (('serviceWorker' in navigator) && ('caches' in window) && window.__mmRegisterModernBackend) {
  window.__mmRegisterModernBackend();
  try { navigator.serviceWorker.register('js/sw.js'); } catch (e) {}
}
```

- [ ] **Step 3: Manual verify (modern browser)** — load the display page in Chrome/desktop with a cached playlist; confirm `mmCache.backend.name === 'modern'`, the SW registers (DevTools → Application → Service Workers), and a PRECACHE caches the segment (Application → Cache Storage → `mm-seg`). iPad-1 is unaffected (registers mmvideo).

- [ ] **Step 4: Run the JS suite (no regression)** — `node --test tests/unit/js/*.js` (all pass; the new file parses).

- [ ] **Step 5: Commit**

```bash
git add index.html
git commit -m "feat(client): feature-detect + register mmvideo|modern cache backend + SW"
```

---

## PART B — Legacy retirement (full deletion)

## Task 4: Client reports cache-capable on REGISTER (replaces the probe)

**Files:**
- Modify: `js/mosiacmesh.js` (the REGISTER payload built in the SockJS `onopen`)
- Modify: `mosaicmesh/websocket/legacy.py` (a small `apply_cache_capability` helper + call it in the REGISTER branch)
- Test: `tests/unit/test_register_cache_capable.py` (Create)

**Interfaces:**
- Produces: REGISTER payload carries `cacheCapable: bool`; `apply_cache_capability(client, payload)` sets `client.cacheMode = "lighttpd-localhost"` when true and the client is still at the default `"none"`. Replaces the deleted SSH probe. **REGISTER fires on connect, AFTER index.html registered the backend, so `window.mmCache.backend` is set by then** — announcing at register-time (before the socket opens) would silently no-op, which is why this rides REGISTER.

- [ ] **Step 1: Add cacheCapable to the REGISTER payload** — in `js/mosiacmesh.js`, the SockJS `onopen` builds the REGISTER message (`sock.send(generateMessage("SRV","REGISTER",{ "width": ..., "touch": hasTouch }))`). Add one field to that PAYLOAD object:

```javascript
					"touch": hasTouch, "cacheCapable": !!(window.mmCache && window.mmCache.backend)}));
```
(ES5-safe. `window.mmCache.backend` is non-null iff index.html registered the mmvideo or modern backend before connect.)

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_register_cache_capable.py
import types
from mosaicmesh.websocket.legacy import apply_cache_capability

def test_cacheCapable_true_upgrades_default_none():
    c = types.SimpleNamespace(cacheMode="none")
    apply_cache_capability(c, {"cacheCapable": True})
    assert c.cacheMode == "lighttpd-localhost"

def test_cacheCapable_false_leaves_none():
    c = types.SimpleNamespace(cacheMode="none")
    apply_cache_capability(c, {"cacheCapable": False})
    assert c.cacheMode == "none"

def test_does_not_override_a_non_default_mode():
    c = types.SimpleNamespace(cacheMode="none")   # simulate an already-decided value
    c.cacheMode = "something-else"
    apply_cache_capability(c, {"cacheCapable": True})
    assert c.cacheMode == "something-else"        # only upgrades from the default 'none'
```

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/unit/test_register_cache_capable.py -c tests/pytest.ini -q`
Expected: FAIL — `ImportError: cannot import name 'apply_cache_capability'`.

- [ ] **Step 4: Implement + wire the helper** — add to `mosaicmesh/websocket/legacy.py` (module level, near `handle_cache_ack`):

```python
def apply_cache_capability(client, payload):
    """Client-reported cache capability on REGISTER (replaces the deleted SSH probe). A
    client with an mmCache backend (mmvideo tweak or modern SW) serves its pulled segments
    at http://127.0.0.1:8080/<name>, so mark it cache-capable -> _per_client_items routes it
    the local URL. Only upgrades from the default 'none'; a later ANNOUNCE_CACHE_MODE 'none'
    (client-side local-play failure) remains authoritative."""
    if client is not None and (payload or {}).get("cacheCapable") and getattr(client, "cacheMode", "none") == "none":
        client.cacheMode = "lighttpd-localhost"
```
Then in the REGISTER branch of `msg_response` (grep `"REGISTER"`), after the `Client` is created/updated and in scope, call `apply_cache_capability(client, msg.get("PAYLOAD"))`.

- [ ] **Step 5: Run to verify it passes** — `python -m pytest tests/unit/test_register_cache_capable.py -c tests/pytest.ini -q` → PASS (3). Then `python -c "import server"` (side-effect-free) + `node --test tests/unit/js/*.js` (mosiacmesh.js still parses).

- [ ] **Step 6: Commit**

```bash
git add js/mosiacmesh.js mosaicmesh/websocket/legacy.py tests/unit/test_register_cache_capable.py
git commit -m "feat(register): client reports cache-capable -> cacheMode (replaces the SSH probe)"
```

---

## Task 5: Delete the SSH cache-capability probe

**Files:**
- Modify: `server.py` — delete `_is_probe_eligible` (~367), `_probe_cache_capability` (~379), `_maybe_fire_cache_probe` (~448), and the call to `_maybe_fire_cache_probe(...)` (in the REGISTER path — grep `_maybe_fire_cache_probe(`)
- Test: `tests/unit/` — delete any test exercising these (grep `probe_cache` / `_maybe_fire_cache_probe` / `_is_probe_eligible` under tests/)

- [ ] **Step 1: Find all references** — `grep -rnE "_probe_cache_capability|_maybe_fire_cache_probe|_is_probe_eligible" server.py mosaicmesh tests` — note every call site (expect: the 3 defs + the REGISTER call + possibly tests).

- [ ] **Step 2: Delete the three functions** in `server.py` and the `_maybe_fire_cache_probe(...)` call site (the REGISTER handler / wherever it fires). Remove now-unused imports they alone used (e.g. the SSH-probe shell string helpers if not shared).

- [ ] **Step 3: Delete/adjust probe tests** — remove test functions that call the deleted probe functions.

- [ ] **Step 4: Verify** — `python -c "import server; print('OK')"` (side-effect-free import still works) and `python -m pytest tests/unit/ -c tests/pytest.ini -q -k "not e2e"` (no import errors; nothing references the deleted names).

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit
git commit -m "refactor(cache): delete the SSH cache-capability probe (cacheMode is client-announced)"
```

---

## Task 6: Delete the SSH segment-push + remove the render push loops

**Files:**
- Modify: `mosaicmesh/render.py` — delete the seg/full push loops in `_encode_group` (~852-862; KEEP the pull block ~868-877 and its `seg_push_targets`/`full_push_targets` collection, which the pull `_pull_urls` still uses)
- Modify: `server.py` — delete `_push_segment_to_cached_clients` (~469) and `_poll_push_progress` (~608), plus the VNC/scp helpers used ONLY by the push (grep to confirm sole use), and `_reconcile_ipad_cache` if it only re-pushes
- Test: remove push tests (grep `_push_segment_to_cached_clients` / `_poll_push_progress` under tests/)

- [ ] **Step 1: Map sole-use helpers** — `grep -rnE "_push_segment_to_cached_clients|_poll_push_progress|_reconcile_ipad_cache|cachePushProgress" server.py mosaicmesh tests`. Anything referenced ONLY by the push is deletable; anything shared (e.g. the Veency pool, used by auto-arm) stays.

- [ ] **Step 2: Remove the render push loops** — in `_encode_group`, delete the two `for _push_key, _push_n in seg_push_targets:` / `full_push_targets:` loops that call `server._push_segment_to_cached_clients(...)` (~852-862). Leave `seg_push_targets`/`full_push_targets` collection and the client-pull `_pull_urls` block intact.

- [ ] **Step 3: Delete the push functions** in `server.py` (`_push_segment_to_cached_clients`, `_poll_push_progress`, and sole-use helpers found in Step 1).

- [ ] **Step 4: Delete push tests.**

- [ ] **Step 5: Verify** — `python -c "import server"`; `python -m pytest tests/unit/test_render_precache_hook.py tests/unit/test_cache_pull.py tests/unit/test_start_precache.py tests/unit/test_cache_pull_msg.py -c tests/pytest.ini -q` (the pull path still green) + a broad `-k "render or cache"` run.

- [ ] **Step 6: Commit**

```bash
git add server.py mosaicmesh/render.py tests/unit
git commit -m "refactor(cache): delete the SSH segment-push; client-pull is the sole cache path"
```

---

## Task 7: Remove lighttpd from onboarding + the config

**Files:**
- Modify: `tools/onboard_devices.ps1` — remove the `'lighttpd'` package from the install list (~173) and the `lighttpd.conf` deploy step (~908-918)
- Delete: `tools/lighttpd.conf` (if it exists and is unused elsewhere — grep first)

- [ ] **Step 1: Grep for lighttpd references** — `grep -rniE "lighttpd" tools server.py mosaicmesh` — confirm nothing at runtime depends on lighttpd (the `127.0.0.1:8080` URL is served by mmvideo now, not lighttpd; `_probe_cache_capability` which checked lighttpd is already deleted in Task 5).

- [ ] **Step 2: Remove the onboarding lines** — drop `'lighttpd'` from the apt install array and the config-deploy block. Add a one-line note that cache serving is now the mmcache tweak's `MosaicMeshCache/` + mmvideo's `mm_url_to_path`, no on-device web server.

- [ ] **Step 3: Delete `tools/lighttpd.conf`** if unreferenced.

- [ ] **Step 4: Commit**

```bash
git add tools/onboard_devices.ps1
git rm tools/lighttpd.conf   # if unreferenced
git commit -m "chore(onboard): drop lighttpd install + config (client-pull needs no on-device server)"
```

- [ ] **Step 5 (operator, not code):** on the fleet, lighttpd can be left running harmlessly or stopped; new onboards won't install it. No fleet action required for the pull to work (mmvideo already bypasses lighttpd).

---

## Self-Review (plan author)

- **Spec coverage:** modern backend (Tasks 1-3) covers the design's "Modern backend (JS only)"; retirement (Tasks 4-7) covers "Phase 2 — retire … delete _push_segment_to_cached_clients + _poll_push_progress, the cache-capability SSH probe, and the lighttpd onboarding step + config; cacheMode collapses to feature-detected client-side."
- **Placeholder scan:** none — JS tasks carry complete code + tests; deletion tasks name exact functions + line anchors + a grep-first step (deletions can't pre-quote every downstream line, so each starts by mapping references — a concrete, bounded action).
- **Type consistency:** modern backend implements the same 5-method interface as the mmvideo backend + the Plan-1 mock; `__mmRegisterModernBackend`/`_mmCacheBackendModern` mirror the mmvideo names; `ANNOUNCE_CACHE_MODE {mode:"lighttpd-localhost"}` matches the existing whitelisted server handler.
- **Ordering:** Task 4 (client announces cacheMode) MUST land before Task 5 (delete probe) so `cacheMode` is never left unset — the plan orders it that way.

## Risk note (retirement)

Full deletion removes the push safety net while the pull is ~1 day proven. The pull is verified end-to-end and the deletions are on a feature branch (revertable). Recommend: land Part A + Part B, keep the branch unmerged for a few days of fleet observation (MEMWATCH + `cachedSegments` propagation), then merge.
