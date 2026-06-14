# Sync-Gated Play Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Hold coordinated-start GO until each online client's clock is genuinely converged (fresh + precise offset AND stable beat), not merely "video armed" — so video and animations start in sync across all screens for all content.

**Architecture:** Client emits READY only when `clockReady()` (fresh/precise GoTime offset + `clockSettled()` std-dev) is true; PREPARE kicks a fresh GoTime resample burst; since GO already waits for all-online-READY, this becomes hold-until-synced for every content type. An extended PREPARE_TIMEOUT is the bounded best-effort fallback. The pure clock-ready decision lives in GoTime (unit-tested); the readiness wiring is iPad-1 ES5 (verified via `tdbg`/e2e). `tdbg` is extended with clock metrics for live verification.

**Tech Stack:** ES5 display client (`index.html` inline + `js/GoTime.js` classic script); Python/aiohttp server; pytest; node `--test` (incl. a `vm`-sandbox harness for GoTime).

**Spec:** `docs/superpowers/specs/2026-06-14-sync-gated-play-release-design.md`

**Constraints:**
- `index.html` inline script + `js/GoTime.js` must stay **ES5** (1st-gen iPad / Safari 5.1): no `let`/`const`/arrow/template-literals/`class`/`Promise`/`fetch`.
- Test runners: `python -m pytest tests/unit/<f> -c tests/pytest.ini -v` (never bare pytest); `node --test tests/unit/js/<f>.js`. Full-suite baseline ≈ **14 failures** (legacy `_begin_prepare` event-loop-in-sync + ReconcileQuad) — do not exceed.
- Current values: `PREPARE_TIMEOUT_MS = 25000`, `RELEASE_LEAD_MS = 750` (server.py:120-121).

---

## File Structure

**Modified**
- `js/GoTime.js` — add `resync(n, spacingMs)`, `msSinceAccept()`, and the pure static `readyVerdict(precisionMs, accAgeMs, phaseStd, phaseMean, opts)`. (ES5; additive to the returned object.)
- `index.html` (inline ES5) — `clockReady()` (computes phase std/mean, calls `GoTime.readyVerdict`); PREPARE handler calls `GoTime.resync`; READY emission gated via `sendReadyWhenClockReady`; `dbg()` payload extended with clock metrics; clock-ready constants.
- `server.py` — extend `PREPARE_TIMEOUT_MS`.
- `CLAUDE.md` — document the sync-gated release.

**New tests**
- `tests/unit/js/gotime-ready.test.js` — `vm`-sandbox harness; `readyVerdict` truth table, `msSinceAccept` default, `resync` schedules N samples.
- Server release behavior asserted in `tests/unit/test_coordinated_start.py` (append) or a new `tests/unit/test_sync_release.py`.

---

## Task 1: GoTime — `resync`, `msSinceAccept`, pure `readyVerdict`

**Files:** Modify `js/GoTime.js`; Create `tests/unit/js/gotime-ready.test.js`.

**Context:** `GoTime` is an IIFE returning a public object; `_sync` and `options` are private in its closure. `options._lastAcceptTime` is set by `_reviseOffset` when a sample is accepted; `options._precision` holds the accepted RTT/2. Add three public members.

- [ ] **Step 1: Write the failing test** (`tests/unit/js/gotime-ready.test.js`):

```javascript
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

// GoTime.js is a classic ES5 script (sets globals, no exports). Load it into a
// sandbox so we can exercise the new public members without a browser.
function loadGoTime(opts) {
  opts = opts || {};
  const code = fs.readFileSync(new URL('../../../js/GoTime.js', import.meta.url), 'utf8');
  let now = opts.startNow || 1000000;
  function FakeDate() {}
  FakeDate.now = function () { return now; };
  FakeDate.prototype.getTime = function () { return now; };
  const sandbox = {
    Date: FakeDate,
    setTimeout: opts.setTimeout || function () { return 0; },
    setInterval: function () { return 0; },
    clearTimeout: function () {},
    XMLHttpRequest: function () { this.open = function () {}; this.send = function () {}; },
    console: { log: function () {} },
    window: {},
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  sandbox.__setNow = function (t) { now = t; };
  return sandbox;
}

test('readyVerdict: all within thresholds -> true', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime.readyVerdict(20, 5000, 5, 3, {}), true);
});
test('readyVerdict: stale offset -> false', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime.readyVerdict(20, 999999, 5, 3, {}), false);
});
test('readyVerdict: imprecise offset -> false', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime.readyVerdict(200, 5000, 5, 3, {}), false);
});
test('readyVerdict: jittery beat -> false', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime.readyVerdict(20, 5000, 50, 3, {}), false);
  assert.equal(s.GoTime.readyVerdict(20, 5000, 5, 99, {}), false); // mean off
});
test('readyVerdict: custom thresholds honored', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime.readyVerdict(80, 5000, 5, 3, { maxPrecisionMs: 100 }), true);
});
test('msSinceAccept: Infinity before any accepted sample', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime.msSinceAccept(), Infinity);
});
test('resync: schedules n samples', () => {
  let calls = 0;
  const s = loadGoTime({ setTimeout: function () { calls++; return 0; } });
  s.GoTime.resync(4, 100);
  assert.equal(calls, 4);
});
```

- [ ] **Step 2: Run to fail** — `node --test tests/unit/js/gotime-ready.test.js`
Expected: FAIL — `readyVerdict`/`msSinceAccept`/`resync` are not functions.

- [ ] **Step 3: Implement** — in `js/GoTime.js`, add these three members to the **`GoTime` IIFE's returned object** (the `return { now: ..., getOffset: ..., ... }` block). They can read `options` and call `_sync` (both in closure scope):

```javascript
		// Force fresh clock samples now (used on PREPARE so the offset isn't
		// up-to-15-min stale). Fires n _sync() calls spaced spacingMs apart.
		resync: function(n, spacingMs) {
			n = n || 4; spacingMs = spacingMs || 400;
			for (var i = 0; i < n; i++) { setTimeout(_sync, i * spacingMs); }
		},

		// Age (ms) of the currently-locked offset sample; Infinity if none yet.
		// Used to require a FRESH offset before play-release.
		msSinceAccept: function() {
			return (options._lastAcceptTime == null) ? Infinity : (GoTime.now() - options._lastAcceptTime);
		},

		// PURE clock-ready decision (no closure state) so it is unit-testable:
		// the offset must be fresh (accAgeMs) AND precise (precisionMs) AND the
		// beat stable (phaseStd) AND centered (phaseMean). Thresholds overridable.
		readyVerdict: function(precisionMs, accAgeMs, phaseStd, phaseMean, opts) {
			opts = opts || {};
			var maxPrec = (opts.maxPrecisionMs != null) ? opts.maxPrecisionMs : 50;
			var maxAge  = (opts.maxAgeMs       != null) ? opts.maxAgeMs       : 30000;
			var maxStd  = (opts.maxStdMs       != null) ? opts.maxStdMs       : 10;
			var maxMean = (opts.maxMeanMs      != null) ? opts.maxMeanMs      : 20;
			return (precisionMs <= maxPrec) && (accAgeMs <= maxAge) &&
			       (phaseStd <= maxStd) && (Math.abs(phaseMean) <= maxMean);
		},
```

(Add a comma after the existing last member `wsReceived: function(...) {...}` so the object stays valid. Keep everything ES5.)

- [ ] **Step 4: Run to pass** — `node --test tests/unit/js/gotime-ready.test.js` → PASS (7). Then `node --test tests/unit/js/*.js` → all pass.

- [ ] **Step 5: Commit**
```bash
git add js/GoTime.js tests/unit/js/gotime-ready.test.js
git commit -m "feat(gotime): resync(), msSinceAccept(), pure readyVerdict() for sync-gated release"
```

---

## Task 2: Client `clockReady()` + constants (`index.html`)

**Files:** Modify `index.html` (inline ES5).

**Context:** `index.html` already has `recordTickPhase` + `clockSettled()` (computes phase std-dev/mean over `_phaseHistory` with `SETTLE_SAMPLES=5`, `SETTLE_STDDEV_MS=10`, `SETTLE_MEAN_MS=20`). Refactor the std/mean computation so both `clockSettled()` and the new `clockReady()` can use it, then define `clockReady()` to combine fresh/precise offset (GoTime) with the stable beat.

- [ ] **Step 1: Add clock-ready constants** near the existing `SETTLE_*` consts (index.html ~line 699):

```javascript
	var CLOCK_PRECISION_MS = 50;     // max accepted offset precision (RTT/2)
	var CLOCK_FRESH_MS = 30000;      // offset must have been (re)locked this recently
```

- [ ] **Step 2: Extract phase stats** — replace the body of `clockSettled()` (index.html ~line 713-722) so it computes std/mean via a small helper that `clockReady()` also calls. Read the current `clockSettled()` first; replace with:

```javascript
	// Returns {n, mean, std} of the recent tick phases, or null if too few samples.
	function phaseStats() {
		var n = _phaseHistory.length, i, sum = 0;
		if (n < SETTLE_SAMPLES) { return null; }
		for (i = 0; i < n; i++) { sum += _phaseHistory[i]; }
		var mean = sum / n, sq = 0;
		for (i = 0; i < n; i++) { var d = _phaseHistory[i] - mean; sq += d * d; }
		return { n: n, mean: mean, std: Math.sqrt(sq / n) };
	}
	function clockSettled() {
		var s = phaseStats();
		return !!s && (s.std <= SETTLE_STDDEV_MS) && (Math.abs(s.mean) <= SETTLE_MEAN_MS);
	}
	// True only when the GoTime offset is FRESH + PRECISE *and* the beat is stable.
	// This is the gate the play-release waits on (see prepare/READY path).
	function clockReady() {
		var s = phaseStats();
		if (!s) { return false; }
		return GoTime.readyVerdict(GoTime.getPrecision(), GoTime.msSinceAccept(),
			s.std, s.mean,
			{ maxPrecisionMs: CLOCK_PRECISION_MS, maxAgeMs: CLOCK_FRESH_MS,
			  maxStdMs: SETTLE_STDDEV_MS, maxMeanMs: SETTLE_MEAN_MS });
	}
```

(Preserve `recordTickPhase` and the `clockSettled()` call sites — `updateHeartbeat` at ~line 744 still calls `clockSettled()`, now backed by `phaseStats`. Behavior of the heartbeat color is unchanged.)

- [ ] **Step 3: Verify it still loads** — `node --test tests/unit/js/*.js` (module-load smoke; index.html isn't imported by node tests, so this only confirms nothing else broke). Manually sanity-check the file parses by opening the dev server `/` (or `node -e "require('fs').readFileSync('index.html','utf8')"` is trivial; the real check is the e2e in Task 6). Re-read the edited region for balanced braces + ES5 (no `const`/arrow).

- [ ] **Step 4: Commit**
```bash
git add index.html
git commit -m "feat(client): clockReady() = fresh+precise GoTime offset AND stable beat (clockSettled)"
```

---

## Task 3: PREPARE kicks resync; gate READY on `clockReady()` (`index.html`)

**Files:** Modify `index.html` (inline ES5 — `prepareFirstItem` ~line 331 and the `recv-PREPARE` handler ~line 872).

**Context:** `prepareFirstItem(prepareId)` reports READY via `reportReady()` (armed video) or an immediate `sendMsg("READY", {prepareId})` (no-video). We replace direct READY sends with a clock-gated sender, and kick a fresh resample when PREPARE arrives.

- [ ] **Step 1: Add the clock-gated READY sender** — define near `prepareFirstItem` (so it's in scope):

```javascript
	// Emit READY only once the clock is converged (clockReady). Re-checks on a
	// short timer until then; the server's extended PREPARE_TIMEOUT is the
	// bounded fallback if a client never converges. Cancelled on STOP / new PREPARE.
	function sendReadyWhenClockReady(prepareId) {
		if (playback.readyWaitTimer) { clearTimeout(playback.readyWaitTimer); playback.readyWaitTimer = null; }
		function attempt() {
			// Bail if this PREPARE is no longer current (stopped / superseded).
			if (playback.prepareId !== prepareId) { return; }
			if (clockReady()) {
				dbg("send-READY");
				sendMsg("READY", { prepareId: prepareId });
				return;
			}
			dbg("hold-READY-clock");
			playback.readyWaitTimer = setTimeout(attempt, 500);
		}
		attempt();
	}
```

(Requires `playback.prepareId` to hold the current prepareId — confirm the PREPARE handler stores it (it sets `playback.startEpoch` etc.); if not already stored, set `playback.prepareId = prepareId` in the PREPARE handler. Read the handler and wire it.)

- [ ] **Step 2: Route the two READY emissions through it** — in `prepareFirstItem`:
  - Replace the no-video early return `if (!item) { sendMsg("READY", { prepareId: prepareId }); return; }` with:
    ```javascript
    if (!item) { sendReadyWhenClockReady(prepareId); return; }  // no video to arm; still wait for clock
    ```
  - Replace `var reportReady = function () { dbg("send-READY"); sendMsg("READY", { prepareId: prepareId }); };` with:
    ```javascript
    var reportReady = function () { sendReadyWhenClockReady(prepareId); };
    ```
  (The `NEEDS_ARM` path is unchanged — a gesture is still required and still holds GO; once armed, `onArmed`→`reportReady`→clock-gated READY applies.)

- [ ] **Step 3: Kick a fresh resync on PREPARE** — in the `recv-PREPARE` handler (~line 872, where `dbg("recv-PREPARE")` / `playback.startEpoch = ...` happen), add right after PREPARE is accepted (and `playback.prepareId` is set):

```javascript
				if (typeof GoTime.resync === 'function') { GoTime.resync(4, 400); }
```

- [ ] **Step 4: Cancel the wait on STOP / new PREPARE** — in `stopPlayback()` (and at the top of the PREPARE handler before re-arming), add:
```javascript
		if (playback.readyWaitTimer) { clearTimeout(playback.readyWaitTimer); playback.readyWaitTimer = null; }
```
(Read `stopPlayback` + the PREPARE handler; place the cancel so a superseded/stopped prepare can't later emit a stale READY. The `playback.prepareId !== prepareId` guard in `attempt()` is the backstop.)

- [ ] **Step 5: Verify** — `node --test tests/unit/js/*.js` (nothing else broke). Re-read edits for ES5 + balanced braces. Functional verification is the Task 6 e2e + manual tdbg.

- [ ] **Step 6: Commit**
```bash
git add index.html
git commit -m "feat(client): hold READY until clockReady; kick GoTime.resync on PREPARE (all content)"
```

---

## Task 4: Extend `tdbg` with clock metrics (`index.html`)

**Files:** Modify `index.html` (`dbg()` ~line 300).

- [ ] **Step 1: Add clock fields to the `dbg()` payload** — in `dbg(tag, extra)`, after the existing `elapsed:` line in the `payload` object, add:

```javascript
			off: (typeof GoTime.getOffset === 'function') ? GoTime.getOffset() : null,
			prec: (typeof GoTime.getPrecision === 'function') ? GoTime.getPrecision() : null,
			accAge: (typeof GoTime.msSinceAccept === 'function') ?
				(GoTime.msSinceAccept() === Infinity ? null : Math.round(GoTime.msSinceAccept())) : null,
			phStd: (function () { var s = phaseStats(); return s ? Math.round(s.std) : null; })(),
			phMean: (function () { var s = phaseStats(); return s ? Math.round(s.mean) : null; })(),
			synced: (typeof ProgrammableTimer !== 'undefined') ? ProgrammableTimer.isSynced() : null,
			settled: clockSettled(),
			cready: clockReady()
```

(Add a comma after the existing `elapsed: ...` entry. `phaseStats`/`clockSettled`/`clockReady` are defined in Task 2; `dbg` must be able to call them — confirm scope (same inline script). All ES5.)

- [ ] **Step 2: Verify** — `node --test tests/unit/js/*.js` (unaffected). The payload is exercised live in Task 6 (`?tdbg` → server `CLIENTLOG` log lines show `prec`/`accAge`/`phStd`/`phMean`/`settled`/`cready`).

- [ ] **Step 3: Commit**
```bash
git add index.html
git commit -m "feat(client): tdbg payload carries clock metrics (off/prec/accAge/phStd/phMean/synced/settled/cready)"
```

---

## Task 5: Extend PREPARE_TIMEOUT; assert release of armed-but-not-READY (server)

**Files:** Modify `server.py:121`; Test `tests/unit/test_sync_release.py` (new).

**Context:** With Task 3, an armed client withholds READY until clock-ready, so during the (now longer) window it is in **neither** `readyClients` nor `armPending`. `_maybe_release` must NOT release it early (needs all-READY); `_release_expired_prepares` must release best-effort after the deadline (it already does, since `armPending` is empty for such a client). This task raises the timeout and locks the behavior with tests. These helpers run synchronously (no `asyncio.ensure_future`), unlike `_begin_prepare`, so they're unit-testable directly.

- [ ] **Step 1: Write the failing test** (`tests/unit/test_sync_release.py`):

```python
"""Sync-gated release: a client that armed but is withholding READY (clock not
yet converged) is NOT released early by _maybe_release, but IS released best-
effort by _release_expired_prepares after the (extended) PREPARE timeout."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import argparse
_orig = argparse.ArgumentParser.parse_args
class _MockArgs:
    Port = 3000
    Verbose = False
argparse.ArgumentParser.parse_args = lambda self, a=None, n=None: _MockArgs()
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

import time
import pytest
from mosaicmesh.state import Settings, Display, Client, PlayState


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev


def _preparing_group(settings):
    d = Display(); d.action = PlayState.PREPARING
    d.readyClients = set(); d.armPending = set()
    settings.displays["G1"] = d
    c = Client(); c.displayID = "G1"; c.isOnline = True; c.synced = True
    settings.clients["c1"] = c
    return d


def test_maybe_release_holds_until_ready(fresh_settings, monkeypatch):
    d = _preparing_group(fresh_settings)
    released = []
    monkeypatch.setattr(server, "_release_group", lambda did: released.append(did))
    # Client online but not in readyClients (armed, withholding READY on clock).
    server._maybe_release("G1")
    assert released == []                      # not all-ready -> no GO
    d.readyClients.add("c1")
    server._maybe_release("G1")
    assert released == ["G1"]                   # now all-ready -> GO


def test_expired_prepare_releases_best_effort(fresh_settings, monkeypatch):
    d = _preparing_group(fresh_settings)
    released = []
    monkeypatch.setattr(server, "_release_group", lambda did: released.append(did))
    # Deadline in the past, client NOT ready and NOT armPending (clock-holding).
    d.prepareDeadline = int(time.time() * 1000) - 1
    server._release_expired_prepares()
    assert released == ["G1"]                   # best-effort release after timeout


def test_expired_prepare_waits_for_arm_tap(fresh_settings, monkeypatch):
    d = _preparing_group(fresh_settings)
    released = []
    monkeypatch.setattr(server, "_release_group", lambda did: released.append(did))
    d.armPending.add("c1")                      # still awaiting a human tap
    d.prepareDeadline = int(time.time() * 1000) - 1
    server._release_expired_prepares()
    assert released == []                       # NEEDS_ARM hold preserved
```

- [ ] **Step 2: Run to fail/pass baseline** — `python -m pytest tests/unit/test_sync_release.py -c tests/pytest.ini -v`. These assert *existing* release semantics still hold under the new client behavior; they should PASS against current code (the contract Task 3 relies on). If any fails, the release logic needs adjustment — investigate before changing the timeout. (This is a guard test: it pins the contract the client change depends on.)

- [ ] **Step 3: Extend the timeout** — `server.py:121`, change:
```python
PREPARE_TIMEOUT_MS = 25000  # Safety-net timeout for SILENT/stuck clients only. A
```
to:
```python
PREPARE_TIMEOUT_MS = 45000  # Safety-net timeout. Lengthened (was 25000) so PSM-
# jittery clocks have time to re-converge after the PREPARE resync burst before
# the best-effort release (sync-gated play). Covers SILENT/stuck clients too.
```
(Keep the rest of the original comment that follows on subsequent lines.)

- [ ] **Step 4: Run to pass + regression** — `python -m pytest tests/unit/test_sync_release.py -c tests/pytest.ini -v` (3 pass). Then `python -m pytest tests/unit -c tests/pytest.ini -q 2>&1 | tail -3` → failed count must stay at the ~14 baseline.

- [ ] **Step 5: Commit**
```bash
git add server.py tests/unit/test_sync_release.py
git commit -m "feat(sync): extend PREPARE_TIMEOUT to 45s; pin armed-but-not-READY release contract"
```

---

## Task 6: e2e/manual verification + docs

**Files:** `tests/e2e/` (extend an existing coordinated-start spec if present, else document manual), `CLAUDE.md`.

- [ ] **Step 1: e2e (best-effort).** Coordinated start needs real clients (the e2e harness drives the admin, not display clients), so a full sync e2e isn't practical headless. READ `tests/e2e/run.js` + existing specs. IF an existing spec already simulates a display client / PLAY path, add a light assertion that the server does not broadcast GO while a client withholds READY. Otherwise, do NOT fabricate a brittle harness — record the manual verification procedure (Step 2) in the spec/docs and rely on the server unit tests (Task 5) + the GoTime unit tests (Task 1). Report which path you took.

- [ ] **Step 2: Manual verification procedure (document in CLAUDE.md or the spec).** With the dev server + a display client at `?tdbg`:
  1. Load a display client with `?tdbg`; watch the server log `CLIENTLOG` lines.
  2. Issue PLAY for that group; confirm the log shows `recv-PREPARE` then repeated `hold-READY-clock` with `cready:false` while `accAge`/`prec`/`phStd` settle, then `send-READY` with `cready:true`.
  3. Confirm GO (playback start) only after READY; on a 2nd client, confirm both start together.
  4. Confirm a client that can't converge is released after ~45s (`PREPARE timeout` log line).

- [ ] **Step 3: Docs** — add to `CLAUDE.md` Conventions:

```
- **Coordinated start is clock-gated (sync-gated play).** A display client emits READY (which the server's GO waits on for all online clients) only when `clockReady()` — a FRESH + PRECISE GoTime offset (`GoTime.msSinceAccept()`/`getPrecision()`) AND a stable beat (`clockSettled()` phase std-dev). PREPARE triggers `GoTime.resync(4,400)` so the offset isn't up-to-15-min stale. This holds PLAY until synced for ALL content (video + animation + image), since position derives from `GoTime.now()-startEpoch`. `PREPARE_TIMEOUT_MS` (45s) is the bounded best-effort fallback. `?tdbg` payloads carry `off/prec/accAge/phStd/phMean/synced/settled/cready` for per-screen convergence diagnosis. Mid-playback re-sync cadence (GoTime's 15-min interval) is a known follow-up.
```

- [ ] **Step 4: Commit**
```bash
git add CLAUDE.md tests/e2e/ 2>/dev/null; git commit -m "docs(sync): document sync-gated play + manual tdbg verification"
```

- [ ] **Step 5: Final review + finish** — dispatch a final review across the change, then `superpowers:finishing-a-development-branch`.

---

## Self-Review

**Spec coverage:**
- Part 1 (GoTime resync + msSinceAccept): Task 1. ✓
- Part 2 (PREPARE resync + clockReady gate on READY): Task 2 (`clockReady`) + Task 3 (resync + READY gating). ✓
- Part 3 (extended PREPARE_TIMEOUT, release unchanged): Task 5. ✓
- Part 4 (tdbg clock metrics): Task 4. ✓
- "clock-ready = fresh+precise AND settled": `readyVerdict` (Task 1) + `clockReady` (Task 2). ✓
- Bounded fallback (extend window): Task 5 (45s) + the release contract tests. ✓
- All-content coverage: Task 3 routes BOTH the no-video (animation/image) and armed-video READY paths through `sendReadyWhenClockReady`. ✓
- Non-goal (mid-playback cadence): untouched; documented as follow-up (Task 6 docs). ✓

**Placeholder scan:** none. Client-wiring steps say "read the handler and confirm `playback.prepareId` is stored / place the cancel" — these are concrete integration instructions against named functions, not vague TODOs; the code to add is given in full.

**Type consistency:** `readyVerdict(precisionMs, accAgeMs, phaseStd, phaseMean, opts)` consistent (Task 1 def ↔ Task 2 call). `phaseStats()` returns `{n,mean,std}` used by both `clockSettled` and `clockReady` (Task 2) and `dbg` (Task 4). `clockReady()`/`clockSettled()`/`GoTime.resync`/`GoTime.msSinceAccept`/`GoTime.getPrecision`/`GoTime.getOffset` names consistent across Tasks 1–4. `sendReadyWhenClockReady(prepareId)` consistent (Task 3). Server `PREPARE_TIMEOUT_MS` (Task 5) matches server.py:121.

**Testing honesty:** GoTime decision logic is genuinely unit-tested (Task 1 `vm` harness); server release contract is unit-tested (Task 5); the inline-ES5 wiring (Tasks 2–4) is verified via `tdbg`/manual/e2e per the spec's acknowledged constraint — not faked with a brittle harness.
