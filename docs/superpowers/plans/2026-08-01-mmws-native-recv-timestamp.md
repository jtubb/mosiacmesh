# mmws native receipt-timestamp — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture the clock-beat's receipt time natively, off the iOS-5 main thread, at true frame arrival — so `beatReceived` uses a jitter-free `localRecv` and the beat's common-mode benefit reaches tens-of-ms (vs the current ~193ms main-thread floor).

**Architecture:** mmws services its CFStreams on a **dedicated I/O thread** (actor: all socket I/O on that thread, cross-thread only via dispatch). `cb_msg` stamps `recvTs = gettimeofday()` epoch-ms at frame arrival on the I/O thread, then the existing `dispatch_async_f`→`__mmwsDispatch` bridge carries (data, recvTs) to main. `recvTs` reaches `beatReceived` past SockJS via a `window.__mmwsRecvTs` side-channel (SockJS dispatches synchronously → race-free). Falls back to `getAccurateTimestamp()` when `recvTs` is absent.

**Tech Stack:** ES5 JS (iPad-1) + node `node:test`; C (mmwsconn.c) + Logos/ObjC (Tweak.x), iOS 5.1 SDK, built via WSL theos.

## Global Constraints

- **iPad-1 ES5 only** in `js/GoTime.js`, `js/mosiacmesh.js`, `tweak/mmws/mmws.js`: `var`+`function`, no let/const/arrow/template-literal/class.
- **Graceful degradation is mandatory:** every JS change must behave identically to today when `recvTs`/`window.__mmwsRecvTs` is absent (so JS tasks 1-3 are safe to ship before any native change lands, and XHR-fallback screens are unaffected).
- **Time base:** native `recvTs` = `gettimeofday()` epoch-ms, to match JS `Date.getTime()` (what `getAccurateTimestamp()` resolves to on iOS-5). Never mix in a monotonic clock for `recvTs`.
- **mmws actor invariant:** ALL socket I/O (stream scheduling, reads, writes, teardown) happens on the dedicated I/O thread. Main touches the connection only via dispatch onto the I/O thread (sends/close) or receives via `dispatch_async_f` to main (the existing JS-eval bridge). No shared mutable socket state accessed from two threads.
- **mmws is the fragile core** ([[ios5-websocket-transport]]): native changes are validated by host-compile (`mmws_sm_test.c`/`mmws_test.c` still build+pass) + a **canary** deploy (one screen, sequential SSH via Windows OpenSSH per [[fleet-tweak-deploy-ssh-doze]], never a burst per [[fleet-ssh-no-burst]]) before any wave rollout. Keep the prior dylib for instant fallback.
- Pure JS helpers (`_pickRecv`) take primitives, no `window`/timers — node-testable.
- Tests: `node --test tests/unit/js/<file>`, `python pytest_runner.py --js`; native host tests build under the mmws test harness.

---

### Task 1: GoTime — `beatReceived(serverSendMs, recvTs)` + `_pickRecv`

**Files:**
- Modify: `js/GoTime.js` (add `_pickRecv`; `beatReceived` gains optional `recvTs`)
- Test: `tests/unit/js/gotime-beat.test.js` (extend)

**Interfaces:**
- Produces: pure `GoTime._pickRecv(recvTs, nowTs, toleranceMs) -> number` (returns `recvTs` if a finite number within `toleranceMs` of `nowTs`, else `nowTs`). `beatReceived(serverSendMs, recvTs)` uses `_pickRecv(recvTs, getAccurateTimestamp(), BEAT_RECV_TOL_MS)` as `localRecv`.

- [ ] **Step 1: Write failing tests** in `tests/unit/js/gotime-beat.test.js`:
```javascript
test('_pickRecv: fresh native recvTs within tolerance is used', () => {
  assert.equal(G._pickRecv(1000000, 1000120, 3000), 1000000); // 120ms apart < 3s
});
test('_pickRecv: implausible recvTs (too far / NaN / missing) falls back to nowTs', () => {
  assert.equal(G._pickRecv(500000, 1000000, 3000), 1000000);  // 500s apart -> reject
  assert.equal(G._pickRecv(NaN, 1000000, 3000), 1000000);
  assert.equal(G._pickRecv(undefined, 1000000, 3000), 1000000);
});
test('beatReceived: with fresh recvTs, offset uses recvTs not the local read', () => {
  var s = G.getSteerState ? null : null;   // exercise via _beatSample path
  // offset must equal serverSendMs - recvTs when recvTs is plausible
  var off = G._beatSample(1000000, G._pickRecv(999880, 1000000, 3000), 12).offset;
  assert.equal(off, 120);   // 1000000 - 999880
});
```

- [ ] **Step 2: Run to verify fail** — `node --test tests/unit/js/gotime-beat.test.js` → `_pickRecv` undefined.

- [ ] **Step 3: Implement** in `js/GoTime.js`:
```javascript
    // Choose localRecv for a beat: prefer a native receipt stamp (recvTs, epoch-ms
    // from mmws' off-main I/O thread) when it's a finite value close to now; else
    // fall back to the main-thread clock read. toleranceMs rejects stale/garbage stamps.
    _pickRecv = function (recvTs, nowTs, toleranceMs) {
        if (typeof recvTs === 'number' && isFinite(recvTs) &&
            Math.abs(nowTs - recvTs) <= toleranceMs) { return recvTs; }
        return nowTs;
    };
```
Add constant near the others: `_beatRecvTolMs: 3000`. Change `beatReceived`:
```javascript
        beatReceived: function (serverSendMs, recvTs) {
            var nowTs = getAccurateTimestamp();
            var localRecv = _pickRecv(recvTs, nowTs, options._beatRecvTolMs);
            options._lastBeatMs = nowTs;   // freshness/failsafe uses the local read, not recvTs
            var sample = _beatSample(serverSendMs, localRecv, options._beatPrecisionMs);
            return _reviseOffset(sample, "beat");
        },
```
Export `_pickRecv: _pickRecv` in the returned object. (Note: `_lastBeatMs` stays `nowTs` so the `/time` failsafe/staleness logic is unchanged.)

- [ ] **Step 4: Run** — `node --test tests/unit/js/gotime-beat.test.js` → all pass.
- [ ] **Step 5: Full JS suite** — `python pytest_runner.py --js` → green (existing `beatReceived(t)` one-arg callers still work: `recvTs` undefined → `_pickRecv` returns `nowTs`, i.e. current behavior).
- [ ] **Step 6: Commit** — `git add js/GoTime.js tests/unit/js/gotime-beat.test.js && git commit -m "feat(clock): GoTime.beatReceived accepts a native recvTs (falls back to local clock)"`

---

### Task 2: mmws.js — `__mmwsDispatch` recvTs param + `window.__mmwsRecvTs` side-channel

**Files:**
- Modify: `tweak/mmws/mmws.js` (`__mmwsDispatch` signature + message branch)
- Test: `tests/unit/js/mmws-recvts-smoke.test.js` (create — string/behavior smoke via readFileSync)

**Interfaces:**
- Consumes: native calls `window.__mmwsDispatch(id, 'message', data, recvTs)` (Task 5 adds the 4th arg).
- Produces: sets `window.__mmwsRecvTs = recvTs` immediately before firing `ws.onmessage`, for synchronous downstream read.

- [ ] **Step 1: Write the smoke test** `tests/unit/js/mmws-recvts-smoke.test.js`:
```javascript
import { test } from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
const src = readFileSync(new URL('../../../tweak/mmws/mmws.js', import.meta.url), 'utf8');
test('mmws __mmwsDispatch takes recvTs and sets window.__mmwsRecvTs before onmessage', () => {
  assert.match(src, /__mmwsDispatch\s*=\s*function\s*\([^)]*recvTs/);
  const msgIdx = src.indexOf("=== 'message'");
  const setIdx = src.indexOf('__mmwsRecvTs');
  const onmsgIdx = src.indexOf('ws.onmessage', msgIdx);
  assert.ok(setIdx > -1 && setIdx < onmsgIdx, '__mmwsRecvTs set before ws.onmessage');
});
```

- [ ] **Step 2: Run to verify fail** — `node --test tests/unit/js/mmws-recvts-smoke.test.js`.

- [ ] **Step 3: Implement** in `tweak/mmws/mmws.js` (`__mmwsDispatch`, message branch):
```javascript
    w.__mmwsDispatch = function (id, type, data, recvTs) {
        var ws = reg[id];
        if (!ws) return;
        if (type === 'open') {
            ws.readyState = OPEN;
            if (ws.onopen) try { ws.onopen({ type: 'open' }); } catch (e) {}
        } else if (type === 'message') {
            w.__mmwsRecvTs = recvTs;   // native off-main receipt stamp for THIS frame (epoch-ms);
                                       // read synchronously downstream (SockJS dispatches sync)
            if (ws.onmessage) try { ws.onmessage({ type: 'message', data: data }); } catch (e) {}
        } else if (type === 'close') {
            ws._closed(typeof data === 'number' ? data : 1000);
        } else if (type === 'error') {
            ws._fail(data || 'error');
        }
    };
```

- [ ] **Step 4: Run** — smoke passes. **Step 5:** `python pytest_runner.py --js` green.
- [ ] **Step 6: Commit** — `git add tweak/mmws/mmws.js tests/unit/js/mmws-recvts-smoke.test.js && git commit -m "feat(mmws): __mmwsDispatch carries recvTs via window.__mmwsRecvTs side-channel"`

---

### Task 3: App wiring — TIMEBEAT passes `window.__mmwsRecvTs`

**Files:** Modify `js/mosiacmesh.js` (the TIMEBEAT branch from the prior beat work).

- [ ] **Step 1:** In the `s.onmessage` TIMEBEAT branch, pass the side-channel value:
```javascript
				if(data_obj.REQUEST == "TIMEBEAT")
				{
					if (typeof GoTime !== 'undefined' && GoTime.beatReceived && window._mmClockBeat) {
						GoTime.beatReceived(data_obj.PAYLOAD.t, window.__mmwsRecvTs);
					}
				}
```
(When mmws hasn't set `__mmwsRecvTs` — XHR fallback or pre-native build — it's `undefined` → `beatReceived` falls back to the local clock. Byte-identical behavior.)

- [ ] **Step 2:** `python pytest_runner.py --js` green (no test asserts the old one-arg form). **Step 3: Commit** — `git add js/mosiacmesh.js && git commit -m "feat(clock): pass mmws native recvTs into beatReceived"`

---

### Task 4: Native — dedicated I/O thread in mmwsconn.c

**Files:** Modify `tweak/mmws/mmwsconn.c` (thread lifecycle + schedule streams on the I/O runloop + marshal sends + teardown).

**Interfaces:** `mmwsconn_open/_send_text/_send_binary/_close/_free` signatures UNCHANGED (Tweak.x callers are untouched by this task). Only the threading behind them changes.

- [ ] **Step 1:** Add a per-conn I/O thread. In `mmwsconn_open`, instead of scheduling on `CFRunLoopGetCurrent()`: spawn a `pthread` that (a) creates the CFStream pair + sets clients + schedules them on **its own** `CFRunLoopGetCurrent()` + opens them + `mmws_sm_start`, then (b) runs `CFRunLoopRun()`. Store the I/O thread's `CFRunLoopRef` on the conn (published under a small mutex + a "ready" condition so `mmwsconn_open` can return only once the runloop ref is set). `read_cb`/`write_cb`/`conn_send`/`conn_event` now fire on the I/O thread — unchanged logic.

- [ ] **Step 2:** Marshal sends onto the I/O thread. `mmwsconn_send_text/_binary` (called from main) must not touch the streams directly. Copy the payload and `CFRunLoopPerformBlock(io_rl, kCFRunLoopCommonModes, ^{ mmws_sm_send_msg(...); })` + `CFRunLoopWakeUp(io_rl)` — so the actual `mmws_sm_send_msg` runs on the I/O thread. (Guard against a torn-down conn.)

- [ ] **Step 3:** Teardown on the I/O thread. `mmwsconn_close`/`_free` signal the I/O thread (perform a block that unschedules+closes+`CFRelease`s the streams and `CFRunLoopStop(io_rl)`), then join the thread and free `c`. Add a generation/`dead` guard so an in-flight main-dispatched delivery (Task 5) referencing a freed conn is dropped safely.

- [ ] **Step 4: Host-compile gate** — build the mmws host test harness (`mmws_sm_test.c` + `mmws_test.c`) and run it; the protocol/state-machine tests must still pass (this task doesn't touch `mmws_sm.c`, only the CFStream transport threading, so they should be unaffected). If the harness only compiles `mmws.c`/`mmws_sm.c` (not `mmwsconn.c`), at minimum ensure `mmwsconn.c` compiles under the iOS SDK via the tweak build (Step in Task 6).

- [ ] **Step 5: Commit** — `git add tweak/mmws/mmwsconn.c && git commit -m "feat(mmws): service the socket on a dedicated I/O thread (off the main runloop)"`

*Note: full validation of the threading is on-device (Task 6 canary) — CFRunLoop threading isn't host-unit-testable. Keep the actor invariant (Global Constraints) as the review lens.*

---

### Task 5: Native — `cb_msg` stamps recvTs + threads it into the bridge

**Files:** Modify `tweak/mmws/Tweak.x` (the `cb_msg` on_message impl + the `__mmwsDispatch` string builder, ~lines 86-103).

- [ ] **Step 1:** In `cb_msg` (fires on the I/O thread after Task 4), capture `recvTs` at frame arrival: `double recvTs = ({ struct timeval tv; gettimeofday(&tv, NULL); (double)tv.tv_sec*1000.0 + tv.tv_usec/1000.0; });` — stamp it FIRST, before building/dispatching the JS string.

- [ ] **Step 2:** Thread `recvTs` into the dispatched JS. Extend the `__mmwsDispatch` string builder (line ~95) for the message case to emit the 4th arg: `...__mmwsDispatch(%d,'message','<escaped-data>',%.0f)` with `recvTs`. Keep the existing escaping of `data`. The open/close/error cases are unchanged (no recvTs).

- [ ] **Step 3:** Confirm the existing `dispatch_async_f(dispatch_get_main_queue(), buf, eval_and_free)` (line ~103) is retained — the receipt time is already captured (Step 1) before this hop, so late main-thread eval doesn't affect correctness. Ensure the generation/`dead` guard from Task 4 Step 3 prevents evaluating against a freed conn.

- [ ] **Step 4: Commit** — `git add tweak/mmws/Tweak.x && git commit -m "feat(mmws): stamp gettimeofday recvTs off-main + pass to __mmwsDispatch"`

---

### Task 6: Build + canary + measure (operator-run)

Run by the operator/controller. Native dylib build + on-device validation.

- [ ] **Step 1: Build** the new mmws dylib via WSL theos (CRLF-stripping build wrapper per [[fleet-tweak-deploy-ssh-doze]]).
- [ ] **Step 2: Canary** — deploy the dylib to ONE screen (Windows OpenSSH, sequential; push dylib then relaunch Web). Watch device `mmws.log`: real ws holds, NO rx-overflow/reconnect-loop regression, and (with beatflag on) `window.__mmwsRecvTs` is populating (add a one-shot `?tdbg`/console check if needed).
- [ ] **Step 3: Measure the canary** — is that screen's per-screen clock contribution tighter? (Its `corr`/`err` in synctool, and cross-check `elapsed`.) Confirm no crash/jetsam over ~15 min.
- [ ] **Step 4: Wave rollout** — only if the canary holds: deploy in paced waves (never a burst), relaunch in waves.
- [ ] **Step 5: Full A/B** — measure fleet clock-disagreement (synctool + `elapsed`-spread, matched elapsed-since-reload, distributions over many windows). Target: below the ~193ms floor toward tens-of-ms. Record in `memory/wall-cross-screen-sync-state.md`.
- [ ] **Step 6: Fallback ready** — if any regression, re-push the prior dylib + relaunch to revert instantly.

## Self-Review

- **Spec coverage:** off-main I/O thread (design §Native) → Task 4; recvTs stamp + bridge (§Native, §recvTs delivery) → Task 5; side-channel (§recvTs delivery past SockJS) → Task 2; GoTime consume + fallback (§JS/GoTime) → Task 1; app wiring → Task 3; time base → Global Constraints + Task 5 Step 1; validation/rollout (§Validation) → Task 6.
- **Placeholder scan:** JS tasks carry full code; native tasks carry concrete CF calls + the invariant (exact locking refined in-impl, validated by canary — appropriate for CFRunLoop threading which isn't unit-testable).
- **Type consistency:** `beatReceived(serverSendMs, recvTs)` (Task 1) matches the caller `GoTime.beatReceived(data_obj.PAYLOAD.t, window.__mmwsRecvTs)` (Task 3); `window.__mmwsRecvTs` set in Task 2 read in Task 3; `__mmwsDispatch(id,type,data,recvTs)` 4th arg emitted by Task 5, consumed by Task 2; `recvTs` epoch-ms (gettimeofday, Task 5) matches `_pickRecv`'s `Date.getTime()` frame (Task 1).
- **Ordering:** JS (1-3) ships safe + graceful before native (4-5); native validated on canary (6). Task 4 (thread) precedes Task 5 (stamp) — stamping is only meaningful once cb_msg fires off-main.
