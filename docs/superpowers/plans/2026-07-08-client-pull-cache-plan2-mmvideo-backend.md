# Client-Pull Cache — Plan 2: mmvideo iOS-5 Backend

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **NOTE:** Tasks 1, 4, 5 are ON-DEVICE (physical iPad-1 + deploy) and CANNOT be auto-run by a subagent — they are collaborative with the human operator. Tasks 2-3 are pure JS, node-testable, and run like normal subagent tasks.

**Goal:** Give the Plan-1 coordinator (`js/mmCache.js`) a working iOS-5 backend so a 1st-gen iPad pulls its own rendered segment to a local file and plays it via mmvideo's AVPlayer — no lighttpd, no SSH push.

**Architecture:** Mirror the `mmws` JS↔native bridge. JS→native: a hidden iframe navigates `mmcache://fetch?token=&url=` (and `mmcache://evict?token=`), intercepted by the mmvideo tweak's `webView:shouldStartLoadWithRequest:` hook, which runs an `NSURLSession` download to `/var/mobile/Media/mmcache/<token>.mp4`. native→JS: the tweak calls `stringByEvaluatingJavaScriptFromString` → `window.__mmCacheDone(token, bytes)` / `window.__mmCacheFail(token, reason)`. The JS adapter (`js/mmCacheBackendMmvideo.js`) wraps this as the coordinator's backend interface. A one-device spike de-risks the bridge + `file://` playback before any production native code.

**Tech Stack:** ES5 client JS + `node:vm` tests (Plan-1 harness); mmvideo tweak = plain ObjC (no ARC), Theos/WSL build; SockJS protocol.

## Global Constraints

- Client display JS (`js/mmCacheBackendMmvideo.js`, index.html inline) is **ES5 ONLY** — `var`/`function`, no `let`/`const`/arrow/`class`/`Promise`/`fetch`/template literals.
- mmvideo tweak: **plain ObjC, NO ARC, NO static ObjC classes, NO `sel_registerName`/`objc_getClass`** — use `@selector()` literals + `objc_msgSend` + C functions (per tweak/mmvideo/REFINDINGS §8/§11/§12; a static ObjC class or unbindable symbol SIGKILLs the load pre-`%ctor`). Foundation-only frameworks + `mmbuiltins.c` for compiler-rt builtins.
- Backend interface (from Plan 1, exact): `fetchToCache(url, token, onDone, onFail)`, `localSrc(token) -> string|null`, `evict(token)`, `has(token) -> bool`, `size(token) -> bytes`.
- Cache dir: `/var/mobile/Media/mmcache/`; local playable src: `file:///var/mobile/Media/mmcache/<token>.mp4`.
- Bridge scheme `mmcache://` (JS→native); callbacks `window.__mmCacheDone(token, bytes)` / `window.__mmCacheFail(token, reason)` (native→JS); readiness flag `window.__mmCacheReady` (tweak sets it at inject; JS registers the backend only if present).
- JS tests: append to a NEW `tests/unit/js/mmcache-backend.test.js` (ESM, `node:vm` sandbox). Run: `node --test tests/unit/js/mmcache-backend.test.js`.
- Branch: `fix/mmvideo-vtfix2` — do NOT switch/create branches.
- NEVER burst-SSH the fleet (memory: fleet-ssh-no-burst) — the on-device rollout is sequential + paced.
- Plan-1 coordinator is committed: `mmCache.registerBackend(b)`, `mmCache.handlePrecache({group,url,token})`, `mmCache.onAck`, `mmCache.localSrc(token)`.

> **⚑ SPIKE OUTCOME (Task 1 PASS, 2026-07-08 — supersedes `file://`/`mmcache/` below; see `tweak/mmcache-spike/SPIKE-FINDINGS.md`):**
> - Download API is **`NSData dataWithContentsOfURL:`** on a bg `dispatch_async_f` queue (iOS-5 has NO `NSURLSession`; no Blocks).
> - Download dir is **`/var/mobile/Media/MosaicMeshCache/<token>.mp4`** (the dir `mm_url_to_path` maps), NOT `mmcache/`.
> - **`localSrc` returns `http://127.0.0.1:8080/<token>.mp4`, NOT `file://`** — a raw `file://` media src is cross-origin-blocked by WebKit from an http page; mmvideo intercepts the `127.0.0.1:8080` URL and plays the local file (no lighttpd fetch). Already fixed in `js/mmCacheBackendMmvideo.js` (Task 2) + its test.
> - The bridge (`mmcache://` `WebAppController` hook) coexists with mmws and the tweak loads clean — Task 4 productionizes the spike tweak into `tweak/mmvideo/`.

---

## File Structure

- **Create `js/mmCacheBackendMmvideo.js`** — ES5 iOS-5 backend adapter: drives the `mmcache://` bridge, receives `__mmCacheDone/Fail`, tracks present tokens + sizes, implements the backend interface, exposes `__mmRegisterMmvideoBackend()`.
- **Create `tests/unit/js/mmcache-backend.test.js`** — node/vm tests for the adapter (mock the iframe nav + fire the window callbacks).
- **Modify `index.html`** — load `mmCache.js` + `mmCacheBackendMmvideo.js`; on connect, if `window.__mmCacheReady`, register the backend + wire `mmCache.onAck` to `sendMsg`; handle the `PRECACHE` request → `mmCache.handlePrecache`; in the video-src resolution, prefer `mmCache.localSrc(token) || centralUrl`.
- **Modify `tweak/mmvideo/Tweak.x`** — add the `mmcache://` scheme intercept + `__mmCacheDone/Fail` dispatch + `window.__mmCacheReady` flag (mirror `tweak/mmws/Tweak.x`).
- **Create `tweak/mmvideo/mmcache.h` / add to `MMTransplantEngine.m`** — the `NSURLSession` download-to-file primitive (C-callable; no static ObjC class).
- **Spike scratch:** `tweak/mmvideo/spike/` (throwaway; not shipped).

---

## Task 1: SPIKE — prove download → file:// → autoplay on ONE device (ON-DEVICE, collaborative)

**Goal:** Before any production native code, prove on one physical iPad-1 that (a) a JS `mmcache://` iframe nav reaches an mmvideo hook, (b) an `NSURLSession` download writes `/var/mobile/Media/mmcache/<token>.mp4`, (c) mmvideo's AVPlayer plays that `file://` with NO tap, and (d) the `__mmCacheDone` callback fires back into JS. This is exploratory — a throwaway build under `tweak/mmvideo/spike/`, NOT shipped.

**No automated test** (physical device). Success = operator confirmation.

- [ ] **Step 1: Minimal spike tweak** — in `tweak/mmvideo/spike/`, copy the current mmvideo build config; add to `Tweak.x` a `webView:shouldStartLoadWithRequest:` hook (mirror `tweak/mmws/Tweak.x:205`) that, on scheme `mmcache`, parses `token`+`url` from the query and calls a C function `mm_cache_fetch(token, url)`; return `NO` to swallow the nav. Add `mm_cache_fetch` to `MMTransplantEngine.m`: an `NSURLSession` (or `[NSData dataWithContentsOfURL:]` on a background `dispatch_queue`) that writes to `/var/mobile/Media/mmcache/<token>.mp4`, then on completion calls `stringByEvaluatingJavaScriptFromString:@"if(window.__mmCacheDone)window.__mmCacheDone('<token>',<bytes>)"` (mirror `tweak/mmws/Tweak.x:61,86`). Set `window.__mmCacheReady=true` at inject.

- [ ] **Step 2: Build (WSL/Theos)** — `cd tweak/mmvideo/spike && ./build.sh` (or the milestone build script). Confirm a symbol-clean armv7 dylib per REFINDINGS (no static ObjC class, no unbindable symbols).

- [ ] **Step 3: Deploy to ONE device + serve a test page** — sequential scp the spike dylib to a single screen (paced; NEVER burst the fleet). Serve a test page via the MM server route (file:// is rejected by iOS-5 uiopen) that: sets `<video>` hidden, runs `document.body.appendChild(iframe with src='mmcache://fetch?token=spk1&url=<a-real-central-seg-url>')`, defines `window.__mmCacheDone=function(t,b){ v.src='file:///var/mobile/Media/mmcache/'+t+'.mp4'; v.play(); }`.

- [ ] **Step 4: Observe (operator)** — confirm on the device: the file lands (`ls -l /var/mobile/Media/mmcache/`), `__mmCacheDone` fired (log), and the video **plays fullscreen with NO tap** from the `file://` src. Capture a screenshot.

- [ ] **Step 5: Record findings** — write outcomes to `tweak/mmvideo/REFINDINGS.md` (a new section): does the `mmcache://` hook coexist with the mmws hook (load order / `%orig` chaining)? does `NSURLSession` bind cleanly (or use `NSData`)? exact save path + permissions? Any load-fragility. **These findings gate Task 4's production code.** Remove the spike build from the device; leave `spike/` in-repo (gitignored dylibs). Commit the findings + spike source: `git commit -m "spike(mmcache): prove mmvideo download->file://->autoplay bridge"`.

**If the spike fails** (bridge won't coexist, or `file://` won't autoplay): STOP and escalate — the design's iOS-5 backend assumption is wrong and Plan 2 needs rethinking before Tasks 2-5.

---

## Task 2: JS iOS-5 backend adapter (`js/mmCacheBackendMmvideo.js`)

**Files:**
- Create: `js/mmCacheBackendMmvideo.js`
- Test: `tests/unit/js/mmcache-backend.test.js`

**Interfaces:**
- Consumes: `mmCache.registerBackend(b)` (Plan 1).
- Produces: `window._mmCacheBackendMmvideo` (the backend object), `window.__mmRegisterMmvideoBackend()`, and the native→JS callbacks `window.__mmCacheDone(token, bytes)` / `window.__mmCacheFail(token, reason)`. Backend methods: `fetchToCache(url,token,onDone,onFail)`, `localSrc(token)`, `evict(token)`, `has(token)`, `size(token)`.

- [ ] **Step 1: Write the failing test**

```js
// tests/unit/js/mmcache-backend.test.js
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

function loadBackend() {
  const code = fs.readFileSync(new URL('../../../js/mmCacheBackendMmvideo.js', import.meta.url), 'utf8');
  const navs = [];
  const sandbox = {
    window: {},
    document: {
      documentElement: { appendChild: function () {} },
      createElement: function () { return { style: {}, parentNode: null,
        set src(v) { navs.push(v); } }; }
    },
    setTimeout: function (fn) { return 0; },   // don't auto-remove during the test
    encodeURIComponent: encodeURIComponent
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return { w: sandbox.window, navs: navs };
}

test('fetchToCache navigates mmcache://fetch and resolves on __mmCacheDone', function () {
  const { w, navs } = loadBackend();
  const b = w._mmCacheBackendMmvideo;
  let done = null;
  b.fetchToCache('http://c/seg-a.mp4', 'T1', function (t) { done = t; }, function () {});
  assert.ok(navs[0].indexOf('mmcache://fetch?token=T1&url=') === 0);
  assert.strictEqual(b.has('T1'), false);            // not present until acked
  w.__mmCacheDone('T1', 12345);                       // native fires back
  assert.strictEqual(done, 'T1');
  assert.strictEqual(b.has('T1'), true);
  assert.strictEqual(b.size('T1'), 12345);
  assert.strictEqual(b.localSrc('T1'), 'file:///var/mobile/Media/mmcache/T1.mp4');
  assert.strictEqual(b.localSrc('T2'), null);
});

test('fetchToCache rejects on __mmCacheFail; evict clears + navigates', function () {
  const { w, navs } = loadBackend();
  const b = w._mmCacheBackendMmvideo;
  let failed = null;
  b.fetchToCache('http://c/x.mp4', 'T9', function () {}, function (t, r) { failed = [t, r]; });
  w.__mmCacheFail('T9', 'net');
  assert.deepStrictEqual(failed, ['T9', 'net']);
  assert.strictEqual(b.has('T9'), false);
  w.__mmCacheDone('T5', 10); assert.strictEqual(b.has('T5'), true);
  b.evict('T5');
  assert.strictEqual(b.has('T5'), false);
  assert.ok(navs.some(function (u) { return u.indexOf('mmcache://evict?token=T5') === 0; }));
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/mmcache-backend.test.js`
Expected: FAIL — cannot read `js/mmCacheBackendMmvideo.js` / `_mmCacheBackendMmvideo` undefined.

- [ ] **Step 3: Write the implementation**

```js
// js/mmCacheBackendMmvideo.js — ES5. iOS-5 backend for the mmCache coordinator: drives
// the mmvideo native cache bridge (mmcache:// scheme JS->native; window.__mmCacheDone/
// __mmCacheFail native->JS). Mirrors the mmws bridge pattern. No Promise/fetch (ES5).
(function (root) {
  var CACHE_DIR = 'file:///var/mobile/Media/mmcache/';
  var _present = {};   // token -> bytes (download acked-present on device)
  var _pending = {};   // token -> { onDone: fn, onFail: fn }

  function _nav(url) {
    // JS->native trigger: a hidden iframe nav to mmcache:// so the mmvideo tweak's
    // shouldStartLoadWithRequest hook intercepts it. An iframe (not location.href)
    // keeps the page from navigating away. Removed on the next tick.
    var f = document.createElement('iframe');
    f.style.display = 'none';
    f.src = url;
    document.documentElement.appendChild(f);
    setTimeout(function () { if (f.parentNode) { f.parentNode.removeChild(f); } }, 0);
  }

  var backend = {
    name: 'mmvideo',
    fetchToCache: function (url, token, onDone, onFail) {
      _pending[token] = { onDone: onDone, onFail: onFail };
      _nav('mmcache://fetch?token=' + encodeURIComponent(token) + '&url=' + encodeURIComponent(url));
    },
    localSrc: function (token) {
      return _present.hasOwnProperty(token) ? (CACHE_DIR + token + '.mp4') : null;
    },
    evict: function (token) {
      delete _present[token];
      _nav('mmcache://evict?token=' + encodeURIComponent(token));
    },
    has: function (token) { return _present.hasOwnProperty(token); },
    size: function (token) { return _present[token] || 0; }
  };

  // native -> JS: the tweak invokes these via stringByEvaluatingJavaScriptFromString.
  root.__mmCacheDone = function (token, bytes) {
    _present[token] = bytes || 1;
    var p = _pending[token]; delete _pending[token];
    if (p && p.onDone) { p.onDone(token); }
  };
  root.__mmCacheFail = function (token, reason) {
    var p = _pending[token]; delete _pending[token];
    if (p && p.onFail) { p.onFail(token, reason); }
  };

  root._mmCacheBackendMmvideo = backend;
  root.__mmRegisterMmvideoBackend = function () {
    if (root.mmCache && root.mmCache.registerBackend) { root.mmCache.registerBackend(backend); }
  };
})(typeof window !== 'undefined' ? window : global);
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/mmcache-backend.test.js`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add js/mmCacheBackendMmvideo.js tests/unit/js/mmcache-backend.test.js
git commit -m "feat(mmCache): iOS-5 mmvideo backend adapter (mmcache:// bridge)"
```

---

## Task 3: index.html integration

**Files:**
- Modify: `index.html` (script includes near the existing `js/mosiacmesh.js` include; the SockJS message handler; the video-src resolution)

**Interfaces:**
- Consumes: `mmCache.handlePrecache`, `mmCache.onAck`, `mmCache.localSrc` (Plan 1); `window.__mmRegisterMmvideoBackend`, `window.__mmCacheReady` (Task 2 + Task 4).
- Produces: PRECACHE handling + local-src preference on the display client.

**No new automated test** (index.html wiring is exercised by the on-device Task 5 + the existing e2e). Keep each edit minimal + ES5.

- [ ] **Step 1: Load the new scripts** — add after the `js/mmCache.js`-adjacent includes (or add both if absent), before the inline display script:

```html
<script src="js/mmCache.js"></script>
<script src="js/mmCacheBackendMmvideo.js"></script>
```

- [ ] **Step 2: Register the backend + wire onAck (inside the SockJS onopen/REGISTER path, ES5)** — where the client sets up after connect, add:

```javascript
// Client-pull cache: register the iOS-5 backend only if the mmvideo bridge injected
// (window.__mmCacheReady). Without it, mmCache stays backend-less and PRECACHE acks
// CACHE_FAILED 'no-backend' -> server advances -> this client streams centrally (safe).
if (window.__mmCacheReady && window.__mmRegisterMmvideoBackend) {
  window.__mmRegisterMmvideoBackend();
}
if (window.mmCache) {
  mmCache.onAck = function (req, payload) {
    if (sock && typeof SockJS !== 'undefined' && sock.readyState === SockJS.OPEN) {
      sock.send(generateMessage('SRV', req, payload));   // req = 'CACHED' | 'CACHE_FAILED'
    }
  };
}
```

- [ ] **Step 3: Handle the PRECACHE request (in the message dispatch, ES5)** — where incoming requests are switched (the `sock_callback`/`data_obj.REQUEST` handling), add:

```javascript
if (data_obj.REQUEST === 'PRECACHE' && window.mmCache) {
  // PAYLOAD: { url, token }. group is this client's displayID (not needed by the
  // coordinator's supersede, which keys by the token's group slot) — pass the token's
  // group as the displayID so evict-on-supersede scopes per group.
  mmCache.handlePrecache({ group: (window._mmDisplayID || 'self'), url: data_obj.PAYLOAD.url, token: data_obj.PAYLOAD.token });
}
```

- [ ] **Step 4: Prefer the local src at play (in the video-src resolution, ES5)** — where the client sets `<video>.src`/`item.file` for a video item (see `prepareFirstItem` / the play path in the inline script), resolve local-first:

```javascript
// Prefer the locally-cached file when this item's render token is cached; else the
// central URL (stream fallback). item.token is the render token carried in the item.
var _localSrc = (window.mmCache && item.token) ? mmCache.localSrc(item.token) : null;
var _srcToUse = _localSrc || item.file;
```
Use `_srcToUse` wherever the code currently assigns the video element `src` from `item.file` for a video item. (If items don't yet carry `token`, this is a no-op — `localSrc` returns null → central URL — and Plan 3 / a server change threads the token onto items; note this in the commit.)

- [ ] **Step 5: Commit**

```bash
git add index.html
git commit -m "feat(client): wire mmCache PRECACHE + local-src preference into the display client"
```

---

## Task 4: native mmcache:// bridge + download (mmvideo tweak) — ON-DEVICE, informed by Task 1

**Files:**
- Modify: `tweak/mmvideo/Tweak.x` (add the `mmcache://` scheme intercept + `__mmCacheReady` flag + the `__mmCacheDone/Fail` dispatch helper)
- Modify: `tweak/mmvideo/MMTransplantEngine.m` (+ `.h`): add `mm_cache_fetch(const char *token, const char *url)` and `mm_cache_evict(const char *token)` — C-callable, `NSURLSession`/`NSData` background download to `/var/mobile/Media/mmcache/<token>.mp4`, completion → dispatch `__mmCacheDone/Fail`.

**Verification is ON-DEVICE** (build + deploy to one screen + confirm), not a host test. Apply Task 1's REFINDINGS exactly (bridge coexistence with mmws, NSURLSession-vs-NSData, no static class / no unbindable symbols).

- [ ] **Step 1: Add the scheme intercept** — in `tweak/mmvideo/Tweak.x`, hook `webView:shouldStartLoadWithRequest:navigationType:` (mirror `tweak/mmws/Tweak.x:205`): if `[[u scheme] isEqualToString:@"mmcache"]`, parse `host` (`fetch`/`evict`) + query `token`/`url`, call `mm_cache_fetch`/`mm_cache_evict`, return `NO`. Else `%orig`. Set `window.__mmCacheReady=true` via `stringByEvaluatingJavaScriptFromString` when the webview is captured (mirror the mmws `g_webview` capture).

- [ ] **Step 2: Add the download primitive** — in `MMTransplantEngine.m`, `mm_cache_fetch`: ensure `/var/mobile/Media/mmcache/` exists (`NSFileManager createDirectoryAtPath:`), start a background download of `url` → temp → atomic rename to `<token>.mp4`; on success dispatch `if(window.__mmCacheDone)window.__mmCacheDone('<token>',<bytes>)`, on failure `__mmCacheFail('<token>','<reason>')` (mirror the mmws dispatch at `Tweak.x:61,86`). `mm_cache_evict`: `unlink` the file. C-callable, no static ObjC class.

- [ ] **Step 3: Build** — `cd tweak/mmvideo && ./build.sh`; confirm symbol-clean armv7 dylib (REFINDINGS load-gate checks).

- [ ] **Step 4: Deploy to ONE device + verify** (operator, paced — NEVER burst): scp to one screen, respring, drive a real PRECACHE from the server to that screen, confirm the file lands + `CACHED` ack reaches the server (`grep CACHED` in the server log) + the client plays local (`?tdbg`). Screenshot.

- [ ] **Step 5: Commit** — `git add tweak/mmvideo/Tweak.x tweak/mmvideo/MMTransplantEngine.m tweak/mmvideo/MMTransplantEngine.h && git commit -m "feat(mmvideo): mmcache:// bridge + NSURLSession download-to-file"`.

---

## Task 5: throttled one-then-few fleet verification (ON-DEVICE, operator)

**No code.** Roll the Task-4 dylib to a small subset (2-3 screens), sequential + paced (fleet-ssh-no-burst). Play a SEGMENT video on that group; confirm via passive server reads: `CACHED` acks arrive, `PrecacheWindow` advances (≤N concurrent), `readyToDisplay` flips, and `MEMWATCH threads:` stays flat (the pull path has no SSH-to-dozing-device leak). Confirm the wall plays from local (`?tdbg` shows `file://`), and an intentionally-offline screen falls back to central stream without blocking the coordinated start (Plan-1 behavior C). Record results in the SDD ledger. If clean, the pull path is proven → Plan 3 (modern backend + lighttpd/SSH-push retirement) is unblocked.

---

## Self-Review (plan author)

- **Spec coverage:** iOS-5 backend download→file:// (Tasks 1,4), backend interface adapter (Task 2), client integration incl. local-src preference + PRECACHE handling + onAck (Task 3), throttled rollout + MEMWATCH (Task 5). Modern backend + retirement remain Plan 3 (out of scope). The spike (Task 1) de-risks per the spec's "one-device spike before fleet rollout."
- **Placeholder scan:** none — JS tasks carry complete code + tests; on-device tasks carry concrete protocols + exact mirror references (`tweak/mmws/Tweak.x` line numbers). Task 3 Step 4's `item.token` dependency is called out explicitly (no-op fallback until threaded) rather than left vague.
- **Type consistency:** backend interface (`fetchToCache/localSrc/evict/has/size`) matches Plan 1's mock + coordinator exactly; callbacks `__mmCacheDone(token,bytes)`/`__mmCacheFail(token,reason)` consistent between Task 2 (JS) and Task 4 (native dispatch); scheme `mmcache://fetch|evict` consistent JS↔native; REQUESTs `PRECACHE`/`CACHED`/`CACHE_FAILED` match Plan 1's server.

## Open dependency for full end-to-end

`item.token` (the render token per playlist item) must be present on the client's play items for local-src resolution (Task 3 Step 4). If the current per-client item payload (`_per_client_items`) doesn't carry the render token, thread it there — a one-field server addition. Flagged here; verify during Task 3 and, if missing, either add the field (small server change, note it) or defer local-src to when it's threaded (fallback = central stream, still correct).
