# Master-clock broadcast beat — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A server-broadcast `TIMEBEAT` that all display clients phase-lock to via a naive common-mode offset (fed into GoTime's existing steer), collapsing cross-screen clock-disagreement for both video and animation. Ships behind a `js/beatflag.js` A/B toggle with a `/time` failsafe.

**Architecture:** Server emits `{REQUEST:"TIMEBEAT", PAYLOAD:{t:serverMs}}` fleet-wide every 2s (stamped at send). Client routes it to `GoTime.beatReceived(serverMs)`, which builds an offset sample `serverMs - localRecv` (no per-screen ToF) and feeds `_reviseOffset` — reusing the robust target + smooth slew unchanged. While fresh beats arrive, the client suppresses its independent `/time` polls (which carry independent asymmetry error); if beats stop for `BEAT_STALE_MS`, `/time` resumes as a failsafe.

**Tech Stack:** Python 3 / aiohttp / sockjs (server); ES5 JavaScript + node 20 `node:test` (client).

## Global Constraints

- **iPad-1 ES5 only** for `js/GoTime.js`, `js/mosiacmesh.js`, `js/beatflag.js`: `var` + `function`, no `let`/`const`/arrow/template-literal/`class`/`Promise`/`fetch`. Match each file's existing style.
- **Pure helpers take primitives, not globals.** `window._mmClockBeat` is read only at the wiring layer and passed in; pure helpers (`_beatSample`, `_beatSuppressesPoll`) never touch `window`/timers, so they stay node-testable (follow the existing `gotime-*.test.js` pattern).
- **The beat must NOT compute per-screen time-of-flight.** The offset is the naive `serverSendMs - localRecv`; transit is left as a common-mode lag. This is the whole point — do not "helpfully" add an RTT/2 correction.
- **`server_ms` is stamped immediately before the broadcast**, once, fleet-wide (a single stamp + single fan-out = every screen aligns to the identical number).
- **Server import side-effect free** (CLAUDE.md): the beat task is launched only under `if __name__ == '__main__'`, never at import.
- Constants: `BEAT_INTERVAL_S = 2` (server), `BEAT_PRECISION_MS = 12`, `BEAT_STALE_MS = 6000` (client).
- Tests: `python pytest_runner.py --unit` (server), `node --test tests/unit/js/<file>` / `python pytest_runner.py --js` (client).

---

### Task 1: Server — `broadcast_all` helper + `_time_beat` task

**Files:**
- Modify: `mosaicmesh/broadcast.py` (add `broadcast_all`)
- Modify: `server.py` (import `broadcast_all`; add `BEAT_INTERVAL_S` + `_time_beat`; launch it under `__main__`)
- Test: `tests/unit/test_time_beat.py` (create)

**Interfaces:**
- Produces: `broadcast_all(response_dict)` — encodes the dict ONCE and calls `socketmanager.broadcast` once (no per-client DEST substitution). `_time_beat()` — async loop broadcasting `{REQUEST:"TIMEBEAT", PAYLOAD:{t:<int ms>}}` every `BEAT_INTERVAL_S`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_time_beat.py`:
```python
import json
import server
from mosaicmesh.broadcast import broadcast_all


class _MockManager:
    def __init__(self):
        self.broadcasts = []
    def broadcast(self, encoded):
        self.broadcasts.append(encoded)


def test_broadcast_all_encodes_once_and_broadcasts(monkeypatch):
    mgr = _MockManager()
    monkeypatch.setattr(server, "socketmanager", mgr, raising=False)
    broadcast_all({"REQUEST": "TIMEBEAT", "PAYLOAD": {"t": 1234567890}})
    assert len(mgr.broadcasts) == 1
    decoded = json.loads(mgr.broadcasts[0])
    assert decoded["REQUEST"] == "TIMEBEAT"
    assert decoded["PAYLOAD"]["t"] == 1234567890


def test_broadcast_all_noop_without_manager(monkeypatch):
    monkeypatch.setattr(server, "socketmanager", None, raising=False)
    broadcast_all({"REQUEST": "TIMEBEAT", "PAYLOAD": {"t": 1}})  # must not raise
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_time_beat.py -c tests/pytest.ini -v`
Expected: FAIL — `ImportError: cannot import name 'broadcast_all'`.

- [ ] **Step 3: Add `broadcast_all` to `mosaicmesh/broadcast.py`**

```python
def broadcast_all(response_dict):
    """Fleet-wide broadcast: encode ONCE and hand the same bytes to every
    session via socketmanager.broadcast. Used by the TIMEBEAT clock beat,
    where the payload is identical for every client (no per-client DEST) —
    a single serialization + fan-out is both cheapest and maximally
    common-mode (every screen aligns to the identical bytes)."""
    import server
    import jsonpickle
    if server.socketmanager is None:
        return
    response_dict["DEST"] = "ALL"
    encoded = jsonpickle.encode(response_dict)
    server.socketmanager.broadcast(encoded)
```

- [ ] **Step 4: Run to verify the test passes**

Run: `python -m pytest tests/unit/test_time_beat.py -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Add `BEAT_INTERVAL_S` + `_time_beat` to `server.py`, import `broadcast_all`, launch under `__main__`**

Add `broadcast_all` to the existing `from mosaicmesh.broadcast import (...)` block (near line 49, alongside `broadcast_to_client`).

Add near the other module constants:
```python
BEAT_INTERVAL_S = 2   # master-clock TIMEBEAT cadence (see master-clock-beat design)
```

Add the coroutine (near `process`):
```python
async def _time_beat():
    """Broadcast a server-time beat every BEAT_INTERVAL_S. server_ms is stamped
    immediately before the fan-out (never staged) so server-side scheduling
    can't skew it; clients phase-lock to it via a naive common-mode offset."""
    while True:
        try:
            server_ms = int(time.time() * 1000)
            broadcast_all({"REQUEST": "TIMEBEAT", "PAYLOAD": {"t": server_ms}})
        except Exception:
            logging.exception("time beat failed")
        await asyncio.sleep(BEAT_INTERVAL_S)
```

Launch it once, right before the `while True: await process()` loop (inside `if __name__ == '__main__'`, after `socketmanager` is set up):
```python
                asyncio.ensure_future(_time_beat())
```

- [ ] **Step 6: Verify import stays side-effect-free + unit suite green**

Run: `python -c "import server"` (must not start a beat / bind a port)
Run: `python pytest_runner.py --unit`
Expected: import clean; unit suite green (no regressions).

- [ ] **Step 7: Commit**

```bash
git add mosaicmesh/broadcast.py server.py tests/unit/test_time_beat.py
git commit -m "feat(clock): server TIMEBEAT broadcast beat (fleet-wide, stamped at send)"
```

---

### Task 2: Client GoTime — `beatReceived` + naive sample + `/time` suppression

**Files:**
- Modify: `js/GoTime.js` (add `_beatSample`, `_beatSuppressesPoll`, `beatReceived`, `_lastBeatMs` state, gate in `_scheduleAdaptiveSync`, constants + exports)
- Test: `tests/unit/js/gotime-beat.test.js` (create)

**Interfaces:**
- Consumes: `_reviseOffset(sample, method)`, `getAccurateTimestamp()`, `options`, `_scheduleAdaptiveSync`.
- Produces: `GoTime.beatReceived(serverSendMs)`; pure `GoTime._beatSample(serverSendMs, localRecv, precisionMs) -> {offset, precision}`; pure `GoTime._beatSuppressesPoll(enabled, lastBeatMs, nowMs, staleMs) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/js/gotime-beat.test.js`:
```javascript
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/GoTime.js');
const G = globalThis.GoTime;

test('_beatSample: naive offset = serverSendMs - localRecv, fixed precision', () => {
  const s = G._beatSample(1000000, 999880, 12);
  assert.equal(s.offset, 120);       // 1000000 - 999880, NO ToF correction
  assert.equal(s.precision, 12);
});

test('_beatSuppressesPoll: fresh beat + enabled -> suppress /time', () => {
  assert.equal(G._beatSuppressesPoll(true, 10000, 12000, 6000), true);   // age 2000 < 6000
});
test('_beatSuppressesPoll: stale beat -> poll (failsafe)', () => {
  assert.equal(G._beatSuppressesPoll(true, 10000, 20000, 6000), false);  // age 10000 > 6000
});
test('_beatSuppressesPoll: disabled -> never suppress', () => {
  assert.equal(G._beatSuppressesPoll(false, 10000, 12000, 6000), false);
});
test('_beatSuppressesPoll: no beat yet (null) -> poll', () => {
  assert.equal(G._beatSuppressesPoll(true, null, 12000, 6000), false);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/gotime-beat.test.js`
Expected: FAIL — `_beatSample`/`_beatSuppressesPoll` undefined.

- [ ] **Step 3: Implement in `js/GoTime.js`**

Add the two pure helpers (near `_calculateOffset`):
```javascript
    // Naive one-way offset from a broadcast beat: NO per-screen ToF correction
    // (that asymmetry error is exactly what we're eliminating). Transit stays a
    // common-mode lag. precision is a fixed nominal (broadcast transit is low +
    // common-mode), since a one-way beat has no RTT to measure.
    _beatSample = function (serverSendMs, localRecv, precisionMs) {
        return { offset: serverSendMs - localRecv, precision: precisionMs };
    };
    // Should the /time poll be suppressed this cycle? Yes only when beat mode is
    // enabled AND a beat arrived within staleMs (else fall back to /time).
    _beatSuppressesPoll = function (enabled, lastBeatMs, nowMs, staleMs) {
        if (!enabled || lastBeatMs === null || lastBeatMs === undefined) { return false; }
        return (nowMs - lastBeatMs) < staleMs;
    };
```

Add state defaults (in the `options` object, near `_offset`): `_lastBeatMs: null`. Add constants near the other option defaults: `_beatPrecisionMs: 12, _beatStaleMs: 6000`.

Add the public method (in the returned object, near `wsReceived`):
```javascript
        beatReceived: function (serverSendMs) {
            var localRecv = getAccurateTimestamp();   // SAME clock fn now() uses
            options._lastBeatMs = localRecv;
            var sample = _beatSample(serverSendMs, localRecv, options._beatPrecisionMs);
            return _reviseOffset(sample, "beat");
        },
```

Gate the poll in `_scheduleAdaptiveSync` (after computing the next delay, before arming the timer): if `_beatSuppressesPoll(!!(typeof window !== 'undefined' && window._mmClockBeat), options._lastBeatMs, getAccurateTimestamp(), options._beatStaleMs)` is true, arm the next check at `options._beatStaleMs` instead of the `/time` delay (so it re-checks for beat staleness without polling `/time`). Otherwise keep the existing behavior.

Export the pure helpers (in the returned object): `_beatSample: _beatSample, _beatSuppressesPoll: _beatSuppressesPoll`.

- [ ] **Step 4: Run to verify pass**

Run: `node --test tests/unit/js/gotime-beat.test.js`
Expected: all PASS.

- [ ] **Step 5: Run the full GoTime + JS suite (no regressions)**

Run: `node --test tests/unit/js/gotime-*.test.js`
Run: `python pytest_runner.py --js`
Expected: all green (the scheduler gate is inert when `window._mmClockBeat` is falsy — node has no `window`, so `_beatSuppressesPoll` returns false and cadence is unchanged).

- [ ] **Step 6: Commit**

```bash
git add js/GoTime.js tests/unit/js/gotime-beat.test.js
git commit -m "feat(clock): GoTime.beatReceived naive-offset sample + /time suppression"
```

---

### Task 3: Client wiring — `TIMEBEAT` handler + `beatflag.js` + index.html

**Files:**
- Create: `js/beatflag.js`
- Modify: `js/mosiacmesh.js` (route `TIMEBEAT` → `GoTime.beatReceived`)
- Modify: `index.html` (load `beatflag.js` before `GoTime.js`)
- Test: `tests/unit/js/beatflag-smoke.test.js` (create — module-load smoke)

**Interfaces:**
- Consumes: `GoTime.beatReceived` (Task 2); the `onmessage` dispatch in `js/mosiacmesh.js`.

- [ ] **Step 1: Create `js/beatflag.js`** (mirrors `js/tdbgflag.js` — default OFF for A/B):

```javascript
/* js/beatflag.js — A/B toggle for the master-clock TIMEBEAT (see
 * docs/superpowers/specs/2026-08-01-master-clock-beat-design.md).
 * false: pure /time round-trip clock (legacy, byte-identical). true: phase-lock
 * to the server broadcast beat. Set true, save, RELOAD the fleet to enable.
 * The server emits TIMEBEAT unconditionally; only flagged clients consume it. */
window._mmClockBeat = false;
```

- [ ] **Step 2: Load it in `index.html`** — add before `GoTime.js` (so the flag exists when GoTime initializes), next to the `tdbgflag.js` tag:

```html
	<script src="/js/beatflag.js"></script>
```

- [ ] **Step 3: Route `TIMEBEAT` in `js/mosiacmesh.js`** — in the `s.onmessage` dispatch, beside the `SERVERTIME` branch:

```javascript
				if(data_obj.REQUEST == "TIMEBEAT")
				{
					if (typeof GoTime !== 'undefined' && GoTime.beatReceived && window._mmClockBeat) {
						GoTime.beatReceived(data_obj.PAYLOAD.t);
					}
				}
```

- [ ] **Step 4: Write a module-load smoke test**

Create `tests/unit/js/beatflag-smoke.test.js`:
```javascript
import { test } from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';

test('beatflag.js sets window._mmClockBeat and defaults false', () => {
  const src = readFileSync(new URL('../../../js/beatflag.js', import.meta.url), 'utf8');
  assert.match(src, /window\._mmClockBeat\s*=\s*false/);
});
test('index.html loads beatflag.js before GoTime.js', () => {
  const html = readFileSync(new URL('../../../index.html', import.meta.url), 'utf8');
  const bf = html.indexOf('beatflag.js');
  const gt = html.indexOf('GoTime.js');
  assert.ok(bf !== -1 && gt !== -1 && bf < gt, 'beatflag.js must load before GoTime.js');
});
```

- [ ] **Step 5: Run the smoke + full JS suite**

Run: `node --test tests/unit/js/beatflag-smoke.test.js`
Run: `python pytest_runner.py --js`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add js/beatflag.js js/mosiacmesh.js index.html tests/unit/js/beatflag-smoke.test.js
git commit -m "feat(clock): wire TIMEBEAT -> GoTime.beatReceived behind beatflag A/B toggle"
```

---

### Task 4: On-wall A/B deploy + measurement (operator-run)

Run by the operator/controller, not a subagent. JS is served directly; the server change needs a restart.

- [ ] **Step 1: Deploy server** — restart `server.py` so `_time_beat` runs (verify: `grep TIMEBEAT mm_server.err` shows beats broadcasting, and access log shows fan-out).
- [ ] **Step 2: Deploy client, flag OFF** — reload the OEB webclips (paced waves, never a mass `killall Web` burst per [[fleet-ssh-no-burst]]); confirm byte-identical behavior (beat ignored; clock still via `/time`).
- [ ] **Step 3: Baseline measure** — PullTest3 on OEB Sign 1, ~15 min settled; record synctool `clock-disagreement` + `video-offset`, and cross-screen `elapsed`-spread. Also run an animation playlist (`All Effects Demo`) and record `elapsed`-spread.
- [ ] **Step 4: Flip flag ON** — set `js/beatflag.js` `window._mmClockBeat = true`, reload the fleet.
- [ ] **Step 5: Beat measure** — repeat Step 3 with the beat on.
- [ ] **Step 6: Compare + record** in `memory/wall-cross-screen-sync-state.md`. Expected: clock-disagreement drops from ~400ms toward tens of ms; video-offset + Lever-A seek behavior unchanged; animation `elapsed`-spread tightens. Note per-screen transport (XHR-fallback screens benefit less — [[ios5-websocket-transport]]).

**Success criteria:** clock-disagreement < ~100ms (ideally tens of ms) with the beat on; video-offset unchanged; animation visibly tighter.

## Self-Review

- **Spec coverage:** server emitter (design §Architecture/Server) → Task 1; client sample + `/time` suppression (§Architecture/Client + §prefer-beat) → Task 2; TIMEBEAT routing + flag (§A/B toggle) → Task 3; measurement/rollout (§Measurement) → Task 4. Naive-offset / no-ToF constraint → Global Constraints + Task 2 helper. Failsafe (§client) → `_beatSuppressesPoll` stale branch (Task 2).
- **Placeholder scan:** none — every code step carries full code; Task 4 is a concrete operator runbook.
- **Type consistency:** `_beatSample`/`_beatSuppressesPoll` signatures identical across the test (Task 2 Step 1) and impl (Step 3) and exports; `beatReceived(serverSendMs)` matches the Task 3 caller `GoTime.beatReceived(data_obj.PAYLOAD.t)`; `PAYLOAD.t` matches the server payload `{t: server_ms}` (Task 1).
- **Refinement noted:** the design said "suppress `/time` during `playback.active`"; the plan gates on **beat-freshness** instead (`_beatSuppressesPoll`), which is a strictly better implementation of the same "beat primary, `/time` failsafe" intent — it needs no playback coupling and folds the failsafe into one predicate. Within design intent.
