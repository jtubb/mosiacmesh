# mmvideo-aware drift steer (damped rate-PLL) — design

**Date:** 2026-07-27
**Branch:** `feat/drift-steer-rate-pll`
**Scope:** video-steer only (`index.html` `driftTick`). The GoTime frequency-discipline
skew clamp is explicitly OUT of scope (separate follow-up).

## Problem

On the OEB Sign 1 wall, cached single-segment playback (PullTest3) sits at a
**~140 ms median on-device drift error that oscillates ±150 ms** and never
converges. Per-tick telemetry shows the video `playbackRate` being slammed
between **1.15 and 0.94 (±15 %) every ~1 s**, with the error sign-flipping every
2–3 ticks — the textbook signature of an under-damped feedback loop.

### Root cause

The drift steer in `index.html` (`driftTick`, `setInterval(…, 500)`) was tuned for
**stock iOS-5, where `HTMLMediaElement.playbackRate` is a no-op**. Under that
assumption the design:

- dropped the seek threshold to `DRIFT_SEEK_MS = 30` (aggressive seeking as the
  only real correction), and
- treated the fine-correction line `rate = 1 - errorMs / 2000` (clamped ±15 %) as
  a harmless no-op.

The fleet now runs the **mmvideo transplant, where `playbackRate` is live** (the
telemetry `rateGot` shows 1.15/0.94, not pinned to 1.0). So the formerly-dormant
rate term is now a **±15 % actuator driven at 2 Hz by an error measurement that
carries ±150 ms of phase/read jitter**. It amplifies measurement noise into real
position error, and the `30 ms` seek threshold means it also thrashes the seek
path. Two correction mechanisms fight each other. Nothing in the steer code
changed — the platform under it did (this is why the floor appeared with no code
regression).

### Non-goals

- **Clamped-skew screens (screen5/8/19, +300 ppm rail).** Their *clock* (`GoTime.now()`)
  is systematically offset, so a perfect video-PLL locks them tightly to a *wrong*
  target → they settle to a **steady offset** rather than oscillating. That is an
  accepted outcome here; the skew-clamp root cause is a separate thread.
- **The 6 non-playing screens (mmvideo cold-load stall).** Separate reliability
  thread.

## Approach

A single **damped rate-PLL that observes rate-liveness** instead of assuming it
(chosen over a hard platform flag precisely because a stale platform assumption is
what caused this bug). Two regimes keyed on error **magnitude**, not platform.

### Section 1 — Control law

`driftTick` still fires every 500 ms and computes
`errorMs = v.currentTime*1000 - pos.offsetMs` (`<0` = behind, `>0` = ahead).
The correction becomes two-regime:

- **Coarse regime** — `|err| > COARSE_SEEK_MS` (~350 ms) or startup: keep the
  existing **boundary-aligned seek** + seekAhead lead-tuning unchanged. Correct for
  big jumps / a fresh GO (a screen 28 s behind must seek, not rate-crawl). Rate
  reset to 1 after a seek. `SEEK_COOLDOWN_MS` and `KEYFRAME_GRID_MS` unchanged.
- **Fine regime** — `|err| ≤ COARSE_SEEK_MS`: a gently-damped rate PLL replacing
  `1 - err/2000`. Three coupled changes kill the limit cycle:
  1. **Filter the input** — median-of-`ERR_FILTER_N` (N = 5, ~2.5 s) over recent
     err samples. React to the trend, not per-sample jitter.
  2. **Gentle gain + tight clamp** — `RATE_CLAMP` ≈ ±0.035 (±3.5 %), gain sized to
     correct the *filtered* err over several seconds. No single tick injects
     more than ~30 ms.
  3. **Deadband** — `|filtered err| < FINE_DEADBAND_MS` (~20 ms) ⇒ command
     `rate = 1`. Below the noise floor, do nothing. This is what stops the hunt.

The three fixes map 1:1 to the three failure mechanisms (noise amplification →
filter; overshoot → gentle gain/clamp; hunting-at-floor → deadband). All three are
required; any one alone is insufficient.

### Section 2 — Rate-liveness auto-detect

After commanding `rate ≠ 1`, read back `v.playbackRate`. If it fails to track the
command (`|rateGot − rateCmd| > LIVENESS_EPS`) for `LIVENESS_K` consecutive fine-
regime ticks, mark the screen **rate-inert** and fall back to **seek-primary**
correction (old behavior, but with the raised `COARSE_SEEK_MS` threshold so it does
not thrash). Re-probe periodically: if `rateGot` later tracks a command, resume the
PLL. The controller observes the platform rather than assuming it.

### Section 3 — Structure & testability

Extract the decision as a **pure ES5 function**:

```
mmDriftCommand(state, errSample, opts) ->
    { action: 'seek' | 'rate' | 'hold', rate, filteredErr, rateLive }
```

`state` carries the err ring buffer + liveness counters (caller owns it across
ticks). All logic — filter ring, regime select, gain, deadband, liveness state
machine — lives here. `driftTick` becomes a thin wrapper: read err, call
`mmDriftCommand`, apply the action (schedule boundary seek / set `playbackRate` /
do nothing) and feed back the observed `rateGot`. Matches the existing pure-helper +
node-test pattern (`_steerStep`, `mmMeshTransform`, `mmSlideOffset`).

**Node tests** (`tests/unit/js/`):
- A jittery err sequence (constant true offset + ±150 ms noise) produces a **gentle,
  non-oscillating** rate (no sign-flipping, within clamp) and the filtered err
  **converges** toward 0.
- Regime transition exactly at `COARSE_SEEK_MS` (seek above, rate below).
- Deadband: `|filtered err| < FINE_DEADBAND_MS` ⇒ `action:'hold'`, `rate:1`.
- Rate-inert: commanded rate not reflected in `rateGot` for `K` ticks ⇒ falls back
  to seek-primary; recovery when `rateGot` tracks again.

### Section 4 — Constants

New (replace `DRIFT_SEEK_MS = 30`):

| constant | value (initial) | meaning |
|---|---|---|
| `COARSE_SEEK_MS` | 350 | coarse/fine regime boundary |
| `FINE_DEADBAND_MS` | 20 | below this filtered err, hold rate=1 |
| `RATE_CLAMP` | 0.035 | max fractional rate delta (±3.5 %) |
| `RATE_GAIN` | 0.0002 | rate delta per ms of filtered err (`rateDelta = −filteredErr·RATE_GAIN`, clamped to ±`RATE_CLAMP`). Initial 0.0002 closes a 100 ms err at ~2 % → ~5 s settle. Sign: err<0 (behind) ⇒ rate>1. |
| `ERR_FILTER_N` | 5 | median filter window (samples) |
| `LIVENESS_EPS` | 0.02 | rateGot-vs-cmd tolerance |
| `LIVENESS_K` | 4 | consecutive inert ticks → seek-primary |

Unchanged: `KEYFRAME_GRID_MS` (250), `SEEK_COOLDOWN_MS` (2500), seekAhead lead-tuning.

Values are initial; final tuning of `RATE_GAIN`/`RATE_CLAMP`/`FINE_DEADBAND_MS`
comes from the on-wall A/B.

### Section 5 — Validation

- **Node tests** above (pure helper).
- **On-wall A/B** on PullTest3 / OEB Sign 1 via the existing drift telemetry +
  `driftsnap.py`: before (~140 ms median, ±150 ms sign-flipping) vs after.
  Success = limit cycle gone (no sign-flipping), healthy-screen pack median well
  under 66 ms (expected 20–40 ms). Clamped screens settle to a steady offset (not
  oscillating) — expected, not a regression.

### Section 6 — Rollout & constraints

- **ES5 only** — `index.html` inline script + the new pure helper must run on
  iPad-1 / Safari 5.1: no `let`/`const`/arrow/template-literal/`class`/`Promise`.
- **No dylib rebuild** — this is server-served JS. Deploy = file change + fleet
  **RELOAD** to re-fetch `index.html`.
- Wall is in maintenance → A/B freely.
- Keep the coarse-seek machinery as-is (it is not implicated in the oscillation);
  only the fine-regime rate term and the regime threshold change.

## Risks

- **Too-gentle fine gain** → slow to close a mid-size (100–300 ms) error. Mitigated
  by the coarse-seek regime handling anything > 350 ms and by tuning gain on-wall.
- **Median filter latency** (~2.5 s) delays response to a genuine step. Acceptable:
  genuine steps that large cross into the seek regime; the fine regime only handles
  small residuals where latency is harmless.
- **Rate-liveness false-negative** (mmvideo live but rateGot briefly lags) → a
  transient drop to seek-primary. Bounded by `LIVENESS_K` + periodic re-probe; worst
  case is temporarily the *old* seek behavior, not worse.
