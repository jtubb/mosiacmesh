# Clock / comms qualification diagnostic — Design

**Date:** 2026-08-02
**Status:** Design approved (scope + ladder); ready for implementation plan.
**Related:** [[wall-cross-screen-sync-state]], [[onwall-clock-transport-debug]], [[ios5-websocket-transport]], [[drift-steer-rate-pll]]. GoTime NTP math: `js/GoTime.js` `_calculateOffset`. Master clock: `/time` (server.py, returns epoch ms).

## Goal

A self-contained diagnostic page that measures a device's **clock + communication-path health against the server master clock, independent of GoTime** — to (a) qualify hardware for future users, and (b) isolate the source of the ~225ms cross-screen clock-disagreement floor (measured on-wall via the animation test) by establishing what the raw hardware/path can achieve before GoTime's algorithm is layered on.

## Why (the ground-truth ladder)

Three versions, each adding exactly one layer, so a metric that is clean on rung N but degrades on rung N+1 localizes the fault:

1. **XHR (v1, this spec)** — raw round-trip NTP probes over plain XHR. Ground truth of the hardware crystal + the request/response network path. This is the exact path GoTime's offset-estimation + precision-ratchet use, so it directly targets the per-screen offset-bias suspect.
2. **WebSocket (v2)** — identical measurements over the ws (mmws on iPad-1). Reveals whether the transport itself adds jitter/asymmetry (rx-overflow, mmws main-runloop servicing).
3. **GoTime (v3)** — measures what GoTime *achieves* (its steered offset vs a parallel ground-truth reference). Reveals algorithm-level compounding failure modes (steer droop, ratchet staleness, skew discipline).

**The prime suspect this targets:** the on-wall ~225ms floor is a stable per-screen offset bias that is NOT main-thread jitter (~5ms), transit magnitude (~16ms), or skew (magnitude-impossible). The leading remaining hypothesis is **one-way transit asymmetry** (up-path delay ≠ down-path delay), which biases every NTP offset estimate irreducibly and differently per device. v1 measures this directly.

## Architecture

**Shared, testable core — pluggable probe source.** All three versions share the analysis + render; only the *sample source* differs.

- `js/clockdiag/analysis.js` (ES5, node-testable, NO DOM/network): pure functions over a stream of NTP samples `{t1,t2,t3,t4}` (t1=client send, t2=server recv, t3=server send, t4=client recv). Produces a stats object (RTT, offset, offset-jitter, skew, transit-symmetry) + PASS/WARN/FAIL verdicts. This is the piece that must be UNIT-TESTED (the recvTs lesson: never trust a measurement whose math isn't tested).
- `js/clockdiag/probe-xhr.js` (ES5): `xhrProbe(url, cb)` — one XHR `GET /clockprobe?t1=<clientMs>`, parses `{t1,t2,t3}`, stamps `t4`, calls `cb(sample)`. (v2 adds `probe-ws.js` with the same `(cb)` contract.)
- `js/clockdiag/render.js` (ES5): renders the stats object into a big, readable, color-coded table + a copy-results button. DOM only, no logic.
- `clocktest.html` (repo root, served like index.html): loads the three modules, runs the probe train, feeds samples to analysis, renders. Self-contained; **ES5 only** (must run in iPad-1 Safari 5.1); NO external deps.

**Server — one stateless endpoint** `GET /clockprobe` (new route in server.py's route table):
```python
async def clockprobe_handler(request):
    t2 = int(time.time() * 1000)                      # receive stamp
    t1 = request.query.get('t1', '0')
    t3 = int(time.time() * 1000)                      # send stamp (t2≈t3; both surface server processing time)
    return web.json_response({"t1": t1, "t2": t2, "t3": t3})
```
`add_route('GET', '/clockprobe', clockprobe_handler)`. No auth, no state.

## Metrics (v1)

Per probe: `RTT = (t4−t1) − (t3−t2)`; `offset = ((t2−t1) + (t3−t4)) / 2`.

| Metric | Computation | Indicates | PASS / WARN / FAIL (initial) |
|---|---|---|---|
| **RTT** | min/median/p90/max/std over the train | path latency + jitter (radio/PSM/AP) | jitter(std) <30 / <80 / ≥80 ms |
| **Clock offset** | offset from the min-RTT sample (NTP best estimate) | drift from master clock | reported (no pass/fail; context) |
| **Offset jitter** | std of offset across probes | clock/measurement noise | <20 / <60 / ≥60 ms |
| **Oscillator skew** | least-squares slope of offset vs local-time (ppm) | crystal quality | <30 / <80 / ≥80 ppm |
| **Transit symmetry** ⭐ | up=`(t2−t1)−offset`, down=`(t4−t3)+offset`; asym = `median(up) − median(down)` | asymmetric path → irreducible per-device offset bias (the ±110ms suspect) | \|asym\| <15 / <40 / ≥40 ms |
| **Main-thread timer jitter** | schedule fixed 100ms interval; report actual-firing std | CPU/render starvation | <20 / <60 / ≥60 ms |
| **Clock source** | `window.performance && performance.now` monotonic? else `Date` | resolution/monotonicity | performance=PASS, Date-only=WARN |

Thresholds are initial guesses; the page displays raw numbers prominently so they can be recalibrated from real device data.

## Test flow (v1, Safari-first)

Safari is fully scriptable start-to-end (unlike webclips, which need a VNC icon-tap): load via `uiopen 'http://<server>:3000/clocktest.html'`, iterate with `killall MobileSafari` + `uiopen`. Kill/relaunch cycles let us run the full build→test loop on-device without the webclip launch dance. Once v1 is stable in Safari, drop `clocktest.html` into a webclip and compare Safari vs webapp — a divergence there means the webapp *container* adds jitter GoTime inherits.

## Success criteria (v1)

- Runs clean in iPad-1 Safari 5.1 (ES5), reports all metrics with verdicts, ~60s run.
- `analysis.js` pure functions unit-tested (node `--test`) against synthetic sample sets with known RTT/offset/skew/asymmetry — the math is trustworthy before any device reads it.
- Establishes a per-device ground-truth number for transit symmetry + RTT jitter + skew, so we can state whether the on-wall ~225ms floor is hardware/path-bound or GoTime-bound.

## Out of scope (v1)

- WS probe (v2) and GoTime comparison (v3) — same core, later rungs.
- Multi-device aggregation / server-side collection — v1 is a per-device page; the operator reads/records each device's result.
- Any change to GoTime or the production clock path — this is a *measurement* tool, strictly additive (one new endpoint + new files).
