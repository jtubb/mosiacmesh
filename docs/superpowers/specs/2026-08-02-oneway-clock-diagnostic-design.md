# One-Way Downlink Clock Diagnostic (v4) — Design

**Date:** 2026-08-02
**Status:** Approved (brainstorming) → ready for implementation plan
**Predecessors:** `2026-08-02-clock-qualification-diagnostic-design.md` (the v1/v3 ground-truth ladder). This is the v4 rung.

## Problem

The ~225 ms cross-screen sync floor on the first-gen-iPad video wall is physical: WiFi
uplink transit asymmetry/jitter (~84 ms) plus an AP/server baseline (~50 ms). The v1–v3
ladder proved that **no round-trip clock sync (NTP or GoTime) can remove a device's
constant one-way transit asymmetry** — it is absorbed 1:1 into the offset estimate as
`(Du − Dd)/2` and is unobservable from round-trips. GoTime was shown to already track its
(biased) target optimally (~4 ms error). Client-side filtering can reduce the *random*
jitter but never the *constant* asymmetry bias, which is the larger term.

## Key insight (what makes v4 different)

A round-trip conflates the two legs. A **one-way downlink** measurement does not — and it
is fundamentally better because queueing delay is *strictly additive*:

For each probe `{t1,t2,t3,t4}` (client-send, server-recv, server-send, client-recv):

- **uplink leg:**   `up   = t2 − t1 = offset + Du`  → robust **min** = `offset + Du_floor`
- **downlink leg:** `down = t3 − t4 = offset − Dd`  → robust **max** = `offset − Dd_floor`
- **round-trip:**   `((t2−t1)+(t3−t4))/2 = offset + (Du − Dd)/2`  (today's asymmetry-limited estimate)

Queueing/MAC/PSM delay only ever makes `t4` *later*, i.e. `down` *smaller*. So the
**largest** `down` samples are the **least-delayed** ones, and a high percentile lands on
the downlink floor `Dd_floor` (near the physical minimum) while rejecting outliers.
Min-RTT filtering cannot do this: it minimizes `Du + Dd` (the sum), so a fast-uplink /
slow-downlink sample can win min-RTT while carrying a large `Dd` — exactly the asymmetry
that survives. **The one-way max targets `Dd` directly.**

The eventual production direction is a **hybrid**: each client locks its clock via the
one-way downlink estimate (min-downlink filtered), and the server measures the residual
cross-screen disagreement (from playback telemetry) and pushes a small per-screen trim.
Client-applied correction, server-side (cross-screen) measurement.

## Fundamental limit v4 must respect

A single screen's round-trips can **never** reveal its *absolute* clock error — the true
device offset is unobservable (the same degeneracy). Two consequences fix the design:

- `roundTrip − downlink = minRtt / 2` **identically**. The gap between the estimators is
  just half the min round-trip, NOT the asymmetry. `(Du − Dd)/2` and `Dd_floor` are each
  individually unobservable per screen; only their sum `minRtt` is.
- Therefore a per-screen page **cannot prove** the downlink method gives better
  cross-screen agreement. Proving that needs a **server-coordinated common reference
  event** (the way `synctool` uses playback position).

This is why the work is **staged**.

## Staging

- **v4a — per-screen floor de-risk (this spec's build target).** Cheap; reuses the
  existing `/clockprobe` stream and adds no server surface. Its job is *mechanism
  validation*: is the downlink leg `t3 − t4` a **sharp, stable, well-defined floor** on
  real iPad-1 WiFi? If the floor is garbage/unstable, the hybrid is dead and we learn it
  for the price of one HTML page.
- **v4b — cross-screen proof (sketched here, designed properly when it starts).** The
  rung that actually shows the wall tightens; needs a server-coordinated common reference.

## v4a architecture

One more rung on the existing ladder. **Zero new server surface** — `/clockprobe` already
echoes `t2`/`t3`, and `/clockresult` already logs `CLOCKRESULT <ip> <json>`.

### New pure function — `js/clockdiag/analysis.js`

```js
// mmOneWayEstimates(samples, opts) -> the three offset estimators + the floor's stability.
//   up   = t2 - t1  = offset + Du   -> robust MIN (p05)  = offset + Du_floor
//   down = t3 - t4  = offset - Dd   -> robust MAX (p95)  = offset - Dd_floor
//   roundTrip = min-RTT sample's ((t2-t1)+(t3-t4))/2      = offset + (Du-Dd)/2
// opts: { pct = 0.95, subWindows = 5 }
// returns {
//   n,
//   roundTrip,        // offset of the min-RTT sample (reuse existing best-offset)
//   downlink,         // percentile(down, pct)         -> robust max = offset - Dd_floor
//   uplink,           // percentile(up, 1 - pct)       -> robust min = offset + Du_floor
//   minRtt,
//   dlStabilityMs,    // std of `downlink` computed independently over `subWindows`
//                     //   contiguous sub-windows -> "is the floor sharp/repeatable?"
//   dlSharpnessMs     // percentile(down, pct) - percentile(down, 0.5)
//                     //   -> how far the floor sits above the typical queued sample
// }
```

- ES5 only (runs in iPad-1 Safari 5.1 AND as a node side-effect import for tests), no
  DOM/network/timers/`Date`/`Math.random`. Reuses the existing `percentile`, `pstdev`,
  `rttOf`, `offsetOf` helpers.
- `roundTrip` reuses the existing min-RTT best-offset (already `mmClockStats(...).offset`).
- **Skew:** the max-of-`down` filter could conflate crystal drift with the floor, but
  measured skew ≈ −19 ppm → ~1 ms drift over 60 s, negligible against ~45 ms downlink
  jitter. For v4a's ≤~a-few-minute windows we **skip detrending** and note the assumption.

`dlStabilityMs` is the **go/no-go number for v4b**: tight-and-low across the fleet → the
one-way lock is viable → build v4b; wandering → stop.

### Unit tests — `tests/unit/js/clockdiag-analysis.test.js` (add to the existing suite)

Encode the algebra as executable assertions (the recvTs lesson: never trust a measurement
whose math isn't tested). Sample builder is the existing
`mk(t1, OFF, Du, Dd) = {t1, t2: t1+OFF+Du, t3: t1+OFF+Du, t4: t1+Du+Dd}`.

| Test | Construction | Asserts |
|---|---|---|
| symmetric path | `Du=Dd=20, OFF=100` (×N) | `down≈80`, `up≈120`, `roundTrip≈100`, `roundTrip − downlink ≈ minRtt/2` |
| the asymmetry (the point) | `Du=50, Dd=10, OFF=0` (×N) | `roundTrip≈+20` (false offset) but `downlink≈−10 = offset − Dd_floor`; the estimators disagree by the asymmetry |
| queueing → floor via MAX | `Dd ∈ {10,10,10,50,80,100}`, `OFF=100` | `downlink ≈ 90 = offset − Dd_floor` (added queueing only lowers `down`; p95 recovers the least-queued sample) |
| robust to a glitched `t4` | one sample with a wild-early `t4` (huge `down`) | p95 keeps `downlink` stable — one outlier doesn't set the floor |
| stability metric | stable-floor stream vs floor-wander stream | `dlStabilityMs` small for the first, large for the second |
| empty input safe | `[]` | returns `{ n: 0 }`-shaped safe object |

### New page — `clocktest-oneway.html` (repo root)

Near-clone of `clocktest-gotime.html`, minus GoTime (it only needs raw probes):

- Loads `/js/clockdiag/analysis.js` + `/js/clockdiag/probe-xhr.js`. Reuse `render.js`
  color bands for consistency (PASS `#1a7f37` / WARN `#9a6700` / FAIL `#cf222e`).
- Drive loop identical to v3: `qp('probes', 400)` / `qp('interval', 200)` config, the same
  `mmXhrProbe('/clockprobe', …)` train and `setTimeout` pacing. Default ~400 × 200 ms
  ≈ 80 s — enough for `subWindows=5` to each hold a solid sample count.
- Render a card: the **three estimators side by side** (`roundTrip` / `downlink` /
  `uplink`), `minRtt`, and prominently **`dlStabilityMs`** with a PASS/WARN/FAIL band
  (thresholds provisional, recalibrated from the first fleet run — same discipline as the
  existing verdicts). Include the raw `up`/`down` jitter from `mmClockStats` for path
  context.
- Report: fire-and-forget `GET /clockresult?d=<json>` with
  `{ mode:'oneway', n, roundTrip, downlink, uplink, minRtt, dlStabilityMs, dlSharpnessMs, jitter, failed }`.

### Fleet go/no-go run

Launch on all screens via `uiopen` (Safari is fine — pure clock path, no webclip/mmvideo
dependency). Collect the `CLOCKRESULT` lines from the server log; tabulate the **fleet
distribution of `dlStabilityMs`**. Tight-and-low across all 24 → downlink floor is real
hardware-wide → build v4b. Wandering on many → hybrid dead, cheaply.

## v4b sketch (not built here)

Proves the wall tightens; needs a **server-coordinated common reference** (v4a cannot fake
it). Likely shape:

- Server emits a scheduled beat / target server-time `T`; each screen runs **both** clocks
  (round-trip GoTime *and* the downlink-locked estimate) and reports its local time at `T`.
- Server aggregates and computes **cross-screen spread under each clock** — the direct A/B.
- **Trap to design around:** the beat/report path is itself jittery and non-simultaneous
  (the reason `synctool` exists and defeats its 5 traps). v4b will most likely **reuse
  `synctool`'s `ct@T` machinery** — measure a virtual playhead driven off each candidate
  clock with the tool we already trust — rather than invent a new comparator. That reuse
  decision is the first thing to pin down when v4b starts.

## Non-goals

- No production clock/playback change. v4a is a diagnostic page only.
- No new server routes. Reuse `/clockprobe` + `/clockresult`.
- No live rolling-max tracker (that convergence behaviour belongs to v4b/production; YAGNI here).
- No skew detrending (measured skew is negligible over these windows).

## Files

- Modify: `js/clockdiag/analysis.js` — add `mmOneWayEstimates`, export it.
- Modify: `tests/unit/js/clockdiag-analysis.test.js` — add the six test cases above.
- Create: `clocktest-oneway.html` — the v4a page.
