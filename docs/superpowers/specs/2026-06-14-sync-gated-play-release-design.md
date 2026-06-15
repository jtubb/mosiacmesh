# Sync-Gated Play Release — Design

**Date:** 2026-06-14
**Status:** Design — approved in brainstorming; pending spec review → implementation plan.
**Area:** coordinated-start playback (client clock readiness + server release gate). Display-client (`index.html` ES5 + `js/GoTime.js`) + server (`mosaicmesh/render.py` prepare/release, `server.py` release timeout).

---

## Problem

Coordinated start (`PLAY → PREPARE → clients arm → READY/NEEDS_ARM → GO at a shared `startEpoch`) releases a group into playback when every online client has reported **READY**, which today means **"video is armed"** (buffered + gesture-blessed) — or, for non-video items, effectively immediately. It does **not** wait for the client's **clock** to be accurately synchronized.

Both video position and animation phase are computed identically on the client from `GoTime.now() - startEpoch` (video seeks `currentTime` to it; animations are pure functions of the derived `offsetMs`). So **any inaccuracy in a client's clock offset desynchronizes both** — a screen starts at the wrong position. Observed symptoms: one screen's animation out of phase; video starting at different times across screens (worse right after a mass manual reconnect).

Two root causes in the current clock machinery:

1. **The release gate uses the wrong readiness signal.** The client announces SYNACK (→ server `client.synced`, which gates PREPARE/release) on the **loose** `ProgrammableTimer.isSynced()` (drift-tolerance over the rolling history). A **stricter** convergence flag, `clockSettled()` (phase std-dev ≤ `SETTLE_STDDEV_MS` AND mean ≤ `SETTLE_MEAN_MS`), already exists — but is wired only to the **heartbeat indicator color**, never to the release gate.
2. **The offset goes stale.** GoTime samples the server clock at `0/3/9/18/45 s` then only **every 15 minutes** (`_syncInterval`). It locks the offset from the lowest-RTT sample (`_precision`). A screen whose initial burst landed during BCM4329 PSM RTT-jitter **locks a poor offset and keeps it for 15 minutes**. Worse, `clockSettled()` measures tick-phase *stability* relative to the current offset — a screen can be perfectly **stable around a wrong offset** ("stably wrong"), so the std-dev flag alone does not prove accuracy.

## Goals

1. **PLAY/GO holds until each online client is genuinely clock-ready** — fresh, accurate offset **and** stable beat — not merely "video armed."
2. Applies to **all content** (video, animation, image) — they share the release gate.
3. **Bounded:** a screen that cannot converge does not hang the wall; release best-effort after an (extended) PREPARE window.
4. Reuse the existing `clockSettled()` std-dev flag (don't reinvent it); add the missing *fresh-offset* dimension.

## Non-goals (explicit follow-up)

- **Mid-playback re-sync cadence.** GoTime's 15-minute re-sample interval can let a converged screen drift *during* playback. Tightening that cadence (and/or re-announcing de-sync mid-session) is a **separate follow-up spec**, agreed out of scope here. This spec fixes the **start** gate only.
- No change to the render pipeline, ArUco/calibration, or the per-frame drift correction (`driftTick`) itself.

---

## Design

### "Clock-ready" = fresh + accurate + stable

A client is **clock-ready** when **both** hold:
- **Fresh & accurate offset:** GoTime accepted a sample within a recency window (`CLOCK_FRESH_MS`, e.g. the PREPARE window) whose precision (RTT/2) is at or below `CLOCK_PRECISION_MS` (e.g. 50 ms). This rules out the stale-15-min and the lucky-single-low-RTT cases.
- **Stable beat:** `clockSettled()` — the existing phase std-dev (`≤ SETTLE_STDDEV_MS`) + mean-near-zero (`≤ SETTLE_MEAN_MS`) check.

Expose a single client predicate `clockReady()` combining the two.

### Part 1 — GoTime: forced fresh resync + freshness accessor (`js/GoTime.js`)

- Add a public `GoTime.resync(n)` that fires `n` immediate `_sync()` samples (spaced ~`CLOCK_RESYNC_SPACING_MS`, e.g. 400 ms) so PREPARE can refresh the offset on demand rather than waiting up to 15 min.
- Add `GoTime.msSinceAccept()` returning `now - _lastAcceptTime` (or `Infinity` if never). `getPrecision()` already exists. These let the client compute the fresh+accurate half of `clockReady()`.
- No change to the existing accept logic (precision-decay re-lock) or the 15-min interval (that's the follow-up).

### Part 2 — Client: kick resync on PREPARE, gate READY on `clockReady()` (`index.html`)

- **On `recv-PREPARE`:** call `GoTime.resync(CLOCK_RESYNC_SAMPLES)` (e.g. 4) so the offset is re-measured against the current network, in parallel with video arming/buffering (don't serialize — video keeps buffering while the clock re-settles).
- **Define `clockReady()`** (inline, ES5): `GoTime.msSinceAccept() <= CLOCK_FRESH_MS && GoTime.getPrecision() <= CLOCK_PRECISION_MS && clockSettled()`.
- **Gate the READY emission:** the client emits `READY` only when its content is armed **and** `clockReady()`:
  - **Video:** arm as today; if un-blessed, still emit `NEEDS_ARM` (the human-tap path is unchanged and still holds release). Once armed, hold `READY` until `clockReady()`.
  - **Animation / image (no arm):** hold `READY` purely until `clockReady()`.
  - While armed-but-not-clock-ready, the client is in **neither** `readyClients` nor `armPending`, so the server's timeout fallback (Part 3) governs it. Re-evaluate `clockReady()` on each GoTime tick (it already fires ~1/s) and send `READY` the first tick it passes.
- The existing SYNACK announce (loose `isSynced()` → `client.synced`) is **unchanged**: it still gates *PREPARE eligibility* so PREPARE goes out early. `clockReady()` is the stricter, separate gate on *READY*. (Clean separation: "eligible to prepare" vs "ready to GO".)

### Part 3 — Server: extend the PREPARE window; release logic unchanged (`server.py`, `mosaicmesh/render.py`)

- The release machinery is unchanged in shape: `_maybe_release` still GOes when `online ⊆ readyClients`; `_release_expired_prepares` still best-effort releases at the deadline unless `armPending & online` (the human-tap hold). Because READY now means "armed **and** clock-ready", GO automatically holds until all online clients are clock-ready — for every content type.
- **Extend `PREPARE_TIMEOUT_MS`** (currently 25 s) to a longer bounded window (e.g. **45 s**, env-overridable) so PSM-jittery clocks have time to re-settle after the PREPARE resync burst before the best-effort release. Trade-off: a slightly longer worst-case start latency in exchange for a synchronized wall; documented.
- No new server message types — the change rides the existing READY/PREPARE/GO protocol.

### Part 4 — `tdbg` clock observability (`index.html`)

The clock-ready logic is iPad-1 ES5 (inline `index.html` + classic-script `GoTime.js`) and not cleanly node-unit-testable, so **`?tdbg` is the verification mechanism** for this change — but its current `dbg()` payload is video-centric and carries no clock-quality fields. Extend the `dbg()` CLIENTLOG payload with the clock metrics this design turns on, so per-screen convergence is observable in the server log (and a "stably wrong" screen is diagnosable):

- `offset` — `GoTime.getOffset()`
- `prec` — `GoTime.getPrecision()` (RTT/2 of the locked sample)
- `accAge` — `GoTime.msSinceAccept()` (offset freshness; from Part 1)
- `phStd`, `phMean` — phase std-dev and mean over `_phaseHistory` (the inputs to `clockSettled()`)
- `synced` — `ProgrammableTimer.isSynced()` (the loose flag)
- `settled` — `clockSettled()` (the strict std-dev flag)
- `cready` — `clockReady()` (the new combined gate)

These are added to the existing payload (alongside `tag`/`elapsed`/video fields), ES5-safe, opt-in under `?tdbg` exactly as today. No new transport — same CLIENTLOG path.

### Constants (one place, tunable)

`CLOCK_PRECISION_MS` (~50), `CLOCK_FRESH_MS` (~30000, ≈ PREPARE window), `CLOCK_RESYNC_SAMPLES` (~4), `CLOCK_RESYNC_SPACING_MS` (~400), extended `PREPARE_TIMEOUT_MS` (~45000). Tunable; defaults chosen to converge a healthy client in a few seconds while tolerating jitter.

---

## Data flow (happy path)

```
PLAY → _begin_prepare → PREPARE to synced clients
  client: recv-PREPARE → GoTime.resync(4)   ── refresh offset (parallel)
                       → arm video (buffer)  ── if video
  client tick (~1/s): clockReady()?  (fresh+precise offset AND settled beat)
                       → when armed && clockReady → send READY
server: online ⊆ readyClients → _release_group → GO(startEpoch)
  (or, a laggard never converges → after extended PREPARE_TIMEOUT,
   _release_expired_prepares GOes best-effort; that screen self-corrects
   via driftTick once its clock settles)
```

## Failure / edge handling

- **Never converges (PSM):** extended-timeout best-effort release (Goal 3). The per-frame `driftTick` pulls it in once its clock later settles.
- **Un-blessed video (needs tap):** unchanged — `NEEDS_ARM`/`armPending` still holds release for the human tap; clock-ready is an additional, orthogonal condition.
- **Clock de-settles between PREPARE and READY:** the client simply doesn't emit READY yet (re-checks each tick).
- **Reconnect mid-prepare:** existing `_prepare_unsynced_clients` + SYNACK-on-reconnect flow is unchanged; the reconnected client runs the same resync→clockReady→READY path.

## Components to change

- `js/GoTime.js` — `resync(n)`, `msSinceAccept()` (small additive public methods; ES5, no new deps).
- `index.html` (inline ES5) — `clockReady()` predicate; PREPARE handler calls `resync`; READY emission gated on `clockReady()`; constants; **extend the `dbg()` CLIENTLOG payload with the clock metrics (Part 4)**.
- `server.py` / `mosaicmesh/render.py` — extend `PREPARE_TIMEOUT_MS` (and confirm `_release_expired_prepares` best-effort path covers armed-but-not-clock-ready; no logic change expected).

## Testing

- **Server (pytest):** `_release_expired_prepares` releases an armed-but-not-yet-READY client after the (extended) timeout; `_maybe_release` still requires all-READY; the human-tap (`armPending`) hold is preserved. These are unit-testable against the existing prepare/release helpers.
- **Client clock-ready math:** `clockReady()` combines `clockSettled()` (std-dev/mean) + precision + freshness. The display client is inline ES5 in `index.html` (not a module) and GoTime is a classic script, so direct node unit testing is limited. Where feasible, extract the pure `clockReady` comparison (given precision, msSinceAccept, stddev, mean, thresholds) into a tiny ES5-safe testable form; otherwise verify via the e2e/manual path. Document this constraint rather than over-engineering a harness for iPad-1 ES5 code.
- **E2e / manual:** with the dev server, confirm a PLAY does not GO until the simulated client reports clock-ready; confirm best-effort release after the timeout. Manual fleet check: a coordinated PLAY on OEB Sign 1 starts only once screens are settled, and starts in sync.
- **`tdbg` observability (Part 4) is the primary live-verification lever:** with `?tdbg` on, the server log shows each screen's `prec`/`accAge`/`phStd`/`phMean`/`settled`/`cready`, so you can confirm a screen only emits READY once `cready` is true, and identify a "stably wrong" screen (settled but stale/imprecise offset). This compensates for the limited node-unit-testability of the ES5 client clock code.

## Manual verification

Manual sync-gated-play verification (dev server + a display client at `?tdbg`):

1. Load a display client with `?tdbg`; watch the server log CLIENTLOG lines.
2. Issue PLAY for that group. Expect: recv-PREPARE, then repeated hold-READY-clock
   with `cready:false` while `accAge`/`prec`/`phStd` settle, then send-READY with `cready:true`.
3. Confirm playback (GO) starts only after READY; with a 2nd client, both start together.
4. Confirm a client that can't converge is released after ~45s (PREPARE timeout log line).

## Resolved decisions (from brainstorming)

1. **Need the fresh-offset dimension** on top of the existing `clockSettled()` std-dev flag — the flag proves stability, not accuracy ("stably wrong" is possible because the offset re-samples only every 15 min). Agreed.
2. **Non-converging fallback = extend the window** (longer bounded `PREPARE_TIMEOUT_MS`, then best-effort release), not hold-out or start-anyway-immediately.
3. **Mid-playback re-sync cadence = follow-up**, not in this spec.
4. **`tdbg` clock-metrics extension is IN scope** (Part 4) — it's the live-verification mechanism for this otherwise-hard-to-unit-test ES5 client change, not a separate effort.
