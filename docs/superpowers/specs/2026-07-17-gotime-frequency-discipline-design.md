# Phase 2 — Frequency-disciplined GoTime (skew correction)

**Date:** 2026-07-17
**Status:** design, pending review
**Context:** Phase 2 of the "better playback coordination" track (Phase 1 = ws transport fix,
merged; Phase 3 = mmvideo `file:///`). Phase 1 put the fleet on a persistent real websocket, so
clean low-jitter `SERVERTIME` clock samples are now available — the input quality this design
relies on.

## Problem

Screens **drift apart over time** — worst during fast motion and at effect boundaries/transitions.
This is an oscillator **frequency** (rate) error: each iPad-1's clock runs slightly fast/slow, so
`GoTime.now()` advances at the wrong rate and every screen's playback position (`now() − startEpoch`)
separates.

`js/GoTime.js` today models the clock as **offset only**: `now() = getAccurateTimestamp() + _offset`,
with `_offset` steered toward a robust-median target at a capped **15 ms/s** slew (`_steerStep`).
Nothing estimates or corrects the **rate**:
- The offset steer only *reactively tracks* the drift by chasing a moving target — it lags, is
  capped, and never removes the underlying rate error.
- The `ProgrammableTimer` "drift loop" (`_tick`) corrects `setTimeout` **scheduling jitter relative
  to `now()`** — NOT the oscillator rate. So the beat rides whatever drift `now()` has.

Result: the rate error is uncorrected anywhere, and playback drifts apart.

## Goal / non-goals

- **Goal:** correct each client's oscillator skew so `now()` advances at the server's rate,
  eliminating drift-apart. Keep the offset behavior byte-identical when skew is unknown/zero.
- **Non-goal:** rewriting the field-proven offset steer / ratchet / tuning. Skew is an **additive**
  layer; the steer's algorithm is untouched (it only receives de-trended samples).
- **Non-goal:** server changes. This is client-side `js/GoTime.js` only.

## Design

### Clock model

`now()` (currently `js/GoTime.js:~384`) gains one additive rate term:

```
now() = getAccurateTimestamp() + _offset + _skew · (getAccurateTimestamp() − _skewRef)
```

New state on the GoTime `options` struct: `_skew` (fraction, e.g. 22 ppm = 22e-6; default 0) and
`_skewRef` (raw-local anchor time; default 0). With `_skew = 0` this is byte-identical to today.

### Estimator — `_skewFit(samples, nowMs, opts)` (pure)

- Input: the existing `_steerSamples` ring (`{offset, precision, t}`, ≤64 samples).
- Filter to a window (`windowMs`, reuse ~120 s) and apply the SAME precision gate as
  `_robustTarget`: `gate = max(2·bestInWindow, precisionFloorMs)`.
- Robust slope via **Theil-Sen**: median of pairwise slopes `(off_j − off_i)/(t_j − t_i)` over gated
  samples (n≤64 → ~2k pairs, trivial). Median-of-slopes mirrors the codebase's robust-median
  philosophy and rejects PSM-jitter outliers.
- Return `null` unless `≥ minSamples` gated samples span `≥ minSpanMs` time baseline (defaults: 6
  samples, 60 s) — a slope over a short baseline is noise.

### Steer reconciliation (avoids double-correction)

The offset steer and the skew term both derive from the same samples; left alone they BOTH remove
the drift, and the error **accumulates** (each beat the steer re-adds the drift the skew term
already removed → runaway). Fix: `_steerTick` (`js/GoTime.js:~287`) feeds `_robustTarget`
**de-skewed** samples — `sample.offset − _skew · (sample.t − _skewRef)`. Skew owns the *rate*; the
steer owns the residual *offset*. `_steerStep` / `_robustTarget` / deadband / snap / cap / ratchet
are unchanged — they just see de-trended input. (Once skew removes the linear trend the residual is
~stationary, so the steer sits inside its 33 ms deadband — the "settled beat" state the current
tuning protects.)

### Continuity-preserving update — `_applySkew(model, newSkew, rawNow)` (pure)

When a new fit changes `_skew`, `now()` must not jump. At the update instant (raw = R):

```
_offset  += _skew_old · (R − _skewRef_old)   // absorb the old rate term
_skewRef  = R
_skew     = newSkew
```

Evaluated at R, `now()` is continuous; after R it advances at the new rate. A discontinuity here IS
a desync — this invariant is the most important one to test.

### Bounds, guards, cadence

- Clamp `_skew` to **±300 ppm** (real oscillators ~±100 ppm; stops a bad fit from running away).
- `_skewFit` → `null` ⇒ **hold the last good skew** (rate is a near-constant physical property; on
  sample loss/reconnect keep correcting, don't snap to 0 and reintroduce drift).
- **Gate the first estimate** on `_steering === true` (the offset has locked) plus the
  min-samples/min-baseline test — no skew fitting during initial convergence.
- **Skew deadband:** apply a new fit only if it differs from current by > ~2 ppm, so the model isn't
  churned every beat.
- **Cadence:** fold the re-fit into the existing steer beat (~30 s). No new timer; the fit reads the
  windowed ring so it always has the baseline, and the deadband keeps updates rare.

## Data flow

```
sample arrives (_reviseOffset) → pushed to _steerSamples ring (unchanged)
steer beat (_steerTick, ~30s):
    newSkew = _skewFit(ring, now, opts)            # robust Theil-Sen slope, or null
    if newSkew != null and |newSkew − _skew| > 2ppm and _steering:
        {_offset,_skew,_skewRef} = _applySkew(model, clamp(newSkew), rawNow())   # continuity fold
    target = _robustTarget( deskew(ring, _skew, _skewRef), now, opts )           # de-trended
    _offset = _steerStep(_offset, target, dt, opts)                              # unchanged algo
now() = raw + _offset + _skew·(raw − _skewRef)      # rate-corrected, continuous
```

## Testing

- **`_skewFit` (node):** recovers a known slope from synthetic `a + b·t + noise`; a jittery outlier
  barely moves it; `null` on too-few-samples / too-short-baseline; precision gate drops high-RTT
  samples.
- **`_applySkew` (node):** `now()` at the same raw instant just before/after a skew change is EQUAL
  (continuity), then advances at the new rate; `_skew` clamped to ±300 ppm.
- **De-skew (node):** under simulated linear drift, the steer's de-skewed target is stationary — the
  offset stays inside the 33 ms deadband, no accumulation.
- **Integration (node):** a simulated client with fixed skew + noisy samples over an hour keeps
  `now()` within a tight bound of server time, where the offset-only model drifts linearly.
- **On-wall:** add `skew` to the `?tdbg` payload; confirm cross-client drift-apart is gone over a
  long run and `?tdbg` offset stays flat instead of ramping.

## Success criteria

Cross-client `now()` spread stays tight (~<10–20 ms) over hours with no linear ramp; visually, no
drift-apart at fast motion / transitions.

## Constraints

- **ES5 only** (`js/GoTime.js` runs on iPad-1 Safari 5.1) — no let/const/arrow/class.
- Pure helpers (`_skewFit`, `_applySkew`, de-skew) node-testable via the existing GoTime test
  harness pattern.
- The offset steer's ALGORITHM and tuning are untouched; only its input (de-skewed samples) and the
  `now()` formula change. `_skew = 0` ⇒ behavior identical to today (safe default / rollback).

## Risks

- **Bad slope on jittery radios:** mitigated by the precision gate + Theil-Sen robustness + min
  baseline + ±300 ppm clamp + 2 ppm deadband. Worst case a `null` fit holds the last good skew.
- **Continuity bug:** a non-continuous `_applySkew` desyncs — covered by the before/after-equal test.
- **Interaction with the ratchet:** skew is gated on `_steering` so it never runs during the initial
  monotonic-precision lock.
