# arm-recache poll-until-cached re-arm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the arm-phase recache poll `mmCache.state` until the self-pulled seg lands (re-arm the exact element) or fails (NEEDS_ARM), with no wall-clock bound — so a genuinely-missing seg self-heals regardless of file size, instead of the current fixed single 2.5 s reload that races the download and loses.

**Architecture:** A new pure decision helper `mmRecachePollAction(cacheState)` in `js/mmVideoRecovery.js` (node-tested), wired into the `index.html` arm `error` listener: the `'recache'` branch fires `_mmSelfPull` once, then self-reschedules a poll through the existing single `_armRetryTimer` — re-arming on `'cached'`, stopping on `'failed'`, waiting on `'pending'`. `mmArmRetryAction` and the poll watchdog are untouched.

**Tech Stack:** ES5 browser JS (iPad-1 / iOS 5.1), Node 20 `node --test` for the pure helper.

## Global Constraints

- **ES5 only** for `js/mmVideoRecovery.js` + `index.html`: no `let`/`const`, arrow functions, template literals, `class`, `Promise`, `fetch`. Keep the existing `var` + function-expression style.
- **Client-only.** No server change, no tweak rebuild.
- **Never fall to central.** On `'failed'` or an unparseable src, the device stays on the existing NEEDS_ARM / tap path — never a central stream.
- **Do not change `mmArmRetryAction`** (its `retry → recache → skip` ladder still returns `'recache'`) and **do not touch the poll watchdog** (`_watchdogTick`, `_wdRecachePending`) — it already polls-until-terminal correctly for active playback.
- **`mmCache.state` returns** one of `'none' | 'pending' | 'cached' | 'failed'` (js/mmCache.js:18–23).
- **Run JS tests via the runner:** `python pytest_runner.py --js` (or `node --test tests/unit/js/mmvideo-recovery.test.js`).
- **Deploy** is a STAGED single/small-batch client reload (never a whole-group reload). The `MMFORCE_TDBG_TEMP` force in `index.html` is re-applied uncommitted for the on-wall sign-off and reverted after — never committed.

---

### Task 1: `mmRecachePollAction` pure helper + node tests

**Files:**
- Modify: `js/mmVideoRecovery.js` (add one function next to `mmArmRetryAction`)
- Test: `tests/unit/js/mmvideo-recovery.test.js` (append cases)

**Interfaces:**
- Consumes: nothing new (same module-global pattern as `mmWatchdogAction` / `mmArmRetryAction`; the test's existing `loadRecovery()` helper).
- Produces: `mmVideoRecovery.mmRecachePollAction(cacheState: string) -> 'rearm' | 'giveup' | 'wait'`. `'cached'`→`'rearm'`, `'failed'`→`'giveup'`, everything else (incl. `'pending'`, `'none'`, `undefined`)→`'wait'`. Task 2 (index.html) calls this each poll tick with `mmCache.state(token)`.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/js/mmvideo-recovery.test.js`, append after the last arm test (after line 73):

```js
// --- arm recache poll decision: poll mmCache.state until the self-pull terminates.
// 'cached' -> re-arm the arming element; 'failed' -> give up (NEEDS_ARM, never central);
// 'pending'/'none'/unknown -> keep waiting (no wall-clock bound -> any seg size). ---
const RP = loadRecovery().mmRecachePollAction;

test('recache-poll: cached -> rearm', () => {
  assert.strictEqual(RP('cached'), 'rearm');
});

test('recache-poll: failed -> giveup', () => {
  assert.strictEqual(RP('failed'), 'giveup');
});

test('recache-poll: pending -> wait (download in progress)', () => {
  assert.strictEqual(RP('pending'), 'wait');
});

test('recache-poll: none -> wait', () => {
  assert.strictEqual(RP('none'), 'wait');
});

test('recache-poll: unknown/undefined -> wait (defensive)', () => {
  assert.strictEqual(RP(undefined), 'wait');
  assert.strictEqual(RP('weird'), 'wait');
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `node --test tests/unit/js/mmvideo-recovery.test.js`
Expected: FAIL — `RP` is `undefined` (`mmRecachePollAction` not defined yet), so calling it throws `TypeError: RP is not a function`.

- [ ] **Step 3: Add the helper**

In `js/mmVideoRecovery.js`, immediately after the `mmArmRetryAction` function (after its closing `};` on line 36, before `root.mmVideoRecovery = mmVideoRecovery;`), insert:

```js
  // cacheState = 'none' | 'pending' | 'cached' | 'failed'  (from mmCache.state)
  // Poll decision for a pending arm-phase recache self-pull:
  //   'cached' -> 'rearm'  (seg landed; the arm listener does load()+play())
  //   'failed' -> 'giveup' (pull errored; fall to NEEDS_ARM/tap — NEVER central)
  //   else     -> 'wait'   ('pending' download in progress, or 'none'; keep polling — no time bound)
  mmVideoRecovery.mmRecachePollAction = function (cacheState) {
    if (cacheState === 'cached') { return 'rearm'; }
    if (cacheState === 'failed') { return 'giveup'; }
    return 'wait';
  };
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `node --test tests/unit/js/mmvideo-recovery.test.js`
Expected: PASS — all suites (the 5 new `recache-poll` cases plus the unchanged `watchdog` and `arm` cases).

- [ ] **Step 5: Run the full JS suite (no regression)**

Run: `python pytest_runner.py --js`
Expected: PASS (the module-load smoke + `util/*` suites + this file all green).

- [ ] **Step 6: Commit**

```bash
git add js/mmVideoRecovery.js tests/unit/js/mmvideo-recovery.test.js
git commit -m "feat(arm-recache): mmRecachePollAction pure helper (cached->rearm/failed->giveup/wait)

Node-tested decision for the arm-phase recache poll: re-arm on 'cached',
give up (NEEDS_ARM) on 'failed', wait otherwise. Wiring lands in Task 2.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PVeEo4Mcq6YhcjArDRikoX"
```

---

### Task 2: Wire the poll into the `index.html` arm `error` listener

**Files:**
- Modify: `index.html` — the arm constants (~277–282), the `playing` listener (~210–214), and the `error` listener's `'recache'` branch + new-src reset (~220–250).

**Interfaces:**
- Consumes: `mmVideoRecovery.mmRecachePollAction` (Task 1); the existing `_mmSelfPull(v, src) -> token|null` (index.html:475), `mmCache.state(token)` (js/mmCache.js:18), `mmArmRetryAction` (unchanged), and the per-element fields `v._armRetryN` / `v._armRecacheN` / `v._armSrc` / `v._armRetryTimer`.
- Produces: no new exported symbols. Adds one per-element field `v._armRecachePending` (seg token being polled, or `null`). Behavior change only: the `'recache'` action now polls instead of doing a single fixed reload.

> This task changes DOM-driven browser code that the Node suite cannot exercise; its automated gate is "the JS suite still passes (helper untouched here) and the file parses." The behavioral proof is the on-wall sign-off (Task 3). Keep edits confined to the three regions below.

- [ ] **Step 1: Replace the two arm-recache constants**

In `index.html`, find (~lines 279–282):

```js
	// Arm-phase recache: after the retry budget, re-pull a genuinely-missing seg (self-pull)
	// this many times, then reload after a longer settle so the pull lands. Then NEEDS_ARM.
	var ARM_MAX_RECACHES = 1;
	var ARM_RECACHE_RELOAD_MS = 2500;
```

Replace with:

```js
	// Arm-phase recache: after the retry budget, re-pull a genuinely-missing seg (self-pull)
	// once, then POLL mmCache.state until the download terminates — re-arm on 'cached', give
	// up (NEEDS_ARM) on 'failed'. No wall-clock bound, so any seg size self-heals. Never central.
	var ARM_MAX_RECACHES = 1;
	var ARM_RECACHE_POLL_MS = 500;
```

- [ ] **Step 2: Reset `_armRecachePending` in the `playing` listener**

In `index.html`, find (~lines 210–214):

```js
				v.addEventListener('playing', function () {
					playback.activated = true; hideTapStart();
					v._armRetryN = 0; v._armRecacheN = 0;  // armed OK -> forget arm-error retries/recaches
					if (v._armRetryTimer) { clearTimeout(v._armRetryTimer); v._armRetryTimer = null; }
				});
```

Replace with:

```js
				v.addEventListener('playing', function () {
					playback.activated = true; hideTapStart();
					v._armRetryN = 0; v._armRecacheN = 0; v._armRecachePending = null;  // armed OK -> forget arm-error retries/recaches/poll
					if (v._armRetryTimer) { clearTimeout(v._armRetryTimer); v._armRetryTimer = null; }
				});
```

- [ ] **Step 3: Replace the `error` listener body (recache→poll + new-src reset)**

In `index.html`, find the whole `error` listener (~lines 220–250):

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

Replace with:

```js
				v.addEventListener('error', function () {
					try {
						var s = '' + (v.currentSrc || v.src || '');
						var isLocal = s.indexOf('127.0.0.1') !== -1;
						if (isLocal && s !== v._armSrc) { v._armSrc = s; v._armRetryN = 0; v._armRecacheN = 0; v._armRecachePending = null; }
						var action = mmVideoRecovery.mmArmRetryAction({
							active: playback.active, isLocal: isLocal,
							retries: v._armRetryN || 0, recaches: v._armRecacheN || 0,
							maxRetries: ARM_MAX_RETRIES, maxRecaches: ARM_MAX_RECACHES });
						if (action === 'skip') { return; }
						if (action === 'recache') {
							// retry budget exhausted -> the seg is likely genuinely missing: re-pull it,
							// then POLL mmCache.state until the download terminates. Re-arm on 'cached',
							// give up (NEEDS_ARM) on 'failed'. No wall-clock bound -> any seg size. The
							// single _armRetryTimer carries the self-rescheduling poll. NEVER central.
							v._armRecacheN = (v._armRecacheN || 0) + 1;
							if (typeof dbg === 'function') { dbg('arm-recache'); }
							v._armRecachePending = _mmSelfPull(v, s);   // seg token, or null if not a 127.0.0.1 cached URL
							var armPoll = function () {
								v._armRetryTimer = null;
								if (!v._armRecachePending) { return; }   // superseded / reset -> stop polling
								var cs = window.mmCache ? mmCache.state(v._armRecachePending) : 'none';
								var pa = mmVideoRecovery.mmRecachePollAction(cs);
								if (pa === 'rearm') {
									v._armRecachePending = null;
									if (typeof dbg === 'function') { dbg('arm-rearm'); }
									// iOS-5 needs load()+play() together to (re)start a held/errored element.
									try { v.load(); } catch (e2) {}
									try { var p = v.play(); if (p && p['catch']) { p['catch'](function () {}); } } catch (e3) {}
									return;
								}
								if (pa === 'giveup') { v._armRecachePending = null; return; }  // NEEDS_ARM/tap owns it
								v._armRetryTimer = setTimeout(armPoll, ARM_RECACHE_POLL_MS);   // 'wait' -> keep polling
							};
							if (v._armRetryTimer) { clearTimeout(v._armRetryTimer); }
							v._armRetryTimer = setTimeout(armPoll, ARM_RECACHE_POLL_MS);
							return;
						}
						// 'retry': single fixed-backoff reload through the same timer slot.
						v._armRetryN = (v._armRetryN || 0) + 1;
						if (typeof dbg === 'function') { dbg('arm-retry'); }
						if (v._armRetryTimer) { clearTimeout(v._armRetryTimer); }
						v._armRetryTimer = setTimeout(function () {
							v._armRetryTimer = null;
							// iOS-5 needs load()+play() together to (re)start a held/errored element.
							try { v.load(); } catch (e2) {}
							try { var p = v.play(); if (p && p['catch']) { p['catch'](function () {}); } } catch (e3) {}
						}, ARM_RETRY_BACKOFF_MS);
					} catch (e) { /* best-effort */ }
				});
```

- [ ] **Step 4: Verify no stale references + the file still parses**

Run:
```bash
grep -n "ARM_RECACHE_RELOAD_MS" index.html || echo "OK: no stale ARM_RECACHE_RELOAD_MS"
grep -n "ARM_RECACHE_POLL_MS\|_armRecachePending\|mmRecachePollAction\|arm-rearm" index.html
node --check index.html 2>&1 | head -1 || echo "note: node --check on HTML is expected to error on the tags; ignore if the only errors are HTML syntax, not JS"
```
Expected: no `ARM_RECACHE_RELOAD_MS` remains; `ARM_RECACHE_POLL_MS`, `_armRecachePending`, `mmRecachePollAction`, and `arm-rearm` are all present. (`node --check` on a `.html` file will report an HTML parse error — that's expected; it is only a sanity check that you didn't leave an obvious JS bracket imbalance. Rely on the grep results.)

- [ ] **Step 5: Confirm the JS suite still passes (helper unaffected here)**

Run: `python pytest_runner.py --js`
Expected: PASS (Task 2 does not touch `js/mmVideoRecovery.js`; this guards against an accidental edit there).

- [ ] **Step 6: Commit**

```bash
git add index.html
git commit -m "feat(arm-recache): poll mmCache.state until cached, then re-arm (no fixed reload)

The recache branch fired one fixed 2.5s reload that raced the WiFi
download and lost (on-wall 2026-07-11: pull succeeded, device never
re-armed -> black). Now it self-pulls once then polls mmCache.state via
the single _armRetryTimer: re-arm on 'cached', NEEDS_ARM on 'failed',
wait on 'pending' — no wall-clock bound, so any seg size self-heals.
Resets _armRecachePending on new-src + playing. Never central.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PVeEo4Mcq6YhcjArDRikoX"
```

---

### Task 3: iPad-1 on-wall re-sign-off (manual; gated on deploy)

**Files:** none (operational). Uses `tools/_reload_one.py`, `tools/_assign_play.py`, and single-device SSH.

**Interfaces:**
- Consumes: the deployed Task-2 `index.html` (arm-recache poll build) served by the running server; a single-video playlist (`PullTest3`, render token `4b47e31ce445`, single `_0` seg) on group `OEB Sign 1`; a target device that holds that seg.

This is the decisive test — it reproduces the exact 2026-07-11 failure and confirms the fix. It is a **manual, human-in-the-loop** sequence (not an automated step); the subagent executing this plan should STOP after Task 2 and hand back for the operator to run it, because it reloads physical devices, deletes a cached file over SSH, and reads the live wall.

- [ ] **Step 1: Re-apply the tdbg force (uncommitted) so devices emit CLIENTLOG diagnostics**

In `index.html`, temporarily change the diagnostics gate:
```js
	if (/tdbg/.test(location.href)) {
```
to:
```js
	if (/tdbg/.test(location.href) || true /* MMFORCE_TDBG_TEMP: diagnostic force, REVERT */) {
```
Do NOT commit this. (It is reverted in Step 6.)

- [ ] **Step 2: Deploy to ONE target device (staged reload)**

Pick a target that holds the seg (e.g. `sign1screen4`, clientKey `m9n397juqkr442t9`, IP `192.168.1.73`).
```bash
python tools/_reload_one.py m9n397juqkr442t9
```
Wait ~8 s; confirm it reconnects online (`GET /api/discovery/devices`).

- [ ] **Step 3: SSH-delete the target's cached seg (create the genuinely-missing scenario)**

```bash
KEY=~/.ssh/mosaic_ipad
OPTS="-o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes"
ssh -i "$KEY" $OPTS root@192.168.1.73 "rm -f /var/mobile/Media/MosaicMeshCache/seg_4b47e31ce445_0.mp4; ls -la /var/mobile/Media/MosaicMeshCache/seg_4b47e31ce445_0.mp4 2>&1"
```
Expected: "No such file or directory" (deleted). (Single-device SSH only — never a fleet burst.)

- [ ] **Step 4: PLAY the single-video playlist + watch the target's telemetry**

```bash
python tools/_assign_play.py "OEB Sign 1" "PullTest3"
```
Then tail `mm_server.err` for `CLIENTLOG m9n397juqkr442t9` over ~30–60 s.

**PASS criteria (the fix works):** the CLIENTLOG shows `arm-recache` → (one or more poll ticks while the pull runs) → **`arm-rearm`**, and the device then reaches **`verr` clear AND `rs>=2` AND `ct` advancing across two snapshots** — with NO manual seg push. (`elapsed` advancing does NOT count — it advances on black screens.) Confirm the seg re-landed on disk (SSH `ls` shows the file back) and, if practical, repeat against a larger seg to demonstrate the no-timeout / size-agnostic behavior.

**FAIL criteria:** device stays `verr=3, rs=0` (as it did on 2026-07-11) — capture the CLIENTLOG and return to systematic-debugging.

- [ ] **Step 5: Restore the wall to a known-good state**

```bash
python tools/_assign_play.py "OEB Sign 1" "Demo"
```
Confirm the group returns to `active=True` across devices (Demo leads with animations — no cached-video dependency).

- [ ] **Step 6: Revert the tdbg force**

In `index.html`, restore the gate to `if (/tdbg/.test(location.href)) {` (remove the `|| true /* MMFORCE_TDBG_TEMP … */`). Confirm `git diff -- index.html` is empty. The `MMFORCE_TDBG_TEMP` line must never be committed.

---

## Deploy note (not a task step)

Client JS only — no server restart, no tweak rebuild. Fleet rollout is STAGED single/small-batch reloads via `tools/_reload_one.py` after the target-device sign-off passes; never a whole-group reload (a whole-group RELOAD has dropped the fleet offline for minutes before).
