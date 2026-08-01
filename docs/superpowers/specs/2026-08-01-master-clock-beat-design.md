# Master-clock broadcast beat — cross-screen clock sync (Lever B, pivoted) — Design

**Date:** 2026-08-01
**Status:** Design approved; ready for implementation plan.
**Related:** [[wall-cross-screen-sync-state]], [[drift-steer-rate-pll]], [[onwall-clock-transport-debug]], [[ios5-websocket-transport]]. Prior arc: `2026-08-01-drift-steer-droop-and-spurious-seeks-design.md` (Lever A, shipped 7f0fc82).

## Goal

Collapse the ~400ms cross-screen **clock-disagreement** that dominates the OEB wall's remaining desync after Lever A cleared the video/seek layer — by making all screens phase-lock to a single server-broadcast time beat instead of each independently estimating server time. Serves **video and animation** playback equally (both derive position from `GoTime.now()`).

## Why this, and why now (the measure-gate that pivoted Lever B)

After Lever A (filtered coarse-seek, shipped) was deployed on-wall (PullTest3 / OEB Sign 1, 24 screens), synctool over 4 tight windows showed:
- **video-offset STABLE ~150–224ms** (Lever A's layer — at/below its floor; roughly halved from the FF-era ~414ms).
- **clock-disagreement DOMINANT and bouncing 266–538ms** — the larger, noisier component.

Per-screen clock diagnosis (current window): **precision excellent everywhere (6–22ms)** — poor RTT is *not* the problem — but **`accAge` median ~19 min, max ~59 min**: each screen locked its offset at a precise early sample and has drifted *independently* since, corrected only by its own skew. `settled=False` on 21/24. So the ~400ms is **24 nodes each holding an independently-locked, independently-drifting offset with its own one-way-asymmetry bias**. NTP-style estimation has irreducible per-node asymmetry error; 24 independent nodes spread ~400ms. The cure is **cross-screen agreement, not better absolute accuracy** — the "master clock" idea from the start of this effort. Original Lever B (droop integral) is deferred: it targets the *smaller, stable* 188ms video-offset while this dominates.

## Core mechanism — convert independent error into common-mode error

Today each screen runs **independent** round-trip `/time` estimation (polls at different moments, each with its own one-way asymmetry) → independent errors → they spread. A single **broadcast beat** that all screens receive and phase-lock to makes the dominant error (server→client transit) **common-mode**: it shifts every screen *together*, which does not cause cross-screen disagreement. Residual cross-screen spread collapses to the *variance* of transit between screens (tens of ms, and the steer's robust median averages the random part out).

**Deliberately NO per-screen time-of-flight correction.** Measuring per-screen ToF (RTT/2, as `_calculateOffset` does today) *is* the one-way-asymmetry error we are eliminating — doing it independently on 24 screens is what spreads them. The beat carries server-send-time; the client computes the **naive offset `serverSendMs − localRecv`** and does *not* correct for its own transit. Transit becomes a common-mode lag: every screen reads ~ToF behind absolute server time, but *together*. Since `startEpoch` (playback origin) is also server-clock, a uniform few-ms LAN lag shared by all screens is invisible — the wall has no external timing reference it must hit. The ToF constant is dropped entirely (it solved absolute accuracy, which the wall doesn't need).

## Why it serves video AND animation

Both read `GoTime.now()`. `GoTime.now() = raw + _offset + _skew*(raw − _skewRef)`; playback `elapsed = now() − startEpoch`.
- **Video:** the `<video>` element runs on the media pipeline's *own* clock that drifts from GoTime — that drift is why video needs the rate-steer + seeks (Lever A). The beat tightens video's *clock* layer (on top of Lever A).
- **Animation (SCRIPT mode):** `runScriptLoop` recomputes `rawElapsed = GoTime.now() − startEpoch` **every frame** and passes `pos.offsetMs` + `GoTime.now()` straight into the draw fn (index.html:850, 936, 1024). Content is a **pure function of the shared clock** — it self-corrects to the clock every frame, needs **no rate-steer or seeks**, and frame-rate differences (iOS-5 rAF throttling) affect only smoothness, not sync position. For animation there is no video-offset layer, so **the clock is the entire sync lever** — the beat helps animation even more than video. Caveat: *stateful* animations (e.g. gameOfLife, integrating generation-to-generation) don't self-correct from the clock; they must be time-deterministic (state = pure function of clock-time + seed). `mmLoopItemSeed` already provides that; frame-accumulated state does not. No separate animation controller is introduced — **the master clock is the animation steer.**

## Architecture

### Server — beat emitter (server.py)

New message type, distinct from request/response `SERVERTIME` (reusing it would break `wsReceived`, which needs `_wsRequestTime`):
```python
# BEAT_INTERVAL_S = 2
async def _time_beat():
    while True:
        server_ms = int(time.time() * 1000)     # STAMP right before fan-out, once
        broadcast_all({"REQUEST": "TIMEBEAT", "PAYLOAD": {"t": server_ms}})
        await asyncio.sleep(BEAT_INTERVAL_S)
```
- **Cadence 2s** — fresher than the ~30s the offset currently goes stale; ~30-byte payload, negligible load (well clear of the mmws rx-overflow regime).
- **Stamp at send, once, fleet-wide** — a single stamp + single fan-out means every screen aligns to the *exact same number* → maximally common-mode. Not staged in the 5s `process()` loop.
- **Always-on, all online clients** — keeps clocks warm (fast PREPARE) and idle screens from drifting apart; same global clock serves every group.
- **Broadcast primitive:** confirm at spec-write time whether a fleet-wide `broadcast_all` exists or fan out over `broadcast_to_display_group` per group / iterate the socketmanager. Shape holds either way.

### Client — TIMEBEAT handler + naive-offset sample (js/mosiacmesh.js, js/GoTime.js)

Routing (js/mosiacmesh.js `onmessage`, beside the `SERVERTIME` branch):
```javascript
if (data_obj.REQUEST == "TIMEBEAT") { GoTime.beatReceived(data_obj.PAYLOAD.t); }
```
New GoTime method:
```javascript
beatReceived: function (serverSendMs) {
    var localRecv = getAccurateTimestamp();   // SAME clock fn now() uses -> frame-consistent
    var sample = { offset: serverSendMs - localRecv, precision: BEAT_PRECISION_MS };  // ~12 nominal
    return _reviseOffset(sample, "beat");
}
```
- `_reviseOffset` pushes to `_steerSamples` **before** the precision ratchet (line 430 vs 446), so beat samples always feed the robust target + slew that own the offset during playback, regardless of the precision nominal. The ratchet governs only the pre-steering initial lock.
- On the iPad-1, `getAccurateTimestamp()` = `Date.getTime()` (epoch) — `window.performance` doesn't exist on Safari 5.1 — so `serverSendMs − localRecv` is a clean epoch-frame offset consistent with `now()`.
- **ES5 only** (iPad-1 constraint): `var`, `function`, no `let`/`const`/arrow/template-literal in js/GoTime.js and js/mosiacmesh.js.

### Client — prefer beat during playback, `/time` failsafe

- During `playback.active`, suppress the periodic `_sync` `/time` polls so independent round-trip samples don't dilute the common-mode beat samples in the shared 64-deep `_steerSamples` ring.
- **Failsafe:** if no beat arrives within `BEAT_STALE_MS` (~6000, i.e. 3× interval) — server restart, transport death — resume `/time` polling so the clock never goes stale. Beat is primary; `/time` is the safety net.
- PREPARE's `resync()` burst stays as-is for arm robustness.

### A/B toggle

Flag file `js/beatflag.js` (`window._mmClockBeat`, mirroring `tdbgflag.js`/`driftff.js`): off → pure `/time` (current behavior, byte-identical, beat ignored); on → beat-driven. Server emits the beat unconditionally; only flagged clients consume it. Instant rollback.

**New constants:** `BEAT_INTERVAL_S = 2` (server), `BEAT_PRECISION_MS ≈ 12`, `BEAT_STALE_MS ≈ 6000` (client).

## Measurement & validation

- **Primary metric:** synctool `clock-disagreement` attribution on PullTest3 (video) — already isolates clock from video-offset. Content-independent equivalent: cross-screen spread of the `elapsed` field (already in `hb`/`drift` telemetry) at a common instant.
- **Animation confirm (video-offset-free):** an animation playlist (`All Effects Demo`); `elapsed`-spread is literally the whole visible desync there.
- **A/B protocol** (temporal, matches prior runs): measure baseline flag-off → flip flag → reload → measure. Run on both PullTest3 and the animation playlist.
- **Rollout order:** (1) server beat emitter (always-on, harmless to flag-off clients) — deploy server-side (restart). (2) client JS flag-off → fleet reload → verify byte-identical. (3) flip flag on → reload → A/B measure.

## Success criteria

- clock-disagreement drops from ~400ms (bouncing) toward the fanout-jitter floor — target < ~100ms, ideally tens of ms.
- video-offset (~150–220ms) and Lever-A seek behavior unchanged (the beat touches only the clock layer).
- animation `elapsed`-spread visibly tightens.

## Risks

- **Transport coupling (the main one):** common-mode benefit assumes every screen receives the beat with low, *similar* delay. A screen on the SockJS XHR-polling fallback (not a real mmws websocket) gets the beat with polling latency + jitter → less common-mode → residual spread. Payoff is coupled to transport health (the open mmws work, task #417). Real-ws screens benefit fully; XHR-fallback screens partially. Not a blocker; state plainly.
- **Failsafe correctness:** a transport blip must fall back to `/time`, or the clock freezes. The `BEAT_STALE_MS` watchdog must be solid.
- **Precision nominal:** too optimistic → a jittery beat could lock a bad pre-steering offset; too pessimistic → beats never gate `readyVerdict`. Start at ~12ms; refine from beat inter-arrival jitter later if needed.

## Out of scope

- Per-screen ToF / absolute-accuracy correction (the anti-pattern here).
- Elected-screen master / peer failover (server-is-master chosen for simplicity + trivial election).
- Droop integral (original Lever B) — deferred; targets the smaller stable video-offset.
- mmws transport hardening (task #417) — separate; this design degrades gracefully to partial benefit on XHR-fallback screens.
