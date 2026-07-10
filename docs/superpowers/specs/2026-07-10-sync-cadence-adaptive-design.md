# Adaptive fast-sync-until-converged clock cadence — design

**Date:** 2026-07-10
**Status:** Approved (design), pending plan
**Files:** `js/GoTime.js` (scheduler), `js/mosiacmesh.js` (option overrides). ES5 — runs on the 1st-gen iPad fleet.

## Problem

On join, a display client must clock-sync to the server before the wall can start
crisply-together. Today `GoTime._setupSync` fires a **fixed** burst of syncs at
`[500, 3000, 9000, 15000]` ms (the `mosiacmesh.js` `SyncInitialTimeouts` override)
and then hands off to `setInterval(_sync, 30000)`. The schedule is decided **once, up
front**, with no feedback from whether the offset has actually converged.

On the PSM (power-save) fleet the offset frequently is *not* precise by the end of
that fixed burst — so a panel drops to the 30 s cadence still coarse, and its next
chance to tighten up is a full 30 s later. Observed symptom (2026-07-10): panels sit
`synced=true` but with a wide `getPrecision()` and never converge to a tight,
start-ready offset in bounded time; only ~2/24 reached `clockSettled()`. The wall
starts ragged (some panels idle at `idx=1`, no `verr`), because start-gating waits on
convergence that the fixed cadence doesn't drive hard enough early.

## Goal

Sync **fast (≈1 s)** on join until the offset is demonstrably precise, then relax to
the existing **30 s** drift cadence. Bounded so it can never fast-sync forever on a
panel that never converges.

## Non-goals

- No change to `_sync` itself (the request/sample/`_reviseOffset` path), the drift
  steer (`_steerTick`), the play gate (`ProgrammableTimer.isSynced()`), or
  `clockSettled()` (kept as-is for `?tdbg` diagnostics).
- **No re-entry to fast** on later drift. Once converged → slow, stay slow. Ongoing
  drift is already handled by the 30 s beat + the drift steer (YAGNI).
- No server change. Deploy is a fleet **reload** (JS is server-served; no tweak rebuild).

## Design

### Termination signal: precision target (chosen)

The fast→slow transition gates on **`getPrecision()` ≤ target for K consecutive
samples**. `getPrecision()` returns the offset precision (one-way RTT/2 of the sample
that set the current offset); it is the direct, already-computed measure of "how tight
is this offset," and unlike `clockSettled()` it does **not** decay on the PSM fleet
(that decay is exactly why `clockSettled()` was dropped from the play gate).

Defaults (all overridable via `setOptions`):

| Knob | Default | Meaning |
|---|---|---|
| `FastSyncInterval` | `1000` ms | base delay between syncs while converging |
| `FastSyncJitterMs` | `150` ms | per-sample uniform jitter added to the fast interval (herd de-sync) |
| `SyncPrecisionTargetMs` | `40` ms | precision at/below which a sample counts as "good" |
| `SyncPrecisionStreak` | `2` | consecutive good samples required to transition |
| `FastSyncCapMs` | `60000` ms | hard cap: after this much fast-syncing, go slow regardless |
| `SyncInterval` | `30000` ms | existing slow/drift cadence (unchanged) |

### Cadence

- **Fast phase** (join): schedule next `_sync` at `FastSyncInterval`. After each sample
  is processed, if `getPrecision() ≤ SyncPrecisionTargetMs`, increment a streak
  counter; else reset it to 0. When the streak reaches `SyncPrecisionStreak`
  → **transition to slow**. Independently, if the fast phase has run longer than
  `FastSyncCapMs` (measured from the first fast sample) → **transition to slow**
  (safety cap — never fast-sync forever).
- **Slow phase**: schedule next `_sync` at `SyncInterval` (the existing 30 s drift
  cadence). No return to fast.

The legacy `SyncInitialTimeouts` fixed-burst array is **retired** — the adaptive fast
phase supersedes it. `_setupSync` no longer reads it.

### Architecture: self-rescheduling scheduler

Replace `_setupSync`'s "fixed setTimeouts + setInterval" with a self-rescheduling loop
keyed on **sample completion** (not on `_sync` dispatch — precision only updates once
the sample's response returns and `_reviseOffset` runs):

1. `_scheduleNextSync(delayMs)` — sets a single `setTimeout(_sync, delayMs)`. Clears any
   prior handle so there's never a double-schedule.
2. After each sample is processed (hook at the end of the sample path, alongside where
   the `OnSync` callback already fires), call the pure decision helper with the current
   state, then `_scheduleNextSync(decision.delayMs)`.

### Pure decision helper (testable core)

Matching the codebase's `_steerStep` / `_robustTarget` pattern (pure function + node
`--test`):

```
_nextSyncDelay(state) -> { delayMs, phase, streak }
  state = {
    phase:            'fast' | 'slow',
    precision:        <number ms, from getPrecision()>,
    streak:           <int, consecutive good samples so far>,
    fastElapsedMs:    <ms since the first fast sample>,
    opts: { FastSyncInterval, SyncPrecisionTargetMs, SyncPrecisionStreak,
            FastSyncCapMs, SyncInterval }
  }
```

Logic (pure, no side effects, no `Date`/timers inside):

- If `phase === 'slow'` → `{ delayMs: SyncInterval, phase: 'slow', streak }` (stays slow).
- Else (fast):
  - `nextStreak = precision <= SyncPrecisionTargetMs ? streak + 1 : 0`
  - Converged: `nextStreak >= SyncPrecisionStreak` → `{ SyncInterval, 'slow', nextStreak }`
  - Capped: `fastElapsedMs >= FastSyncCapMs` → `{ SyncInterval, 'slow', nextStreak }`
  - Otherwise → `{ FastSyncInterval, 'fast', nextStreak }`

The scheduler is a thin wrapper: it owns the mutable `phase`/`streak`/`fastStartMs`
state and the `setTimeout` handle, reads `getPrecision()` and the current time, calls
`_nextSyncDelay`, stores the returned `phase`/`streak`, and arms the timer.

**Jitter is applied in the wrapper, not in the pure helper** — `_nextSyncDelay` stays
deterministic (no `Math.random`, node-testable). When the returned `phase === 'fast'`,
the wrapper arms `setTimeout(_sync, delayMs + Math.floor(Math.random() * FastSyncJitterMs))`
so a synchronized fleet reboot smears the 24 panels' fast samples across the second
instead of stacking on one tick. The slow phase (30 s) gets no jitter — one probe per
30 s per panel needs no de-herding.

### Boot / first sample

`_setupSync` starts the fast phase: `phase='fast'`, `streak=0`, `fastStartMs` unset,
then arms an initial short kick — `setTimeout(_sync, 500)` (preserving today's `500 ms`
first-sample latency) so the first sample lands quickly. Every sample after that is
scheduled by the adaptive loop at `FastSyncInterval`/`SyncInterval`. `fastStartMs` is
stamped on the first sample-completion tick so `FastSyncCapMs` measures wall-time in the
fast phase.

## Error handling

- A failed/timed-out sync sample must still reschedule (else the loop dies). The
  reschedule happens on sample completion whether the sample succeeded or failed; a
  failed sample doesn't update precision, so it naturally resets the streak (precision
  stays coarse) and stays fast — correct behavior (keep trying hard until a good
  sample). The `FastSyncCapMs` guarantees termination even if good samples never come.
- Guard against double-scheduling: `_scheduleNextSync` always clears the prior
  `setTimeout` handle before arming a new one.

## Testing

Node `--test` (`tests/unit/js/gotime-cadence.test.js`), pure `_nextSyncDelay`:

- fast, coarse precision → `{ FastSyncInterval, 'fast', streak: 0 }`
- fast, good precision, streak below target → stays fast, streak increments
- fast, good precision, streak reaches target → `{ SyncInterval, 'slow' }`
- fast, coarse precision but `fastElapsedMs >= cap` → `{ SyncInterval, 'slow' }` (cap wins)
- fast, one good then one coarse → streak resets to 0
- slow phase → always `{ SyncInterval, 'slow' }` regardless of precision

Plus a scheduler-level test with a mocked `setTimeout` + stubbed `getPrecision()`
verifying the sequence fast×N → slow, that exactly one timer is armed at a time, and
that a stubbed `Math.random` puts the fast delay in `[FastSyncInterval,
FastSyncInterval + FastSyncJitterMs)` while the slow delay is exactly `SyncInterval`
(no jitter).

## Deploy

Fleet **reload** (RELOAD broadcast / webclip reload). No tweak rebuild, no server
restart required for the JS change. Verify on-wall via `?tdbg`:
`accAge` small + `prec` ≤ target within a few seconds of join, then the 30 s cadence.
