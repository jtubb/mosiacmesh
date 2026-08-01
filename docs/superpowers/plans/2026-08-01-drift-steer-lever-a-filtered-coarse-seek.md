# Lever A: gate coarse-seek on filtered err — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate spurious ~30s hard seeks by triggering the coarse-seek regime on the median-filtered err (`fErr`) instead of raw `err`, so single-tick bimodal readback spikes no longer fire a seek.

**Architecture:** One logic change in the pure `mmDriftCommand` controller (`js/drift-steer.js`) — swap the coarse trigger's operand from `err` to the already-computed `fErr`. Node `--test` unit tests prove noise-rejection and preserve genuine-drift seeking. No DOM/timer/wiring changes; `index.html`'s `driftTick` calls `mmDriftCommand` unchanged.

**Tech Stack:** ES5 JavaScript (iPad-1 constraint — no `let`/`const`/arrow/template-literal in `js/drift-steer.js`), node 20+ `node:test`.

## Global Constraints

- `js/drift-steer.js` is loaded as a classic `<script>` on the iPad-1 display client → **ES5 only** (no `let`/`const`, arrow functions, template literals, `class`). Match the existing file's style (`var`, `function`).
- `mmDriftCommand` must stay a **pure function** — no DOM, no timers, no globals beyond the exported `root.*` assignments.
- The median filter (`fErr`) is already computed inside `mmDriftCommand` at line ~59, before the coarse check at line ~62. Do not move the filter computation.
- Test file `tests/unit/js/test_drift_steer.js` uses ESM (`import { test } from 'node:test'`) and drives the ES5 module via `await import('../../../js/drift-steer.js')` + `globalThis`. New tests follow that pattern. Run with `python pytest_runner.py --js` or `node --test tests/unit/js/test_drift_steer.js`.

---

### Task 1: Gate coarse-seek on `fErr` + noise-rejection tests

**Files:**
- Modify: `js/drift-steer.js:61-62` (the coarse-regime comment + trigger)
- Test: `tests/unit/js/test_drift_steer.js` (add cases)

**Interfaces:**
- Consumes: `mmDriftState()` → `{ ring, inertCount, rateLive, lastRateCmd, probeCountdown }`; `mmDriftCommand(state, err, obs, opts)` where `obs = { rateGot, canSeek }` and the return is `{ action: 'seek'|'rate'|'hold', rate, filteredErr, rateLive }`.
- Produces: no signature change. Only the *condition* under which `action:'seek'` is returned changes (raw-err → filtered-err).

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/js/test_drift_steer.js`:

```javascript
test('coarse: single bimodal spike past COARSE does NOT seek (median rejects it)', () => {
  const s = g.mmDriftState();
  // Steady bimodal readback: true err ~-160, alternating +50 artifact. None should seek.
  const seq = [-160, 50, -160, 50, -160];
  let c;
  seq.forEach(function (e) { c = g.mmDriftCommand(s, e, { rateGot: 1, canSeek: true }); });
  assert.notEqual(c.action, 'seek', 'steady bimodal must not seek');
  // Now a single-tick readback spike to -395 (raw > 350) while the ring's median stays ~-160.
  c = g.mmDriftCommand(s, -395, { rateGot: 1, canSeek: true });
  assert.notEqual(c.action, 'seek', 'lone -395 spike must not fire a seek');
  assert.ok(Math.abs(c.filteredErr) < 350, 'filtered err stayed sub-threshold: ' + c.filteredErr);
});

test('coarse: persistent err past COARSE DOES seek (genuine drift)', () => {
  const s = g.mmDriftState();
  // Fill the ring with a sustained large negative err; median crosses COARSE.
  let c;
  [-400, -400, -400].forEach(function (e) { c = g.mmDriftCommand(s, e, { rateGot: 1, canSeek: true }); });
  assert.equal(c.action, 'seek', 'sustained -400 must seek');
});

test('coarse: fresh-state large err still seeks immediately (startup jump)', () => {
  // Empty ring -> median of one sample = that sample -> preserves fast reaction on a real jump.
  const c = g.mmDriftCommand(g.mmDriftState(), -500, { rateGot: 1, canSeek: true });
  assert.equal(c.action, 'seek');
});
```

- [ ] **Step 2: Run the tests to verify the noise-rejection one fails**

Run: `node --test tests/unit/js/test_drift_steer.js`
Expected: `coarse: single bimodal spike past COARSE does NOT seek` FAILS (current code keys on raw `err`, so the lone −395 returns `action:'seek'`). The other two new tests PASS already (they don't depend on the change), as do all pre-existing tests.

- [ ] **Step 3: Make the change**

In `js/drift-steer.js`, update the comment and trigger (lines ~61-62):

```javascript
    // coarse regime keys on FILTERED err (median rejects the single-tick bimodal
    // currentTime readback spike that otherwise fires a spurious seek; a genuine
    // persistent jump still crosses COARSE within a few ticks, and a fresh ring's
    // median equals the lone sample so real startup jumps still react fast).
    if (Math.abs(fErr) > COARSE && canSeek) {
```

(Only `err` → `fErr` in the condition; the comment is rewritten. The `state.lastRateCmd = 1` line and the returned object below it are unchanged.)

- [ ] **Step 4: Run the full JS suite to verify green**

Run: `node --test tests/unit/js/test_drift_steer.js`
Expected: all tests PASS, including the previously-failing bimodal-spike test. No pre-existing test regresses (the existing coarse tests feed single large samples whose one-element median equals the sample, so they still seek).

- [ ] **Step 5: Run the whole JS test runner**

Run: `python pytest_runner.py --js`
Expected: all JS suites PASS.

- [ ] **Step 6: Commit**

```bash
git add js/drift-steer.js tests/unit/js/test_drift_steer.js
git commit -m "fix(drift): gate coarse-seek on filtered err to kill spurious noise seeks"
```

---

### Task 2: On-wall deploy + measurement gate (operator-run)

This task is **run by the human operator / controller**, not a subagent — it deploys the client change to the fleet and measures the effect, which also gates Lever B.

**Files:** none (deploy + telemetry analysis only). `js/drift-steer.js` is served directly to clients; deployment = the fleet reloading the webclip (no dylib rebuild — this is a JS-only change).

- [ ] **Step 1: Deploy** — ensure the dev/prod server is serving the updated `js/drift-steer.js`, then reload the OEB webclips (single-screen or paced waves — never a mass `killall Web` burst, per [[fleet-ssh-no-burst]]).
- [ ] **Step 2: Let it run** ≥15 min on a video playlist with `?tdbg` telemetry flowing (`js/tdbgflag.js`).
- [ ] **Step 3: Re-run the characterization** (the scratchpad scripts used to author this plan): seek interval per screen, per-screen steady-state `ferr` (droop baseline) spread, and — decisive for B — whether `rateGot` is still railed near the ±3.5% clamp.
- [ ] **Step 4: Record the result** in `memory/wall-cross-screen-sync-state.md` + `seek-residual-next-lever.md`. Expected: seek interval ≫30s (spurious cadence gone); 128ms droop spread persists (confirms droop is the floor). The rail check selects Lever B's branch (authority-first if railed; integral-direct if headroom).

**Success criteria:** spurious 30s seek cadence eliminated with no regression in genuine-drift re-align or startup; droop baseline persists (tees up B).

## Self-Review

- **Spec coverage:** Lever A (spec §"Lever A") → Task 1. On-wall measurement gate (spec §"On-wall measurement (the gate to B)") → Task 2. Lever B is explicitly out of this plan (measure-gated, follow-on) per the spec.
- **Placeholder scan:** none — Task 1 carries full test code + the exact edit; Task 2 is an operator runbook with concrete steps.
- **Type consistency:** `mmDriftCommand` signature and return shape unchanged; only the coarse trigger operand changes. `fErr` is in scope at the edit site (computed line ~59).
