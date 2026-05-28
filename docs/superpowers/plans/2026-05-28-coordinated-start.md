# Coordinated Start Release — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate group playback start on a readiness handshake (`PREPARE` → `READY`/`NEEDS_ARM` → `GO`) so every display releases the first frame on a shared future clock instant from a pre-buffered frame-0 hold — with the server auto-arming iOS devices via Veency during PREPARE.

**Architecture:** Two-phase start. The server's PLAY (fresh start only) broadcasts `PREPARE` instead of playing immediately; clients buffer/seek/hold frame 0 and report `READY` (or `NEEDS_ARM`); once all online clients are ready (or a timeout), the server releases with a `PLAY` whose `startEpoch` is ~750 ms in the future; clients hold until that instant then play. Resume (pause→play), late-join, and reconnect keep today's direct/catch-up path (`PLAY` with a past epoch). Drift correction is unchanged maintenance.

**Tech Stack:** Python 3 / aiohttp / SockJS (server, `server.py`); ES5 + jQuery 1.x (client, `index.html`, `js/mosiacmesh.js`); pytest (`tests/unit`, run via `python pytest_runner.py --unit`); `vncdotool` `vncdo` CLI for the auto-arm subprocess.

**Spec:** `docs/superpowers/specs/2026-05-28-coordinated-start-design.md`

---

## File Structure

- `server.py` — new constants; `PlayState.PREPARING`; `Display` prepare state; helpers `_begin_prepare` / `_group_online_keys` / `_maybe_release` / `_release_group` / `_auto_arm_client`; `msg_response` handlers for `READY` / `NEEDS_ARM`; PLAY-request routes fresh starts through PREPARE; `process()` timeout release.
- `index.html` — `PREPARE` handler + `prepareFirstItem()` (buffer/hold + READY/NEEDS_ARM + arm-then-hold); `PLAY` handler honors a future `startEpoch` (deferred start).
- `js/mosiacmesh.js` — (no change; uses existing `generateMessage`/`sock` already global).
- `tests/unit/test_coordinated_start.py` — new; server-side handshake + release + auto-arm tests.

---

## Task 1: Constants + `PlayState.PREPARING` + `Display` prepare state

**Files:**
- Modify: `server.py` (constants near other module constants; `PlayState`; `Display.__init__`)
- Test: `tests/unit/test_coordinated_start.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_coordinated_start.py
import sys, time
from pathlib import Path
import argparse
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
_orig = argparse.ArgumentParser.parse_args
argparse.ArgumentParser.parse_args = lambda self, *a, **k: argparse.Namespace(Port=3000, Verbose=False)
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig


def test_prepare_state_and_constants_exist():
    assert hasattr(server.PlayState, "PREPARING")
    assert server.RELEASE_LEAD_MS > 0
    assert server.PREPARE_TIMEOUT_MS > 0
    d = server.Display()
    assert d.prepareId is None
    assert d.readyClients == set()
    assert d.prepareDeadline == 0
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/unit/test_coordinated_start.py::test_prepare_state_and_constants_exist -c tests/pytest.ini -v`
Expected: FAIL (`PlayState` has no `PREPARING` / no `RELEASE_LEAD_MS`).

- [ ] **Step 3: Implement**

In `server.py`, add module constants (near the top, with other config):
```python
RELEASE_LEAD_MS = 750       # ms in the future the GO start epoch is set to
PREPARE_TIMEOUT_MS = 5000   # ms to wait for all READYs before releasing anyway
AUTO_ARM = True             # server fires a Veency tap to arm un-armed iOS devices
VEENCY_PORT = 5900
VEENCY_PASSWORD = "mosaic"
```
Add to the `PlayState` enum:
```python
    PREPARING = 3
```
In `Display.__init__`, alongside `scheduledPlaying`:
```python
        self.prepareId = None
        self.readyClients = set()
        self.prepareDeadline = 0
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_coordinated_start.py -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_coordinated_start.py
git commit -m "feat(sync): PREPARING state + coordinated-start constants/Display fields"
```

---

## Task 2: `_begin_prepare` broadcasts PREPARE; fresh PLAY routes through it

**Files:**
- Modify: `server.py` (`_begin_prepare`, `_group_online_keys` helpers; PLAY request handler ~line 1528)
- Test: `tests/unit/test_coordinated_start.py`

- [ ] **Step 1: Write the failing test**

```python
from unittest.mock import patch

def _display_with_items(server, display_id="g1", n=2):
    server.settings = server.Settings()
    disp = server.Display()
    disp.mediaElements = [server.MediaElement() for _ in range(n)]
    for me in disp.mediaElements:
        me.duration = 1000
    disp.renderedToken = server.compute_render_token(display_id) if hasattr(server, "compute_render_token") else ""
    server.settings.displays[display_id] = disp
    return disp

def test_begin_prepare_broadcasts_prepare_and_sets_state():
    disp = _display_with_items(server)
    with patch.object(server, "broadcast_to_display_group") as bc:
        server._begin_prepare("g1")
    assert disp.action == server.PlayState.PREPARING
    assert disp.prepareId
    assert disp.readyClients == set()
    assert disp.prepareDeadline > 0
    req = bc.call_args[0][1]
    assert req["REQUEST"] == "PREPARE"
    assert req["PAYLOAD"]["prepareId"] == disp.prepareId
    assert len(req["PAYLOAD"]["items"]) == 2
```

- [ ] **Step 2: Run to confirm fail**

Run: `python -m pytest tests/unit/test_coordinated_start.py::test_begin_prepare_broadcasts_prepare_and_sets_state -c tests/pytest.ini -v`
Expected: FAIL (`_begin_prepare` undefined).

- [ ] **Step 3: Implement**

In `server.py` add:
```python
import uuid  # if not already imported

def _group_online_keys(display_id):
    return {k for k, c in settings.clients.items()
            if getattr(c, "displayID", None) == display_id and getattr(c, "isOnline", False)}

def _begin_prepare(display_id):
    """Phase 1: tell the group to buffer + hold frame 0 (don't start the clock)."""
    display = settings.displays.get(display_id)
    if not display or not display.mediaElements:
        return
    display.prepareId = uuid.uuid4().hex
    display.readyClients = set()
    display.prepareDeadline = int(time.time() * 1000) + PREPARE_TIMEOUT_MS
    display.action = PlayState.PREPARING
    items = [_media_item_payload(me) for me in display.mediaElements]
    broadcast_to_display_group(display_id, {
        "REQUEST": "PREPARE",
        "PAYLOAD": {"prepareId": display.prepareId, "items": items, "loop": display.loop}})
```

In the PLAY request handler (the `else:` at ~line 1541 that calls `_start_group_playback(display_id, resume_epoch)`), branch fresh-vs-resume:
```python
            else:
                if display.action == PlayState.PAUSE:
                    _start_group_playback(display_id, resume_epoch)   # resume: direct, today's path
                else:
                    _begin_prepare(display_id)                        # fresh start: coordinated
                response["PAYLOAD"] = "SUCCESS"
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_coordinated_start.py -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_coordinated_start.py
git commit -m "feat(sync): _begin_prepare broadcasts PREPARE; fresh PLAY coordinates"
```

---

## Task 3: `READY` handler + release when all online clients ready

**Files:**
- Modify: `server.py` (`_maybe_release`, `_release_group`; `READY` handler in `msg_response`)
- Test: `tests/unit/test_coordinated_start.py`

- [ ] **Step 1: Write the failing test**

```python
def _online_client(server, key, display_id):
    c = server.Client()
    c.displayID = display_id
    c.isOnline = True
    server.settings.clients[key] = c
    return c

def test_release_when_all_online_ready():
    disp = _display_with_items(server)
    _online_client(server, "a", "g1")
    _online_client(server, "b", "g1")
    with patch.object(server, "broadcast_to_display_group"):
        server._begin_prepare("g1")
    pid = disp.prepareId
    with patch.object(server, "_start_group_playback") as sgp:
        server._maybe_release("g1")                    # not all ready yet
        assert sgp.call_count == 0
        disp.readyClients = {"a", "b"}
        server._maybe_release("g1")                    # now all ready -> release
        assert sgp.call_count == 1
        # released with a FUTURE epoch
        epoch = sgp.call_args[0][1]
        assert epoch > int(time.time() * 1000)
    assert disp.prepareId is None
```

- [ ] **Step 2: Run to confirm fail**

Run: `python -m pytest tests/unit/test_coordinated_start.py::test_release_when_all_online_ready -c tests/pytest.ini -v`
Expected: FAIL (`_maybe_release` undefined).

- [ ] **Step 3: Implement**

```python
def _release_group(display_id):
    """Phase 2: pick a shared near-future start epoch and broadcast the GO."""
    display = settings.displays.get(display_id)
    if not display:
        return
    start_epoch = int(time.time() * 1000) + RELEASE_LEAD_MS
    display.prepareId = None
    display.prepareDeadline = 0
    _start_group_playback(display_id, start_epoch)   # sets playStartEpoch, action=PLAY, broadcasts PLAY

def _maybe_release(display_id):
    display = settings.displays.get(display_id)
    if not display or display.action != PlayState.PREPARING:
        return
    online = _group_online_keys(display_id)
    if online and online.issubset(display.readyClients):
        _release_group(display_id)
```

`READY` handler in `msg_response` (add as a new `elif`, e.g. before `SETPLAYLIST`):
```python
    elif(msg["REQUEST"] == "READY"):
        client = settings.clients.get(msg["SRC"])
        display = settings.displays.get(getattr(client, "displayID", None)) if client else None
        if display and display.action == PlayState.PREPARING \
                and (msg.get("PAYLOAD") or {}).get("prepareId") == display.prepareId:
            display.readyClients.add(msg["SRC"])
            _maybe_release(client.displayID)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_coordinated_start.py -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_coordinated_start.py
git commit -m "feat(sync): READY handler releases group with a future start epoch"
```

---

## Task 4: Timeout release in `process()`

**Files:**
- Modify: `server.py` (`process()` loop)
- Test: `tests/unit/test_coordinated_start.py`

- [ ] **Step 1: Write the failing test**

```python
def test_timeout_release_helper():
    disp = _display_with_items(server)
    _online_client(server, "a", "g1")
    with patch.object(server, "broadcast_to_display_group"):
        server._begin_prepare("g1")
    disp.prepareDeadline = int(time.time() * 1000) - 1   # already past
    with patch.object(server, "_start_group_playback") as sgp:
        server._release_expired_prepares()
        assert sgp.call_count == 1
    assert disp.action != server.PlayState.PREPARING or disp.prepareId is None
```

- [ ] **Step 2: Run to confirm fail**

Run: `python -m pytest tests/unit/test_coordinated_start.py::test_timeout_release_helper -c tests/pytest.ini -v`
Expected: FAIL (`_release_expired_prepares` undefined).

- [ ] **Step 3: Implement**

Add the helper, and call it from `process()` (the existing ~5 s loop):
```python
def _release_expired_prepares():
    now = int(time.time() * 1000)
    for display_id, display in list(settings.displays.items()):
        if display.action == PlayState.PREPARING and display.prepareDeadline and now > display.prepareDeadline:
            logging.warning("PREPARE timeout for %s; releasing without %s",
                            display_id, _group_online_keys(display_id) - display.readyClients)
            _release_group(display_id)
```
In `async def process():`, add a call each tick (near the existing stale-client / schedule work):
```python
    _release_expired_prepares()
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_coordinated_start.py -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_coordinated_start.py
git commit -m "feat(sync): release PREPARING groups on timeout so a laggard can't freeze the wall"
```

---

## Task 5: `NEEDS_ARM` handler + `_auto_arm_client` (Veency VNC tap)

**Files:**
- Modify: `server.py` (`_auto_arm_client`; `NEEDS_ARM` handler)
- Test: `tests/unit/test_coordinated_start.py`

- [ ] **Step 1: Write the failing test**

```python
import asyncio

def test_auto_arm_invokes_vncdo_with_center_coords():
    server.settings = server.Settings()
    c = server.Client()
    c.displayID = "g1"; c.isOnline = True; c.ip = "192.168.1.50"
    c.deviceWidth = 1024; c.deviceHeight = 768
    server.settings.clients["a"] = c

    called = {}
    async def fake_exec(*args, **kwargs):
        called["args"] = args
        class P:
            async def wait(self): return 0
        return P()

    server.AUTO_ARM = True
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        asyncio.get_event_loop().run_until_complete(server._auto_arm_client("a"))
    assert "vncdo" in called["args"][0]
    assert "192.168.1.50::5900" in called["args"]
    assert "512" in called["args"] and "384" in called["args"]   # center of 1024x768
```

- [ ] **Step 2: Run to confirm fail**

Run: `python -m pytest tests/unit/test_coordinated_start.py::test_auto_arm_invokes_vncdo_with_center_coords -c tests/pytest.ini -v`
Expected: FAIL (`_auto_arm_client` undefined).

- [ ] **Step 3: Implement**

```python
async def _auto_arm_client(client_key):
    """Deliver one Veency VNC tap (screen centre) to arm an un-armed iOS device.
    Best-effort: missing vncdo / no IP / failure just logs — the PREPARE timeout
    covers a device that can't be armed."""
    if not AUTO_ARM:
        return
    client = settings.clients.get(client_key)
    if not client or not getattr(client, "ip", ""):
        return
    cx = int((getattr(client, "deviceWidth", 0) or 1024) / 2)
    cy = int((getattr(client, "deviceHeight", 0) or 768) / 2)
    target = f"{client.ip}::{VEENCY_PORT}"
    try:
        proc = await asyncio.create_subprocess_exec(
            "vncdo", "-s", target, "-p", VEENCY_PASSWORD,
            "move", str(cx), str(cy), "click", "1",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), timeout=10)
        logging.info("auto-arm: tapped %s at %d,%d", client_key, cx, cy)
    except Exception as e:  # noqa: BLE001
        logging.warning("auto-arm tap failed for %s: %s", client_key, e)
```

`NEEDS_ARM` handler in `msg_response`:
```python
    elif(msg["REQUEST"] == "NEEDS_ARM"):
        client = settings.clients.get(msg["SRC"])
        display = settings.displays.get(getattr(client, "displayID", None)) if client else None
        if display and display.action == PlayState.PREPARING \
                and (msg.get("PAYLOAD") or {}).get("prepareId") == display.prepareId:
            asyncio.ensure_future(_auto_arm_client(msg["SRC"]))
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_coordinated_start.py -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_coordinated_start.py
git commit -m "feat(sync): NEEDS_ARM triggers best-effort Veency auto-arm tap"
```

---

## Task 6: Full server unit suite green (regression gate)

**Files:** none (verification only)

- [ ] **Step 1: Run the whole unit suite**

Run: `python pytest_runner.py --unit`
Expected: all pass (prior 239 + the new coordinated-start tests), 2 skipped. Fix any handler-ordering / import issues the new `elif` branches introduced.

- [ ] **Step 2: Commit (only if fixes were needed)**

```bash
git add server.py
git commit -m "fix(sync): resolve regressions surfaced by full unit suite"
```

---

## Task 7: Client `PREPARE` handler — buffer + hold frame 0, READY/NEEDS_ARM, arm-then-hold

**Files:**
- Modify: `index.html` (message dispatch in `mosiacMeshCallback`; add `prepareFirstItem`)

> Client ES5 JS isn't pytest-tested; verify with `node --check` (syntax) + on-device.

- [ ] **Step 1: Add a `sendMsg` helper if not present, and the PREPARE handler**

In `index.html`, near the other senders, add (ES5):
```javascript
	function sendMsg(request, payload) {
		try { sock.send(generateMessage("SRV", request, payload)); } catch (e) {}
	}
```
In `mosiacMeshCallback`, add a branch (before `PLAY`):
```javascript
			else if (data_obj.REQUEST == "PREPARE") {
				blink = false;
				$('html').css({ width: '', height: '', border: '', position: '', 'z-index': '', overflow: '' });
				$('html, body').css('background-color', '');
				hideBlackout();
				playback.items = data_obj.PAYLOAD.items || [];
				playback.loop = !!data_obj.PAYLOAD.loop;
				playback.active = false;      // clock not started yet
				playback.paused = false;
				prepareFirstItem(data_obj.PAYLOAD.prepareId);
			}
```

- [ ] **Step 2: Implement `prepareFirstItem` (buffer/hold + READY/NEEDS_ARM + arm-then-hold)**

```javascript
	// Buffer the first item and HOLD frame 0 (paused). Report READY when buffered
	// (and armed, for gesture devices); a gesture device not yet armed reports
	// NEEDS_ARM and, when the arming touch lands, "arm-then-hold": let play() fire
	// to consume the gesture, then pause back to 0 and send READY.
	function prepareFirstItem(prepareId) {
		clearScript();
		var item = playback.items[0];
		if (!item) { sendMsg("READY", { prepareId: prepareId }); return; }
		if (item.playmode === 'SCRIPT' || !isVideoItem(item.file)) {
			sendMsg("READY", { prepareId: prepareId });   // no buffering/gesture needed
			return;
		}
		var v = getPersistentVideo();
		v.style.display = 'block'; v.style.opacity = '1'; v.loop = false; v.muted = false;
		$('#canvas').empty();
		playback.video = v; playback.videoIndex = 0; playback.videoSrc = item.file;
		var holdAtZero = function () {
			try { v.pause(); } catch (e) {}
			try { v.currentTime = 0; } catch (e) {}
		};
		var reportReady = function () { sendMsg("READY", { prepareId: prepareId }); };
		var onArmed = function () {           // 'playing' fired => gesture consumed
			playback.activated = true; hideTapStart();
			holdAtZero(); reportReady();
		};
		var afterLoad = function () {
			v.removeEventListener('loadedmetadata', afterLoad);
			holdAtZero();
			if (playback.activated) {
				reportReady();                // already blessed: buffered + held
			} else {
				// Need a gesture: ask the server to arm us, and arm-then-hold when it lands.
				sendMsg("NEEDS_ARM", { prepareId: prepareId });
				v.addEventListener('playing', function once() { v.removeEventListener('playing', once); onArmed(); });
				showTapStart();               // manual tap also works; activatePlayback() calls play()
			}
		};
		if (v.getAttribute('src') !== item.file) {
			v.src = item.file; v.addEventListener('loadedmetadata', afterLoad); try { v.load(); } catch (e) {}
		} else if (v.readyState >= 1) {
			afterLoad();
		} else {
			v.addEventListener('loadedmetadata', afterLoad);
		}
	}
```

- [ ] **Step 3: Syntax-check**

Run:
```bash
s=$(grep -n "^  <script>$" index.html | head -1 | cut -d: -f1); e=$(grep -n "^  </script>$" index.html | head -1 | cut -d: -f1); sed -n "$((s+1)),$((e-1))p" index.html > /tmp/mm.js; node --check /tmp/mm.js && echo OK; rm -f /tmp/mm.js
```
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add index.html
git commit -m "feat(client): PREPARE handler — buffer + hold frame 0, READY/NEEDS_ARM, arm-then-hold"
```

---

## Task 8: Client `PLAY` honors a future `startEpoch` (deferred release)

**Files:**
- Modify: `index.html` (`PLAY` handler)

- [ ] **Step 1: Implement deferred start**

In the existing `PLAY` handler, after setting `playback.items/startEpoch/loop/paused` and `playback.active = true`, replace the bare `renderPlayback();` with:
```javascript
				var _delay = playback.startEpoch - GoTime.now();
				if (_delay > 0) {
					// Coordinated GO: we're already holding frame 0 from PREPARE;
					// release exactly at the shared start epoch.
					if (playback.startTimer) { clearTimeout(playback.startTimer); }
					playback.startTimer = setTimeout(function () { renderPlayback(); }, _delay);
				} else {
					renderPlayback();   // past epoch: late-join / resume / reconnect — start now
				}
```
Add `startTimer: null` to the `playback` object initializer. In `stopPlayback`/`pausePlayback`, also clear it: `if (playback.startTimer) { clearTimeout(playback.startTimer); playback.startTimer = null; }`.

- [ ] **Step 2: Syntax-check**

Run:
```bash
s=$(grep -n "^  <script>$" index.html | head -1 | cut -d: -f1); e=$(grep -n "^  </script>$" index.html | head -1 | cut -d: -f1); sed -n "$((s+1)),$((e-1))p" index.html > /tmp/mm.js; node --check /tmp/mm.js && echo OK; rm -f /tmp/mm.js
```
Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "feat(client): PLAY honors a future startEpoch (hold then release)"
```

---

## Task 9: On-device verification (manual)

**Files:** none

- [ ] **Step 1: Restart the server** (`python server.py -p 3000`) and reload both clients (Windows + iPad) with `?tdbg`.
- [ ] **Step 2: Start playback** of `[bouncingBalls, big buck bunny]` from admin. Expected: brief PREPARE pause, then both screens release together; the iPad auto-arms (no manual tap) via the Veency tap; `?tdbg` shows `err` ≈ 0 on both at start (not -467), `el`/`off`/`ct` climbing together.
- [ ] **Step 3: Pause→Play.** Expected: resumes in place (direct path), no restart-from-0.
- [ ] **Step 4: Reload one client mid-play (reconnect).** Expected: late-joiner jumps to the current offset via the catch-up path; the other client is undisturbed (no re-PREPARE).
- [ ] **Step 5: Kill `vncdo` / set `AUTO_ARM = False`, start playback.** Expected: iPad shows tap-to-start; manual tap arms it; the group still releases (or the ~5 s timeout releases the rest). Confirms graceful degradation.

---

## Self-Review

- **Spec coverage:** PREPARE/READY/NEEDS_ARM/GO (Tasks 2,3,5,7,8); release-all-ready (3); timeout release (4); auto-arm (5); arm-then-hold + deferred start (7,8); resume/late-join unchanged (2 routes PAUSE→direct; `sync_new_client_to_group` untouched → past-epoch PLAY). ✓
- **Type/name consistency:** `prepareId`, `readyClients`, `prepareDeadline`, `RELEASE_LEAD_MS`, `PREPARE_TIMEOUT_MS`, `AUTO_ARM`, `VEENCY_PORT`, `VEENCY_PASSWORD`, `_begin_prepare`/`_maybe_release`/`_release_group`/`_release_expired_prepares`/`_auto_arm_client`/`_group_online_keys` used consistently across tasks. Client `prepareFirstItem`, `sendMsg`, `playback.startTimer`. ✓
- **Placeholders:** none — every step has concrete code/commands. ✓
- **Risk note:** the future-epoch `PLAY` is the GO *and* the late-join/resume signal — disambiguated purely by epoch (future = hold, past = now). `sync_new_client_to_group` already sends the existing (past) epoch, so it needs no change. Verify Task 8's deferred-start branch doesn't fire for those (it won't: `_delay <= 0`). ✓
