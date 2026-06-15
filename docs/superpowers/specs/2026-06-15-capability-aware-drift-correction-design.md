# Capability-aware drift correction — Design

**Date:** 2026-06-15
**Status:** ❌ REJECTED after fleet validation — superseded by the flat `DRIFT_SEEK_MS=30`
(single boundary-aligned-keyframe-seek path for all devices), shipped in `index.html`.

> **Why rejected (fleet finding):** Strategy A's *direct exact seek* for large drift
> fails on iOS-5 — `currentTime=target` keyframe-snaps AND has no seek-latency
> compensation (lead stuck at default), landing ~200ms+ behind every time. Worse,
> the capability probe only runs in the rate-nudge band (≤100ms), so iOS-5 devices
> with large drift never enter the probe → never flip to Strategy B → trapped in A,
> stuck ~340ms (≈10 frames) out of sync. The boundary-aligned keyframe seek +
> lead-tuner (which the design bypassed for "modern") is exactly what compensates
> iOS-5's keyframe + latency, and it works on modern devices too — so ONE path is
> correct for both, and the validated flat `DRIFT_SEEK_MS=30` is that path. The
> capability split's only gain (rate-nudge smoothness for modern mid-range drift)
> is marginal: modern devices have accurate clocks and rarely drift past 30ms.
> Kept for the record of why direct-seek-per-capability doesn't work here.

## Problem

`driftTick` (inline ES5 in `index.html`) keeps each client's video aligned to the
shared clock. It currently uses ONE strategy for all devices, built entirely
around iPad-1 / iOS-5 constraints:

- iOS-5 **ignores `playbackRate`** (the property is stored but `currentTime`
  advances at 1.0× regardless), so the in-band "rate-nudge" is a no-op there.
- iOS-5 seeks are **keyframe-only** (land on the 250ms `KEYFRAME_GRID_MS`), so
  the code does an elaborate **boundary-aligned seek** (timed to land on a
  keyframe as the clock arrives) plus a **lead self-tuner** (`seekAheadMs`) to
  manage the snap + seek latency.

Two consequences:

1. **iOS-5 drift was trapped** below the seek threshold: a seek-landing residual
   (e.g. −78 ms) sat under `DRIFT_SEEK_MS` (100 ms) → no re-seek → the lead-tuner
   (which only runs after a seek) never ran → the error froze. Live profiling
   confirmed ~half the fleet stuck at static 33–100 ms residuals with perfect
   clocks (one screen: prec 2.5 ms, drift −85 ms). Lowering `DRIFT_SEEK_MS` to 30
   fixed it (median 37→11 ms, max 100→25 ms, +9 stalls incl. reload) — validated
   on the live fleet, and affordable because segments are now cached on-device
   (cheap local seeks).
2. **Modern devices are mis-served.** They honor `playbackRate` AND can seek
   frame-accurately, so the iOS-5 keyframe/lead machinery is pure overhead for
   them, and a globally-narrow band would make them seek-thrash where a smooth
   rate-nudge (or an exact seek) would do better.

## Design

Branch `driftTick` into **two correction strategies**, chosen per-device by a
**self-calibrating capability flag** — no user-agent sniffing.

### Correction is a magnitude crossover, not "band vs seek"

The two correction tools have different costs:
- **rate-nudge** — smooth/invisible (trim ±1–2 % to converge over ~1 s), but slow
  and only works if the device honors `playbackRate`.
- **seek** — instant, but causes a momentary visual hitch (decoder reposition);
  on iOS-5 it is keyframe-only.

So: smooth-correct small drift, seek large drift; the threshold is the crossover.

### Strategy A — rate-capable + frame-accurate (modern)

- `|err| ≤ RATE_BAND_MS` (e.g. 100): **rate-nudge** (existing `rate = 1 −
  err/2000`, clamped). Smooth, no hitch.
- `|err| > RATE_BAND_MS`: **direct seek to the exact target**
  (`v.currentTime = pos.offsetMs / 1000`). Frame-accurate; NO keyframe grid, NO
  `seekAheadMs` lead-tuning. The seek is the fallback for jumps rate can't close
  quickly (scene change, resume, post-stall).

### Strategy B — rate-incapable + keyframe-only (iOS-5)

Unchanged from the validated experiment: narrow band + the existing
boundary-aligned keyframe seek + lead-tuner.
- `|err| > SEEK_NARROW_MS` (30): boundary-aligned keyframe seek (existing code
  path), which re-runs the lead-tuner so the residual converges.
- `|err| ≤ SEEK_NARROW_MS`: no-op (rate-nudge is dead here; seeking on jitter
  below ~30 ms would thrash).

### Capability detection (self-calibrating, no UA)

`playback.rateCapable` — default **true** (optimistic, so modern devices use
Strategy A from the first tick).

Detect ineffective rate by measuring the **effective playback rate** between
ticks: `effRate = ΔcurrentTime / Δwall`. When the code has applied a rate ≠ 1.0
(a real correction attempt) for `RATE_PROBE_TICKS` consecutive ticks and `effRate`
stays ≈ 1.0 (within a tolerance) — i.e. the rate is set but `currentTime` advances
at wall speed — set `rateCapable = false`. The device then switches to Strategy B
within ~2 s of its first real correction and converges to the tight sync.

This formalizes exactly what diagnosis observed: iOS-5 sat at −78 ms with
`rateGot = 1.039` (rate set, `currentTime` unmoved) for 18 ticks. A modern device
under the same rate ≠ 1 shows `effRate ≈ 1.039` → stays `rateCapable = true`.

(Detection is one-way: once flipped to incapable it stays — a device's media stack
doesn't gain rate support mid-session, and re-probing risks flapping.)

## Components / file map

- `index.html` (inline ES5):
  - `playback.rateCapable` (default true) + effective-rate tracker fields
    (`_lastCt`, `_lastCtWall`, `_rateProbeCount`).
  - Constants: `RATE_BAND_MS` (100), `SEEK_NARROW_MS` (30),
    `RATE_PROBE_TICKS` (~4), `RATE_EFFECT_TOL` (e.g. 0.05).
  - `driftTick`: compute `effRate`; update `rateCapable`; branch into Strategy A
    / Strategy B. Keep the existing boundary-aligned-seek + lead-tuner intact for
    Strategy B; add the simple rate-nudge + direct-exact-seek for Strategy A.
  - Extend the `dbg("drift", …)` payload with `cap` (rateCapable) + `effRate` so
    both strategies are observable in `?tdbg`.
- A **pure, node-testable helper** for the capability decision so it isn't buried
  in the untestable inline loop:
  `js/timeline/...`? — NO; the display client is ES5 and not an ES module. Instead
  extract the decision as a small pure ES5 function in `index.html` and unit-test
  its logic by mirroring it (or accept fleet-profiling as the verification, per
  the sync-gated-play precedent where `?tdbg` is the test lever).

## Error handling / edge cases

- **Default optimistic:** a modern device never sees a wrong-mode tick; an iOS-5
  device spends ≤ `RATE_PROBE_TICKS` in the wrong mode before flipping — bounded.
- **Direct seek hitch (modern):** reserved for large drift only; small/continuous
  drift stays on smooth rate-nudge, so no periodic micro-hitches.
- **Seek cooldown** still applies to both strategies (no seek storms).
- **`effRate` noise:** computed only when the video is actually playing
  (`!paused`, `readyState ≥ 3`, not seeking) and across a clean inter-tick
  interval; the `RATE_PROBE_TICKS` consecutive requirement filters single-tick
  noise.

## Testing / validation

- `?tdbg` is the primary lever (driftTick is iOS-5 ES5, not node-unit-testable),
  per the sync-gated-play precedent. The `cap`/`effRate` fields make both
  strategies observable.
- **Fleet validation:** reload → play → profile on (a) an iPad-1 (OEB Sign 1):
  confirm it flips to `rateCapable=false` and converges to the validated ~11 ms
  median / ~25 ms max; (b) a modern device (Desktop/Mobile group): confirm it
  stays `rateCapable=true`, uses rate-nudge for small drift (no seek-thrash), and
  only direct-seeks on large jumps.
- If any pure decision helper is extracted, add a node `--test` for it.

## Non-goals

- No change to GoTime clock sync, the sync-gated release, or the iOS-5
  boundary-aligned keyframe-seek math (Strategy B reuses it as-is).
- No user-agent sniffing.
- The flat `DRIFT_SEEK_MS=30` experiment value is **superseded** by this design
  (it becomes `SEEK_NARROW_MS` for Strategy B); it is not committed separately.
