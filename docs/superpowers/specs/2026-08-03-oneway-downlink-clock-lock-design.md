# One-Way Downlink Clock Lock (v4b) — Design

**Date:** 2026-08-03
**Status:** Approved (brainstorming) → ready for implementation plan
**Predecessors:** `2026-08-02-oneway-clock-diagnostic-design.md` (v4a — the per-screen downlink-floor de-risk), `2026-08-01-master-clock-beat-design.md` (the TIMEBEAT infrastructure this reuses).

## Problem

After Lever A cleared the video/seek layer, the OEB wall's dominant remaining cross-screen desync is the **clock layer**. The master-clock beat (TIMEBEAT, 2s) bounded the clock-disagreement drift but left a ~193 ms floor (bouncing 266–538 ms) because it feeds the beat samples through a robust **median** steer — `offset − Dd_median`, contaminated by the typical ~45 ms downlink queue.

v4a proved (24/24 iPad-1s, `dlStabilityMs` median 0.5 ms) that the **one-way downlink floor is razor-sharp fleet-wide**: `down = t3 − t4 = offset − Dd`, queueing is strictly additive, so the **max** of the downlink leg recovers `offset − Dd_floor` (near the physical minimum, asymmetry-free) — something min-RTT provably cannot, since it minimizes `Du + Dd` and leaves `(Du − Dd)/2`.

## Key insight

`GoTime._beatSample` already computes `offset = serverSendMs − localRecv` — **exactly** the one-way downlink estimate. Every screen already receives these via the 2s TIMEBEAT broadcast. The only thing between us and a downlink lock is the **filter**: the beat samples feed `_reviseOffset → _robustTarget` (median). v4b feeds them through a windowed **MAX** (high percentile) instead → `offset − Dd_floor`. The infrastructure exists; this changes one input.

## Scope (staged, YAGNI)

v4b is the **client downlink-lock proof only**. The client lock drives each screen's clock error to `−Dd_floor_i`, so cross-screen disagreement becomes the spread of `Dd_floor` — expected in the tens of ms from v4a's ~20–30 ms downlink jitter. The **server-side cross-screen trim** (the hybrid's other half) becomes a separate **v4c**, built only if a meaningful *systematic* residual survives the client lock. We can't know that until we measure the client lock alone.

## Architecture

All changes are **client-side**, behind one A/B flag (`window._mmDownlinkLock`, default `false`), byte-identical when off — the same pattern as `js/beatflag.js`. Proof is via the existing synctool clock-disagreement attribution (median-beat vs max-beat).

The offset steer already separates concerns: **skew owns rate**, the **steer owns offset**, `_steerStep` owns the **smooth slew**. v4b changes exactly one input — where the offset *target* comes from. Injection point is `_steerTick` (GoTime.js:442), where `target = _robustTarget(...)` is computed.

### Estimator approach (chosen of three)

- **(A, chosen) Windowed high-percentile over a dedicated beat ring.** `options._beatRing` is pushed only in `beatReceived`, so its input is *pure one-way downlink* samples — never polluted by `/time` round-trips (which carry the asymmetry, and can enter `_steerSamples` when beats lapse >6s). Target = **p90 of beat offsets in the last ~90 s**. Same shape as v4a's proven batch estimator, windowed + live; a pure, node-testable function feeding the existing steer.
- (B) Decaying leaky-max tracker — fiddlier decay tuning, less obviously correct. Rejected.
- (C) Explicit `Dd`-min tracking — more machinery, same result. YAGNI.

### Components (all byte-identical when the flag is off)

- **`js/dllockflag.js`** (new) — `window._mmDownlinkLock = false`. Loaded from `index.html` next to `beatflag.js`. Only meaningful when `_mmClockBeat` is also on.
- **`GoTime._beatRing`** — capped ring (≤64) of `{offset, t}`, pushed in `beatReceived`; `t` in now-space (`GoTime.now()`), consistent with `_steerSamples.t` and `_steerTick`'s `nowMs`.
- **`GoTime._downlinkTarget(beatRing, nowMs, opts)`** — pure, unit-tested; windowed p90; returns `null` if too few in-window samples (→ steer falls back to `_robustTarget` median).
- **`_steerTick` injection** — when `_mmDownlinkLock` and `_downlinkTarget` is non-null, use it as `target`; else unchanged.
- **`?tdbg`/drift telemetry** — add `dlt` (current downlink target, or `null`) + the `_mmDownlinkLock` mode bit, to confirm the mode is live on-wall.

## Data flow

```
server TIMEBEAT {t:serverMs} --2s--> client mosiacmesh.js
  --> GoTime.beatReceived(serverMs)
        localRecv = getAccurateTimestamp()
        sample.offset = serverMs - localRecv          (= trueOffset - Dd)
        _reviseOffset(sample,"beat")   [median path, unchanged]
        _beatRing.push({offset: sample.offset, t: GoTime.now()})   [v4b]
  --> _steerTick() (30s beat)
        target = _robustTarget(...)                    [median]
        if (_mmDownlinkLock) {
          dlt = _downlinkTarget(_beatRing, now, opts)  [windowed p90 = trueOffset - Dd_floor]
          if (dlt !== null) target = dlt
        }
        _offset = _steerStep(_offset, target, dt, opts)   [slew/deadband/cap unchanged]
  --> GoTime.now() = local + _offset  drives BOTH video and animation playback
```

## The `_downlinkTarget` function

```js
// Downlink-lock target: the windowed high-percentile (robust MAX) of recent
// one-way beat offsets. Each beat offset = serverSendMs - localRecv = trueOffset - Dd;
// queueing only lowers it, so the MAX recovers trueOffset - Dd_floor (asymmetry-free).
// Returns null when too few in-window samples -> _steerTick falls back to _robustTarget.
// opts: { dlWindowMs=90000, dlPct=0.90, dlMinSamples=12 }
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
    var idx = Math.ceil(pct * offs.length) - 1;   // nearest-rank, matches v4a
    if (idx < 0) { idx = 0; }
    if (idx >= offs.length) { idx = offs.length - 1; }
    return offs[idx];
};
```

**Skew:** the ring is **not** deskewed — drift over 90 s at −19 ppm is ~1.7 ms, negligible against the ~45 ms downlink jitter the max operates over (the same documented simplification as v4a). The skew *rate* discipline keeps running on `_steerSamples`, untouched.

**Defaults:** `dlWindowMs=90000` (~45 beats at 2 s), `dlPct=0.90` (5th-largest of ~45 → robust to ~4 glitched beats), `dlMinSamples=12` (~24 s warmup before the lock engages; below it, null → median fallback). All overridable via `setOptions`, like the other steer knobs — these are on-wall tuning knobs vs the synctool spread.

## Unit tests (node `--test`, beside the existing `_robustTarget`/`_steerStep` suite)

| Test | Ring construction | Asserts |
|---|---|---|
| floor recovery | offsets `= 500 − Dd`, `Dd ∈ {10×6, 25,40,60,80,100,100}`, all in-window | target ≈ **490** = `trueOffset − Dd_floor` |
| windowing | a stale high-offset beat older than `dlWindowMs` + a fresh floor cluster | stale sample **excluded**; target tracks the fresh floor |
| outlier rejection | ~40 clean beats + one wild-high glitch | p90 keeps target on the clean floor (single max excluded) |
| too few → null | 8 in-window beats (`< dlMinSamples`) | returns **null** |
| empty ring → null | `[]` | returns **null** |
| steer integration | seed `_beatRing`, set `_mmDownlinkLock=true`, run `_steerTick` | `_lastSteerTarget` equals the p90 floor, **not** the `_robustTarget` median (proves the injection fires only under the flag; flag off ⇒ byte-identical) |

## On-wall A/B protocol

- **Metric:** synctool **clock-disagreement** attribution (basis A) — already isolates the clock layer from video-offset. Flag OFF (median beat = today) vs ON (downlink max).
- **Two playlists:** an **animation** playlist first — position is a pure function of `GoTime.now()`, so its entire cross-screen spread *is* the clock layer, with zero video-offset/seek confound (the sharpest instrument). Then **PullTest3** (video) to confirm the lock survives real decode load. video-offset (~150–220 ms, Lever A's layer) must stay unchanged; only clock-disagreement should move.
- **Critical confound (from the master-clock-beat run):** clock-disagreement is **time-dependent** — fresh after a reload every arm reads ~90 ms regardless; the drift-apart over 20–60 min is what the lock must prevent. Compare **at matched elapsed-since-reload, over many windows, distributions not snapshots** — never a single post-reload snapshot (it flatters both arms).
- **Target:** clock-disagreement drops from the median-beat's ~193 ms floor (266–538 ms bounce) toward the downlink-floor cross-screen spread — **tens of ms**. The surviving residual decides whether v4c (server trim) is worth building.
- **Deploy:** client-JS only → fleet **reload**, no dylib rebuild (GoTime.js / index.html / flag files are server-served). Server already broadcasts TIMEBEAT.

## Non-goals

- No server-side cross-screen trim (that's v4c, gated on a measured residual).
- No change to the median beat path, `/time` sync, `_steerStep`, skew discipline, or Lever A seeks.
- No new server routes or telemetry endpoints (reuse TIMEBEAT + synctool).
- No production default flip — v4b ships behind `_mmDownlinkLock=false`; flipping it is a post-A/B decision.

## Files

- Create: `js/dllockflag.js` — the A/B flag.
- Modify: `index.html` — load `dllockflag.js`; add `dlt` + mode bit to the `?tdbg`/drift telemetry.
- Modify: `js/GoTime.js` — `_beatRing` + its options; push in `beatReceived`; `_downlinkTarget`; `_steerTick` injection; export `_downlinkTarget` for tests.
- Modify: the GoTime/drift-steer node test file — the six `_downlinkTarget` cases above.
