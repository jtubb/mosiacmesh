# mmvideo-aware drift steer (damped rate-PLL) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the drift steer's stock-iOS-tuned fine correction (which oscillates ±140 ms on mmvideo because its `playbackRate` term is now live) with a median-filtered, gently-damped rate-PLL plus rate-liveness auto-detect.

**Architecture:** Extract the control decision into a pure ES5 helper `js/drift-steer.js` (`mmDriftState()` + `mmDriftCommand()`), node-tested. `index.html`'s `driftTick` becomes a thin wrapper: read err → call `mmDriftCommand` → apply `seek` / `rate` / `hold`. The existing boundary-aligned seek machinery is preserved for the coarse regime; only the fine-correction term and the regime threshold change.

**Tech Stack:** ES5 JavaScript (iPad-1 / Safari 5.1 constraint), Node.js `node:test` for unit tests, existing drift telemetry + `driftsnap.py` for on-wall A/B.

## Global Constraints

- **ES5 ONLY** in `js/drift-steer.js` and `index.html`: no `let`/`const`/arrow/template-literal/`class`/`Promise`/`fetch`. (iPad-1 / Safari 5.1 hard requirement.)
- **Dual browser/node export:** helper is an IIFE `(function(root){ … root.fn = fn; })(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));` — a valid classic `<script>` AND a side-effect `import` for tests. NO `module.exports`, NO ES-module syntax.
- **Video-steer only.** Do NOT touch `js/GoTime.js` skew/clamp. Clamped screens settling to a steady offset is expected.
- **Preserve** `KEYFRAME_GRID_MS` (250), `SEEK_COOLDOWN_MS` (2500), and the seekAhead lead-tuning. Only the fine term + regime threshold change.
- **Constants (initial):** `COARSE_SEEK_MS=350`, `FINE_DEADBAND_MS=20`, `RATE_CLAMP=0.035`, `RATE_GAIN=0.0002`, `ERR_FILTER_N=5`, `LIVENESS_EPS=0.02`, `LIVENESS_K=4`, `PROBE_TICKS=8`, `PROBE_DELTA=0.03`.
- **Run JS tests via:** `python pytest_runner.py --js` (or `node --test tests/unit/js/test_drift_steer.js`).

---

### Task 1: Pure control law — filter + regime + damped rate (rate assumed live)

**Files:**
- Create: `js/drift-steer.js`
- Test: `tests/unit/js/test_drift_steer.js`

**Interfaces:**
- Produces:
  - `mmDriftState() -> { ring:[], inertCount:0, rateLive:true, lastRateCmd:1, probeCountdown:0 }`
  - `mmDriftCommand(state, err, obs, opts) -> { action:'seek'|'rate'|'hold', rate:Number, filteredErr:Number, rateLive:Boolean }` where `err` is measured errorMs (`<0` behind, `>0` ahead), `obs = { rateGot:Number, canSeek:Boolean }`, `opts` overrides any constant.
  - `_mmDriftMedian(arr) -> Number` (exposed for tests).
- In this task, `state.rateLive` stays `true` and there is no inert/probe branch (added in Task 2).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/js/test_drift_steer.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/drift-steer.js');
const g = globalThis;

test('median: odd, even, empty', () => {
  assert.equal(g._mmDriftMedian([3, 1, 2]), 2);
  assert.equal(g._mmDriftMedian([4, 1, 3, 2]), 2.5);
  assert.equal(g._mmDriftMedian([]), 0);
});

test('coarse regime: |err|>COARSE and canSeek -> seek, rate 1', () => {
  const s = g.mmDriftState();
  const c = g.mmDriftCommand(s, -500, { rateGot: 1, canSeek: true });
  assert.equal(c.action, 'seek');
  assert.equal(c.rate, 1);
});

test('regime boundary: below COARSE -> not seek, above -> seek', () => {
  const below = g.mmDriftCommand(g.mmDriftState(), -300, { rateGot: 1, canSeek: true });
  assert.notEqual(below.action, 'seek');
  const above = g.mmDriftCommand(g.mmDriftState(), -400, { rateGot: 1, canSeek: true });
  assert.equal(above.action, 'seek');
});

test('deadband: small filtered err -> hold, rate 1', () => {
  const s = g.mmDriftState();
  let c;
  [10, -5, 8, -10, 5].forEach(function (e) { c = g.mmDriftCommand(s, e, { rateGot: 1, canSeek: true }); });
  assert.equal(c.action, 'hold');
  assert.equal(c.rate, 1);
});

test('fine regime: rate command stays within clamp', () => {
  const s = g.mmDriftState();
  let c;
  [-1000, -1000, -1000, -1000, -1000].forEach(function (e) {
    c = g.mmDriftCommand(s, e, { rateGot: 1, canSeek: false }); // cooldown -> no seek
  });
  assert.ok(c.rate >= 1 - 0.035 - 1e-9 && c.rate <= 1 + 0.035 + 1e-9);
  assert.equal(c.action, 'rate');
});

test('closed-loop: clean -100ms err converges into deadband without overshoot', () => {
  const s = g.mmDriftState();
  let err = -100, dt = 0.5, overshoot = false;
  for (let i = 0; i < 80; i++) {
    const c = g.mmDriftCommand(s, err, { rateGot: 1, canSeek: false });
    const rate = c.action === 'rate' ? c.rate : 1;
    err = err + (rate - 1) * dt * 1000;   // apply rate for one 500ms tick
    if (err > 5) overshoot = true;
  }
  assert.ok(Math.abs(err) < 25, 'converged, err=' + err);
  assert.ok(!overshoot, 'no overshoot past zero');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/unit/js/test_drift_steer.js`
Expected: FAIL — `g.mmDriftState is not a function` (file not created yet).

- [ ] **Step 3: Write minimal implementation**

Create `js/drift-steer.js`:

```js
/* js/drift-steer.js — ES5, NO module syntax (classic <script> for the iPad-1
 * client AND a side-effect import for node tests). Pure video drift controller:
 * a median-filtered, gently-damped rate PLL (mmvideo has LIVE playbackRate) with
 * a coarse boundary-seek regime for big errors. Rate-liveness auto-detect is
 * layered in Task 2. No DOM, no timers. */
(function (root) {
  var DEFAULTS = {
    COARSE_SEEK_MS: 350,    // coarse/fine regime boundary
    FINE_DEADBAND_MS: 20,   // below this filtered err, hold rate=1
    RATE_CLAMP: 0.035,      // max fractional rate delta (±3.5%)
    RATE_GAIN: 0.0002,      // rate delta per ms of filtered err
    ERR_FILTER_N: 5,        // median filter window
    LIVENESS_EPS: 0.02,     // rateGot-vs-cmd tolerance (Task 2)
    LIVENESS_K: 4,          // consecutive inert ticks -> seek-primary (Task 2)
    PROBE_TICKS: 8,         // inert re-probe cadence (Task 2)
    PROBE_DELTA: 0.03       // probe rate delta (Task 2)
  };

  function opt(opts, k) { return (opts && opts[k] !== undefined) ? opts[k] : DEFAULTS[k]; }

  function median(arr) {
    var a = arr.slice().sort(function (x, y) { return x - y; });
    var n = a.length;
    if (n === 0) { return 0; }
    var mid = Math.floor(n / 2);
    return (n % 2) ? a[mid] : (a[mid - 1] + a[mid]) / 2;
  }

  function mmDriftState() {
    return { ring: [], inertCount: 0, rateLive: true, lastRateCmd: 1, probeCountdown: 0 };
  }

  function mmDriftCommand(state, err, obs, opts) {
    var COARSE = opt(opts, 'COARSE_SEEK_MS');
    var DEAD = opt(opts, 'FINE_DEADBAND_MS');
    var CLAMP = opt(opts, 'RATE_CLAMP');
    var GAIN = opt(opts, 'RATE_GAIN');
    var N = opt(opts, 'ERR_FILTER_N');
    var canSeek = !!(obs && obs.canSeek);

    // median filter
    state.ring.push(err);
    while (state.ring.length > N) { state.ring.shift(); }
    var fErr = median(state.ring);

    // coarse regime keys on RAW err (fast reaction to a big jump / startup)
    if (Math.abs(err) > COARSE && canSeek) {
      state.lastRateCmd = 1;   // rate resets to 1 after a seek
      return { action: 'seek', rate: 1, filteredErr: fErr, rateLive: state.rateLive };
    }

    // fine regime: deadband, then damped rate on FILTERED err
    if (Math.abs(fErr) < DEAD) {
      state.lastRateCmd = 1;
      return { action: 'hold', rate: 1, filteredErr: fErr, rateLive: state.rateLive };
    }
    var rate = 1 - fErr * GAIN;   // err<0 (behind) => rate>1 (speed up)
    if (rate < 1 - CLAMP) { rate = 1 - CLAMP; }
    if (rate > 1 + CLAMP) { rate = 1 + CLAMP; }
    state.lastRateCmd = rate;
    return { action: 'rate', rate: rate, filteredErr: fErr, rateLive: state.rateLive };
  }

  root.mmDriftState = mmDriftState;
  root.mmDriftCommand = mmDriftCommand;
  root._mmDriftMedian = median;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test tests/unit/js/test_drift_steer.js`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add js/drift-steer.js tests/unit/js/test_drift_steer.js
git commit -m "feat(drift): pure damped rate-PLL control law + median filter + node tests"
```

---

### Task 2: Rate-liveness auto-detect + inert/probe fallback

**Files:**
- Modify: `js/drift-steer.js` (add liveness update at the top of `mmDriftCommand` + an inert branch before the fine regime)
- Test: `tests/unit/js/test_drift_steer.js` (append)

**Interfaces:**
- Consumes: `mmDriftCommand(state, err, obs, opts)` from Task 1.
- Produces: same signature; now `obs.rateGot` drives liveness. When rate proves inert (`|rateGot − state.lastRateCmd| > LIVENESS_EPS` for `LIVENESS_K` consecutive ticks) `state.rateLive` flips false and the fine regime is replaced by hold + periodic probe (`action:'rate'` every `PROBE_TICKS` ticks) so recovery is observable. A honored probe restores `state.rateLive=true`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/js/test_drift_steer.js`:

```js
test('liveness: rateGot ignores commands for K ticks -> rateLive false', () => {
  const s = g.mmDriftState();
  for (let i = 0; i < 6; i++) g.mmDriftCommand(s, -100, { rateGot: 1, canSeek: false });
  assert.equal(s.rateLive, false);
});

test('liveness: honored rate keeps rateLive true', () => {
  const s = g.mmDriftState();
  let last = 1;
  for (let i = 0; i < 6; i++) {
    const c = g.mmDriftCommand(s, -100, { rateGot: last, canSeek: false });
    last = c.rate;   // element honors the command
  }
  assert.equal(s.rateLive, true);
});

test('liveness: inert screen emits periodic probe and recovers when honored', () => {
  const s = g.mmDriftState();
  for (let i = 0; i < 6; i++) g.mmDriftCommand(s, -100, { rateGot: 1, canSeek: false });
  assert.equal(s.rateLive, false);
  let probe = null;
  for (let i = 0; i < 12 && probe === null; i++) {
    const c = g.mmDriftCommand(s, -100, { rateGot: 1, canSeek: false });
    if (c.action === 'rate') probe = c.rate;
  }
  assert.ok(probe !== null, 'a probe rate was emitted while inert');
  g.mmDriftCommand(s, -100, { rateGot: probe, canSeek: false }); // probe honored next tick
  assert.equal(s.rateLive, true);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/unit/js/test_drift_steer.js`
Expected: FAIL — `rateLive` stays true (no liveness logic yet); probe test finds no inert-state probe.

- [ ] **Step 3: Write minimal implementation**

In `js/drift-steer.js`, inside `mmDriftCommand`, add the liveness update immediately after the `opt(...)` reads and BEFORE the median filter:

```js
    var EPS = opt(opts, 'LIVENESS_EPS');
    var K = opt(opts, 'LIVENESS_K');
    var rateGot = (obs && typeof obs.rateGot === 'number') ? obs.rateGot : 1;

    // liveness: did LAST tick's non-unity rate command take effect this tick?
    if (state.lastRateCmd !== 1) {
      if (Math.abs(rateGot - state.lastRateCmd) > EPS) {
        state.inertCount += 1;
        if (state.inertCount >= K) { state.rateLive = false; }
      } else {
        state.inertCount = 0;
        state.rateLive = true;
      }
    }
```

Then, AFTER the coarse-seek block and BEFORE the fine deadband/rate block, add the inert branch:

```js
    // rate inert (stock Safari pins playbackRate to 1): can't fine-steer via rate.
    // Hold, but emit a periodic probe so recovery is observable.
    if (!state.rateLive) {
      state.probeCountdown -= 1;
      if (state.probeCountdown <= 0) {
        state.probeCountdown = opt(opts, 'PROBE_TICKS');
        var probe = 1 + opt(opts, 'PROBE_DELTA');
        state.lastRateCmd = probe;
        return { action: 'rate', rate: probe, filteredErr: fErr, rateLive: false };
      }
      state.lastRateCmd = 1;
      return { action: 'hold', rate: 1, filteredErr: fErr, rateLive: false };
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/unit/js/test_drift_steer.js`
Expected: PASS (9 tests — all Task 1 tests still pass, plus 3 liveness tests).

- [ ] **Step 5: Commit**

```bash
git add js/drift-steer.js tests/unit/js/test_drift_steer.js
git commit -m "feat(drift): rate-liveness auto-detect + inert-screen probe fallback"
```

---

### Task 3: Wire `mmDriftCommand` into `index.html` driftTick

**Files:**
- Modify: `index.html:32` (add script include), `index.html:320` (retire `DRIFT_SEEK_MS`, add `DRIFT_OPTS`), `index.html:1143` (`startDriftLoop` inits `playback.driftState`), `index.html:1087-1132` (replace decision block; preserve seek machinery)

**Interfaces:**
- Consumes: `mmDriftState()`, `mmDriftCommand()` from Task 1/2.
- Produces: no new exports — `driftTick` now applies `cmd.action`.

- [ ] **Step 1: Add the script include**

In `index.html`, after line 32 (`<script src="/js/mesh-viewport.js"></script>`), add:

```html
  <script src="/js/drift-steer.js"></script>
```

- [ ] **Step 2: Retire `DRIFT_SEEK_MS`, add `DRIFT_OPTS`**

At `index.html:320`, replace the line `var DRIFT_SEEK_MS = 30;` (and its comment) with:

```js
	// Drift-steer tuning (js/drift-steer.js). Coarse = boundary seek; fine = damped
	// rate PLL on median-filtered err with a deadband. Replaces the stock-iOS
	// DRIFT_SEEK_MS=30 + rate=1-err/2000 (±15%) that oscillated on mmvideo's live rate.
	var DRIFT_OPTS = {
		COARSE_SEEK_MS: 350, FINE_DEADBAND_MS: 20, RATE_CLAMP: 0.035, RATE_GAIN: 0.0002,
		ERR_FILTER_N: 5, LIVENESS_EPS: 0.02, LIVENESS_K: 4, PROBE_TICKS: 8, PROBE_DELTA: 0.03
	};
```

- [ ] **Step 3: Init `playback.driftState` in `startDriftLoop`**

At `index.html:1143`, inside `function startDriftLoop()`, before the `setInterval`, add a fresh state per playback (so the err ring doesn't carry across a stop/start):

```js
		playback.driftState = mmDriftState();
```

- [ ] **Step 4: Replace the decision block**

In `driftTick`, replace the block from `// Rate-limit hard seeks…` (currently `index.html:1087`) through the closing brace of the `else { var rate = 1 - errorMs / 2000; … }` block (currently `index.html:1132`) with:

```js
		// Rate-limit hard seeks so each settles before the next.
		var nowMs = GoTime.now();
		var canSeek = (nowMs - playback.lastSeekAt) > SEEK_COOLDOWN_MS;
		var didSeek = false;
		var cmd = mmDriftCommand(playback.driftState, errorMs,
			{ rateGot: v.playbackRate, canSeek: canSeek }, DRIFT_OPTS);
		if (cmd.action === 'seek') {
			// BOUNDARY-ALIGNED seek: schedule the seek to SETTLE exactly as the clock
			// reaches the next grid keyframe, so the residual is CONSISTENT across the
			// fleet (no random keyframe snap). Unchanged from the pre-PLL steer.
			var lead = playback.seekAheadMs;
			var maxMs = ((v.duration || (durations[pos.index] / 1000)) * 1000) - 50;
			var G = Math.ceil((pos.offsetMs + lead + 40) / KEYFRAME_GRID_MS) * KEYFRAME_GRID_MS;
			var issueDelay = G - pos.offsetMs - lead;
			if (issueDelay < 0) { issueDelay = 0; }
			if (G > maxMs) { G = maxMs; }
			if (G < 0) { G = 0; }
			playback.lastSeekAt = nowMs;
			didSeek = true;
			if (playback.boundaryTimer) { clearTimeout(playback.boundaryTimer); }
			playback.boundaryTimer = setTimeout(function () {
				playback.boundaryTimer = null;
				var vv = playback.video;
				if (!playback.active || playback.paused || !vv) { return; }
				try { vv.currentTime = G / 1000; } catch (e) {}
				vv.playbackRate = 1;
				playback.seekPending = true;
				playback.lastSeekAt = GoTime.now();
				playback.seekTargetMs = G;
				playback.seekIssuedAt = playback.lastSeekAt;
				playback.seekIssueLead = lead;
			}, issueDelay);
		} else if (cmd.action === 'rate') {
			v.playbackRate = cmd.rate;
		} else {
			v.playbackRate = 1;   // hold: within deadband, don't chase noise
		}
```

- [ ] **Step 5: Update the drift trace**

At the `dbg("drift", …)` call (currently `index.html:1139`), add the filtered err + liveness so the on-wall A/B can see them:

```js
			dbg("drift", { err: Math.round(errorMs), rateGot: v.playbackRate, sk: (didSeek ? 1 : 0),
				lead: Math.round(playback.seekAheadMs),
				ferr: Math.round(cmd.filteredErr), rlive: (cmd.rateLive ? 1 : 0) });
```

- [ ] **Step 6: Verify ES5 + tests + serve**

Run each and confirm:

```bash
# a) no ES6 tokens introduced in the new helper or wiring
grep -nE "=>|\blet\b|\bconst\b|`" js/drift-steer.js || echo "drift-steer.js ES5-clean"
# b) full JS suite still green (imports drift-steer.js among others)
python pytest_runner.py --js
# c) index.html still serves (server already running on :3000)
curl.exe -s -o /dev/null -w "index -> %{http_code}\n" http://localhost:3000/
# d) the new include is present and no stale DRIFT_SEEK_MS remains
grep -n "drift-steer.js" index.html && (grep -n "DRIFT_SEEK_MS" index.html && echo "STALE REF — FIX" || echo "DRIFT_SEEK_MS retired")
```
Expected: (a) ES5-clean, (b) all JS tests pass, (c) `index -> 200`, (d) include present + `DRIFT_SEEK_MS retired`.

- [ ] **Step 7: Commit**

```bash
git add index.html
git commit -m "feat(drift): wire damped rate-PLL into driftTick, retire stock-iOS DRIFT_SEEK_MS"
```

---

### Task 4: On-wall A/B sign-off (operator-run)

**Files:** none (deploy + measure).

**Interfaces:** consumes the deployed `index.html` + `js/drift-steer.js`; measures via existing drift telemetry + `driftsnap.py`.

- [ ] **Step 1: Capture the BEFORE baseline**

With PullTest3 already looping on OEB Sign 1 (pre-change build), run the drift snapshot 2–3× and record: pack median |err|, within-66 ms count, and whether err sign-flips (oscillation). Baseline expectation: ~140 ms median, ±150 ms sign-flipping.

```bash
python "<scratchpad>/driftsnap.py"
```

- [ ] **Step 2: Deploy the change (fleet RELOAD)**

`index.html` + `js/drift-steer.js` are server-served — no dylib rebuild. The running server serves files from disk, so the new files are live immediately; the fleet must re-fetch:

```bash
python tools/_reload_group.py "OEB Sign 1"   # RELOAD broadcast; webclips re-fetch index.html + js/
```
(If `_reload_group.py` is not the correct helper, use the Fleet "Reload" action / `RELOAD` SockJS message for the group.)

- [ ] **Step 3: Re-arm PullTest3 and let it settle**

```bash
python tools/_assign_play.py "OEB Sign 1" "PullTest3"
```
Wait ~90 s for arm + several steer beats.

- [ ] **Step 4: Capture the AFTER measurement**

Run the snapshot 2–3× over ~2 min. Confirm the drift ticks now show `rate` within ±3.5 % (not ±15 %) and `ferr`/`rlive` fields present:

```bash
python "<scratchpad>/driftsnap.py"
grep "'tag': 'drift'" mm_server.err | tail -20   # spot-check rate within [0.965,1.035], ferr present
```

- [ ] **Step 5: Verdict**

PASS if: no err sign-flipping (limit cycle gone), healthy-screen pack median |err| well under 66 ms (target 20–40 ms), rate commands within ±3.5 %. Clamped screens (5/8/19) settle to a *steady* offset — expected, not a regression. If the pack undershoots the target or is sluggish, tune `RATE_GAIN` / `FINE_DEADBAND_MS` / `RATE_CLAMP` in `DRIFT_OPTS` (`index.html:320`) and re-run Steps 2–4. Record final constants back into the spec.

---

## Self-Review

**Spec coverage:**
- Section 1 control law (filter/gain/clamp/deadband, coarse/fine regime) → Task 1. ✓
- Section 2 rate-liveness auto-detect + inert fallback → Task 2. ✓
- Section 3 pure helper + node tests + thin `driftTick` wrapper → Tasks 1/2 (helper+tests), Task 3 (wrapper). ✓
- Section 4 constants (all 9 in Global Constraints + `DRIFT_OPTS`) → Task 3 Step 2. ✓
- Section 5 validation (node tests + on-wall A/B) → Tasks 1/2 tests, Task 4. ✓
- Section 6 rollout (ES5, file change + RELOAD, no dylib) → Global Constraints + Task 4 Step 2. ✓

**Placeholder scan:** No TBD/TODO. Seek-branch code is repeated in full (not "same as before"). `<scratchpad>/driftsnap.py` is an operator path placeholder (the actual session scratchpad path) — acceptable, it names an existing file the operator already has.

**Type consistency:** `mmDriftState`/`mmDriftCommand`/`_mmDriftMedian` names match across Tasks 1–3. `obs` shape `{ rateGot, canSeek }` consistent in helper, tests, and the `driftTick` call. `cmd.action` values `'seek'|'rate'|'hold'` handled exhaustively in Task 3 Step 4. `state` fields (`ring`,`inertCount`,`rateLive`,`lastRateCmd`,`probeCountdown`) defined in `mmDriftState` (Task 1) and all consumed in Task 2. `DRIFT_OPTS` keys match `DEFAULTS` keys in the helper.
