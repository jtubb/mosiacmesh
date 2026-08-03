# One-Way Downlink Clock Lock (v4b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a flag-gated one-way downlink clock lock — feed the existing TIMEBEAT samples through a windowed high-percentile (max) instead of the median steer, so each screen locks to `offset − Dd_floor` (asymmetry-free) — and prove on the wall whether it tightens cross-screen clock-disagreement below the master-clock beat's ~193 ms floor.

**Architecture:** One new pure function `_downlinkTarget` in `js/GoTime.js` computes the windowed p90 of a dedicated `_beatRing` of one-way beat offsets. In `_steerTick`, when `window._mmDownlinkLock` is set, that value replaces the median `_robustTarget` as the steer target; everything downstream (skew fold, `_steerStep` slew, deadband, cap) is unchanged. Byte-identical when the flag is off. Full spec: `docs/superpowers/specs/2026-08-03-oneway-downlink-clock-lock-design.md`.

**Tech Stack:** ES5 JavaScript (iPad-1 Safari 5.1), Node 20 `--test` (vm-sandbox harness, as in `tests/unit/js/gotime-steer.test.js`), existing server-side TIMEBEAT broadcast (unchanged).

## Global Constraints

- **ES5 ONLY** in `js/GoTime.js`, `js/dllockflag.js`, and the `index.html` inline script — no `let`/`const`, arrow functions, template literals, `class`, `Promise`, `fetch`. These run on iPad-1 Safari 5.1.
- **Byte-identical behavior when `window._mmDownlinkLock` is false.** The flag defaults false; with it off, `_steerTick` must take exactly today's path (median `_robustTarget`).
- **No server change.** Reuse the existing `TIMEBEAT` broadcast and synctool. No new routes/telemetry endpoints.
- `_downlinkTarget` is **pure** (no DOM/network/timers/`Date`/`Math.random`); it takes `(beatRing, nowMs, opts)` and returns a number or `null`.
- Run JS unit tests via `python pytest_runner.py --js`.
- Sample/ring shapes: beat ring entry `{offset, t}` where `offset = serverSendMs − localRecv` and `t` is `GoTime.now()` at push. `_steerTick`'s `nowMs` is `GoTime.now()`.

---

### Task 1: `_downlinkTarget` pure function + unit tests

**Files:**
- Modify: `js/GoTime.js` — add `_downlinkTarget` (near `_robustTarget`, before the public return object) + export it on the `GoTime` object next to `_robustTarget: _robustTarget`.
- Create: `tests/unit/js/gotime-downlink.test.js` — the pure-function tests (own vm-sandbox harness copied from `gotime-steer.test.js`).

**Interfaces:**
- Consumes: nothing (pure).
- Produces: `GoTime._downlinkTarget(beatRing, nowMs, opts)` → number or `null`. `beatRing` = array of `{offset, t}`. `opts` may carry `dlWindowMs` (default 90000), `dlPct` (0.90), `dlMinSamples` (12). Filters to entries with `t >= nowMs - dlWindowMs`; if fewer than `dlMinSamples`, returns `null`; else returns the nearest-rank `dlPct` percentile of the in-window offsets.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/js/gotime-downlink.test.js`:

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
function close(a, b, tol) { assert.ok(Math.abs(a - b) <= (tol || 0.001), a + ' ~= ' + b); }
// ring entry at time `t` carrying offset = trueOffset - Dd
function r(offset, t) { return { offset: offset, t: t }; }

test('_downlinkTarget: additive queueing -> p90 recovers offset - Dd_floor', () => {
  const s = loadGoTime(), now = 1000000;
  // offset=500; Dd in {10x6,25,40,60,80,100,100} -> ring offset = 500 - Dd
  const dds = [10,10,10,10,10,10,25,40,60,80,100,100];
  const ring = dds.map(function (dd, i) { return r(500 - dd, now - i * 2000); });
  close(s.GoTime._downlinkTarget(ring, now, {}), 490, 1); // 500 - Dd_floor(10)
});

test('_downlinkTarget: out-of-window samples excluded', () => {
  const s = loadGoTime(), now = 1000000;
  const ring = [r(9999, now - 200000)]; // stale, older than dlWindowMs -> dropped
  for (let i = 0; i < 12; i++) { ring.push(r(490, now - i * 1000)); } // fresh floor
  close(s.GoTime._downlinkTarget(ring, now, {}), 490, 1); // stale 9999 not counted
});

test('_downlinkTarget: p90 (not literal max) rejects a single glitch', () => {
  const s = loadGoTime(), now = 1000000;
  const ring = [];
  for (let i = 0; i < 40; i++) { ring.push(r(488, now - i * 1000)); } // clean floor
  ring.push(r(9999, now - 40000));                                    // one glitch
  close(s.GoTime._downlinkTarget(ring, now, {}), 488, 1);
});

test('_downlinkTarget: below dlMinSamples -> null', () => {
  const s = loadGoTime(), now = 1000000;
  const ring = [];
  for (let i = 0; i < 8; i++) { ring.push(r(490, now - i * 1000)); } // 8 < 12
  assert.equal(s.GoTime._downlinkTarget(ring, now, {}), null);
});

test('_downlinkTarget: empty ring -> null', () => {
  const s = loadGoTime();
  assert.equal(s.GoTime._downlinkTarget([], 1000000, {}), null);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python pytest_runner.py --js`
Expected: FAIL — `s.GoTime._downlinkTarget is not a function`.

- [ ] **Step 3: Implement `_downlinkTarget`**

In `js/GoTime.js`, add just before the public `return { ... }` object (near `_robustTarget`):

```js
    // Downlink-lock target (v4b): the windowed high-percentile (robust MAX) of recent
    // one-way beat offsets. Each beat offset = serverSendMs - localRecv = trueOffset - Dd;
    // queueing only lowers it, so the MAX recovers trueOffset - Dd_floor (asymmetry-free).
    // Returns null when too few in-window samples -> _steerTick falls back to _robustTarget.
    // opts: { dlWindowMs=90000, dlPct=0.90, dlMinSamples=12 }. NOT deskewed: drift over the
    // window (~1.7ms at -19ppm) is negligible vs the ~45ms downlink jitter the max spans.
    _downlinkTarget = function (beatRing, nowMs, opts) {
        opts = opts || {};
        var windowMs = (opts.dlWindowMs   != null) ? opts.dlWindowMs   : 90000;
        var pct      = (opts.dlPct        != null) ? opts.dlPct        : 0.90;
        var minS     = (opts.dlMinSamples != null) ? opts.dlMinSamples : 12;
        var i, offs = [];
        for (i = 0; i < beatRing.length; i++) {
            if (beatRing[i].t >= nowMs - windowMs) { offs.push(beatRing[i].offset); }
        }
        if (offs.length < minS) { return null; }
        offs.sort(function (a, b) { return a - b; });
        var idx = Math.ceil(pct * offs.length) - 1;   // nearest-rank
        if (idx < 0) { idx = 0; }
        if (idx >= offs.length) { idx = offs.length - 1; }
        return offs[idx];
    };
```

Declare `_downlinkTarget` in the same `var`/private declaration group as `_robustTarget`/`_steerStep` (match the file's existing pattern for private function vars). Add to the public return object, beside `_robustTarget: _robustTarget,`:

```js
        _downlinkTarget: _downlinkTarget,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python pytest_runner.py --js`
Expected: PASS — all existing tests plus the 5 new `_downlinkTarget` cases.

- [ ] **Step 5: Commit**

```bash
git add js/GoTime.js tests/unit/js/gotime-downlink.test.js
git commit -m "feat(gotime): _downlinkTarget windowed-max one-way downlink estimator + tests"
```

---

### Task 2: Wire the flag, beat ring, steer injection, telemetry

**Files:**
- Modify: `js/GoTime.js` — options (`_beatRing`, `_dlWindowMs`, `_dlPct`, `_dlMinSamples`, `_lastDlTarget`), `setOptions` mappings, `beatReceived` push, `_steerTick` injection, `getSteerState` expose `dlTarget`.
- Create: `js/dllockflag.js` — the A/B flag.
- Modify: `index.html` — load `dllockflag.js`; add `dl` + `dlt` to the `?tdbg` heartbeat payload.
- Modify: `tests/unit/js/gotime-downlink.test.js` — the steer-integration test.

**Interfaces:**
- Consumes: `GoTime._downlinkTarget` (Task 1); the existing `beatReceived`, `_steerTick`, `getSteerState`, `_robustTarget`, `_steerStep`.
- Produces: `window._mmDownlinkLock` (bool, default false); `options._beatRing` (pushed in `beatReceived`); `GoTime.getSteerState().dlTarget` (number or null); `?tdbg` payload fields `dl` (0/1) + `dlt` (ms or null).

- [ ] **Step 1: Write the failing integration test**

Append to `tests/unit/js/gotime-downlink.test.js`:

```js
test('steer integration: _mmDownlinkLock routes _steerTick to the p90 max, off = median', () => {
  // Seed 12 beats: ten at offset 445, two at 490. Median (robustTarget) = 445; p90 = 490.
  function seed(s) {
    s.__setNow(2000000);
    var offs = [445,445,445,445,445,445,445,445,445,445,490,490], i;
    for (i = 0; i < offs.length; i++) { s.GoTime.beatReceived(2000000 + offs[i]); }
  }
  // flag ON -> downlink max target
  var on = loadGoTime();
  on.window._mmDownlinkLock = true;
  seed(on);
  on.GoTime._steerTick();
  close(on.GoTime.getSteerState().target, 490, 1);   // p90 max, NOT the 445 median

  // flag OFF -> today's median target (byte-identical path)
  var off = loadGoTime();
  off.window._mmDownlinkLock = false;
  seed(off);
  off.GoTime._steerTick();
  close(off.GoTime.getSteerState().target, 445, 1);   // median
});
```

Run: `python pytest_runner.py --js` → FAIL (`_beatRing` not populated / injection absent → both arms give the median, ON arm ≠ 490).

- [ ] **Step 2: Add the options + setOptions knobs**

In the `options` object (after `_beatStaleMs: 6000`), add:

```js
        ,_beatRing: []
        ,_dlWindowMs: 90000
        ,_dlPct: 0.90
        ,_dlMinSamples: 12
        ,_lastDlTarget: null
```
(Match the object's existing comma/format style — the leading-comma form above is illustrative; insert valid properties consistent with the file.)

In `setOptions` (after the `SteerPrecisionFloorMs` mapping):

```js
            if (opts.DlWindowMs != null) { options._dlWindowMs = opts.DlWindowMs; }
            if (opts.DlPct != null) { options._dlPct = opts.DlPct; }
            if (opts.DlMinSamples != null) { options._dlMinSamples = opts.DlMinSamples; }
```

- [ ] **Step 3: Push to the beat ring in `beatReceived`**

In `beatReceived` (GoTime.js ~609), after the existing `_reviseOffset` call, capture its result and push the pure downlink sample:

```js
        beatReceived: function (serverSendMs) {
            var localRecv = getAccurateTimestamp();
            options._lastBeatMs = localRecv;
            var sample = _beatSample(serverSendMs, localRecv, options._beatPrecisionMs);
            var revised = _reviseOffset(sample, "beat");
            options._beatRing.push({ offset: sample.offset, t: GoTime.now() });   // v4b: pure one-way ring
            while (options._beatRing.length > 64) { options._beatRing.shift(); }
            return revised;
        },
```

- [ ] **Step 4: Inject the downlink target in `_steerTick`**

In `_steerTick`, replace the target computation (GoTime.js ~442-444) with:

```js
        var target = _robustTarget(_deskewSamples(options._steerSamples, options._skew, options._skewRef),
                                   GoTime.now(), opts);
        options._lastDlTarget = null;
        if (typeof window !== 'undefined' && window._mmDownlinkLock) {
            var dlt = _downlinkTarget(options._beatRing, GoTime.now(), opts);
            options._lastDlTarget = dlt;
            if (dlt !== null) { target = dlt; }   // else fall back to the median target
        }
        if (target === null) { return; }
```

Add the dl knobs to the `opts` object built at the top of `_steerTick` (after `precisionFloorMs: ...`):

```js
            , dlWindowMs: options._dlWindowMs, dlPct: options._dlPct, dlMinSamples: options._dlMinSamples
```
(again, insert as valid properties matching the object's style).

- [ ] **Step 5: Expose `dlTarget` in `getSteerState`**

In `getSteerState` return object, add:

```js
                     , dlTarget: options._lastDlTarget
```

- [ ] **Step 6: Create the flag file**

Create `js/dllockflag.js`:

```js
/* js/dllockflag.js — A/B toggle for the one-way downlink clock lock (v4b).
 * Only meaningful when _mmClockBeat is also on: switches the beat steer's
 * target from the median (_robustTarget) to the windowed-max (_downlinkTarget).
 * Default false -> GoTime behaves byte-identically to today. */
window._mmDownlinkLock = false;
```

- [ ] **Step 7: Load the flag + add telemetry in `index.html`**

Add the script tag next to `beatflag.js` (index.html ~30):

```html
  <script src="/js/dllockflag.js"></script>
```

In the `?tdbg` heartbeat payload (after the `skew:` field, index.html ~656), add:

```js
				dl: (typeof window !== 'undefined' && window._mmDownlinkLock) ? 1 : 0,
				dlt: (function () { var _ss = (typeof GoTime !== 'undefined' && typeof GoTime.getSteerState === 'function') ? GoTime.getSteerState() : null; return (_ss && _ss.dlTarget != null) ? Math.round(_ss.dlTarget) : null; })(),
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python pytest_runner.py --js`
Expected: PASS — the integration test now shows target 490 (flag on) vs 445 (flag off), and all existing GoTime/steer/beat tests still pass (flag-off path unchanged).

- [ ] **Step 9: Commit**

```bash
git add js/GoTime.js js/dllockflag.js index.html tests/unit/js/gotime-downlink.test.js
git commit -m "feat(gotime): wire _mmDownlinkLock flag -> beat ring + steer injection + tdbg telemetry"
```

---

### Task 3: iPad-1 on-wall A/B (manual — controller runs)

**This task is manual and gated on the fleet being up + on a video/animation playlist. Not code — it produces the go/no-go for v4c (server trim).** The subagent runner should mark it for the human controller, not dispatch an implementer.

**Files:** none.

- [ ] **Step 1: Deploy** — fleet reload (GoTime.js / index.html / dllockflag.js are server-served; no dylib rebuild). Confirm `_mmClockBeat` is already on and the server is broadcasting `TIMEBEAT`.

- [ ] **Step 2: Baseline (flag OFF)** — put "OEB Sign 1" on an **animation** playlist (pure clock probe). Arm once, wait for settle, then sample synctool over many windows at recorded elapsed-since-reload. Record the clock-disagreement distribution. Repeat on **PullTest3** (video).

- [ ] **Step 3: Flag ON** — flip `_mmDownlinkLock` (edit `js/dllockflag.js` → reload, or set `window._mmDownlinkLock=true` via the same soft-reload mechanism used for other flags). Confirm on-wall via the `?tdbg` payload: `dl:1` and a non-null `dlt` on each screen. Re-run Step 2's measurement at **matched elapsed-since-reload**.

- [ ] **Step 4: Compare** — clock-disagreement distributions OFF vs ON, matched elapsed, both playlists. video-offset must be unchanged (~150-220 ms); only clock-disagreement should move. Watch for the time-dependence confound: never compare single post-reload snapshots (both read ~90 ms fresh).

- [ ] **Step 5: Decision** — if clock-disagreement drops from the ~193 ms median-beat floor toward tens of ms, the client lock works → keep `_mmDownlinkLock` on (candidate default flip) and decide whether a systematic residual justifies v4c (server trim). If it doesn't improve, record the residual's character and stop. Update `memory/wall-cross-screen-sync-state.md` either way.

---

## Self-Review

**Spec coverage:** design §"Components" → Task 1 (`_downlinkTarget`) + Task 2 (flag, `_beatRing`, injection, telemetry); §"Unit tests" 6 cases → Task 1 (5 pure) + Task 2 (integration); §"On-wall A/B protocol" → Task 3. §"Non-goals" (no server trim, no median-path change, no default flip) respected — flag defaults false, only the target source changes under it. ✓

**Placeholder scan:** every step has runnable code/commands. The options-object insert notes "match the file's comma/format style" because GoTime's object literal style must be followed exactly — the properties themselves (`_beatRing`, `_dlWindowMs`, `_dlPct`, `_dlMinSamples`, `_lastDlTarget`) and values are fully specified, not a TODO. ✓

**Type consistency:** ring entry `{offset, t}` is identical across `beatReceived` push (Task 2), `_downlinkTarget` filter (Task 1), and the tests. `dlWindowMs`/`dlPct`/`dlMinSamples` defaults (90000/0.90/12) match across the function, the options block, and the tests (which pass `{}` → defaults). `getSteerState().dlTarget` (Task 2 step 5) feeds `index.html`'s `dlt` (Task 2 step 7) and is set from `options._lastDlTarget` (Task 2 step 4). The integration test's 445-median vs 490-p90 split is arithmetic-checked: sorted `[445×10, 490, 490]`, median = 445, nearest-rank p90 idx = `ceil(0.9·12)−1 = 10` → `offs[10] = 490`. ✓
