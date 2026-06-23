# Animation Clock-Drift Correction — Design

**Date:** 2026-06-22
**Status:** Approved (design); pending implementation plan
**Area:** `js/GoTime.js` (clock), with a documented invariant about `index.html`'s `driftTick`

## Goal

Keep clock-driven playback (animations *and* video) synchronized across the wall on
**long-running** playlists, by correcting drift in the shared time reference
smoothly — without the on-screen "snap" that a single-sample offset re-lock causes,
and without reintroducing the jitter-chasing that a prior re-lock attempt suffered.

This is item "B" of a two-part effort. Item "A" (already implemented) guards the
*render path* so an animation holds black until the clock is ready the first time
(pre-sync / fresh-join starts). B addresses *ongoing* drift during playback.

## Background / root cause

Playback position is a pure function of the shared clock, recomputed every frame:

```js
// index.html runScriptLoop:   rawElapsed = GoTime.now() - playback.startEpoch
// js/GoTime.js:               now() = getAccurateTimestamp() + options._offset
```

`_offset` is set by a **monotonic precision ratchet** (`_reviseOffset`): it only
updates when a new sample's precision (RTT/2) beats the best ever seen. After the
first good lock it effectively **freezes**. That freeze is deliberate — a previous
"decaying re-lock" chased fresh samples and, on PSM-jittery iPad-1 radios, kept
re-locking onto 90–190 ms-RTT samples, moving the offset out from under an
already-settled beat. (Samples keep arriving — every 60 s at runtime, set by
`SyncInterval: 60000` in `js/mosiacmesh.js:197` — but the ratchet discards them
unless precision improves, so a faster cadence alone does not help; the frozen
offset is the problem.)

Consequences:

- The constant inter-device offset is corrected at lock time, but **ongoing
  oscillator rate drift is not** — two crystals tick at slightly different ppm, so
  each device's `now()` slowly diverges over minutes.
- `ProgrammableTimer`'s median-drift loop only smooths its **own 1 s heartbeat**
  (feeds `isSynced()`); it reads `now()` but never writes `_offset`, so it does
  nothing for playback position.
- `driftTick` (video) is a pure **follower** of `now()`: it reads the clock to build
  a target and corrects the *video element* (rate/seek); it never writes `_offset`.
  It therefore makes each video match its **own** drifting `now()` — so video also
  drifts cross-screen on long runs, for the same root cause.

Because the drift lives in the **shared reference** both content types derive from,
the correct fix is global: steer `_offset` itself. `driftTick` then keeps following
the corrected clock — the two **layer** (reference-correction beneath element-follow),
they do not compound.

## Decisions (locked during brainstorming)

1. **Correction feel:** hybrid — deadband (ignore tiny error) / bounded slew (smooth)
   / snap (large error only).
2. **Scope:** global — steer `GoTime._offset`; benefits animations and video.
3. **No compounding:** `driftTick` is and must remain a pure follower; the slew rate
   is capped well under `driftTick`'s correction authority.
4. **Algorithm:** median of recent precision-gated samples + bounded hybrid slew
   (approach A1). An explicit PLL/rate-slope estimator (A2) was rejected as
   unnecessary complexity — frequent bounded re-anchoring absorbs rate drift.

## Architecture / components

All new logic lives in `js/GoTime.js`, exposed as **pure functions** (node-testable,
mirroring the existing `readyVerdict`). The tick wiring and `setOptions` plumbing are
thin glue.

1. **Sample ring.** `_reviseOffset` already computes `{offset, precision}` per sample.
   Push each as `{offset, precision, t}` into a bounded recent-window buffer (revive
   the currently-commented `_history`, capped to the window).

2. **`_robustTarget(samples, nowMs, opts)` — pure.** Returns the steer target offset,
   or `null` when there is not enough trustworthy data:
   - keep samples with `t >= nowMs - opts.windowMs`;
   - compute `bestInWindow = min(precision)` over those;
   - `gate = max(2 * bestInWindow, opts.precisionFloorMs)`;
   - keep samples with `precision <= gate`;
   - if fewer than `opts.minSamples` remain, return `null`;
   - return the **median** of the remaining offsets.

3. **`_steerStep(offset, target, dtMs, opts)` — pure.** The hybrid rule:
   - `err = target - offset`;
   - `|err| <= opts.deadbandMs` → return `offset` (unchanged);
   - `|err| >= opts.snapMs` → return `target` (one step);
   - else → `offset + clamp(err, ±ratePerMs * dtMs)` where
     `ratePerMs = opts.capMsPerSec / 1000`.

4. **Steering tick.** On each `ProgrammableTimer` beat (~1 s), compute
   `target = _robustTarget(ring, GoTime.now(), opts)`; if non-null,
   `options._offset = _steerStep(options._offset, target, dtMs, opts)`. `dtMs` is the
   wall time since the previous steer tick (so a skipped/stalled beat doesn't
   over-correct beyond the per-second cap × elapsed).

5. **Ratchet hand-off.** A `_steering` flag starts `false`.
   - `_reviseOffset` *always* records the sample and keeps `_precision` (best-ever)
     for `?tdbg`.
   - While `!_steering`, `_reviseOffset` also writes `_offset` (existing ratchet →
     fast initial lock).
   - Once the ring holds `>= minSamples` gated samples, `_steering` flips `true`; from
     then on **only** the steering tick writes `_offset`, so a late low-RTT sample
     cannot step it behind the slew.

6. **Sampling cadence.** The runtime samples every 60 s (`SyncInterval: 60000`,
   `js/mosiacmesh.js:197`; GoTime.js's `900000` default is overridden and never used).
   Lower that override to `30000` so a ~120 s window reliably holds ≥`minSamples`
   samples (~2 WS time-syncs/min — negligible). The initial burst
   (`_syncInitialTimeouts` 0/3/9/18/45 s) is unchanged.

## Parameters (defaults; all overridable via `GoTime.setOptions`)

| Param | Default | Rationale |
|-------|---------|-----------|
| `steerDeadbandMs` | 33 | ~1 frame @30fps; below this, inaction is invisible and avoids jitter-chasing |
| `steerSnapMs` | 500 | above this (post-PSM-stall), a big desync is worse than one snap |
| `steerCapMsPerSec` | 15 | content plays ≤1.5% fast/slow while slewing: imperceptible |
| `steerWindowMs` | 120000 | window the median is taken over; ≥3 samples at the 30 s cadence |
| `steerMinSamples` | 3 | median quorum; fewer → skip (no steering on thin data) |
| `steerPrecisionFloorMs` | 60 | gate `= max(2×bestInWindow, 60)`; excludes PSM-jittery high-RTT samples |
| `SyncInterval` (mosiacmesh.js:197) | 30000 | keeps the ring fresh (currently 60000; GoTime.js `900000` default is unused) |

`setOptions` keys (PascalCase, matching the existing convention, e.g. `SyncInterval`):
`SteerDeadbandMs`, `SteerSnapMs`, `SteerCapMsPerSec`, `SteerWindowMs`,
`SteerMinSamples`, `SteerPrecisionFloorMs`. Each maps to its `options._steer*` field
when present.

## Invariants / scope guards

- **`driftTick` stays a pure follower.** It must never write `GoTime._offset`. (It
  reads `now()` and corrects only the video element + `playback.seekAheadMs`.) This
  is what keeps reference-correction and element-follow from compounding.
- **`steerCapMsPerSec` ≪ `driftTick` authority.** The slew (≤15 ms/s) is far below the
  video controller's seek/rate range (hundreds of ms), so `driftTick` simply sees a
  clock ticking ≤1.5% off-nominal — a perturbation it is already built to absorb.
- **Monotonicity.** Slew cap (15 ms/s) ≪ clock rate (1000 ms/s) ⇒ slewing never
  reverses `now()` (it advances at 985–1015 ms/s). Only a **snap** can move `now()`
  backward, and only for a >`snapMs` correction (rare, post-stall); `driftTick`
  re-seeks video to match.

## Error handling / edge cases

- **Thin / no data:** `_robustTarget` returns `null` → steering tick is a no-op; the
  last good `_offset` holds.
- **All-jittery window (every sample > gate):** gate is relative
  (`2×bestInWindow`), so the best-available samples still pass; with `< minSamples`
  good ones, returns `null` (hold) rather than steer on noise.
- **Tab backgrounded / beat stalls:** `dtMs` is measured wall time, and the
  per-second cap bounds the move; a large accumulated error that exceeds `snapMs`
  snaps once on resume.
- **Pre-lock:** ratchet governs until `_steering` flips, so initial convergence speed
  is unchanged from today.

## Testing

Pure functions get node `--test` unit suites under `tests/unit/js/` (new file, e.g.
`gotime-steer.test.js`):

- `_robustTarget`: median selection; precision-gate excludes high-RTT samples;
  window excludes stale samples; `< minSamples` → `null`; relative-gate behavior.
- `_steerStep`: deadband (no move); slew direction + magnitude clamp; snap on large
  error; **monotonicity bound** (for any `dtMs`, slew move magnitude `< dtMs`, i.e.
  `now()` cannot reverse on a slew).

To unit-test the pure helpers they must be reachable without a DOM. They are exposed on
the returned `GoTime` object as `GoTime._robustTarget` and `GoTime._steerStep`, the same
way `readyVerdict` is public.

Tick wiring + `setOptions` plumbing: covered by the existing module-load smoke (no new
integration test). Final acceptance is on-wall: a long-running animation playlist
stays synced across screens, with no visible per-frame stepping during correction.

## Out of scope

- Explicit per-device rate-slope / PLL modeling (A2).
- Changes to `driftTick` beyond confirming/keeping the follower invariant.
- Changes to item "A" (the pre-sync render guard), already implemented.
- Server-side changes (this is entirely client clock logic).

## Legacy-compat constraints

`js/GoTime.js` runs on the iPad-1 (iOS 5.1 / Safari 5.1) display client, so all new
code is **ES5 only** — no `let`/`const`, arrow functions, template literals, `class`,
`Promise`, or `fetch`. Pure helpers use only arithmetic + array sort/slice.
