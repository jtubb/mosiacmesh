# Animation Clock-Drift Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Steer `GoTime._offset` toward the median of recent precision-gated clock samples with a deadband/slew/snap rule, so long-running playlists stay synced across the wall (animations and video) without single-sample re-lock jitter.

**Architecture:** Two pure helpers in `js/GoTime.js` (`_steerStep`, `_robustTarget`) plus a `steerTick()` that assembles them, a sample ring fed by `_reviseOffset`, and a `_steering` hand-off flag that transfers `_offset` ownership from the precision ratchet (initial lock) to the slew (maintenance). The tick is driven from the existing `ProgrammableTimer` beat in `index.html`; sample cadence is tightened in `js/mosiacmesh.js`. `driftTick` stays a pure follower (invariant).

**Tech Stack:** ES5 JavaScript (iPad-1 / Safari 5.1 — no `let`/`const`/arrow/`class`); Node `--test` for the pure-function unit suites (vm-sandbox harness, mirroring `tests/unit/js/gotime-ready.test.js`).

**Reference spec:** `docs/superpowers/specs/2026-06-22-animation-clock-drift-design.md`

**Pre-existing uncommitted change (item A):** `index.html` already contains the pre-sync render guard (`clockEverReady` latch in `runScriptLoop`). It is intentionally left uncommitted and is folded into Task 5's commit.

---

## File Structure

- `js/GoTime.js` (modify) — new steer options + ring + `_steerStep`/`_robustTarget`/`steerTick`/`getSteerState`; `_reviseOffset` records samples and gates the ratchet on `_steering`.
- `index.html` (modify) — call `GoTime.steerTick()` from the `ProgrammableTimer` beat callback; one-line follower-invariant comment on `driftTick`. (Also carries item A.)
- `js/mosiacmesh.js` (modify) — `SyncInterval: 60000` → `30000`.
- `tests/unit/js/gotime-steer.test.js` (create) — node `--test` suite for `_steerStep`, `_robustTarget`, ring recording, and `steerTick` hand-off/slew.

### Shared test harness (used by every test step below)

All test steps in this plan begin the file with this exact harness (a superset of
`gotime-ready.test.js`'s loader — adds `__setNow` so the fake local clock can advance):

```js
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

function loadGoTime() {
  const code = fs.readFileSync(new URL('../../../js/GoTime.js', import.meta.url), 'utf8');
  let now = 1000000;
  function FakeDate() {}
  FakeDate.now = function () { return now; };
  FakeDate.prototype.getTime = function () { return now; };
  const sandbox = {
    Date: FakeDate,
    setTimeout: function () { return 0; },
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

function smp(offset, precision, t) { return { offset: offset, precision: precision, t: t }; }
```

> Note: GoTime.js assigns its private functions as bare globals (e.g. `_reviseOffset = function…`), so inside the vm sandbox they are reachable as `s._reviseOffset(...)`. Tests use that to seed the ring deterministically.

---

### Task 1: `_steerStep` pure helper

**Files:**
- Modify: `js/GoTime.js`
- Test: `tests/unit/js/gotime-steer.test.js`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/js/gotime-steer.test.js` with the shared harness above, then append:

```js
const O = { deadbandMs: 33, snapMs: 500, capMsPerSec: 15 };

test('_steerStep: error within deadband -> unchanged', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime._steerStep(1000, 1020, 1000, O), 1000); // err 20 <= 33
});

test('_steerStep: slews toward target, capped by rate*dt', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime._steerStep(1000, 1200, 1000, O), 1015); // +err 200, cap 15ms/1s
  assert.equal(s.GoTime._steerStep(1000, 800, 1000, O), 985);   // -err 200, cap 15ms/1s
  assert.equal(s.GoTime._steerStep(1000, 1200, 2000, O), 1030); // dt 2s -> cap 30ms
});

test('_steerStep: snaps on large error', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime._steerStep(1000, 1600, 1000, O), 1600); // err 600 >= 500
});

test('_steerStep: slew can never reverse now() (|move| < dt)', () => {
  const s = loadGoTime();
  const dt = 1000;
  const out = s.GoTime._steerStep(1000, 1400, dt, O); // err 400 < snap -> slew
  assert.ok(Math.abs(out - 1000) < dt);               // 15 < 1000
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/gotime-steer.test.js`
Expected: FAIL — `s.GoTime._steerStep is not a function`.

- [ ] **Step 3: Implement `_steerStep`**

In `js/GoTime.js`, add this private function alongside the other privates (e.g. right after `_calculateOffset = function(...) {...};`):

```js
    // Pure hybrid correction step: deadband (ignore tiny error) / bounded slew /
    // snap (large error only). dtMs = wall ms since the previous step. opts:
    // {deadbandMs, snapMs, capMsPerSec}. Returns the new offset (float).
    _steerStep = function(offset, target, dtMs, opts) {
        var dead = (opts && opts.deadbandMs != null) ? opts.deadbandMs : 33;
        var snap = (opts && opts.snapMs != null) ? opts.snapMs : 500;
        var cap = (opts && opts.capMsPerSec != null) ? opts.capMsPerSec : 15;
        var err = target - offset;
        var aerr = err < 0 ? -err : err;
        if (aerr <= dead) { return offset; }
        if (aerr >= snap) { return target; }
        var maxMove = cap * (dtMs / 1000);
        var move = err;
        if (move > maxMove) { move = maxMove; }
        else if (move < -maxMove) { move = -maxMove; }
        return offset + move;
    };
```

Then expose it on the returned object: in the `return { ... }` block (where `readyVerdict` lives), add a member:

```js
        _steerStep: _steerStep,
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/gotime-steer.test.js`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add js/GoTime.js tests/unit/js/gotime-steer.test.js
git commit -m "feat(gotime): pure _steerStep (deadband/slew/snap) for drift correction"
```

---

### Task 2: `_robustTarget` pure helper

**Files:**
- Modify: `js/GoTime.js`
- Test: `tests/unit/js/gotime-steer.test.js`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/js/gotime-steer.test.js`:

```js
const RT = { windowMs: 120000, minSamples: 3, precisionFloorMs: 60 };

test('_robustTarget: median of in-window gated samples', () => {
  const s = loadGoTime(); const now = 200000;
  const samples = [smp(100, 10, now - 1000), smp(110, 12, now - 2000), smp(105, 11, now - 3000)];
  assert.equal(s.GoTime._robustTarget(samples, now, RT), 105); // median[100,105,110]
});

test('_robustTarget: excludes high-RTT (gated) outliers', () => {
  const s = loadGoTime(); const now = 200000;
  // best precision 10 -> gate = max(20, 60) = 60; the 200-precision sample is dropped
  const samples = [smp(100, 10, now - 1000), smp(110, 12, now - 2000),
                   smp(106, 11, now - 3000), smp(9999, 200, now - 1500)];
  assert.equal(s.GoTime._robustTarget(samples, now, RT), 106); // median[100,106,110]
});

test('_robustTarget: excludes out-of-window samples', () => {
  const s = loadGoTime(); const now = 500000;
  const samples = [smp(100, 10, now - 1000), smp(110, 12, now - 2000), smp(105, 11, now - 999999)];
  assert.equal(s.GoTime._robustTarget(samples, now, RT), null); // only 2 in window < 3
});

test('_robustTarget: below minSamples -> null', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime._robustTarget([smp(100, 10, 99000), smp(110, 12, 98000)], 100000, RT), null);
});

test('_robustTarget: empty -> null', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime._robustTarget([], 100000, RT), null);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/gotime-steer.test.js`
Expected: FAIL — `s.GoTime._robustTarget is not a function`.

- [ ] **Step 3: Implement `_robustTarget`**

In `js/GoTime.js`, add the private function near `_steerStep`:

```js
    // Pure robust target offset: median of recent (within windowMs) samples whose
    // precision passes a RELATIVE gate (max(2*bestInWindow, precisionFloorMs)), so a
    // single PSM-jittery high-RTT sample can't move it. Returns null when fewer than
    // minSamples pass. samples: [{offset, precision, t}]. nowMs: GoTime.now().
    _robustTarget = function(samples, nowMs, opts) {
        var windowMs = (opts && opts.windowMs != null) ? opts.windowMs : 120000;
        var minS = (opts && opts.minSamples != null) ? opts.minSamples : 3;
        var floor = (opts && opts.precisionFloorMs != null) ? opts.precisionFloorMs : 60;
        var i, recent = [];
        for (i = 0; i < samples.length; i++) {
            if (samples[i].t >= nowMs - windowMs) { recent.push(samples[i]); }
        }
        if (recent.length === 0) { return null; }
        var best = recent[0].precision;
        for (i = 1; i < recent.length; i++) {
            if (recent[i].precision < best) { best = recent[i].precision; }
        }
        var gate = 2 * best;
        if (gate < floor) { gate = floor; }
        var kept = [];
        for (i = 0; i < recent.length; i++) {
            if (recent[i].precision <= gate) { kept.push(recent[i].offset); }
        }
        if (kept.length < minS) { return null; }
        kept.sort(function(a, b) { return a - b; });
        var h = Math.floor(kept.length / 2);
        return (kept.length % 2) ? kept[h] : (kept[h - 1] + kept[h]) / 2;
    };
```

Expose it in the `return { ... }` block:

```js
        _robustTarget: _robustTarget,
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/gotime-steer.test.js`
Expected: PASS (9 tests total).

- [ ] **Step 5: Commit**

```bash
git add js/GoTime.js tests/unit/js/gotime-steer.test.js
git commit -m "feat(gotime): pure _robustTarget (windowed, precision-gated median)"
```

---

### Task 3: Steer options + sample ring + `getSteerState` + `setOptions` plumbing

**Files:**
- Modify: `js/GoTime.js`
- Test: `tests/unit/js/gotime-steer.test.js`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/js/gotime-steer.test.js`:

```js
test('getSteerState: initial state', () => {
  const s = loadGoTime();
  const st = s.GoTime.getSteerState();
  assert.equal(st.steering, false);
  assert.equal(st.samples, 0);
  assert.equal(st.offset, 0);
});

test('setOptions: steer knobs are accepted', () => {
  const s = loadGoTime();
  s.GoTime.setOptions({ SteerCapMsPerSec: 25, SteerMinSamples: 2 });
  // no throw; state still queryable
  assert.equal(s.GoTime.getSteerState().samples, 0);
});

test('_reviseOffset records samples into the ring', () => {
  const s = loadGoTime();
  s._reviseOffset({ offset: 100, precision: 10 }, 't');
  s._reviseOffset({ offset: 110, precision: 12 }, 't');
  assert.equal(s.GoTime.getSteerState().samples, 2);
});

test('ring is bounded (does not grow without limit)', () => {
  const s = loadGoTime();
  for (var i = 0; i < 200; i++) { s._reviseOffset({ offset: 100, precision: 10 }, 't'); }
  assert.ok(s.GoTime.getSteerState().samples <= 64);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/gotime-steer.test.js`
Expected: FAIL — `s.GoTime.getSteerState is not a function`.

- [ ] **Step 3: Add options, ring recording, and `getSteerState`**

3a. In the `options` object literal at the top of `GoTime`, add these fields (after `_wsRequestTime: null` — add a comma to that line):

```js
        _steerSamples: [],
        _steering: false,
        _lastSteerAt: null,
        _lastSteerTarget: null,
        _steerDeadbandMs: 33,
        _steerSnapMs: 500,
        _steerCapMsPerSec: 15,
        _steerWindowMs: 120000,
        _steerMinSamples: 3,
        _steerPrecisionFloorMs: 60
```

3b. In `_reviseOffset`, immediately after `options._lastSyncMethod = method;`, record the sample into the ring:

```js
        options._steerSamples.push({ offset: sample.offset, precision: sample.precision, t: timestamp });
        while (options._steerSamples.length > 64) { options._steerSamples.shift(); }
```

3c. In `setOptions`, after the existing `SyncInterval` block, add:

```js
            if (opts.SteerDeadbandMs != null) { options._steerDeadbandMs = opts.SteerDeadbandMs; }
            if (opts.SteerSnapMs != null) { options._steerSnapMs = opts.SteerSnapMs; }
            if (opts.SteerCapMsPerSec != null) { options._steerCapMsPerSec = opts.SteerCapMsPerSec; }
            if (opts.SteerWindowMs != null) { options._steerWindowMs = opts.SteerWindowMs; }
            if (opts.SteerMinSamples != null) { options._steerMinSamples = opts.SteerMinSamples; }
            if (opts.SteerPrecisionFloorMs != null) { options._steerPrecisionFloorMs = opts.SteerPrecisionFloorMs; }
```

3d. In the `return { ... }` block, add the getter (also used by `?tdbg`):

```js
        getSteerState: function() {
            return { steering: options._steering, samples: options._steerSamples.length,
                     offset: options._offset, target: options._lastSteerTarget };
        },
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/gotime-steer.test.js`
Expected: PASS (13 tests total).

- [ ] **Step 5: Commit**

```bash
git add js/GoTime.js tests/unit/js/gotime-steer.test.js
git commit -m "feat(gotime): steer options, sample ring, getSteerState"
```

---

### Task 4: Ratchet hand-off + `steerTick`

**Files:**
- Modify: `js/GoTime.js`
- Test: `tests/unit/js/gotime-steer.test.js`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/js/gotime-steer.test.js`:

```js
test('steerTick: no usable samples -> no-op, stays un-steering', () => {
  const s = loadGoTime();
  s.GoTime.steerTick();
  const st = s.GoTime.getSteerState();
  assert.equal(st.steering, false);
  assert.equal(st.offset, 0);
});

test('steerTick: hand-off then bounded slew (ratchet stops moving offset)', () => {
  const s = loadGoTime();
  s.GoTime.setOptions({ SteerMinSamples: 3, SteerCapMsPerSec: 15,
    SteerWindowMs: 120000, SteerDeadbandMs: 33, SteerSnapMs: 500 });
  // 3 good samples at offset 1000 -> ratchet locks _offset to 1000
  s._reviseOffset({ offset: 1000, precision: 10 }, 't');
  s._reviseOffset({ offset: 1000, precision: 10 }, 't');
  s._reviseOffset({ offset: 1000, precision: 10 }, 't');
  s.GoTime.steerTick();                       // target 1000, err 0 -> flips steering, no move
  let st = s.GoTime.getSteerState();
  assert.equal(st.steering, true);
  assert.equal(st.offset, 1000);
  // New truth drifts to 1300; with steering on, the ratchet must NOT jump _offset
  s._reviseOffset({ offset: 1300, precision: 10 }, 't');
  s._reviseOffset({ offset: 1300, precision: 10 }, 't');
  s._reviseOffset({ offset: 1300, precision: 10 }, 't');
  assert.equal(s.GoTime.getSteerState().offset, 1000); // ratchet disabled by hand-off
  s.__setNow(1001000);                        // advance local clock 1s so dt = 1000
  s.GoTime.steerTick();                        // median[1000,1000,1000,1300,1300,1300]=1150; err 150; slew 15
  assert.equal(s.GoTime.getSteerState().offset, 1015);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/gotime-steer.test.js`
Expected: FAIL — `s.GoTime.steerTick is not a function`.

- [ ] **Step 3: Implement the hand-off and `steerTick`**

3a. Gate the ratchet's `_offset` write on `!_steering` while still tracking `_precision`. Replace the existing block in `_reviseOffset`:

```js
        if (sample.precision <= options._precision) {
            options._offset = Math.round(sample.offset);
            options._precision = sample.precision;
            options._lastAcceptTime = timestamp;
        }
```

with:

```js
        if (sample.precision <= options._precision) {
            options._precision = sample.precision;
            options._lastAcceptTime = timestamp;
            // Initial lock only: once steering owns the offset, a late better-precision
            // sample must NOT step it (would defeat the smooth slew).
            if (!options._steering) { options._offset = Math.round(sample.offset); }
        }
```

3b. Add `steerTick` as a private function near `_steerStep`/`_robustTarget`:

```js
    // One maintenance iteration: pull _offset toward the robust target via _steerStep.
    // Flips _steering true the first time a target is available, transferring offset
    // ownership from the ratchet to the slew. dtMs is real local elapsed time, so a
    // skipped beat can't over-correct beyond cap*dt. getAccurateTimestamp() is the raw
    // (offset-free) local clock, so dt is immune to offset changes.
    _steerTick = function() {
        var nowLocal = getAccurateTimestamp();
        var dtMs = (options._lastSteerAt == null) ? 1000 : (nowLocal - options._lastSteerAt);
        options._lastSteerAt = nowLocal;
        if (dtMs <= 0) { return; }
        var opts = {
            deadbandMs: options._steerDeadbandMs, snapMs: options._steerSnapMs,
            capMsPerSec: options._steerCapMsPerSec, windowMs: options._steerWindowMs,
            minSamples: options._steerMinSamples, precisionFloorMs: options._steerPrecisionFloorMs
        };
        var target = _robustTarget(options._steerSamples, GoTime.now(), opts);
        if (target === null) { return; }
        options._steering = true;
        options._lastSteerTarget = target;
        options._offset = Math.round(_steerStep(options._offset, target, dtMs, opts));
    };
```

3c. Expose it in the `return { ... }` block:

```js
        steerTick: function() { return _steerTick(); },
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/gotime-steer.test.js`
Expected: PASS (15 tests total).

- [ ] **Step 5: Commit**

```bash
git add js/GoTime.js tests/unit/js/gotime-steer.test.js
git commit -m "feat(gotime): steerTick + ratchet->slew hand-off (_steering)"
```

---

### Task 5: Wire the beat, tighten cadence, confirm follower invariant, fold item A

**Files:**
- Modify: `index.html` (beat hook + `driftTick` invariant comment; also carries item A's pre-sync guard)
- Modify: `js/mosiacmesh.js:197`

- [ ] **Step 1: Confirm the `driftTick` follower invariant (read-only)**

Run: `grep -nE "_offset|GoTime\." index.html | grep -i drift` and inspect `driftTick` (around `index.html:633`). Verify it only **reads** `GoTime.now()` and writes the video element + `playback.seekAheadMs` — it must NOT assign `GoTime._offset` or call any GoTime setter.
Expected: no `_offset` writes inside `driftTick`. (If any exist, STOP — the design's no-compounding invariant is violated.)

- [ ] **Step 2: Add the invariant comment to `driftTick`**

In `index.html`, on the line directly above `function driftTick() {`, append to the existing comment block:

```js
	// INVARIANT (clock-drift design): driftTick is a pure FOLLOWER of GoTime.now() —
	// it never writes GoTime._offset. The offset is steered separately (GoTime.steerTick),
	// and steerCapMsPerSec stays well under this controller's authority, so the two layer
	// (reference-correction beneath element-follow) and never compound.
```

- [ ] **Step 3: Drive `steerTick` from the synchronized beat**

In `index.html`'s `tickcallback`, immediately after the `triggerPulse();` line (~`index.html:45`), add:

```js
        if (typeof GoTime !== 'undefined' && GoTime.steerTick) { GoTime.steerTick(); }
```

- [ ] **Step 4: Tighten the sample cadence**

In `js/mosiacmesh.js`, change line 197 inside the `GoTime.setOptions({ ... })` call:

```js
		SyncInterval: 30000
```

(was `60000`; a ~120 s window then holds ~4 samples for the median.)

- [ ] **Step 5: Run the full JS suite + ES5 lint**

Run: `node --test tests/unit/js/*.js`
Expected: all pass (327 prior + 15 new steer tests).

Run: `grep -nE "\b(let|const|class)\b|=>" js/GoTime.js js/mosiacmesh.js` over the edited regions and confirm no non-ES5 tokens were introduced (the new code uses only `var`/`function`/ternary). Backticks/`=>` inside comments are fine.

- [ ] **Step 6: Commit (folds in item A)**

`git add index.html` here picks up BOTH the beat hook + invariant comment AND item A's already-present pre-sync `clockEverReady` guard — that is the intended fold.

```bash
git add index.html js/mosiacmesh.js
git commit -m "feat(gotime): drive steerTick from beat, 30s cadence; pre-sync render guard (item A)"
```

---

### Task 6: iPad-1 on-wall sign-off (manual)

**Files:** none (verification only)

- [ ] **Step 1: Deploy + reload**

Client-side only (`js/GoTime.js`, `js/mosiacmesh.js`, `index.html`) — no server restart needed (static files are mtime-cached). Reload the display clients.

- [ ] **Step 2: Verify pre-sync guard (item A)**

Reload one display; confirm it shows the item background (black) until its clock converges, then joins playback at the correct synced position — no wrong-position-then-snap.

- [ ] **Step 3: Verify long-run drift (item B)**

Play a long-running animation playlist (e.g. **Transition Test**) for 15–30 min across the wall. Confirm screens stay visually synchronized with no progressive divergence, and no visible per-frame stepping during correction. Optionally append `?tdbg` and watch `GoTime.getSteerState()` (`steering:true`, `offset` slewing in small steps).

- [ ] **Step 4: Confirm video unaffected**

Play a SEGMENT/INDIVIDUAL video item; confirm `driftTick` still keeps it synced (no fighting, no oscillation) now that the offset it follows is being gently steered.

---

## Self-Review

**Spec coverage:** sample ring (T3) ✓; `_robustTarget` median/gate/window/min (T2) ✓; `_steerStep` deadband/slew/snap + monotonicity bound (T1) ✓; ratchet hand-off `_steering` (T4) ✓; `steerTick` on the beat (T4 impl + T5 wiring) ✓; cadence 60000→30000 + window 120000 (T5 + T3 default) ✓; `setOptions` plumbing (T3) ✓; `driftTick` follower invariant (T5 check + comment) ✓; pure helpers exposed for node tests (T1/T2) ✓; item A fold (T5) ✓; on-wall acceptance (T6) ✓.

**Placeholder scan:** none — every code/edit step shows exact code and exact run/expected output.

**Type/name consistency:** `_steerStep(offset, target, dtMs, opts)` and `_robustTarget(samples, nowMs, opts)` signatures, the `opts` keys (`deadbandMs/snapMs/capMsPerSec/windowMs/minSamples/precisionFloorMs`), the `options._steer*` fields, the `Steer*` setOptions keys, and `getSteerState`/`steerTick`/`_steerTick` names are used identically across tasks.
