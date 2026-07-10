# Adaptive Fast-Sync Cadence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make display clients clock-sync fast (~1 s) on join until the offset is precise, then relax to the existing 30 s drift cadence — so the wall starts crisply-together instead of ragged.

**Architecture:** Replace `GoTime._setupSync`'s fixed `setTimeout`-burst + `setInterval` with a **self-rescheduling** `_sync`: after each probe it computes the next delay from the just-observed precision via a pure `_nextSyncDelay` helper, arms one `setTimeout`, and (in the fast phase) adds per-client jitter. A sentinel precision for a non-returned sample folds dropped samples into the same helper, so the loop can never die and coarse/dropped samples keep it fast.

**Tech Stack:** ES5 JavaScript (`js/GoTime.js`, `js/mosiacmesh.js`) — runs on 1st-gen iPad / iOS 5.1 Safari. Node 20 `--test` for the pure-helper + scheduler unit tests (`tests/unit/js/`). No server change; deploy is a fleet reload.

## Global Constraints

- **ES5 only** in `js/GoTime.js` and `js/mosiacmesh.js`: no `let`/`const`, arrow functions, template literals, `class`, `Promise`, `fetch`. These load on the 1st-gen iPad.
- **Match existing GoTime style:** private helpers are assigned without `var` (e.g. `_steerStep = function(){…}`) and exposed for tests on the returned object (`_steerStep: _steerStep`). Follow that pattern exactly.
- **Pure helper stays pure:** `_nextSyncDelay` must contain no `Date`, no `Math.random`, no timers, no `options` access — only its `state` argument. Jitter and clock reads live in the scheduler wrapper.
- **No re-entry to fast:** once the phase is `'slow'`, it stays slow. Ongoing drift is handled elsewhere (30 s beat + drift steer). Do not add fast re-entry.
- **Knob defaults (verbatim):** `FastSyncInterval=1000`, `FastSyncJitterMs=150`, `SyncPrecisionTargetMs=40`, `SyncPrecisionStreak=2`, `FastSyncCapMs=60000`, `SyncInterval=30000` (production override; GoTime default stays `900000`).
- **Retire `SyncInitialTimeouts`:** remove its option default, its `setOptions` handler, and its use in `_setupSync`; remove it from the `mosiacmesh.js` override. The adaptive fast phase supersedes it.
- **Run JS tests with:** `python pytest_runner.py --js` (or `node --test tests/unit/js/*.js`).

---

### Task 1: `_nextSyncDelay` pure decision helper + node tests

**Files:**
- Modify: `js/GoTime.js` — add the `_nextSyncDelay` helper near the other private helpers (after `_setupSync`, ~line 326) and expose it on the returned object (~line 448, beside `_robustTarget: _robustTarget`).
- Create: `tests/unit/js/gotime-cadence.test.js`

**Interfaces:**
- Produces: `GoTime._nextSyncDelay(state) -> { delayMs, phase, streak }` where
  `state = { phase: 'fast'|'slow', precision: <number ms>, streak: <int>, fastElapsedMs: <number ms>, opts: { SyncInterval, FastSyncInterval, SyncPrecisionTargetMs, SyncPrecisionStreak, FastSyncCapMs } }`.
  Pure, deterministic. Task 2's scheduler consumes it.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/js/gotime-cadence.test.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

// Loads GoTime.js into a sandbox with recording timers + a stub-able Math.random.
function loadGoTime() {
  const code = fs.readFileSync(new URL('../../../js/GoTime.js', import.meta.url), 'utf8');
  let now = 1000000;
  let randVal = 0;
  const timers = [];
  function FakeDate() {}
  FakeDate.now = function () { return now; };
  FakeDate.prototype.getTime = function () { return now; };
  FakeDate.prototype.valueOf = function () { return now; };   // so `date - number` works in _calculateOffset
  const fakeMath = Object.create(Math);          // inherits floor/round/abs/min/max…
  fakeMath.random = function () { return randVal; };
  const sandbox = {
    Date: FakeDate,
    Math: fakeMath,
    setTimeout: function (fn, d) { timers.push({ fn: fn, delay: d }); return timers.length; },
    setInterval: function () { return 0; },
    clearTimeout: function () {},
    XMLHttpRequest: function () { this.open = function () {}; this.send = function () {}; },
    console: { log: function () {} },
    window: {},
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  sandbox.__setNow = function (t) { now = t; };
  sandbox.__setRand = function (v) { randVal = v; };
  sandbox.__timers = timers;
  return sandbox;
}

const OPTS = {
  SyncInterval: 30000, FastSyncInterval: 1000, SyncPrecisionTargetMs: 40,
  SyncPrecisionStreak: 2, FastSyncCapMs: 60000,
};

test('_nextSyncDelay: fast + coarse precision -> stay fast, streak resets to 0', () => {
  const s = loadGoTime();
  assert.deepStrictEqual(
    s.GoTime._nextSyncDelay({ phase: 'fast', precision: 120, streak: 1, fastElapsedMs: 1000, opts: OPTS }),
    { delayMs: 1000, phase: 'fast', streak: 0 });
});

test('_nextSyncDelay: fast + good precision below streak target -> stay fast, streak++', () => {
  const s = loadGoTime();
  assert.deepStrictEqual(
    s.GoTime._nextSyncDelay({ phase: 'fast', precision: 30, streak: 0, fastElapsedMs: 1000, opts: OPTS }),
    { delayMs: 1000, phase: 'fast', streak: 1 });
});

test('_nextSyncDelay: fast + good precision reaching streak -> transition to slow', () => {
  const s = loadGoTime();
  assert.deepStrictEqual(
    s.GoTime._nextSyncDelay({ phase: 'fast', precision: 30, streak: 1, fastElapsedMs: 2000, opts: OPTS }),
    { delayMs: 30000, phase: 'slow', streak: 2 });
});

test('_nextSyncDelay: precision exactly at target counts as good', () => {
  const s = loadGoTime();
  assert.deepStrictEqual(
    s.GoTime._nextSyncDelay({ phase: 'fast', precision: 40, streak: 1, fastElapsedMs: 2000, opts: OPTS }),
    { delayMs: 30000, phase: 'slow', streak: 2 });
});

test('_nextSyncDelay: fast + coarse but past cap -> transition to slow (cap wins)', () => {
  const s = loadGoTime();
  assert.deepStrictEqual(
    s.GoTime._nextSyncDelay({ phase: 'fast', precision: 500, streak: 0, fastElapsedMs: 60000, opts: OPTS }),
    { delayMs: 30000, phase: 'slow', streak: 0 });
});

test('_nextSyncDelay: slow phase always stays slow regardless of precision', () => {
  const s = loadGoTime();
  assert.deepStrictEqual(
    s.GoTime._nextSyncDelay({ phase: 'slow', precision: 5, streak: 2, fastElapsedMs: 999999, opts: OPTS }),
    { delayMs: 30000, phase: 'slow', streak: 2 });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `node --test tests/unit/js/gotime-cadence.test.js`
Expected: FAIL — `TypeError: s.GoTime._nextSyncDelay is not a function` (helper not defined / not exposed yet).

- [ ] **Step 3: Add the pure helper**

In `js/GoTime.js`, immediately after the `_setupSync = function() {…};` block (currently ends ~line 326), add (no `var`, matching `_steerStep`'s style):

```js
    // Pure fast->slow cadence decision. Given the current phase, the effective
    // precision of the last cycle's sample (a large sentinel when none returned),
    // the good-sample streak so far, and ms elapsed in the fast phase, return the
    // next {delayMs, phase, streak}. No Date / Math.random / timers here — the
    // scheduler wrapper owns those. See docs/superpowers/specs/2026-07-10-sync-cadence-adaptive-design.md
    _nextSyncDelay = function(state) {
        var o = state.opts;
        if (state.phase === 'slow') {
            return { delayMs: o.SyncInterval, phase: 'slow', streak: state.streak };
        }
        var nextStreak = (state.precision <= o.SyncPrecisionTargetMs) ? (state.streak + 1) : 0;
        if (nextStreak >= o.SyncPrecisionStreak) {
            return { delayMs: o.SyncInterval, phase: 'slow', streak: nextStreak };
        }
        if (state.fastElapsedMs >= o.FastSyncCapMs) {
            return { delayMs: o.SyncInterval, phase: 'slow', streak: nextStreak };
        }
        return { delayMs: o.FastSyncInterval, phase: 'fast', streak: nextStreak };
    };
```

- [ ] **Step 4: Expose the helper for tests**

In the returned object at the bottom of GoTime (currently lines 446-448):

```js
		steerTick: function() { return _steerTick(); },
		_steerStep: _steerStep,
		_robustTarget: _robustTarget
```

change to:

```js
		steerTick: function() { return _steerTick(); },
		_steerStep: _steerStep,
		_robustTarget: _robustTarget,
		_nextSyncDelay: _nextSyncDelay
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `node --test tests/unit/js/gotime-cadence.test.js`
Expected: PASS (6/6).

- [ ] **Step 6: Commit**

```bash
git add js/GoTime.js tests/unit/js/gotime-cadence.test.js
git commit -m "feat(sync): _nextSyncDelay pure fast->slow cadence helper + node tests"
```

---

### Task 2: Adaptive self-rescheduling scheduler in GoTime

**Files:**
- Modify: `js/GoTime.js` — options block (~lines 122-150), `_sync` (~173-189), `_reviseOffset` (~270-274), `_setupSync` (~313-326), `setOptions` (~355-378).
- Modify: `tests/unit/js/gotime-cadence.test.js` — append scheduler integration tests.

**Interfaces:**
- Consumes: `GoTime._nextSyncDelay` (Task 1); existing `options._precision`, `options._syncInterval`, `_ajaxSample`, `options._wsCall`, `Date.now`.
- Produces: a self-rescheduling `_sync` (one live `setTimeout` at a time via `options._syncTimer`); new options `_fastSyncInterval`, `_fastSyncJitterMs`, `_syncPrecisionTargetMs`, `_syncPrecisionStreak`, `_fastSyncCapMs`, and mutable state `_syncPhase`, `_syncStreak`, `_fastStartMs`, `_syncSampleReturned`, `_syncTimer`; new `setOptions` keys `FastSyncInterval`, `FastSyncJitterMs`, `SyncPrecisionTargetMs`, `SyncPrecisionStreak`, `FastSyncCapMs`.

- [ ] **Step 1: Write the failing scheduler tests**

Append to `tests/unit/js/gotime-cadence.test.js` (reuses `loadGoTime`/`OPTS` from Task 1):

```js
// --- scheduler integration (drives the real _sync/_scheduleAdaptiveSync path) ---

// Wire GoTime for a scheduler run: ws sender always "succeeds", knobs set,
// which arms the 500ms kick via _setupSync. Returns the sandbox.
function primeScheduler(s) {
  s.GoTime.wsSend(function () { return true; });   // _sync's wsCall path succeeds
  s.GoTime.setOptions({
    FastSyncInterval: 1000, FastSyncJitterMs: 150, SyncPrecisionTargetMs: 40,
    SyncPrecisionStreak: 2, FastSyncCapMs: 60000, SyncInterval: 30000,
  });
  return s;
}
function lastDelay(s) { return s.__timers[s.__timers.length - 1].delay; }
function fireLast(s) { s.__timers[s.__timers.length - 1].fn(); }
// Feed one good clock sample through the real revise path (RTT 0 -> precision 0).
function goodSample(s) { s.GoTime.wsReceived(String(1000000)); }

test('scheduler: _setupSync arms the 500ms kick', () => {
  const s = primeScheduler(loadGoTime());
  assert.strictEqual(lastDelay(s), 500);
});

test('scheduler: first sync (no sample yet) schedules fast, jitter 0 -> exactly 1000', () => {
  const s = primeScheduler(loadGoTime());
  s.__setRand(0);
  fireLast(s);                       // fire _sync #1
  assert.strictEqual(lastDelay(s), 1000);
});

test('scheduler: fast jitter stays within [1000, 1150)', () => {
  const s = primeScheduler(loadGoTime());
  s.__setRand(0.999);
  fireLast(s);                       // _sync #1
  const d = lastDelay(s);
  assert.ok(d >= 1000 && d < 1150, 'delay ' + d + ' in [1000,1150)');
});

test('scheduler: two consecutive good samples transition to the 30000ms slow cadence', () => {
  const s = primeScheduler(loadGoTime());
  s.__setRand(0);
  fireLast(s);            goodSample(s);   // _sync #1 -> streak 0, then a good sample
  fireLast(s);            goodSample(s);   // _sync #2 -> streak 1, then a good sample
  assert.strictEqual(lastDelay(s), 1000);  // still fast after streak 1
  fireLast(s);                             // _sync #3 -> streak 2 -> slow
  assert.strictEqual(lastDelay(s), 30000);
});

test('scheduler: a dropped sample (no wsReceived) resets the streak, stays fast', () => {
  const s = primeScheduler(loadGoTime());
  s.__setRand(0);
  fireLast(s); goodSample(s);              // _sync #1 -> streak 0, good sample
  fireLast(s);                             // _sync #2 -> streak 1 (no new sample after)
  fireLast(s);                             // _sync #3: sample NOT returned -> sentinel -> streak 0
  assert.strictEqual(lastDelay(s), 1000);  // still fast, not slow
});
```

- [ ] **Step 2: Run the scheduler tests to verify they fail**

Run: `node --test tests/unit/js/gotime-cadence.test.js`
Expected: FAIL — the kick asserts 500 but current `_setupSync` fires the old `[500,3000,9000,15000]` burst + `setInterval`, and `_scheduleAdaptiveSync`/jitter don't exist, so the fast/slow/drop assertions fail.

- [ ] **Step 3: Add the new options + retire `_syncInitialTimeouts`**

In `js/GoTime.js`, replace the option line (currently line 128):

```js
        _syncInitialTimeouts: [0, 3000, 9000, 18000, 45000],
        _syncInterval: 900000,
```

with:

```js
        _syncInterval: 900000,
        _fastSyncInterval: 1000,
        _fastSyncJitterMs: 150,
        _syncPrecisionTargetMs: 40,
        _syncPrecisionStreak: 2,
        _fastSyncCapMs: 60000,
        _syncPhase: 'fast',
        _syncStreak: 0,
        _fastStartMs: null,
        _syncSampleReturned: false,
        _syncTimer: null,
```

- [ ] **Step 4: Rewrite `_sync` to self-reschedule**

Replace the `_sync` function (currently lines 173-189):

```js
    _sync = function() {
        var success;
		if (options._wsCall != null) {
			options._wsRequestTime = Date.now();
			success = options._wsCall();
			if (success) {
				options._syncCount++;
				return;
			}
		}
		if (options._ajaxURL != null) {
			success = _ajaxSample();
			if (success) {
				options._syncCount++;
			}
		}
    };
```

with (fire exactly one probe, then always reschedule):

```js
    _sync = function() {
        var success = false;
		if (options._wsCall != null) {
			options._wsRequestTime = Date.now();
			if (options._wsCall()) { options._syncCount++; success = true; }
		}
		if (!success && options._ajaxURL != null) {
			if (_ajaxSample()) { options._syncCount++; }
		}
		_scheduleAdaptiveSync();
    };

    // Arm the next _sync. Reads the last cycle's sample precision (a large sentinel
    // if no sample returned, so a dropped/coarse sample keeps us fast), asks the
    // pure _nextSyncDelay for the next delay+phase, and applies per-client jitter in
    // the fast phase only. clearTimeout-before-arm keeps exactly one timer live even
    // when resync() fires overlapping _sync calls.
    _scheduleAdaptiveSync = function() {
        var nowMs = Date.now();
        if (options._fastStartMs == null) { options._fastStartMs = nowMs; }
        var effPrec = options._syncSampleReturned ? options._precision : 2e308;
        var d = _nextSyncDelay({
            phase: options._syncPhase,
            precision: effPrec,
            streak: options._syncStreak,
            fastElapsedMs: nowMs - options._fastStartMs,
            opts: {
                SyncInterval: options._syncInterval,
                FastSyncInterval: options._fastSyncInterval,
                SyncPrecisionTargetMs: options._syncPrecisionTargetMs,
                SyncPrecisionStreak: options._syncPrecisionStreak,
                FastSyncCapMs: options._fastSyncCapMs
            }
        });
        options._syncPhase = d.phase;
        options._syncStreak = d.streak;
        options._syncSampleReturned = false;
        var delay = d.delayMs;
        if (d.phase === 'fast') { delay += Math.floor(Math.random() * options._fastSyncJitterMs); }
        if (options._syncTimer != null) { clearTimeout(options._syncTimer); }
        options._syncTimer = setTimeout(_sync, delay);
    };
```

- [ ] **Step 5: Mark samples returned in `_reviseOffset`**

In `_reviseOffset` (currently lines 270-274), after the NaN guard, add the returned flag so only valid samples count toward the streak:

```js
    _reviseOffset = function(sample, method) {
        var timestamp;
        if (isNaN(sample.offset) || isNaN(sample.precision)) {
            return;
        }
        options._syncSampleReturned = true;
```

- [ ] **Step 6: Rewrite `_setupSync` to start the fast phase**

Replace `_setupSync` (currently lines 313-326):

```js
    _setupSync = function() {
        var i, len, ref, time;
        if (options._synchronizing === false) {
            options._synchronizing = true;
            ref = options._syncInitialTimeouts;
            for (i = 0, len = ref.length; i < len; i++) {
                time = ref[i];
                // Initial syncs
                setTimeout(_sync, time);
            }
            // Sync repetitively
            setInterval(_sync, options._syncInterval);
        }
    };
```

with:

```js
    _setupSync = function() {
        if (options._synchronizing === false) {
            options._synchronizing = true;
            options._syncPhase = 'fast';
            options._syncStreak = 0;
            options._fastStartMs = null;
            options._syncSampleReturned = false;
            // Initial short kick (preserves the old 500ms first-sample latency); every
            // sample after this is scheduled adaptively by _sync -> _scheduleAdaptiveSync.
            options._syncTimer = setTimeout(_sync, 500);
        }
    };
```

- [ ] **Step 7: Add `setOptions` handlers + remove the `SyncInitialTimeouts` handler**

In `setOptions`, remove these lines (currently 359-361):

```js
				if (opts.SyncInitialTimeouts != null) {
					options._syncInitialTimeouts = opts.SyncInitialTimeouts;
				}
```

and after the `SyncInterval` handler (currently lines 362-364) add:

```js
			if (opts.SyncInterval != null) {
				options._syncInterval = opts.SyncInterval;
			}
			if (opts.FastSyncInterval != null) { options._fastSyncInterval = opts.FastSyncInterval; }
			if (opts.FastSyncJitterMs != null) { options._fastSyncJitterMs = opts.FastSyncJitterMs; }
			if (opts.SyncPrecisionTargetMs != null) { options._syncPrecisionTargetMs = opts.SyncPrecisionTargetMs; }
			if (opts.SyncPrecisionStreak != null) { options._syncPrecisionStreak = opts.SyncPrecisionStreak; }
			if (opts.FastSyncCapMs != null) { options._fastSyncCapMs = opts.FastSyncCapMs; }
```

- [ ] **Step 8: Declare the new helper alongside existing private helpers**

`_scheduleAdaptiveSync` and `_nextSyncDelay` follow the file's existing no-`var` assignment style (like `_ajaxSample`, `_steerStep`). No separate declaration block exists — confirm the file still parses by running the full JS suite in the next step. (If a `var` declaration list is later added for lint, include both names.)

- [ ] **Step 9: Run the full JS test suite to verify it passes**

Run: `node --test tests/unit/js/gotime-cadence.test.js`
Expected: PASS (all Task 1 + Task 2 tests).

Run: `python pytest_runner.py --js`
Expected: PASS — the whole JS suite green (no regression in `gotime-ready.test.js` / `gotime-steer.test.js`, which don't touch the sync scheduler).

- [ ] **Step 10: Commit**

```bash
git add js/GoTime.js tests/unit/js/gotime-cadence.test.js
git commit -m "feat(sync): adaptive self-rescheduling fast->slow clock cadence

Fast (~1s, jittered) sync on join until getPrecision() <= 40ms for 2
consecutive samples or a 60s cap, then the existing 30s cadence.
Retires the fixed SyncInitialTimeouts burst + setInterval. ES5."
```

---

### Task 3: Wire the production override in `mosiacmesh.js`

**Files:**
- Modify: `js/mosiacmesh.js:189-199` — the `GoTime.setOptions({…})` block.

**Interfaces:**
- Consumes: the new `setOptions` keys from Task 2. No test (integration config; covered on-wall in Task 4).

- [ ] **Step 1: Replace the override block**

In `js/mosiacmesh.js`, replace (lines 189-199):

```js
	GoTime.setOptions({
		AjaxURL: "/time",
		WhenSynced: updateData, // Is called for the first sync
		OnSync: goTimeSync, // Calls on ever sync starting with the second sync
		SyncInitialTimeouts: [500, 3000, 9000, 15000],
		// Re-sync every 60s (was 15min) so a lower-RTT sample can ratchet the offset
		// precision down. The offset itself is HELD (monotonic ratchet, see GoTime
		// _reviseOffset); ongoing oscillator drift is corrected at the beat by
		// ProgrammableTimer's median drift loop, which is what keeps displays aligned.
		SyncInterval: 30000
	});
```

with:

```js
	GoTime.setOptions({
		AjaxURL: "/time",
		WhenSynced: updateData, // Is called for the first sync
		OnSync: goTimeSync, // Calls on ever sync starting with the second sync
		// Adaptive cadence: sync fast (~1s, jittered per client) on join until the
		// offset is precise (getPrecision() <= 40ms for 2 samples) or a 60s cap, then
		// relax to 30s. Replaces the old fixed SyncInitialTimeouts burst. See
		// docs/superpowers/specs/2026-07-10-sync-cadence-adaptive-design.md
		FastSyncInterval: 1000,
		FastSyncJitterMs: 150,
		SyncPrecisionTargetMs: 40,
		SyncPrecisionStreak: 2,
		FastSyncCapMs: 60000,
		// Slow/drift cadence once converged. The offset is HELD (monotonic ratchet,
		// see GoTime _reviseOffset); ongoing oscillator drift is corrected at the beat
		// by ProgrammableTimer's median drift loop, which keeps displays aligned.
		SyncInterval: 30000
	});
```

- [ ] **Step 2: Verify no stray `SyncInitialTimeouts` references remain**

Run: `grep -rn "SyncInitialTimeouts" js/`
Expected: no output (fully retired from both `GoTime.js` and `mosiacmesh.js`).

- [ ] **Step 3: Verify the JS suite still passes**

Run: `python pytest_runner.py --js`
Expected: PASS (no change to tested modules; sanity check only).

- [ ] **Step 4: Commit**

```bash
git add js/mosiacmesh.js
git commit -m "feat(sync): adopt adaptive fast-sync cadence in the client override

Retire SyncInitialTimeouts; set FastSync* + SyncPrecision* knobs so the
iPad-1 fleet converges fast on join then relaxes to 30s."
```

---

### Task 4: iPad-1 on-wall sign-off

**Files:** none (manual verification on the physical fleet). Deploy = fleet **reload** (JS is server-served; no tweak rebuild, no server restart required for the JS change).

**Interfaces:**
- Consumes: the deployed `GoTime.js` + `mosiacmesh.js` from Tasks 1-3.

- [ ] **Step 1: Deploy**

Broadcast a RELOAD to the fleet (or reload the webclips) so panels pick up the new `js/GoTime.js` + `js/mosiacmesh.js`. No server restart needed for the JS.

- [ ] **Step 2: Observe convergence via `?tdbg`**

On a freshly-joined panel, watch the `?tdbg` payload. Expected within a few seconds of join:
- `accAge` small (fresh offset) and `prec` (getPrecision) drops to **≤ 40 ms** and holds,
- then the sample cadence relaxes to ~30 s (no more 1 s bursts once converged),
- a panel that can't converge stops fast-syncing by ~60 s (cap) and settles at 30 s anyway.

- [ ] **Step 3: Confirm the wall starts together**

Assign a video playlist to the group and PLAY. Expected: panels reach the start gate close together (materially fewer idle `idx=1` panels than the pre-change ragged start observed 2026-07-10) — the wall starts crisply.

- [ ] **Step 4: Confirm no herd spike after a synchronized reboot**

Reboot/relaunch several panels at once. Expected: no correlated request spike at the server on the 1 s tick — the ±150 ms jitter smears the fast samples across the second. (Sanity via server access-log timestamps around join; not a hard gate.)

- [ ] **Step 5: Sign off**

Record the on-wall result (converged prec, start-together improvement) in the session notes / a memory update. This task has no automated test — the fleet is the test.
