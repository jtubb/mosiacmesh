# mmvideo verr=3 Recovery + False-Downgrade Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On a cached-video `verr=3`, retry the local load up to 3× (recovering the transient mmvideo cold-start flake) and only downgrade to `cacheMode:none` after all retries fail — fixing both the black-screen non-recovery and the false-downgrade feedback loop that erodes the fleet's cache.

**Architecture:** A new pure ES5 module `js/mmVideoRecovery.js` exports the decision `mmVideoErrorAction({isLocal, retries, maxRetries}) → 'ignore'|'retry'|'downgrade'` (node-tested). `index.html`'s pooled-`<video>` `error` listener holds the stateful wiring (per-element retry counter, backoff `setTimeout`, `load()`+`play()`, the one-shot `ANNOUNCE_CACHE_MODE` downgrade) and calls the helper.

**Tech Stack:** ES5 JavaScript (runs on 1st-gen iPad / iOS 5.1 Safari). Node 20 `--test` for the pure helper. Client-only; deploy = fleet reload.

## Global Constraints

- **ES5 only** in `js/mmVideoRecovery.js` and `index.html`: no `let`/`const`, arrow functions, template literals, `class`, `Promise`, `fetch`. Keep jQuery 1.x / SockJS.
- **Pure helper stays pure:** `mmVideoErrorAction` reads only its `state` argument — no DOM, timers, sockets, or globals. Jitter/timers/sends live only in `index.html`.
- **Module export pattern (mirror `js/mmCache.js`):** IIFE that does `root.mmVideoRecovery = ...; if (typeof module !== 'undefined' && module.exports) { module.exports = ...; }` over `(typeof window !== 'undefined' ? window : global)`.
- **Params verbatim:** `MM_VIDEO_MAX_RETRIES = 3`, `MM_VIDEO_RETRY_BACKOFF_MS = 500`.
- **Downgrade fires at most once per element** (guard flag), and only after the retry budget is exhausted. Non-`127.0.0.1` errors → `ignore` (unchanged behavior).
- **No server change, no central fallback, no re-upgrade path** (per spec non-goals).
- **Run JS tests with:** `python pytest_runner.py --js` (or `node --test tests/unit/js/mmvideo-recovery.test.js`).
- **Commit hygiene for `index.html`:** the working tree has uncommitted session-debug edits + a `MMFORCE_TDBG_TEMP` force. Task 2 resets `index.html` to HEAD before applying the fix so the fix commit is clean. Do NOT `git add -A`.

---

### Task 1: `mmVideoErrorAction` pure helper + node tests

**Files:**
- Create: `js/mmVideoRecovery.js`
- Create: `tests/unit/js/mmvideo-recovery.test.js`

**Interfaces:**
- Produces: `window.mmVideoRecovery.mmVideoErrorAction(state) -> 'ignore' | 'retry' | 'downgrade'`, where `state = { isLocal: bool, retries: int, maxRetries: int }`. Pure. Task 2's `index.html` wiring consumes it.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/mmvideo-recovery.test.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

// js/mmVideoRecovery.js is an ES5 browser-global module (no ESM export; must stay
// ES5 for iPad-1). Run it in a vm context with a stub window + module, mirroring
// tests/unit/js/_mmcache_load.js, and return the attached global.
function loadRecovery() {
  const code = fs.readFileSync(new URL('../../../js/mmVideoRecovery.js', import.meta.url), 'utf8');
  const sandbox = { window: {}, module: { exports: {} } };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox.window.mmVideoRecovery;
}

const A = loadRecovery().mmVideoErrorAction;
const MAX = 3;

test('non-local error -> ignore (regardless of retries)', () => {
  assert.strictEqual(A({ isLocal: false, retries: 0, maxRetries: MAX }), 'ignore');
  assert.strictEqual(A({ isLocal: false, retries: 9, maxRetries: MAX }), 'ignore');
});

test('local, retries below max -> retry', () => {
  assert.strictEqual(A({ isLocal: true, retries: 0, maxRetries: MAX }), 'retry');
  assert.strictEqual(A({ isLocal: true, retries: 2, maxRetries: MAX }), 'retry'); // max-1 boundary
});

test('local, retries at/over max -> downgrade', () => {
  assert.strictEqual(A({ isLocal: true, retries: 3, maxRetries: MAX }), 'downgrade'); // == max boundary
  assert.strictEqual(A({ isLocal: true, retries: 4, maxRetries: MAX }), 'downgrade');
});

test('missing state -> ignore (defensive)', () => {
  assert.strictEqual(A(null), 'ignore');
  assert.strictEqual(A(undefined), 'ignore');
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --test tests/unit/js/mmvideo-recovery.test.js`
Expected: FAIL — cannot read `mmVideoErrorAction` of undefined (module not created yet).

- [ ] **Step 3: Create the module**

Create `js/mmVideoRecovery.js`:

```js
// js/mmVideoRecovery.js  — ES5 only. Pure decision for cached-video <video> error recovery.
// On a pooled <video> 'error', index.html asks this what to do. See
// docs/superpowers/specs/2026-07-10-mmvideo-recovery-design.md
(function (root) {
  var mmVideoRecovery = {};

  // state = { isLocal: bool, retries: int, maxRetries: int }
  //   isLocal false            -> 'ignore'    (non-127.0.0.1 error; not our concern)
  //   isLocal true, retries<max -> 'retry'    (reload the local src)
  //   isLocal true, retries>=max -> 'downgrade' (budget exhausted -> ANNOUNCE_CACHE_MODE none)
  mmVideoRecovery.mmVideoErrorAction = function (state) {
    if (!state || !state.isLocal) { return 'ignore'; }
    if (state.retries < state.maxRetries) { return 'retry'; }
    return 'downgrade';
  };

  root.mmVideoRecovery = mmVideoRecovery;
  if (typeof module !== 'undefined' && module.exports) { module.exports = mmVideoRecovery; }
})(typeof window !== 'undefined' ? window : global);
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --test tests/unit/js/mmvideo-recovery.test.js`
Expected: PASS (4/4).

- [ ] **Step 5: Run the full JS suite (no regression)**

Run: `python pytest_runner.py --js`
Expected: PASS (existing suites unaffected; the new file adds tests).

- [ ] **Step 6: Commit**

```bash
git add js/mmVideoRecovery.js tests/unit/js/mmvideo-recovery.test.js
git commit -m "feat(mmvideo): mmVideoErrorAction pure retry/downgrade decision + node tests"
```

---

### Task 2: Wire retry-then-downgrade into `index.html`

**Files:**
- Modify: `index.html` — script include (~line 27), playback consts (~line 234), the pooled-`<video>` `playing` + `error` listeners (~lines 209–221).

**Interfaces:**
- Consumes: `window.mmVideoRecovery.mmVideoErrorAction` (Task 1).
- Produces: the deployed client behavior; no exports for later tasks.

**IMPORTANT — clean base first.** The working tree's `index.html` has uncommitted session-debug edits (`fsrc/csrc/warm/lmd/spath/epath/ecode/vid-error`, `_srcPath` tags) and a `MMFORCE_TDBG_TEMP` force. Reset the file so the fix commit contains only the fix.

- [ ] **Step 1: Reset `index.html` to the committed baseline**

Run:
```bash
git checkout HEAD -- index.html
```
This discards ALL uncommitted `index.html` edits (session-debug + tdbg-force). Verify the error listener is the clean baseline:

Run: `git diff --quiet index.html && echo "clean" || echo "still dirty"`
Expected: `clean`

- [ ] **Step 2: Add the module `<script>` include**

In `index.html`, after the `mmCache.js` include (line 27):

```html
  <script src="/js/mmCache.js"></script>
```

change to:

```html
  <script src="/js/mmCache.js"></script>
  <script src="/js/mmVideoRecovery.js"></script>
```

- [ ] **Step 3: Add the tuning consts**

In `index.html`, after `var HARD_SEEK_MS = 750;` (line 234):

```js
	var HARD_SEEK_MS = 750; // drift beyond this is corrected with a seek, not a rate nudge
```

add:

```js
	// Cached-video verr=3 recovery: retry the local load this many times (with backoff)
	// before downgrading to cacheMode:none. mmvideo emits spurious verr=3 on healthy
	// cached files; a single flake must NOT downgrade a good device. See
	// docs/superpowers/specs/2026-07-10-mmvideo-recovery-design.md
	var MM_VIDEO_MAX_RETRIES = 3;
	var MM_VIDEO_RETRY_BACKOFF_MS = 500;
```

- [ ] **Step 4: Replace the `playing` + `error` listeners with the retry-then-downgrade wiring**

In `index.html` (the `mkVideo` body, ~lines 209–221), replace:

```js
				v.addEventListener('playing', function () { playback.activated = true; hideTapStart(); });
				v.addEventListener('error', function () {
					try {
						var s = '' + (v.currentSrc || v.src || '');
						if (s.indexOf('127.0.0.1') !== -1 && typeof sock !== 'undefined' && sock !== null) {
							sock.send(generateMessage("SRV", "ANNOUNCE_CACHE_MODE", {"mode": "none"}));
							if (typeof dbg === 'function') { dbg("cache-local-fail"); }
						}
					} catch (e) { /* best-effort */ }
				});
```

with:

```js
				v.addEventListener('playing', function () {
					playback.activated = true; hideTapStart();
					// Recovered: forget this clip's failures + cancel any pending retry.
					v._mmRetryN = 0; v._mmDowngraded = false;
					if (v._mmRetryTimer) { clearTimeout(v._mmRetryTimer); v._mmRetryTimer = null; }
				});
				v.addEventListener('error', function () {
					try {
						var s = '' + (v.currentSrc || v.src || '');
						var isLocal = s.indexOf('127.0.0.1') !== -1;
						// New clip -> fresh retry budget; cancel a stale pending retry.
						if (isLocal && s !== v._mmRetrySrc) {
							v._mmRetrySrc = s; v._mmRetryN = 0; v._mmDowngraded = false;
							if (v._mmRetryTimer) { clearTimeout(v._mmRetryTimer); v._mmRetryTimer = null; }
						}
						var action = mmVideoRecovery.mmVideoErrorAction({
							isLocal: isLocal, retries: v._mmRetryN || 0, maxRetries: MM_VIDEO_MAX_RETRIES });
						if (action === 'ignore') { return; }
						if (action === 'retry') {
							v._mmRetryN = (v._mmRetryN || 0) + 1;
							if (typeof dbg === 'function') { dbg('cache-local-retry'); }
							if (v._mmRetryTimer) { clearTimeout(v._mmRetryTimer); }
							v._mmRetryTimer = setTimeout(function () {
								v._mmRetryTimer = null;
								// iOS-5 needs load()+play() together to restart a held/errored element.
								try { v.load(); } catch (e2) {}
								try { var p = v.play(); if (p && p['catch']) { p['catch'](function () {}); } } catch (e3) {}
							}, MM_VIDEO_RETRY_BACKOFF_MS);
							return;
						}
						// action === 'downgrade': retry budget exhausted -> genuine local failure.
						if (!v._mmDowngraded && typeof sock !== 'undefined' && sock !== null) {
							v._mmDowngraded = true;
							sock.send(generateMessage("SRV", "ANNOUNCE_CACHE_MODE", {"mode": "none"}));
							if (typeof dbg === 'function') { dbg("cache-local-fail"); }
						}
					} catch (e) { /* best-effort */ }
				});
```

- [ ] **Step 5: Syntax-check the served file**

Verify the include and consts landed and the file is well-formed:

Run: `grep -n "mmVideoRecovery.js\|MM_VIDEO_MAX_RETRIES\|cache-local-retry" index.html`
Expected: three matches (script include, const, the retry dbg).

Run: `node --check js/mmVideoRecovery.js && echo "module OK"`
Expected: `module OK` (index.html isn't node-checkable, but the module is).

- [ ] **Step 6: Commit**

```bash
git add index.html
git commit -m "feat(mmvideo): retry cached-video verr=3 locally before downgrading

Pooled <video> error listener now retries the local load 3x (500ms
backoff) via mmVideoErrorAction, recovering the transient mmvideo
cold-start flake, and only sends ANNOUNCE_CACHE_MODE:none after the
budget is exhausted. Stops a single spurious verr=3 from false-
downgrading a healthy cached device. ES5, client-only."
```

Note: this commit does NOT include the `MMFORCE_TDBG_TEMP` force (reset in Step 1). Task 3 re-applies it temporarily for the on-wall sign-off.

---

### Task 3: iPad-1 on-wall sign-off

**Files:** none committed (manual verification; Step 1 makes a throwaway uncommitted edit). Deploy = fleet reload.

**Interfaces:**
- Consumes: the deployed `js/mmVideoRecovery.js` + `index.html` from Tasks 1–2.

- [ ] **Step 1: Temporarily re-enable tdbg (uncommitted) to observe the fix**

In `index.html`, re-apply the diagnostic force so `?tdbg` payloads stream to the server log — change (~line 285):

```js
	if (/tdbg/.test(location.href)) {
```

to:

```js
	if (/tdbg/.test(location.href) || true /* MMFORCE_TDBG_TEMP: diagnostic force, REVERT */) {
```

Do NOT commit this. (The server serves `index.html` from the working tree, so the reload picks it up.)

- [ ] **Step 2: Deploy**

Broadcast a RELOAD to the group (`python tools/_reload_group.py "OEB Sign 1"`) so panels pick up the new `js/mmVideoRecovery.js` + fixed `index.html`.

- [ ] **Step 3: PLAY a cached video playlist and observe**

Assign a cached video playlist (e.g. `Demo`) to the group and PLAY. Watch the server log (`mm_server.err`) `CLIENTLOG` stream. Expected:
- **`cache-local-retry`** emits appear on flaky starts, and those clips **recover** (`elapsed` starts advancing / video shows) instead of going black.
- A device that flakes once but has a healthy cached file is **NOT** downgraded — **no `cache-local-fail`** and **no `ANNOUNCE_CACHE_MODE:none`** for it (verify `cacheMode` stays `lighttpd-localhost` in `/api/discovery/devices`).
- Materially **fewer black panels** than the pre-fix baseline (the whole group should start video).

- [ ] **Step 4: Confirm the downgrade still fires for a genuinely-missing segment**

Optional negative check: on a device known to be missing a segment (e.g. one earlier confirmed to lack `seg_0` on disk), confirm it still emits `cache-local-fail` + downgrades after the 3 retries — the fix must not mask a real local failure.

- [ ] **Step 5: Revert the tdbg force + record the result**

When done observing, revert Step 1's edit (`git checkout HEAD -- index.html`) OR leave it if continuing to test — but it must not be committed. Record the on-wall result (recovery rate, no false downgrades) in the session notes / update memory `wall-verr3-is-mmvideo-not-cache` with the fix outcome. The fleet is the test — this task has no automated coverage.
