# Frequency-disciplined GoTime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct each iPad-1's oscillator skew so `GoTime.now()` advances at the server's rate, eliminating cross-screen drift-apart, via an additive `_skew` term on `now()` with the offset steer left algorithmically untouched.

**Architecture:** `now() = raw + _offset + _skew·(raw − _skewRef)`. A robust Theil-Sen slope estimator (`_skewFit`) reads the existing sample ring; a continuity-preserving fold (`_applySkew`) updates the model without jumping `now()`; the offset steer chases **de-skewed** samples so skew (rate) and steer (offset) don't double-correct. `_skew = 0` ⇒ byte-identical to today.

**Tech Stack:** ES5 client JS (`js/GoTime.js`, iPad-1 Safari 5.1 — no let/const/arrow/class); Node `--test` units loading GoTime in a `vm` sandbox (see `tests/unit/js/gotime-steer.test.js`).

## Global Constraints

- **ES5 only** in `js/GoTime.js` — `var`, function expressions, no arrow/let/const/class/template-literals.
- **Pure helpers are exposed for tests** by adding them to GoTime's public return object (alongside `_steerStep: _steerStep` at `js/GoTime.js:~502`). New helpers must be added there.
- **Skew is a fraction** (ms per ms): 22 ppm = `22e-6`. `_skew = 0` and `_skewRef = 0` are the defaults ⇒ `now()` identical to today.
- **Continuity invariant:** `now()` evaluated at the same raw instant immediately before and after a skew change MUST be equal. A discontinuity is a desync.
- **Bounds:** clamp `_skew` to ±`300e-6`. Skew update deadband `2e-6`. Fit gated on `_steering === true`, `≥6` gated samples spanning `≥60000` ms. On a `null` fit, hold the last good skew.
- **Run tests:** `node --test tests/unit/js/<file>` and the full suite `python pytest_runner.py --js`. Never bare `pytest`.

---

### Task 1: `_skewFit` — robust Theil-Sen slope estimator

**Files:**
- Modify: `js/GoTime.js` — add `_skewFit` near `_robustTarget` (~line 253); add `_skewFit: _skewFit` to the public return object (~line 503)
- Test: `tests/unit/js/gotime-skewfit.test.js` (create)

**Interfaces:**
- Produces: `_skewFit(samples, nowMs, opts) -> Number | null`. `samples`: `[{offset, precision, t}]`. Returns the robust slope (ppm fraction) or `null` when unreliable. `opts`: `{windowMs, skewMinSamples, skewMinSpanMs, precisionFloorMs}`.

- [ ] **Step 1: Write the failing test** — create `tests/unit/js/gotime-skewfit.test.js`:

```js
/**
 * _skewFit: robust Theil-Sen slope of offset-vs-time (the oscillator skew), precision-gated,
 * null unless enough gated samples span enough time baseline. Loaded via the GoTime vm sandbox
 * (same pattern as gotime-steer.test.js), reading the exported _skewFit off the return object.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

function loadGoTime() {
  const code = fs.readFileSync(new URL('../../../js/GoTime.js', import.meta.url), 'utf8');
  const sandbox = { Date: function () {}, setTimeout: function () { return 0; },
    setInterval: function () { return 0; }, clearTimeout: function () {},
    XMLHttpRequest: function () { this.open = function () {}; this.send = function () {}; },
    console: { log: function () {} }, window: {} };
  sandbox.Date.now = function () { return 0; };
  vm.createContext(sandbox); vm.runInContext(code, sandbox);
  return sandbox;
}
const GT = loadGoTime().GoTime;
function smp(offset, precision, t) { return { offset: offset, precision: precision, t: t }; }
const OPTS = { windowMs: 120000, skewMinSamples: 6, skewMinSpanMs: 60000, precisionFloorMs: 60 };

test('_skewFit recovers a known slope (20ppm) from clean samples', () => {
  var b = 20e-6, samples = [], i;
  for (i = 0; i < 12; i++) { var t = i * 10000; samples.push(smp(1000 + b * t, 10, t)); } // 0..110s
  var s = GT._skewFit(samples, 110000, OPTS);
  assert.ok(s !== null);
  assert.ok(Math.abs(s - b) < 1e-6, 'slope ' + s + ' should be ~' + b);
});

test('_skewFit resists an outlier (Theil-Sen)', () => {
  var b = 20e-6, samples = [], i;
  for (i = 0; i < 12; i++) { var t = i * 10000; samples.push(smp(1000 + b * t, 10, t)); }
  samples[6] = smp(9999, 10, 60000);                    // one wild offset
  var s = GT._skewFit(samples, 110000, OPTS);
  assert.ok(s !== null && Math.abs(s - b) < 3e-6, 'outlier moved slope too much: ' + s);
});

test('_skewFit returns null on too-few gated samples', () => {
  var samples = [smp(1000, 10, 0), smp(1000, 10, 70000)];
  assert.strictEqual(GT._skewFit(samples, 70000, OPTS), null);
});

test('_skewFit returns null on too-short baseline', () => {
  var samples = [], i;                                  // 8 samples but only 8s span
  for (i = 0; i < 8; i++) { samples.push(smp(1000, 10, i * 1000)); }
  assert.strictEqual(GT._skewFit(samples, 7000, OPTS), null);
});

test('_skewFit precision gate drops high-RTT samples', () => {
  var b = 20e-6, samples = [], i;
  for (i = 0; i < 8; i++) { var t = i * 10000; samples.push(smp(1000 + b * t, 8, t)); }   // clean
  for (i = 0; i < 6; i++) { samples.push(smp(1000 + 400 * Math.sin(i), 300, i * 10000)); } // jittery, gated out
  var s = GT._skewFit(samples, 80000, OPTS);
  assert.ok(s !== null && Math.abs(s - b) < 2e-6, 'gate should exclude the 300ms-precision noise: ' + s);
});
```

- [ ] **Step 2: Run the test, verify it fails**

Run: `node --test tests/unit/js/gotime-skewfit.test.js`
Expected: FAIL — `GT._skewFit is not a function`.

- [ ] **Step 3: Implement `_skewFit`** — in `js/GoTime.js`, immediately after the `_robustTarget = function(...) {...};` block (~line 280):

```js
    // Robust oscillator-skew estimate: the Theil-Sen slope (median of pairwise slopes) of
    // offset-vs-time over the recent precision-gated samples. Median-of-slopes rejects PSM
    // jitter outliers (mirrors _robustTarget's robust-median philosophy). Returns the slope
    // (ppm fraction, e.g. 22e-6) or null unless >=skewMinSamples gated samples span
    // >=skewMinSpanMs of time (a slope over a short baseline is noise). Pure.
    _skewFit = function(samples, nowMs, opts) {
        var windowMs = (opts && opts.windowMs != null) ? opts.windowMs : 120000;
        var minS = (opts && opts.skewMinSamples != null) ? opts.skewMinSamples : 6;
        var minSpan = (opts && opts.skewMinSpanMs != null) ? opts.skewMinSpanMs : 60000;
        var floor = (opts && opts.precisionFloorMs != null) ? opts.precisionFloorMs : 60;
        var i, j, recent = [];
        for (i = 0; i < samples.length; i++) {
            if (samples[i].t >= nowMs - windowMs) { recent.push(samples[i]); }
        }
        if (recent.length === 0) { return null; }
        var best = recent[0].precision;
        for (i = 1; i < recent.length; i++) { if (recent[i].precision < best) { best = recent[i].precision; } }
        var gate = 2 * best; if (gate < floor) { gate = floor; }
        var kept = [];
        for (i = 0; i < recent.length; i++) { if (recent[i].precision <= gate) { kept.push(recent[i]); } }
        if (kept.length < minS) { return null; }
        var tmin = kept[0].t, tmax = kept[0].t;
        for (i = 1; i < kept.length; i++) {
            if (kept[i].t < tmin) { tmin = kept[i].t; }
            if (kept[i].t > tmax) { tmax = kept[i].t; }
        }
        if (tmax - tmin < minSpan) { return null; }
        var slopes = [];
        for (i = 0; i < kept.length; i++) {
            for (j = i + 1; j < kept.length; j++) {
                var dt = kept[j].t - kept[i].t;
                if (dt !== 0) { slopes.push((kept[j].offset - kept[i].offset) / dt); }
            }
        }
        if (slopes.length === 0) { return null; }
        slopes.sort(function(a, b) { return a - b; });
        var h = Math.floor(slopes.length / 2);
        return (slopes.length % 2) ? slopes[h] : (slopes[h - 1] + slopes[h]) / 2;
    };
```

Then add to the public return object next to `_steerStep: _steerStep,` (~line 502):

```js
		_skewFit: _skewFit,
```

Also declare `_skewFit` wherever the other internal `_` functions are declared (they are assigned as globals inside the IIFE — follow the existing `_steerStep = function...` style, no `var` needed since the file assigns them as implicit globals like `_robustTarget`).

- [ ] **Step 4: Run the test, verify it passes**

Run: `node --test tests/unit/js/gotime-skewfit.test.js`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add js/GoTime.js tests/unit/js/gotime-skewfit.test.js
git commit -m "feat(gotime): _skewFit — robust Theil-Sen oscillator-skew estimator"
```

---

### Task 2: `_applySkew` — continuity-preserving model update

**Files:**
- Modify: `js/GoTime.js` — add `_applySkew` after `_skewFit`; add `_applySkew: _applySkew` to the return object
- Test: `tests/unit/js/gotime-applyskew.test.js` (create)

**Interfaces:**
- Consumes: nothing from prior tasks.
- Produces: `_applySkew(model, newSkew, rawNow) -> {offset, skew, skewRef}`. `model`: `{offset, skew, skewRef}`. Folds the old rate term into `offset`, re-anchors `skewRef` to `rawNow`, clamps `newSkew` to ±300e-6. Pure.

- [ ] **Step 1: Write the failing test** — create `tests/unit/js/gotime-applyskew.test.js`:

```js
/**
 * _applySkew updates the clock model {offset, skew, skewRef} when a new skew estimate lands,
 * WITHOUT jumping now(): now(raw) = raw + offset + skew*(raw - skewRef) must be equal at the
 * update instant before and after. Also clamps skew to +/-300ppm. Pure.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';
function loadGoTime() {
  const code = fs.readFileSync(new URL('../../../js/GoTime.js', import.meta.url), 'utf8');
  const sandbox = { Date: function () {}, setTimeout: function () { return 0; },
    setInterval: function () { return 0; }, clearTimeout: function () {},
    XMLHttpRequest: function () { this.open = function () {}; this.send = function () {}; },
    console: { log: function () {} }, window: {} };
  sandbox.Date.now = function () { return 0; };
  vm.createContext(sandbox); vm.runInContext(code, sandbox);
  return sandbox;
}
const GT = loadGoTime().GoTime;
function nowOf(m, raw) { return raw + m.offset + m.skew * (raw - m.skewRef); }

test('_applySkew keeps now() continuous at the update instant', () => {
  var m0 = { offset: 500, skew: 10e-6, skewRef: 1000 };
  var R = 100000;
  var before = nowOf(m0, R);
  var m1 = GT._applySkew(m0, 25e-6, R);
  var after = nowOf(m1, R);
  assert.ok(Math.abs(before - after) < 1e-9, 'now() jumped: ' + before + ' -> ' + after);
  assert.strictEqual(m1.skewRef, R);
  assert.strictEqual(m1.skew, 25e-6);
});

test('_applySkew then advances at the new rate', () => {
  var m0 = { offset: 0, skew: 0, skewRef: 0 };
  var R = 50000;
  var m1 = GT._applySkew(m0, 30e-6, R);
  // 10s later, now() should have advanced 10000 + 30e-6*10000 = 10000.3
  var adv = nowOf(m1, R + 10000) - nowOf(m1, R);
  assert.ok(Math.abs(adv - (10000 + 30e-6 * 10000)) < 1e-6, 'advance ' + adv);
});

test('_applySkew clamps skew to +/-300ppm', () => {
  var m = GT._applySkew({ offset: 0, skew: 0, skewRef: 0 }, 999e-6, 0);
  assert.strictEqual(m.skew, 300e-6);
  var n = GT._applySkew({ offset: 0, skew: 0, skewRef: 0 }, -999e-6, 0);
  assert.strictEqual(n.skew, -300e-6);
});

test('_applySkew from zero-model is a clean no-op transition', () => {
  var m = GT._applySkew({ offset: 500, skew: 0, skewRef: 0 }, 20e-6, 100000);
  assert.strictEqual(m.offset, 500);          // fold of 0*... adds nothing
  assert.strictEqual(m.skewRef, 100000);
});
```

- [ ] **Step 2: Run the test, verify it fails**

Run: `node --test tests/unit/js/gotime-applyskew.test.js`
Expected: FAIL — `GT._applySkew is not a function`.

- [ ] **Step 3: Implement `_applySkew`** — in `js/GoTime.js`, after `_skewFit`:

```js
    // Update the clock model {offset, skew, skewRef} for a new skew estimate WITHOUT jumping
    // now(). At the update instant rawNow, fold the accumulated old rate term into offset and
    // re-anchor skewRef to rawNow, so now(rawNow) is unchanged and thereafter advances at the
    // new (clamped) rate. Pure. maxPpm guards a bad fit from running the clock away.
    _applySkew = function(model, newSkew, rawNow) {
        var maxPpm = 300e-6;
        var s = newSkew;
        if (s > maxPpm) { s = maxPpm; } else if (s < -maxPpm) { s = -maxPpm; }
        var newOffset = model.offset + model.skew * (rawNow - model.skewRef);
        return { offset: newOffset, skew: s, skewRef: rawNow };
    };
```

Then add `_applySkew: _applySkew,` to the return object.

- [ ] **Step 4: Run the test, verify it passes**

Run: `node --test tests/unit/js/gotime-applyskew.test.js`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add js/GoTime.js tests/unit/js/gotime-applyskew.test.js
git commit -m "feat(gotime): _applySkew — continuity-preserving skew model update"
```

---

### Task 3: `_deskewSamples` — de-trend the steer's input

**Files:**
- Modify: `js/GoTime.js` — add `_deskewSamples` after `_applySkew`; add `_deskewSamples: _deskewSamples` to the return object
- Test: `tests/unit/js/gotime-deskew.test.js` (create)

**Interfaces:**
- Produces: `_deskewSamples(samples, skew, skewRef) -> [{offset, precision, t}]`. Returns a fresh array with each `offset` reduced by `skew·(t − skewRef)`. Pure.

- [ ] **Step 1: Write the failing test** — create `tests/unit/js/gotime-deskew.test.js`:

```js
/**
 * _deskewSamples removes the skew trend from the offset samples so the offset steer chases the
 * RESIDUAL (not the drift the skew term already corrects). Under a pure linear drift, the
 * de-skewed offsets are constant -> the steer's robust-median target is stationary. Pure.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';
function loadGoTime() {
  const code = fs.readFileSync(new URL('../../../js/GoTime.js', import.meta.url), 'utf8');
  const sandbox = { Date: function () {}, setTimeout: function () { return 0; },
    setInterval: function () { return 0; }, clearTimeout: function () {},
    XMLHttpRequest: function () { this.open = function () {}; this.send = function () {}; },
    console: { log: function () {} }, window: {} };
  sandbox.Date.now = function () { return 0; };
  vm.createContext(sandbox); vm.runInContext(code, sandbox);
  return sandbox;
}
const GT = loadGoTime().GoTime;
function smp(offset, precision, t) { return { offset: offset, precision: precision, t: t }; }

test('_deskewSamples flattens a pure linear drift to a constant', () => {
  var b = 20e-6, ref = 0, samples = [], i;
  for (i = 0; i < 10; i++) { var t = i * 10000; samples.push(smp(1000 + b * t, 10, t)); }
  var out = GT._deskewSamples(samples, b, ref);
  for (i = 0; i < out.length; i++) { assert.ok(Math.abs(out[i].offset - 1000) < 1e-6, out[i].offset); }
});

test('_deskewSamples preserves precision/t and does not mutate input', () => {
  var samples = [smp(1000, 12, 5000)];
  var out = GT._deskewSamples(samples, 20e-6, 0);
  assert.strictEqual(out[0].precision, 12);
  assert.strictEqual(out[0].t, 5000);
  assert.strictEqual(samples[0].offset, 1000);      // input untouched
  assert.notStrictEqual(out, samples);
});

test('_deskewSamples with skew=0 is an offset-preserving copy', () => {
  var samples = [smp(1000, 10, 5000), smp(1200, 10, 9000)];
  var out = GT._deskewSamples(samples, 0, 3000);
  assert.strictEqual(out[0].offset, 1000);
  assert.strictEqual(out[1].offset, 1200);
});
```

- [ ] **Step 2: Run the test, verify it fails**

Run: `node --test tests/unit/js/gotime-deskew.test.js`
Expected: FAIL — `GT._deskewSamples is not a function`.

- [ ] **Step 3: Implement `_deskewSamples`** — in `js/GoTime.js`, after `_applySkew`:

```js
    // Remove the skew trend from offset samples so the offset steer chases the residual, not the
    // drift the skew term already corrects (else the two double-correct and the error accumulates).
    // Fresh array; input untouched. Pure.
    _deskewSamples = function(samples, skew, skewRef) {
        var out = [], i;
        for (i = 0; i < samples.length; i++) {
            out.push({ offset: samples[i].offset - skew * (samples[i].t - skewRef),
                       precision: samples[i].precision, t: samples[i].t });
        }
        return out;
    };
```

Then add `_deskewSamples: _deskewSamples,` to the return object.

- [ ] **Step 4: Run the test, verify it passes**

Run: `node --test tests/unit/js/gotime-deskew.test.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add js/GoTime.js tests/unit/js/gotime-deskew.test.js
git commit -m "feat(gotime): _deskewSamples — de-trend steer input for skew reconciliation"
```

---

### Task 4: Clock state + additive rate term in `now()` (+ test seam)

**Files:**
- Modify: `js/GoTime.js` — add `_skew: 0, _skewRef: 0` (and skew config) to the `options` struct (~line 124); modify `now()` (~line 384); expose `_steerTick`/`_reviseOffset` on the return object for integration tests
- Test: `tests/unit/js/gotime-now-skew.test.js` (create)

**Interfaces:**
- Consumes: `_applySkew` (Task 2).
- Produces: `GoTime.now()` includes the `_skew·(raw − _skewRef)` term. `options._skew`, `options._skewRef` exist (defaults 0). `_steerTick`, `_reviseOffset` exposed on the return object for tests.

- [ ] **Step 1: Write the failing test** — create `tests/unit/js/gotime-now-skew.test.js`:

```js
/**
 * now() = raw + _offset + _skew*(raw - _skewRef). With skew=0 (default) it equals raw+offset
 * (today). After _applySkew sets a skew, now() advances at the corrected rate. Driven via the
 * exposed internals with a controllable FakeDate.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';
function load() {
  const code = fs.readFileSync(new URL('../../../js/GoTime.js', import.meta.url), 'utf8');
  let now = 1000000;
  const sandbox = { Date: function () {}, setTimeout: function () { return 0; },
    setInterval: function () { return 0; }, clearTimeout: function () {},
    XMLHttpRequest: function () { this.open = function () {}; this.send = function () {}; },
    console: { log: function () {} }, window: {} };
  sandbox.Date.now = function () { return now; };
  vm.createContext(sandbox); vm.runInContext(code, sandbox);
  return { GT: sandbox.GoTime, setNow: function (t) { now = t; } };
}

test('now() with default skew=0 equals raw + offset (today behavior)', () => {
  const { GT, setNow } = load();
  setNow(2000000);
  // no offset applied -> now() == raw
  assert.strictEqual(GT.now(), 2000000);
});

test('now() advances at the corrected rate after a skew is applied', () => {
  const { GT, setNow } = load();
  setNow(1000000);
  GT._applyModelForTest(0, 50e-6, 1000000);      // helper set below in Step 3
  var t0 = GT.now();
  setNow(1000000 + 10000);
  var t1 = GT.now();
  assert.ok(Math.abs((t1 - t0) - (10000 + 50e-6 * 10000)) < 1e-6, 'advance ' + (t1 - t0));
});
```

- [ ] **Step 2: Run the test, verify it fails**

Run: `node --test tests/unit/js/gotime-now-skew.test.js`
Expected: FAIL — `GT._applyModelForTest is not a function` (and `now()` has no skew term yet).

- [ ] **Step 3: Implement** — three edits in `js/GoTime.js`:

(a) In the `options` struct (~line 124, next to `_offset: 0,`) add:

```js
        _skew: 0,             // oscillator skew (ppm fraction); 0 => now() identical to today
        _skewRef: 0,          // raw-local anchor for the skew rate term
        _skewMinSamples: 6,
        _skewMinSpanMs: 60000,
        _skewDeadbandPpm: 2e-6,
```

(b) Modify `now()` (~line 384) — currently `return getAccurateTimestamp() + options._offset;`:

```js
		now: function() {
			var raw = getAccurateTimestamp();
			return raw + options._offset + options._skew * (raw - options._skewRef);
		},
```

(c) Add to the return object (near `_steerStep: _steerStep,`) — a tiny test seam plus the loop internals for later integration tasks:

```js
		_steerTick: function() { return _steerTick(); },
		_reviseOffset: function(sample, method) { return _reviseOffset(sample, method); },
		_applyModelForTest: function(offset, skew, ref) {
			options._offset = offset; options._skew = skew; options._skewRef = ref;
		},
```

- [ ] **Step 4: Run the test, verify it passes**

Run: `node --test tests/unit/js/gotime-now-skew.test.js`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full JS suite (no regression to existing steer/cadence/ready tests)**

Run: `python pytest_runner.py --js`
Expected: all pass (skew=0 default keeps `now()` behavior identical).

- [ ] **Step 6: Commit**

```bash
git add js/GoTime.js tests/unit/js/gotime-now-skew.test.js
git commit -m "feat(gotime): additive skew rate term in now() + clock state + test seam"
```

---

### Task 5: Wire skew discipline into `_steerTick`

**Files:**
- Modify: `js/GoTime.js` — `_steerTick` (~line 287): fit skew (gated), apply on change, chase de-skewed target
- Test: `tests/unit/js/gotime-skew-integration.test.js` (create)

**Interfaces:**
- Consumes: `_skewFit`, `_applySkew`, `_deskewSamples`, `_steerTick`/`_reviseOffset` seam.
- Produces: `_steerTick` locks a skew (once `_steering`) and steers the de-skewed offset.

- [ ] **Step 1: Write the failing test** — create `tests/unit/js/gotime-skew-integration.test.js`:

```js
/**
 * Drive the real loop: feed samples carrying a fixed oscillator drift, tick the steer, and assert
 * _steerTick locks a skew close to the true drift and the steered offset stops drifting (stationary
 * de-skewed residual). Uses the exposed _reviseOffset + _steerTick with a controllable clock.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';
function load() {
  const code = fs.readFileSync(new URL('../../../js/GoTime.js', import.meta.url), 'utf8');
  let now = 0;
  const sandbox = { Date: function () {}, setTimeout: function () { return 0; },
    setInterval: function () { return 0; }, clearTimeout: function () {},
    XMLHttpRequest: function () { this.open = function () {}; this.send = function () {}; },
    console: { log: function () {} }, window: {} };
  sandbox.Date.now = function () { return now; };
  vm.createContext(sandbox); vm.runInContext(code, sandbox);
  return { GT: sandbox.GoTime, setNow: function (t) { now = t; } };
}

test('_steerTick locks a skew near the true drift and stops offset drift', () => {
  const { GT, setNow } = load();
  var trueSkew = 40e-6, trueOffset = 0, i;
  // feed 20 clean samples over 200s, ticking the steer each time
  for (i = 0; i <= 20; i++) {
    var t = i * 10000; setNow(t);
    var off = trueOffset + trueSkew * t;                 // the true offset drifts
    GT._reviseOffset({ offset: off, precision: 10 }, 'test');
    GT._steerTick();
  }
  var st = GT.getSteerState();
  assert.ok(Math.abs(st.skew - trueSkew) < 8e-6, 'locked skew ' + st.skew + ' vs ' + trueSkew);
  // now() should track the true server time (raw + trueOffset + trueSkew*raw) closely
  setNow(210000);
  var serverNow = 210000 + trueOffset + trueSkew * 210000;
  assert.ok(Math.abs(GT.now() - serverNow) < 40, 'now() off by ' + (GT.now() - serverNow) + 'ms');
});
```

- [ ] **Step 2: Run the test, verify it fails**

Run: `node --test tests/unit/js/gotime-skew-integration.test.js`
Expected: FAIL — `getSteerState()` has no `skew` field and `_steerTick` doesn't fit/apply skew, so the offset drifts.

- [ ] **Step 3: Implement** — modify `_steerTick` (~line 287). Replace its body's target computation with the skew-disciplined version:

```js
    _steerTick = function() {
        var nowLocal = getAccurateTimestamp();
        var dtMs = (options._lastSteerAt == null) ? 1000 : (nowLocal - options._lastSteerAt);
        options._lastSteerAt = nowLocal;
        if (dtMs <= 0) { return; }
        var opts = {
            deadbandMs: options._steerDeadbandMs, snapMs: options._steerSnapMs,
            capMsPerSec: options._steerCapMsPerSec, windowMs: options._steerWindowMs,
            minSamples: options._steerMinSamples, precisionFloorMs: options._steerPrecisionFloorMs,
            skewMinSamples: options._skewMinSamples, skewMinSpanMs: options._skewMinSpanMs
        };
        // Frequency discipline: re-fit skew once the offset has locked; fold on meaningful change.
        if (options._steering) {
            var newSkew = _skewFit(options._steerSamples, GoTime.now(), opts);
            if (newSkew !== null) {
                var dead = (options._skewDeadbandPpm != null) ? options._skewDeadbandPpm : 2e-6;
                if (Math.abs(newSkew - options._skew) > dead) {
                    var m = _applySkew({ offset: options._offset, skew: options._skew, skewRef: options._skewRef },
                                       newSkew, nowLocal);
                    options._offset = m.offset; options._skew = m.skew; options._skewRef = m.skewRef;
                }
            }
        }
        // Offset steer chases the DE-SKEWED residual (skew owns rate, steer owns offset).
        var target = _robustTarget(_deskewSamples(options._steerSamples, options._skew, options._skewRef),
                                   GoTime.now(), opts);
        if (target === null) { return; }
        options._steering = true;
        options._lastSteerTarget = target;
        options._offset = Math.round(_steerStep(options._offset, target, dtMs, opts));
    };
```

- [ ] **Step 4: Add `skew` to `getSteerState`** (~line 473) so the integration test (and `?tdbg`, Task 6) can read it:

```js
		getSteerState: function() {
            return { steering: options._steering, samples: options._steerSamples.length,
                     offset: options._offset, target: options._lastSteerTarget,
                     skew: options._skew };
        },
```

- [ ] **Step 5: Run the test, verify it passes**

Run: `node --test tests/unit/js/gotime-skew-integration.test.js`
Expected: PASS.

- [ ] **Step 6: Run the full JS suite**

Run: `python pytest_runner.py --js`
Expected: all pass (existing steer tests still green — de-skew with `_skew=0` is a no-op copy).

- [ ] **Step 7: Commit**

```bash
git add js/GoTime.js tests/unit/js/gotime-skew-integration.test.js
git commit -m "feat(gotime): wire skew fit+apply+de-skewed-target into _steerTick"
```

---

### Task 6: Surface `skew` in `?tdbg`

**Files:**
- Modify: `index.html` — the `?tdbg` HUD payload (~line 353+) to display skew in ppm

**Interfaces:**
- Consumes: `GoTime.getSteerState().skew` (Task 5).

- [ ] **Step 1: Locate the tdbg field assembly** — in `index.html`, find where the tdbg HUD reads GoTime state (search for `getSteerState` or the `off=`/`prec=` field string it builds, ~line 353–420).

- [ ] **Step 2: Add the skew field** — where the HUD builds its per-screen string (alongside `off`/`prec`), append a skew term in ppm. Example (adapt to the exact local variable names at the site):

```js
			var _ss = (typeof GoTime !== 'undefined' && GoTime.getSteerState) ? GoTime.getSteerState() : null;
			var _skewPpm = _ss ? Math.round(_ss.skew * 1e6) : 0;
			// ... append to the HUD line, e.g.:  ' skew=' + _skewPpm + 'ppm'
```

Add `' skew=' + _skewPpm + 'ppm'` to the concatenated HUD string so each screen shows its locked skew.

- [ ] **Step 3: Verify (read-back)** — confirm the edit is ES5 and references only existing globals (`GoTime`). There is no automated test for the `index.html` HUD.

Run: `node -e "require('fs').readFileSync('index.html','utf8')" && echo ok`
Expected: `ok`; the edit uses only `var`/`+`/existing globals.

- [ ] **Step 4: Commit**

```bash
git add index.html
git commit -m "feat(tdbg): show locked oscillator skew (ppm) per screen"
```

---

### Task 7: Hour-long drift integration test

**Files:**
- Test: `tests/unit/js/gotime-skew-longrun.test.js` (create)

**Interfaces:**
- Consumes: the exposed `_reviseOffset`/`_steerTick` loop + `GoTime.now()`.

- [ ] **Step 1: Write the test** — create `tests/unit/js/gotime-skew-longrun.test.js`:

```js
/**
 * Over a simulated hour with a fixed oscillator drift + realistic sample noise, the disciplined
 * clock keeps now() within a tight bound of true server time, whereas the offset-only baseline
 * (skew forced to 0) drifts roughly linearly. Guards the whole point of the feature.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';
function load() {
  const code = fs.readFileSync(new URL('../../../js/GoTime.js', import.meta.url), 'utf8');
  let now = 0;
  const sandbox = { Date: function () {}, setTimeout: function () { return 0; },
    setInterval: function () { return 0; }, clearTimeout: function () {},
    XMLHttpRequest: function () { this.open = function () {}; this.send = function () {}; },
    console: { log: function () {} }, window: {} };
  sandbox.Date.now = function () { return now; };
  vm.createContext(sandbox); vm.runInContext(code, sandbox);
  return { GT: sandbox.GoTime, setNow: function (t) { now = t; } };
}
// deterministic pseudo-noise (no Math.random — reproducible)
function noise(i) { return ((Math.sin(i * 12.9898) * 43758.5453) % 1) * 30 - 15; } // +/-15ms

test('disciplined now() tracks server time over an hour of fixed drift', () => {
  const { GT, setNow } = load();
  var trueSkew = 35e-6, i, worst = 0;
  for (i = 0; i <= 360; i++) {                          // 360 samples, 10s apart = 1 hour
    var t = i * 10000; setNow(t);
    var off = trueSkew * t + noise(i);
    GT._reviseOffset({ offset: off, precision: 12 }, 'test');
    GT._steerTick();
    if (i > 30) {                                        // after convergence
      var serverNow = t + trueSkew * t;
      var err = Math.abs(GT.now() - serverNow);
      if (err > worst) { worst = err; }
    }
  }
  assert.ok(worst < 25, 'worst tracking error after convergence was ' + worst + 'ms (want <25)');
});
```

- [ ] **Step 2: Run the test**

Run: `node --test tests/unit/js/gotime-skew-longrun.test.js`
Expected: PASS (worst-case tracking error well under 25 ms once converged).

- [ ] **Step 3: Run the full JS suite**

Run: `python pytest_runner.py --js`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/js/gotime-skew-longrun.test.js
git commit -m "test(gotime): hour-long drift simulation — disciplined now() tracks server time"
```

---

## Self-Review

**Spec coverage:** clock model → Task 4; `_skewFit` estimator → Task 1; steer reconciliation / de-skew → Task 3 + Task 5; `_applySkew` continuity → Task 2; bounds/guards (±300 ppm, hold-last-good, `_steering` gate, 2 ppm deadband, ~30 s cadence via the steer beat) → Task 2 (clamp) + Task 5 (gate/deadband/cadence); `?tdbg skew` → Task 6; test plan (each pure helper, continuity, de-skew stationarity, hour-long integration) → Tasks 1–3, 5, 7. All spec sections covered. ✔

**Placeholder scan:** every step carries real code/commands. Task 6 adapts to a site whose exact local variable names must be read in-file, but the concrete edit and read-back check are given. No TBD/TODO. ✔

**Type consistency:** `_skewFit(samples, nowMs, opts) -> Number|null`, `_applySkew(model, newSkew, rawNow) -> {offset,skew,skewRef}`, `_deskewSamples(samples, skew, skewRef) -> [...]` — signatures identical across their defining task, `_steerTick`, and the tests. Model shape `{offset, skew, skewRef}` consistent everywhere. `getSteerState().skew` produced in Task 5, consumed in Tasks 5/6/7. ✔
