# Drift-steer: kill spurious seeks + close the P-droop — Design

**Date:** 2026-08-01
**Status:** Section A ready to build; Section B measure-gated on A's on-wall data.
**Related:** [[wall-cross-screen-sync-state]], [[seek-residual-next-lever]], [[drift-steer-rate-pll]], [[mmvideo-currenttime-observer-bimodal]]. Design docs: `2026-07-27-mmvideo-drift-steer-rate-pll-design.md`, `2026-07-30-synctool-cross-screen-measurement-design.md`.

## Goal

Reduce cross-screen video desync on the OEB wall below the current ~176ms video-offset floor, by attacking the two mechanisms that data shows actually cause it — **not** seek-landing residual (the original hypothesis, now disproven).

## Characterization (what the data proved — 2026-08-01)

Analysis of 492 `seekland` + 19,449 `drift` telemetry samples across 24 OEB screens:

1. **Seek-placement bias is already centered.** Per-screen mean `clk` (residual vs clock at seek landing) sits in a tight −8…+7ms band across 23 of 24 screens (the 24th, `6c14aqrn5du6`, is a glitched sample: std 6497). Cross-screen spread of systematic seek bias ≈ **15ms**. `seekAheadMs` already does the centering the [[seek-residual-next-lever]] memo proposed to add. **Residual-centering is a dead lever.**

2. **The real floor is P-controller droop: 128ms cross-screen spread.** Per-screen steady-state `ferr` (median-filtered err) rails at **−77…−206ms** (all screens behind the clock), each at its own baseline. Spread = **128ms** (std 27). The rate lever is engaged (`rlive=1` on 100% of samples) and pinned near its +3.5% clamp, but proportional control corrects *velocity, not position* → non-zero steady-state droop. Each screen's decode deficit differs → different baseline → **this spread is the visible floor** (matches the known ~176ms video-offset).

3. **The 30s seeks are spurious — triggered by readback noise.** Seeks fire on a dead-regular **~30s cadence** (29–31s), and settle in **331±68ms**. But `driftTick` runs every 500ms with a 2.5s cooldown — it *could* seek far more often. The 30s regularity is an artifact: the coarse-seek regime keys on **raw** `err`, and mmvideo's currentTime readback is **bimodal** (±200ms, from the 250ms time-observer quantization — [[mmvideo-currenttime-observer-bimodal]]). When the bimodal noise spikes raw err to −395 (while true `ferr` is only −164), it fires a genuine 331ms hard seek. The wall kicks itself on its own measurement noise, once per beat, on every screen — and each kick resets the rate loop.

**Corollary — why the old PI-integral branch failed** ("did not tighten the wall alone", `fix/drift-steer-pi-integral`): an integral accumulator cannot converge when a spurious seek resets the loop every 30s. **Fixing the spurious seeks is a prerequisite for any integral to work.**

## Approach

Two sequenced levers, A then B, with an on-wall measurement gate between them.

### Lever A — gate the coarse-seek trigger on filtered err (ready to build)

**File:** `js/drift-steer.js`, `mmDriftCommand`, line ~62.

**Current:**
```js
// coarse regime keys on RAW err (fast reaction to a big jump / startup)
if (Math.abs(err) > COARSE && canSeek) {
  state.lastRateCmd = 1;
  return { action: 'seek', rate: 1, filteredErr: fErr, rateLive: state.rateLive };
}
```

**Change:** key the coarse trigger on `fErr` (the median of the sample ring, already computed at line ~59 and already used by the fine regime) instead of raw `err`:
```js
if (Math.abs(fErr) > COARSE && canSeek) { ... }
```

**Rationale:** The median rejects the single-tick bimodal artifact — proven directly in the trace: during the −395 raw spike that fired a spurious seek, `fErr` held at −164, under the 350 threshold. A *genuine* persistent drift still crosses the threshold within ~2–3 ticks (~1–1.5s), acceptable for a hard re-align. Startup is unaffected (it has its own `readyState<3`-gated boot-seek path). Comment on line ~61 updated to reflect the filtered trigger.

**Tests** (node `--test`, `tests/unit/js/`, extend the existing drift-steer suite):
- A bimodal sequence (alternating ~−170/+50 with a single −395 spike) fed tick-by-tick must **not** return `action:'seek'`.
- A *persistent* err > COARSE sustained across the ring **must** return `action:'seek'`.
- Regression: clean coarse (sustained large), fine (small err → damped rate), and hold (within deadband) paths unchanged.

**Risk:** low — pure function, one-token logic change, fully unit-testable.

**On-wall measurement (the gate to B):** deploy A to the fleet, then re-run the characterization scripts (seek-frequency, per-screen droop baseline, and — critically — whether `rateGot` is still railed at the clamp). Expected: seek interval goes from ~30s to rare (genuine drift only); the 128ms droop spread persists (A doesn't touch droop). **The rail check decides B's form** (below).

### Lever B — close the P-droop (measure-gated, final form set by A's data)

**File:** `js/drift-steer.js`, fine regime (line ~86, `rate = 1 - fErr * GAIN`).

**The open question A answers:** the current trace shows the **P-term already railed at +3.3%** (near ±3.5% clamp) *while droop persists at −165*. After the CMTimebase `MM_CM_SCALE=0.45` scaling, effective rate authority may be too weak to overcome each screen's decode deficit — in which case an integral within the same clamp buys nothing. Two branches, chosen by A's rail check:

- **Rate still railed after A (no headroom):** the fix is *authority*, not an accumulator. Raise `RATE_CLAMP` and/or `MM_CM_SCALE` (the latter is already live-tunable via `mmvideo://scale`, so it can be swept on-wall with no redeploy). Then add the integral for the residual.
- **Rate has headroom after A:** add the integral term directly:
  ```
  state.integral += fErr             // fine regime only; never during coarse/seek/inert
  clamp state.integral to ±I_MAX     // anti-windup
  rate = 1 - fErr*GAIN - state.integral*IGAIN
  clamp rate to ±RATE_CLAMP          // total authority
  ```
  with: integrate only in the fine regime; **freeze** (not reset) the accumulator across a seek so it re-converges; anti-windup clamp so a stall can't wind it up. Tune `IGAIN` / `I_MAX` against the measured droop.

**Risk:** medium — integral windup and the authority interaction above. Explicitly gated behind A's on-wall numbers rather than built blind.

**B is finalized in a follow-on pass** once A is deployed and measured. This design doc records the direction and the decision criterion; the concrete B spec (constants, anti-windup bounds, test values) is written against real post-A data.

## Success criteria

- **A:** spurious 30s seek cadence eliminated (seeks fire only on genuine drift); no regression in genuine-drift re-align or startup; unit tests green.
- **B:** cross-screen droop spread reduced from ~128ms toward the clock-disagreement floor; measured via `synctool` / `subset_spread` attribution on the OEB batch.

## Out of scope

- Seek-landing residual centering (disproven as a lever).
- Clock-resync smoothing / GoTime frequency discipline (a separate layer; may become relevant if B's measurement shows the residual is clock-disagreement, not droop — tracked in [[wall-cross-screen-sync-state]]).
- mmvideo currentTime off-main-queue observation (the bimodal *root* fix; A defends against the bimodal symptom without needing it — 66ms was already deemed sufficient, [[mmvideo-currenttime-observer-bimodal]]).
