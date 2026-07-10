# mmvideo Playback Watchdog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the error-driven retry/downgrade with a single poll-based watchdog that detects both the `verr=3` error and the silent `rs=0` stall, escalates retry → recache (client self-pull) → black, and never downgrades a device to central.

**Architecture:** A `setInterval` watchdog in `index.html` polls the active `<video>` each 2s; a stalled cached clip runs a pure `mmWatchdogAction` decision (in `js/mmVideoRecovery.js`) and the wiring kicks `load()`+`play()`, then self-pulls the segment, then stays black. The `ANNOUNCE_CACHE_MODE:none` downgrade is deleted; eroded `none` devices auto-recover to `lighttpd-localhost` on the deploy reload.

**Tech Stack:** ES5 JavaScript (1st-gen iPad / iOS 5.1). Node 20 `--test` for the pure helper. Client-only; deploy = staged reload.

## Global Constraints

- **ES5 only** in `js/mmVideoRecovery.js` and `index.html`: no `let`/`const`, arrow functions, template literals, `class`, `Promise`, `fetch`. jQuery 1.x / SockJS stay.
- **Pure helper stays pure:** `mmWatchdogAction` reads only its `state` arg — no DOM, timers, sockets, globals.
- **Module export pattern (mirror `js/mmCache.js`):** IIFE + `root.mmVideoRecovery` + `module.exports` over `(typeof window !== 'undefined' ? window : global)`.
- **Params verbatim:** `WATCHDOG_INTERVAL_MS=2000`, `WATCHDOG_START_GRACE_MS=3000`, `WATCHDOG_MAX_RETRIES=2`, `WATCHDOG_MAX_RECACHES=1`, `WATCHDOG_CT_MIN_ADVANCE_MS=100`.
- **Never downgrade to central:** no client path sends `ANNOUNCE_CACHE_MODE {mode:"none"}` on a playback failure. A device that can't recover stays BLACK.
- **Recache is client self-pull:** rebuild the central URL (`http://<location.host>/media/<udid>/videos/seg_<key>.mp4`) and call `mmCache.handlePrecache`.
- **Run JS tests with:** `python pytest_runner.py --js` (or `node --test tests/unit/js/mmvideo-recovery.test.js`).
- **Commit hygiene for `index.html`:** the working tree has uncommitted debug/tdbg edits; Task 2 resets `index.html` to HEAD before editing so the fix commit is clean. Do NOT `git add -A`.

---

### Task 1: `mmWatchdogAction` pure helper + node tests

**Files:**
- Modify: `js/mmVideoRecovery.js` (add `mmWatchdogAction` beside `mmVideoErrorAction`)
- Modify: `tests/unit/js/mmvideo-recovery.test.js` (append tests)

**Interfaces:**
- Produces: `window.mmVideoRecovery.mmWatchdogAction(state) -> 'ok' | 'retry' | 'recache' | 'dead'`, where `state = { shouldPlay: bool, decoding: bool, retries: int, recaches: int, maxRetries: int, maxRecaches: int }`. Pure. Task 2's `index.html` watchdog consumes it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/js/mmvideo-recovery.test.js`:

```js
const W = loadRecovery().mmWatchdogAction;
const WMAX = { maxRetries: 2, maxRecaches: 1 };

test('watchdog: not should-play -> ok', () => {
  assert.strictEqual(W({ shouldPlay: false, decoding: false, retries: 5, recaches: 5, ...WMAX }), 'ok');
});

test('watchdog: decoding -> ok (regardless of counters)', () => {
  assert.strictEqual(W({ shouldPlay: true, decoding: true, retries: 5, recaches: 5, ...WMAX }), 'ok');
});

test('watchdog: stalled, retries below max -> retry', () => {
  assert.strictEqual(W({ shouldPlay: true, decoding: false, retries: 0, recaches: 0, ...WMAX }), 'retry');
  assert.strictEqual(W({ shouldPlay: true, decoding: false, retries: 1, recaches: 0, ...WMAX }), 'retry'); // max-1
});

test('watchdog: retries exhausted, recaches below max -> recache', () => {
  assert.strictEqual(W({ shouldPlay: true, decoding: false, retries: 2, recaches: 0, ...WMAX }), 'recache'); // ==maxRetries
});

test('watchdog: retries + recaches exhausted -> dead', () => {
  assert.strictEqual(W({ shouldPlay: true, decoding: false, retries: 2, recaches: 1, ...WMAX }), 'dead'); // ==both
});

test('watchdog: missing state -> ok (defensive)', () => {
  assert.strictEqual(W(null), 'ok');
});
```

Note: `loadRecovery` already exists in this file (from the previous feature's tests).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `node --test tests/unit/js/mmvideo-recovery.test.js`
Expected: FAIL — `W` is `undefined` (`mmWatchdogAction` not defined yet).

- [ ] **Step 3: Add the helper**

In `js/mmVideoRecovery.js`, after the `mmVideoRecovery.mmVideoErrorAction = ...` block, add:

```js
  // state = { shouldPlay, decoding, retries, recaches, maxRetries, maxRecaches }
  // Poll-watchdog escalation for a video that should be playing:
  //   not should-play OR decoding -> 'ok'   (healthy / n/a)
  //   stalled, retries < maxRetries  -> 'retry'    (kick load()+play() in place)
  //   stalled, recaches < maxRecaches -> 'recache' (client self-pull the segment)
  //   otherwise -> 'dead'                    (stay black; NEVER central)
  mmVideoRecovery.mmWatchdogAction = function (state) {
    if (!state || !state.shouldPlay || state.decoding) { return 'ok'; }
    if (state.retries < state.maxRetries) { return 'retry'; }
    if (state.recaches < state.maxRecaches) { return 'recache'; }
    return 'dead';
  };
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `node --test tests/unit/js/mmvideo-recovery.test.js`
Expected: PASS (the new watchdog tests + the existing `mmVideoErrorAction` tests).

- [ ] **Step 5: Run the full JS suite**

Run: `python pytest_runner.py --js`
Expected: PASS (no regression).

- [ ] **Step 6: Commit**

```bash
git add js/mmVideoRecovery.js tests/unit/js/mmvideo-recovery.test.js
git commit -m "feat(mmvideo): mmWatchdogAction pure retry/recache/dead decision + node tests"
```

---

### Task 2: Poll watchdog in `index.html` + retire the error-driven path

**Files:**
- Modify: `index.html` — remove the `error` listener + `MM_VIDEO_*` consts, simplify the `playing` listener, add `WATCHDOG_*` consts + the watchdog `setInterval`.
- Modify: `js/mmVideoRecovery.js` — remove the now-unused `mmVideoErrorAction`.
- Modify: `tests/unit/js/mmvideo-recovery.test.js` — remove the `mmVideoErrorAction` tests.

**Interfaces:**
- Consumes: `window.mmVideoRecovery.mmWatchdogAction` (Task 1); `playback.pvid`, `playback.active`, `mmCache.handlePrecache`/`mmCache.state`, `udid`, `window._mmDisplayID`, `dbg`.

**IMPORTANT — clean base first** (working tree `index.html` is dirty with debug/tdbg edits).

- [ ] **Step 1: Reset `index.html` to the committed baseline**

Run:
```bash
git checkout HEAD -- index.html
```
Verify: `git diff --quiet index.html && echo clean || echo dirty` → `clean`.

- [ ] **Step 2: Simplify the `playing` listener + remove the `error` listener**

In `index.html`, replace this block (the shipped fix's listeners):

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

with just:

```js
				v.addEventListener('playing', function () { playback.activated = true; hideTapStart(); });
```

- [ ] **Step 3: Replace the `MM_VIDEO_*` consts with `WATCHDOG_*` consts**

In `index.html`, replace:

```js
	// Cached-video verr=3 recovery: retry the local load this many times (with backoff)
	// before downgrading to cacheMode:none. mmvideo emits spurious verr=3 on healthy
	// cached files; a single flake must NOT downgrade a good device. See
	// docs/superpowers/specs/2026-07-10-mmvideo-recovery-design.md
	var MM_VIDEO_MAX_RETRIES = 3;
	var MM_VIDEO_RETRY_BACKOFF_MS = 500;
```

with:

```js
	// Playback watchdog: poll every INTERVAL; after a clip has been current for
	// START_GRACE, a video that should be playing but isn't decoding (verr, rs<2,
	// or currentTime not advancing) escalates retry(MAX_RETRIES) -> recache
	// (MAX_RECACHES, client self-pull) -> black. NEVER falls to central. See
	// docs/superpowers/specs/2026-07-10-mmvideo-watchdog-design.md
	var WATCHDOG_INTERVAL_MS = 2000;
	var WATCHDOG_START_GRACE_MS = 3000;
	var WATCHDOG_MAX_RETRIES = 2;
	var WATCHDOG_MAX_RECACHES = 1;
	var WATCHDOG_CT_MIN_ADVANCE_MS = 100;
```

- [ ] **Step 4: Add the watchdog `setInterval` + helpers**

In `index.html`, find the heartbeat interval line:

```js
		setInterval(function () { if (playback.active) { dbg('hb'); } }, 1000);
```

and insert AFTER it:

```js

		// ---- Playback watchdog (poll-based) --------------------------------------
		// The single detector for BOTH failure modes: verr AND the silent rs=0 stall
		// (which fires no 'error' event). A stalled cached clip escalates
		// retry -> recache(self-pull) -> black; it NEVER falls to central (central is
		// a fleet-scale black hole). See docs/superpowers/specs/2026-07-10-mmvideo-watchdog-design.md
		var _wdSrc = null, _wdLastCt = -1, _wdRetries = 0, _wdRecaches = 0,
		    _wdClipStart = 0, _wdRecachePending = null, _wdDead = false;
		function _wdReset(src) {
			_wdSrc = src; _wdRetries = 0; _wdRecaches = 0; _wdLastCt = -1;
			_wdRecachePending = null; _wdDead = false; _wdClipStart = Date.now();
		}
		function _wdKick(v) {
			try { v.load(); } catch (e) {}
			try { var p = v.play(); if (p && p['catch']) { p['catch'](function () {}); } } catch (e2) {}
		}
		function _wdRecache(v, src) {
			if (!window.mmCache || src.indexOf('127.0.0.1') === -1) { return; }  // no local URL -> can't self-pull
			var m = src.match(/seg_[a-f0-9]+_\d+/);
			if (!m) { return; }
			var token = m[0];
			var central = 'http://' + window.location.host + '/media/' + udid + '/videos/' + token + '.mp4';
			_wdRecachePending = token;
			mmCache.handlePrecache({ group: (window._mmDisplayID || 'self'), url: central, token: token });
		}
		function _watchdogTick() {
			var v = playback.pvid;
			var src = (v && v.getAttribute('src')) ? ('' + v.getAttribute('src')) : null;
			var shouldPlay = !!(playback.active && v && src && v.style.display !== 'none');
			if (!shouldPlay) { if (_wdSrc !== null) { _wdReset(null); } return; }
			if (src !== _wdSrc) { _wdReset(src); }
			if ((Date.now() - _wdClipStart) < WATCHDOG_START_GRACE_MS) {
				_wdLastCt = v.currentTime ? Math.round(v.currentTime * 1000) : 0; return;
			}
			if (_wdRecachePending) {
				var st = window.mmCache ? mmCache.state(_wdRecachePending) : 'none';
				if (st === 'cached') { _wdRecachePending = null; _wdClipStart = Date.now(); _wdKick(v); return; }
				if (st === 'failed') { _wdRecachePending = null; }
				else { return; }  // still pulling
			}
			var ct = v.currentTime ? Math.round(v.currentTime * 1000) : 0;
			var verr = v.error ? v.error.code : null;
			// abs() so a loop-wrap (currentTime resets to 0) still counts as advancing.
			var decoding = (verr == null || verr === 0) && v.readyState >= 2
			    && Math.abs(ct - _wdLastCt) > WATCHDOG_CT_MIN_ADVANCE_MS;
			_wdLastCt = ct;
			var action = mmVideoRecovery.mmWatchdogAction({
				shouldPlay: true, decoding: decoding, retries: _wdRetries, recaches: _wdRecaches,
				maxRetries: WATCHDOG_MAX_RETRIES, maxRecaches: WATCHDOG_MAX_RECACHES });
			if (action === 'ok') { _wdRetries = 0; _wdRecaches = 0; _wdDead = false; return; }
			if (action === 'retry') { _wdRetries++; if (typeof dbg === 'function') { dbg('wd-retry'); } _wdKick(v); return; }
			if (action === 'recache') { _wdRecaches++; if (typeof dbg === 'function') { dbg('wd-recache'); } _wdRecache(v, src); return; }
			if (!_wdDead) { _wdDead = true; if (typeof dbg === 'function') { dbg('wd-dead'); } }
		}
		setInterval(_watchdogTick, WATCHDOG_INTERVAL_MS);
```

- [ ] **Step 5: Remove the now-unused `mmVideoErrorAction` (caller is gone)**

In `js/mmVideoRecovery.js`, delete the `mmVideoRecovery.mmVideoErrorAction = function (state) {...};` block (its only caller — the `index.html` error listener — was removed in Step 2). Keep `mmWatchdogAction`.

In `tests/unit/js/mmvideo-recovery.test.js`, delete the four `mmVideoErrorAction` tests (the ones using `const A = loadRecovery().mmVideoErrorAction;` and its `A(...)` assertions). Keep the `loadRecovery` loader and the `mmWatchdogAction` tests.

- [ ] **Step 6: Verify the module + tests + served identifiers**

Run: `node --check js/mmVideoRecovery.js && echo "module OK"`
Expected: `module OK`.

Run: `node --test tests/unit/js/mmvideo-recovery.test.js`
Expected: PASS (only `mmWatchdogAction` tests remain).

Run: `grep -n "mmVideoErrorAction" index.html js/mmVideoRecovery.js tests/unit/js/mmvideo-recovery.test.js`
Expected: no output (fully retired).

Run: `grep -n "ANNOUNCE_CACHE_MODE" index.html`
Expected: no output (the downgrade send is gone from the client).

Run: `grep -n "_watchdogTick\|WATCHDOG_INTERVAL_MS\|wd-recache" index.html`
Expected: three+ matches (watchdog landed).

Run: `python pytest_runner.py --js`
Expected: PASS (full suite).

- [ ] **Step 7: Commit**

```bash
git add index.html js/mmVideoRecovery.js tests/unit/js/mmvideo-recovery.test.js
git commit -m "feat(mmvideo): poll watchdog replaces error-driven downgrade

Single 2s poll detects both verr and the silent rs=0 stall; a stalled
cached clip escalates retry(2) -> recache(1, client self-pull) -> black,
never to central. Removes the ANNOUNCE_CACHE_MODE:none downgrade and the
now-unused mmVideoErrorAction. Eroded 'none' devices auto-recover to
lighttpd-localhost on the deploy reload. ES5, client-only."
```

---

### Task 3: iPad-1 on-wall sign-off

**Files:** none committed (manual; Step 1 makes a throwaway uncommitted edit). Deploy = STAGED reload (never whole-group).

**Interfaces:**
- Consumes: the deployed `js/mmVideoRecovery.js` + `index.html` from Tasks 1–2.

- [ ] **Step 1: Temporarily re-enable tdbg (uncommitted)**

In `index.html`, change (~line 285):

```js
	if (/tdbg/.test(location.href)) {
```

to:

```js
	if (/tdbg/.test(location.href) || true /* MMFORCE_TDBG_TEMP: diagnostic force, REVERT */) {
```

Do NOT commit this.

- [ ] **Step 2: Deploy via STAGED reloads (never a group reload)**

Reload in small batches with `python tools/_reload_one.py <clientKey>` (a whole-group `_reload_group.py` drops the fleet's SockJS for ~7 min — do NOT use it). Reload a few devices, confirm they reconnect, then continue. The reload also RE-REGISTERs each device, which re-upgrades `none`->`lighttpd-localhost` via `apply_cache_capability`.

- [ ] **Step 3: PLAY a cached video playlist and measure with the CORRECT metric**

Assign `Demo` to `OEB Sign 1` and PLAY. Measure "actually showing video" as **`verr` clear AND `rs>=2` AND `ct` advancing** across two samples — NOT `elapsed` (which advances on a black screen). Expected:
- **Black count → ~0** (physical count on the wall + the `rs>=2`/`ct`-advancing metric).
- The formerly-`none` devices are back on `cacheMode:lighttpd-localhost` (verify via `/api/discovery/devices`).
- `wd-retry` (and occasionally `wd-recache`) emits appear in the `mm_server.err` `CLIENTLOG` stream, and those clips recover to decoding.
- **No device on `cacheMode:none`** after a heavy PLAY — the erosion is stopped (contrast the pre-fix run where a PLAY re-downgraded ~10 devices).

- [ ] **Step 4: Confirm dead-state stays black, never central**

If any clip genuinely can't recover (retries + recache exhausted), confirm it stays **black** and emits `wd-dead` — and that its `cacheMode` is still `lighttpd-localhost` (it did NOT fall to central). A looping playlist should re-attempt the clip on its next loop (state resets on the new/looped src).

- [ ] **Step 5: Revert the tdbg force + record the result**

Revert Step 1 (`git checkout HEAD -- index.html`) when done, OR leave it uncommitted if still testing — it must not be committed. Update memory `wall-verr3-is-mmvideo-not-cache` with the watchdog outcome (black-count before/after, erosion stopped). The fleet is the test — no automated coverage for the wiring.
