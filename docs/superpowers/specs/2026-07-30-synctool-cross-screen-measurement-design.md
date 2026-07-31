# synctool — trustworthy cross-screen sync measurement (CLI) — design

**Date:** 2026-07-30
**Branch:** `feat/synctool-cross-screen-measure`
**Motivation:** This session's sync work churned because the measurement was untrustworthy — swing (jitter) was mistaken for cross-screen agreement, loop-boundary spikes and re-arm transients contaminated aggregates, stale ticks polluted reads, and the client-clock-agreement assumption was never verified. Before any more controller tuning, build a measurement that **cannot be fooled by those artifacts** and is **itself unit-tested**. See `[[wall-cross-screen-sync-state]]`.

## Goal

A CLI tool that reports the **actual cross-screen visual desync** of a display group (headline), attributes it to video-position vs clock-disagreement, gives each screen's **correction offset** for centering, and states whether the measurement is **settled** — computed from existing telemetry with the tool's own math unit-tested.

## Non-goals (v1)
- No live admin-UI panel (CLI first; UI later once the metric is proven).
- No new client-side instrumentation (parse existing `mm_server.err` telemetry; zero wall risk).
- No automatic centering action (the tool *reports* corrections; applying them is separate).

## Section 1 — What it measures

- **Headline (basis B, ground truth):** each screen's actual video position `ct` (mmvideo currentTime, ms) interpolated to a **common server-receive instant T**, then the **circular spread** (mod loop length) of those positions across screens. This is the visible desync — clock-independent (uses the server's log/receive timestamps as the common reference, not each client's clock), so it stays valid even when playback is imperfect.
- **Attribution (basis A):** alongside, from the same telemetry, `err` (video vs each screen's own clock) and `off`/`skew` (clock state). Decompose the headline into a **video-offset component** (spread of `err`) and a **clock-disagreement component** (headline − video-offset, i.e. the part explained by clocks not agreeing) so we fix the right layer.
- **Center:** group center = median of `ct_i(T)`; each screen's **correction** = `center − ct_i(T)` (how far, and which way, to nudge it).

## Section 2 — Algorithm (reliability lives here)

Inputs per screen: the recent stream of `(t_recv_ms, ct, err, off, rate)` from drift/hb CLIENTLOG lines, where `t_recv_ms` is the server log timestamp (ms-precise, e.g. `15:39:21,220`) — the common reference.

Per screen, over a window `WINDOW_MS` (~15000–20000 ms ending at the latest common time):
1. Collect samples with `ct` present.
2. **Reject loop-wrap samples:** where `ct` steps *backward* between consecutive samples (the 30 s loop reset) — split the window at the wrap and keep the longer post-wrap segment (so we fit within one loop, no discontinuity).
3. **Reject outliers:** samples whose `ct` deviates > `OUTLIER_MS` from a first-pass line (guards the ±66 ms readback spikes).
4. **Least-squares linear fit** `ct(t) = ct0 + slope·(t − t0)` over the kept samples. The fit *smooths* readback noise (uses all samples, not one) and yields a clean interpolant. Require ≥ `MIN_SAMPLES` (e.g. 5) or the screen is reported `insufficient-data` (not silently included).
5. Evaluate every screen's fit at the common instant `T` (the latest timestamp common to the fresh set): `ct_i(T) = ct0_i + slope_i·(T − t0_i)`.

Cross-screen:
6. **Circular spread:** treat `ct_i(T) mod loopLen` on a circle of circumference `loopLen` (the item duration, e.g. 30016 ms). Compute the minimal-arc spread: find the rotation that minimizes max−min (equivalently, sort, find the largest gap, and measure the complement). Screens near the wrap (e.g. 29 900 vs 100) read as 200 ms apart, not 29 800. Spread = headline desync.
7. **Center + corrections:** circular median of `ct_i(T)`; `correction_i = signed circular distance from ct_i(T) to the center`.

Loop length is taken from the group's playlist item duration (or inferred from the observed wrap point).

## Section 3 — Data source + settled gating

- **Source:** parse `mm_server.err` (the running server's verbose log). `t_recv_ms` = the log-line timestamp; `ct`/`err`/`off`/`rate` from the payload dict. Zero new instrumentation.
- **Fresh gating:** only screens whose latest sample is within `FRESH_MS` (~20 s) of the common time. Stale screens are listed separately as `stale/absent`, never folded into the spread (prevents the stale-tick trap).
- **Settled verdict (prevents the re-arm-transient trap):** compute the headline spread over two adjacent sub-windows (early half vs late half of the window). If they agree within `SETTLE_TOL_MS` (e.g. 25 ms) AND each screen's fit slope is near 1.0 (steady playback, not seeking/arming), report `SETTLED`; else `CONVERGING` with the delta shown. A number is never presented as trustworthy mid-transient.

## Section 4 — Output

Human-readable table to stdout; optional `--json` for scripting:
```
OEB Sign 1  cross-screen sync   T=15:39:40.220   [SETTLED]
  HEADLINE visual desync (circular spread of ct@T): 42 ms
  attribution: video-offset 38 ms | clock-disagreement ~12 ms
  playing 21 | stale/absent 3 | insufficient-data 0
  screen          ct@T     off_from_center   correction   fit_slope
  sign1screen7    12043         -18             +18          1.00
  ...
  worst offenders: sign1screen4 +31, sign1screen17 -24
  stale/absent: sign1screen18, ...
```

## Section 5 — Making the tool itself trustworthy

The pure functions are extracted (no I/O) and **pytest-unit-tested with synthetic data**, so the measurer is validated before its measurements are trusted:
- `fit_ct(samples) -> (ct0, slope, t0, kept, rejected)` — feed a clean line + injected loop-wrap + ±noise spikes; assert the wrap/outliers are rejected and the slope/intercept recover the truth.
- `circular_spread(positions, loopLen)` — feed known offsets incl. a wrap-straddling set (29 900 & 100); assert spread = 200, not 29 800; assert a perfectly-aligned set = 0.
- `corrections(positions, loopLen) -> (center, per_screen)` — feed known offsets; assert corrections sum-to-center and match by construction.
- `settled_verdict(early_spread, late_spread, slopes)` — assert CONVERGING when sub-windows disagree or slopes ≠ 1, SETTLED otherwise.

The thin CLI shell (log parsing + printing) stays dumb; all judgment is in the tested pure functions.

## File structure
- `tools/synctool.py` — CLI shell: parse `mm_server.err`, call pure functions, print. (`tools/_`-prefixed ad-hoc scripts are gitignored; this one is a kept tool, no underscore.)
- `tools/synctool_core.py` — pure functions (fit, circular spread, corrections, settled verdict). No I/O.
- `tests/unit/test_synctool.py` — pytest for the pure functions with synthetic data.

## Constants (initial, tunable)
`WINDOW_MS=18000`, `FRESH_MS=20000`, `MIN_SAMPLES=5`, `OUTLIER_MS=120`, `SETTLE_TOL_MS=25`, `SLOPE_TOL=0.05` (|slope−1|), loopLen from playlist item duration.

## Risks
- **Network-delay jitter in `t_recv`** — the server log timestamp lags client-send by the (variable) network delay, adding noise to the common-reference alignment. Mitigated by the per-screen fit (averages it) and by reporting a settled verdict; if it dominates, a future v2 could add a client-stamped send time to the payload.
- **Loop-length inference** — if the playlist item duration isn't available, infer from the observed wrap; a wrong loopLen corrupts the circular math. Validate the inferred value against the wrap magnitude.
- **All-screens-share-a-bias** — B measures *relative* cross-screen spread; a uniform offset (all screens equally behind the clock) reads as 0 spread (correctly — it's not *visible* desync). The `err` attribution surfaces such a uniform offset separately.
