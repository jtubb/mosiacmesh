# mmvideo Arm-Phase Recache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the arm-phase error recovery from retry-only to retry→recache→skip, so a genuinely-missing cached segment self-heals (re-pulls) at ARM instead of needing a manual SSH push.

**Architecture:** Extend the pure `mmArmRetryAction` with a `recache` verdict, extract the recache self-pull into a shared `_mmSelfPull(v, src)` used by both the watchdog and the arm listener, and add a `recache` branch to the arm `error` listener that fires `_mmSelfPull` + a longer reload so the pulled segment lands and the next `load()` arms.

**Tech Stack:** ES5 JavaScript (1st-gen iPad / iOS 5.1). Node 20 `--test`. Client-only; deploy = staged reload.

## Global Constraints

- **ES5 only** in `js/mmVideoRecovery.js` and `index.html`: no `let`/`const`, arrow functions, template literals, `class`, `Promise`, `fetch`.
- **Pure helper stays pure:** `mmArmRetryAction` reads only its `state` arg.
- **Never central:** no `ANNOUNCE_CACHE_MODE:none` anywhere; on exhaustion the arm path falls to the existing `NEEDS_ARM`/tap behavior.
- **Params verbatim:** `ARM_MAX_RETRIES=3` (existing), `ARM_MAX_RECACHES=1` (new), `ARM_RETRY_BACKOFF_MS=500` (existing), `ARM_RECACHE_RELOAD_MS=2500` (new).
- **Shared self-pull:** one `_mmSelfPull(v, src) -> token|null` used by the watchdog's `_wdRecache` AND the arm listener (DRY).
- **Run JS tests with:** `python pytest_runner.py --js` (or `node --test tests/unit/js/mmvideo-recovery.test.js`).
- **Commit hygiene for `index.html`:** the working tree has an uncommitted `MMFORCE_TDBG_TEMP` force; Task 2 resets `index.html` to HEAD before editing so the fix commit is clean. Do NOT `git add -A`.

---

### Task 1: Extend `mmArmRetryAction` with a `recache` verdict

**Files:**
- Modify: `js/mmVideoRecovery.js` (the `mmArmRetryAction` body)
- Modify: `tests/unit/js/mmvideo-recovery.test.js` (update the arm tests to the 5-arg form + add recache/skip boundaries)

**Interfaces:**
- Produces: `window.mmVideoRecovery.mmArmRetryAction({active, isLocal, retries, recaches, maxRetries, maxRecaches}) -> 'skip' | 'retry' | 'recache'`. Task 2's `index.html` arm listener consumes it.

- [ ] **Step 1: Update the arm tests to the 5-arg form + recache/skip boundaries**

In `tests/unit/js/mmvideo-recovery.test.js`, replace the existing arm-test block (from `const AR = loadRecovery().mmArmRetryAction;` through the `arm: missing state -> skip` test) with:

```js
const AR = loadRecovery().mmArmRetryAction;
const ARMAX = { maxRetries: 3, maxRecaches: 1 };

test('arm: active -> skip (poll watchdog owns active playback)', () => {
  assert.strictEqual(AR({ active: true, isLocal: true, retries: 0, recaches: 0, ...ARMAX }), 'skip');
});

test('arm: non-local src -> skip', () => {
  assert.strictEqual(AR({ active: false, isLocal: false, retries: 0, recaches: 0, ...ARMAX }), 'skip');
});

test('arm: arming (not active) + local + retries below max -> retry', () => {
  assert.strictEqual(AR({ active: false, isLocal: true, retries: 0, recaches: 0, ...ARMAX }), 'retry');
  assert.strictEqual(AR({ active: false, isLocal: true, retries: 2, recaches: 0, ...ARMAX }), 'retry'); // max-1
});

test('arm: retries exhausted, recaches below max -> recache', () => {
  assert.strictEqual(AR({ active: false, isLocal: true, retries: 3, recaches: 0, ...ARMAX }), 'recache'); // ==maxRetries
});

test('arm: retries + recaches exhausted -> skip (NEEDS_ARM, never central)', () => {
  assert.strictEqual(AR({ active: false, isLocal: true, retries: 3, recaches: 1, ...ARMAX }), 'skip'); // ==both
});

test('arm: missing state -> skip (defensive)', () => {
  assert.strictEqual(AR(null), 'skip');
});
```

- [ ] **Step 2: Run the tests to verify the new one fails**

Run: `node --test tests/unit/js/mmvideo-recovery.test.js`
Expected: FAIL — the `retries=3, recaches=0 -> recache` test fails (current helper returns `'skip'` at that boundary; it has no `recache` branch yet).

- [ ] **Step 3: Add the `recache` branch to the helper**

In `js/mmVideoRecovery.js`, replace:

```js
  mmVideoRecovery.mmArmRetryAction = function (state) {
    if (!state || state.active || !state.isLocal) { return 'skip'; }
    if (state.retries < state.maxRetries) { return 'retry'; }
    return 'skip';
  };
```

with:

```js
  mmVideoRecovery.mmArmRetryAction = function (state) {
    if (!state || state.active || !state.isLocal) { return 'skip'; }
    if (state.retries < state.maxRetries) { return 'retry'; }
    if (state.recaches < state.maxRecaches) { return 'recache'; }
    return 'skip';
  };
```

(Also update the comment above it — change the `-> 'skip'` on the exhausted line to note the recache step. Optional but keep the doc honest.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `node --test tests/unit/js/mmvideo-recovery.test.js`
Expected: PASS (the watchdog tests + all arm tests, including the new recache boundary).

- [ ] **Step 5: Run the full JS suite**

Run: `python pytest_runner.py --js`
Expected: PASS (418 tests — the arm block gains one test: the reworded exhausted-case + the new `recache` boundary, net +1 vs the prior 417).

- [ ] **Step 6: Commit**

```bash
git add js/mmVideoRecovery.js tests/unit/js/mmvideo-recovery.test.js
git commit -m "feat(mmvideo): mmArmRetryAction gains a recache verdict"
```

---

### Task 2: Shared `_mmSelfPull` + arm-listener recache branch (`index.html`)

**Files:**
- Modify: `index.html` — extract `_mmSelfPull` (refactor `_wdRecache` to use it), add `ARM_MAX_RECACHES`/`ARM_RECACHE_RELOAD_MS` consts, extend the arm `error` listener + the `playing` reset.

**Interfaces:**
- Consumes: `mmVideoRecovery.mmArmRetryAction` (Task 1); `mmCache.handlePrecache`, `udid`, `window._mmDisplayID`, `playback.active`, `dbg`.
- Produces: `_mmSelfPull(v, src) -> token|null` (shared); no exports for later tasks.

**IMPORTANT — clean base first** (working tree `index.html` has the uncommitted tdbg force).

- [ ] **Step 1: Reset `index.html` to the committed baseline**

Run:
```bash
git checkout HEAD -- index.html
```
Verify: `git diff --quiet index.html && echo clean || echo dirty` → `clean`.

- [ ] **Step 2: Extract `_mmSelfPull`; refactor `_wdRecache` to use it**

In `index.html`, replace the `_wdRecache` function:

```js
	function _wdRecache(v, src) {
		if (!window.mmCache || src.indexOf('127.0.0.1') === -1) { return; }  // no local URL -> can't self-pull
		var m = src.match(/seg_[a-f0-9]+_\d+/);
		if (!m) { return; }
		var token = m[0];
		var central = 'http://' + window.location.host + '/media/' + udid + '/videos/' + token + '.mp4';
		_wdRecachePending = token;
		mmCache.handlePrecache({ group: (window._mmDisplayID || 'self'), url: central, token: token });
	}
```

with:

```js
	// Shared client self-pull: re-fetch a cached seg from central into the local cache.
	// Returns the seg token (or null if the src isn't a parseable 127.0.0.1 cached URL).
	// Used by the poll watchdog's recache AND the arm-phase recache.
	function _mmSelfPull(v, src) {
		if (!window.mmCache || src.indexOf('127.0.0.1') === -1) { return null; }
		var m = src.match(/seg_[a-f0-9]+_\d+/);
		if (!m) { return null; }
		var token = m[0];
		var central = 'http://' + window.location.host + '/media/' + udid + '/videos/' + token + '.mp4';
		mmCache.handlePrecache({ group: (window._mmDisplayID || 'self'), url: central, token: token });
		return token;
	}
	function _wdRecache(v, src) {
		var t = _mmSelfPull(v, src);
		if (t) { _wdRecachePending = t; }
	}
```

- [ ] **Step 3: Add the arm-recache consts**

In `index.html`, replace:

```js
	var ARM_MAX_RETRIES = 3;
	var ARM_RETRY_BACKOFF_MS = 500;
```

with:

```js
	var ARM_MAX_RETRIES = 3;
	var ARM_RETRY_BACKOFF_MS = 500;
	// Arm-phase recache: after the retry budget, re-pull a genuinely-missing seg (self-pull)
	// this many times, then reload after a longer settle so the pull lands. Then NEEDS_ARM.
	var ARM_MAX_RECACHES = 1;
	var ARM_RECACHE_RELOAD_MS = 2500;
```

- [ ] **Step 4: Extend the arm `error` listener with the recache branch**

In `index.html`, replace the arm `error` listener:

```js
				v.addEventListener('error', function () {
					try {
						var s = '' + (v.currentSrc || v.src || '');
						var isLocal = s.indexOf('127.0.0.1') !== -1;
						if (isLocal && s !== v._armSrc) { v._armSrc = s; v._armRetryN = 0; }
						var action = mmVideoRecovery.mmArmRetryAction({
							active: playback.active, isLocal: isLocal, retries: v._armRetryN || 0, maxRetries: ARM_MAX_RETRIES });
						if (action !== 'retry') { return; }
						v._armRetryN = (v._armRetryN || 0) + 1;
						if (typeof dbg === 'function') { dbg('arm-retry'); }
						if (v._armRetryTimer) { clearTimeout(v._armRetryTimer); }
						v._armRetryTimer = setTimeout(function () {
							v._armRetryTimer = null;
							try { v.load(); } catch (e2) {}
							try { var p = v.play(); if (p && p['catch']) { p['catch'](function () {}); } } catch (e3) {}
						}, ARM_RETRY_BACKOFF_MS);
					} catch (e) { /* best-effort */ }
				});
```

with:

```js
				v.addEventListener('error', function () {
					try {
						var s = '' + (v.currentSrc || v.src || '');
						var isLocal = s.indexOf('127.0.0.1') !== -1;
						if (isLocal && s !== v._armSrc) { v._armSrc = s; v._armRetryN = 0; v._armRecacheN = 0; }
						var action = mmVideoRecovery.mmArmRetryAction({
							active: playback.active, isLocal: isLocal,
							retries: v._armRetryN || 0, recaches: v._armRecacheN || 0,
							maxRetries: ARM_MAX_RETRIES, maxRecaches: ARM_MAX_RECACHES });
						if (action === 'skip') { return; }
						var delay;
						if (action === 'recache') {
							// retry budget exhausted -> the seg is likely genuinely missing: re-pull it.
							v._armRecacheN = (v._armRecacheN || 0) + 1;
							if (typeof dbg === 'function') { dbg('arm-recache'); }
							_mmSelfPull(v, s);
							delay = ARM_RECACHE_RELOAD_MS;   // longer settle so the pull lands before the re-attempt
						} else {  // 'retry'
							v._armRetryN = (v._armRetryN || 0) + 1;
							if (typeof dbg === 'function') { dbg('arm-retry'); }
							delay = ARM_RETRY_BACKOFF_MS;
						}
						if (v._armRetryTimer) { clearTimeout(v._armRetryTimer); }
						v._armRetryTimer = setTimeout(function () {
							v._armRetryTimer = null;
							// iOS-5 needs load()+play() together to (re)start a held/errored element.
							try { v.load(); } catch (e2) {}
							try { var p = v.play(); if (p && p['catch']) { p['catch'](function () {}); } } catch (e3) {}
						}, delay);
					} catch (e) { /* best-effort */ }
				});
```

- [ ] **Step 5: Reset `_armRecacheN` on `playing`**

In `index.html`, replace the `playing` listener:

```js
				v.addEventListener('playing', function () {
					playback.activated = true; hideTapStart();
					v._armRetryN = 0;  // armed OK -> forget arm-error retries
					if (v._armRetryTimer) { clearTimeout(v._armRetryTimer); v._armRetryTimer = null; }
				});
```

with:

```js
				v.addEventListener('playing', function () {
					playback.activated = true; hideTapStart();
					v._armRetryN = 0; v._armRecacheN = 0;  // armed OK -> forget arm-error retries/recaches
					if (v._armRetryTimer) { clearTimeout(v._armRetryTimer); v._armRetryTimer = null; }
				});
```

- [ ] **Step 6: Verify the wiring + retirement invariants**

Run: `grep -n "_mmSelfPull\|ARM_MAX_RECACHES\|arm-recache\|_armRecacheN" index.html`
Expected: `_mmSelfPull` appears 3× (def + `_wdRecache` call + arm-listener call), the const, the `dbg('arm-recache')`, and the `_armRecacheN` resets/uses.

Run: `grep -c "ANNOUNCE_CACHE_MODE" index.html`
Expected: `0` (still never central).

Run: `node --check js/mmVideoRecovery.js && echo "module OK"`
Expected: `module OK`.

Run: `python pytest_runner.py --js`
Expected: PASS (full suite).

- [ ] **Step 7: Commit**

```bash
git add index.html
git commit -m "feat(mmvideo): arm-phase recache — self-heal a missing seg at ARM

Extend the arm error listener to retry->recache->skip: after the retry
budget, _mmSelfPull re-pulls the (likely genuinely-missing) seg and
reloads after a longer settle so the next load() arms. Extract the
shared _mmSelfPull used by both the watchdog and the arm listener.
Never central; slow pull falls to NEEDS_ARM. ES5, client-only."
```

Note: this commit does NOT include the `MMFORCE_TDBG_TEMP` force (reset in Step 1). Task 3 re-applies it for the sign-off.

---

### Task 3: iPad-1 on-wall sign-off

**Files:** none committed (manual; Step 1 makes a throwaway uncommitted edit). Deploy = STAGED reload (never whole-group).

**Interfaces:**
- Consumes: the deployed `js/mmVideoRecovery.js` + `index.html` from Tasks 1–2.

- [ ] **Step 1: Temporarily re-enable tdbg (uncommitted)**

In `index.html`, change (~line 316):

```js
	if (/tdbg/.test(location.href)) {
```

to:

```js
	if (/tdbg/.test(location.href) || true /* MMFORCE_TDBG_TEMP: diagnostic force, REVERT */) {
```

Do NOT commit this.

- [ ] **Step 2: Deploy via STAGED reloads**

Reload in small batches with `python tools/_reload_one.py <clientKey>` (never `_reload_group.py` — a whole-group reload drops the fleet). Confirm reconnect between batches.

- [ ] **Step 3: Create the exact hand-fixed scenario + verify self-heal**

On ONE device, delete `seg_0` from its cache to force the missing-segment condition:
```bash
ssh -i ~/.ssh/mosaic_ipad -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa -o IdentitiesOnly=yes -o StrictHostKeyChecking=no root@<device-ip> \
  "rm -f /var/mobile/Media/MosaicMeshCache/seg_<token>_0.mp4"
```
Then PLAY the cached `Demo` playlist on the group. Watch `mm_server.err` `CLIENTLOG` for that device. Expected:
- `arm-retry` fires (retry budget), then **`arm-recache`** fires (self-pull of `seg_0`).
- The device **self-heals**: after the ~2.5s reload the segment is present, the clip arms, and the device reaches active playback — **with NO manual push**.
- Confirm on-device that `seg_0` reappeared: `ssh … "ls -la /var/mobile/Media/MosaicMeshCache/seg_<token>_0.mp4"`.
- Measure "actually playing" as `verr` clear AND `rs>=2` AND `ct` advancing (NOT `elapsed`).

- [ ] **Step 4: Confirm no regression on healthy devices + never-central**

On a device that already has `seg_0`, confirm PLAY still arms + plays with no `arm-recache` needed, and that NO device sends `ANNOUNCE_CACHE_MODE:none` (grep the log for `cache-local-fail` / `ANNOUNCE_CACHE_MODE` → none). The `wd-*` watchdog behavior is unchanged.

- [ ] **Step 5: Revert the tdbg force + record the result**

Revert Step 1 (`git checkout HEAD -- index.html`) when done, OR leave it uncommitted if still testing — it must not be committed. Update memory `wall-verr3-is-mmvideo-not-cache` with the arm-recache self-heal outcome (a missing seg now re-pulls at arm without a manual push). The fleet is the test — no automated coverage for the wiring.
