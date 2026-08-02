# mmws native receipt-timestamp — break the clock-beat main-thread floor — Design

**Date:** 2026-08-01
**Status:** Design approved; ready for implementation plan.
**Related:** [[wall-cross-screen-sync-state]], [[ios5-websocket-transport]], [[mmvideo-currenttime-observer-bimodal]], [[onwall-clock-transport-debug]]. Prior arc: `2026-08-01-master-clock-beat-design.md` (the beat, merged 1f95d3a). mmws source: `tweak/mmws/` (mmwsconn.c, mmws.c, mmws.js), DESIGN.md.

## Goal

Break the ~193ms residual cross-screen clock-disagreement floor left by the master-clock beat. The beat bounds the drift (470→193ms) but can't go lower because `beatReceived` stamps `localRecv` on the busy iOS-5 main thread, long after the frame actually arrived. Capture the beat's receipt time **natively, off the main thread, at true frame arrival**, and use that as `localRecv` — collapsing the per-screen receive-timestamp variance so the beat's common-mode benefit finally reaches tens-of-ms.

## Root cause (measured + code-confirmed 2026-08-01)

On-wall A/B: the beat is consumed by all 24 screens (accAge 19-59min → ~1s) and caps clock-disagreement at ~193ms, but doesn't collapse it. The residual is **iOS-5 main-thread receive-timestamp jitter**. Code-confirmed in mmws:
- `mmwsconn_open` (mmwsconn.c:128/147) schedules the CFStreams on `CFRunLoopGetCurrent()` — the **main runloop** (no `pthread_create`/`dispatch_queue` anywhere in mmws; DESIGN.md line 68 confirms the callbacks fire "on the main thread").
- So `read_cb` (mmwsconn.c:103) only fires when the main runloop services the stream — and video/animation rendering starves that runloop. The **frame itself is delivered late**, so stamping inside `read_cb` today wouldn't help: `read_cb` is already the delayed point.
- The `event.timeStamp` shortcut is a dead end: SockJS discards the raw ws event and re-stamps its own synthetic event on the main thread (`sockjs event.js initEvent: this.timeStamp=+new Date`) — verified, do not re-attempt ([[wall-cross-screen-sync-state]]).

Fix: service the socket **off the main thread** so receipt can be stamped at true arrival.

## Architecture

### Native — dedicated I/O thread (mmwsconn.c, mmws.c)

- **One dedicated pthread runs a CFRunLoop** ("mmws-io"). `mmwsconn_open` starts it (or reuses a shared I/O thread) and schedules the read + write CFStreams on **that** runloop instead of the caller's (`CFRunLoopGetCurrent()` at open time is currently main).
- **rx runs entirely on the I/O thread**: `read_cb` + the state machine (`mmws_sm_on_recv`, reassembly, the rx buffer) — single-threaded there, so no new locking *within* rx. The I/O thread is never blocked by rendering, so `read_cb` fires at true frame arrival.
- **Receipt stamp**: at `on_message` (frame-complete, mmwsconn.c:85), capture `recvTs = gettimeofday()` epoch-ms (matches JS `Date.getTime()`), jitter-free.
- **Delivery to JS**: `dispatch_async(dispatch_get_main_queue(), ...)` the (connId, data, recvTs) to the main thread (the UIWebView JS bridge must run on main). Use a **serial** path so frame order is preserved. The beat is *processed* late, but the offset is `serverSendMs − recvTs` with `recvTs` fixed at receipt, so a late `beatReceived` still computes the correct offset.
- **tx (JS→native sends)**: marshal writes from main onto the I/O thread (dispatch onto its runloop) so *all* socket ops are single-threaded on the I/O thread. A small lock guards connection lifecycle (open/teardown) against the I/O thread.
- **Teardown**: stop the I/O runloop + join the thread cleanly on close; guard against in-flight main-dispatch delivering to a torn-down conn (connId validity check / generation counter).

**Invariant:** all socket I/O on the I/O thread; cross-thread only via dispatch. The connection is an actor with no shared mutable socket state — the discipline that keeps the fragile core tractable.

### recvTs delivery past SockJS (mmws.js + app)

mmws fires `ws.onmessage({data})` → SockJS transport reads `.data`, unframes, dispatches to the app's `sock.onmessage` — **synchronously, same main-thread call stack**. So do NOT thread `recvTs` through SockJS (it strips it). Side-channel instead:
- `__mmwsDispatch(id, type, data, recvTs)` gains the param (mmws.js:71); for a message it sets `w.__mmwsRecvTs = recvTs` **immediately before** `ws.onmessage`.
- Because the chain (mmws.js → SockJS unframe → app onmessage → beatReceived) runs synchronously with no interleaving (JS single-threaded, SockJS dispatches synchronously), `window.__mmwsRecvTs` holds *this* frame's receipt time when `beatReceived` reads it. Frame N's `beatReceived` completes before frame N+1's `__mmwsDispatch` is called, so no overwrite race. A SockJS array of several app-messages shares one frame `recvTs` (correct — they arrived together). XHR-fallback transports never set it → fallback path.

### JS/GoTime changes (small)

- `mmws.js`: `w.__mmwsRecvTs = recvTs;` before `onmessage` (+ the `__mmwsDispatch` param).
- `js/mosiacmesh.js` TIMEBEAT branch: `GoTime.beatReceived(data_obj.PAYLOAD.t, window.__mmwsRecvTs)`.
- `GoTime.beatReceived(serverSendMs, recvTs)`: use `recvTs` when plausible (fresh epoch-ms within a few seconds of `getAccurateTimestamp()`), else fall back to `getAccurateTimestamp()`. Add a pure, node-tested helper `_pickRecv(recvTs, nowTs, toleranceMs)` for the plausibility gate; `_beatSample` stays pure with the chosen `localRecv`.

### Time base

`gettimeofday` epoch-ms (native) == `Date.getTime()` epoch-ms (JS). Same wall clock — if the device clock steps (NTP), both step together, so `serverSendMs − recvTs` stays valid.

## Validation

Native-build A/B (not a JS flag — a new mmws dylib):
1. Build the new mmws dylib (WSL theos, per the build/deploy discipline).
2. **Canary first** (one screen, sequential SSH per [[fleet-ssh-no-burst]] using Windows OpenSSH per [[fleet-tweak-deploy-ssh-doze]]): confirm no rx-overflow/reconnect regression (device mmws.log), `window.__mmwsRecvTs` populating, ws holding.
3. Measure: with native `recvTs`, clock-disagreement should drop from the ~193ms floor toward tens-of-ms (synctool clock-disagreement + `elapsed`-spread, matched elapsed-since-reload, distributions across many windows — never single snapshots).
4. Roll out in waves only after the canary holds.

## Success criteria

- clock-disagreement drops below the ~193ms main-thread floor (target tens-of-ms) with the native-recvTs build.
- No mmws rx-overflow / reconnect-loop regression on the canary or fleet.
- `beatReceived` falls back cleanly to `getAccurateTimestamp()` when `recvTs` is absent/implausible (XHR fallback, non-beat frames).

## Risks

- **main↔io concurrency boundary (the real one)**: tx marshaling + lifecycle races. Mitigated by the actor discipline (all socket I/O on the I/O thread) + canary.
- **Dedicated-thread runloop lifecycle/teardown**: in-flight main-dispatch delivering to a torn-down conn — guard with a connId/generation validity check.
- **rx-overflow behavior**: the buffer logic is unchanged, only relocated to the I/O thread — watch the canary mmws.log ([[ios5-websocket-transport]]).
- **mmws is the fragile core**: this is a substantial native concurrency change to a tweak with a history of tears. Canary-gated, wave rollout, instant fallback (revert to the current dylib) if the canary regresses.

## Out of scope

- Modern-client raw-WebSocket receipt timestamping (low value — all-iPad-1 wall).
- Beat cadence / GoTime steer changes (unchanged — this only improves the *quality* of `localRecv`).
- General mmws transport hardening beyond what the threading rework touches (task #417 remains its own effort).
