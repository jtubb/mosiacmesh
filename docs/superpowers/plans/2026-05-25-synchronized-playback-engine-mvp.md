# Synchronized Playback Engine MVP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Play a per-group, identical-mode, image-only playlist that every display in the group renders frame-synchronized, with manual PLAY/STOP + loop.

**Architecture:** The server stores the playlist on the group's `Display` and, on PLAY, broadcasts a single message with a `startEpoch` (server-time ms) + item durations + loop. Each client computes the current item itself from the shared GoTime clock and schedules the exact next transition (recomputed from the clock each time, so it self-corrects). No per-frame server messages.

**Tech Stack:** Python 3 / aiohttp / sockjs 0.13 (server), vanilla ES5 + jQuery 1.x + SockJS client + GoTime (display client), pytest (server tests), Playwright (client verification).

**Spec:** `docs/superpowers/specs/2026-05-25-synchronized-playback-engine-mvp-design.md`

**Conventions:** Run the server with `python server.py -p 3000 -v`. Run tests with `python -m pytest tests/unit -c tests/pytest.ini -q`. Client JS must stay ES5 (1st-gen iPad). Commit messages end with the `Co-Authored-By` trailer used on this branch.

---

## Task 1: `playlist_index` helper (the synchronization math)

**Files:**
- Create: `tests/unit/test_playback.py`
- Modify: `server.py` (add function after `cleanup_old_clients`)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_playback.py`:

```python
"""Unit tests for synchronized playback (playlist_index math + WS handlers)."""
import sys
from pathlib import Path
import argparse

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Import server cleanly (arg parsing is under __main__, so no patch needed)
import server


class TestPlaylistIndex:
    def test_empty_playlist_returns_none(self):
        assert server.playlist_index(0, [], False) is None

    def test_zero_total_duration_returns_none(self):
        assert server.playlist_index(100, [0, 0], False) is None

    def test_first_item(self):
        assert server.playlist_index(0, [1000, 2000], False) == {"index": 0, "offsetMs": 0}

    def test_within_second_item(self):
        # 1000ms into a [1000, 2000] playlist -> start of item 1
        assert server.playlist_index(1000, [1000, 2000], False) == {"index": 1, "offsetMs": 0}
        assert server.playlist_index(2500, [1000, 2000], False) == {"index": 1, "offsetMs": 1500}

    def test_non_loop_past_end_returns_none(self):
        assert server.playlist_index(3000, [1000, 2000], False) is None

    def test_loop_wraps(self):
        # total 3000; 3000 wraps to 0
        assert server.playlist_index(3000, [1000, 2000], True) == {"index": 0, "offsetMs": 0}
        assert server.playlist_index(4200, [1000, 2000], True) == {"index": 1, "offsetMs": 200}

    def test_negative_elapsed_clamps_to_start(self):
        assert server.playlist_index(-50, [1000, 2000], False) == {"index": 0, "offsetMs": 0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_playback.py -c tests/pytest.ini -q`
Expected: FAIL — `AttributeError: module 'server' has no attribute 'playlist_index'`

- [ ] **Step 3: Implement `playlist_index` in `server.py`**

Add immediately after the `cleanup_old_clients` function:

```python
def playlist_index(elapsed_ms, durations, loop):
    """Given elapsed playback time and per-item durations (ms), return the
    current {'index', 'offsetMs'} or None when the playlist is empty/ended.

    This is the synchronization core: clients call the JS mirror of this with
    elapsed = GoTime.now() - startEpoch, so every display lands on the same
    item at the same instant.
    """
    total = 0
    for d in durations:
        total += d
    if total <= 0:
        return None
    if loop:
        elapsed_ms = elapsed_ms % total
    elif elapsed_ms >= total:
        return None
    if elapsed_ms < 0:
        elapsed_ms = 0
    cum = 0
    for i in range(len(durations)):
        if elapsed_ms < cum + durations[i]:
            return {"index": i, "offsetMs": elapsed_ms - cum}
        cum += durations[i]
    return {"index": len(durations) - 1, "offsetMs": durations[-1]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_playback.py -c tests/pytest.ini -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_playback.py server.py
git commit -m "feat(playback): add clock-derived playlist_index helper

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `Display.playStartEpoch` field

**Files:**
- Modify: `server.py` (`Display.__init__`)

- [ ] **Step 1: Add the field**

In `server.py`, in `class Display`'s `__init__`, add `self.playStartEpoch = 0` after `self.action = PlayState.NOACTION`:

```python
class Display():
    def __init__(self):
        self.boundingBox = None
        self.boundingBoxCenter = None
        self.mediaElements = []
        self.loop = False
        self.currentFrame = 0
        self.action = PlayState.NOACTION
        self.playStartEpoch = 0   # server-time ms when playback last (re)started
```

- [ ] **Step 2: Verify the module still imports**

Run: `python -c "import server; print(server.Display().playStartEpoch)"`
Expected: prints `0`

- [ ] **Step 3: Commit**

```bash
git add server.py
git commit -m "feat(playback): add Display.playStartEpoch field

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `SETPLAYLIST` message handler

**Files:**
- Modify: `server.py` (`msg_response`, add branch before the final `else`)
- Test: `tests/unit/test_playback.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_playback.py`:

```python
import pytest
from unittest.mock import MagicMock


def _make_session(session_id="sess1"):
    s = MagicMock()
    s.id = session_id
    s.request = MagicMock()
    s.request.remote = "127.0.0.1"
    s.request.headers = {"User-Agent": "Test Browser"}
    return s


class TestSetPlaylist:
    def test_setplaylist_stores_items_and_broadcasts_preload(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        # a client already assigned to the target group so the group broadcast fires
        client = server.Client()
        client.displayID = "Default"
        mock_settings.clients["c1"] = client

        msg = {
            "SRC": "admin", "DEST": "SRV", "REQUEST": "SETPLAYLIST",
            "PAYLOAD": {
                "displayID": "Default",
                "loop": True,
                "items": [
                    {"id": "a", "file": "/media/server/a.jpg", "duration": 1000},
                    {"id": "b", "file": "/media/server/b.jpg", "duration": 2000},
                ],
            },
        }
        server.msg_response(msg, _make_session())

        disp = mock_settings.displays["Default"]
        assert disp.loop is True
        assert len(disp.mediaElements) == 2
        assert disp.mediaElements[0].file == "/media/server/a.jpg"
        assert disp.mediaElements[1].duration == 2000
        # PRELOAD fanned out to the group (one broadcast per in-group client)
        assert server.socketmanager.broadcast.call_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_playback.py::TestSetPlaylist -c tests/pytest.ini -q`
Expected: FAIL — playlist not stored / broadcast not called (the `SETPLAYLIST` request falls through to the echo `else`).

- [ ] **Step 3: Implement the handler**

In `server.py`, in `msg_response`, add this branch immediately before the final `else:` (the echo branch):

```python
    elif(msg["REQUEST"] == "SETPLAYLIST"):
        payload = msg["PAYLOAD"]
        display_id = payload["displayID"]
        display = settings.displays.setdefault(display_id, Display())
        display.mediaElements = []
        for item in payload.get("items", []):
            me = MediaElement()
            me.id = item.get("id")
            me.file = item.get("file")
            me.duration = item.get("duration")
            me.playmode = PlayMode.FULL  # MVP: identical full-screen
            display.mediaElements.append(me)
        display.loop = bool(payload.get("loop", False))
        # Tell the group's clients to cache the media (drives the green heartbeat)
        broadcast_to_display_group(display_id, {
            "REQUEST": "PRELOAD",
            "PAYLOAD": {"items": payload.get("items", [])}
        })
        response["PAYLOAD"] = "SUCCESS"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_playback.py::TestSetPlaylist -c tests/pytest.ini -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_playback.py
git commit -m "feat(playback): add SETPLAYLIST handler (store + preload)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `PLAY` and `STOP` message handlers

**Files:**
- Modify: `server.py` (`msg_response`, two more branches before the final `else`)
- Test: `tests/unit/test_playback.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_playback.py`:

```python
from unittest.mock import patch


class TestPlayStop:
    def _group_with_items(self, mock_settings):
        disp = mock_settings.displays["Default"]
        disp.mediaElements = []
        for f, d in [("/media/server/a.jpg", 1000), ("/media/server/b.jpg", 2000)]:
            me = server.MediaElement(); me.file = f; me.duration = d
            disp.mediaElements.append(me)
        disp.loop = True
        client = server.Client(); client.displayID = "Default"
        mock_settings.clients["c1"] = client
        return disp

    def test_play_sets_state_and_broadcasts(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = self._group_with_items(mock_settings)

        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "PLAY",
               "PAYLOAD": {"displayID": "Default"}}
        with patch("time.time", return_value=1000.0):
            server.msg_response(msg, _make_session())

        assert disp.action == server.PlayState.PLAY
        assert disp.playStartEpoch == 1000000   # int(1000.0 * 1000)
        assert server.socketmanager.broadcast.call_count == 1

    def test_stop_resets_state_and_broadcasts(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = self._group_with_items(mock_settings)
        disp.action = server.PlayState.PLAY
        disp.currentFrame = 5

        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "STOP",
               "PAYLOAD": {"displayID": "Default"}}
        server.msg_response(msg, _make_session())

        assert disp.action == server.PlayState.STOP
        assert disp.currentFrame == 0
        assert server.socketmanager.broadcast.call_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_playback.py::TestPlayStop -c tests/pytest.ini -q`
Expected: FAIL — action unchanged / broadcast not called.

- [ ] **Step 3: Implement the handlers**

In `server.py`, in `msg_response`, add these two branches before the final `else:`:

```python
    elif(msg["REQUEST"] == "PLAY"):
        display_id = msg["PAYLOAD"]["displayID"]
        display = settings.displays.get(display_id)
        if display and display.mediaElements:
            display.action = PlayState.PLAY
            display.playStartEpoch = int(time.time() * 1000)
            items = [{"id": me.id, "file": me.file, "duration": me.duration}
                     for me in display.mediaElements]
            broadcast_to_display_group(display_id, {
                "REQUEST": "PLAY",
                "PAYLOAD": {"startEpoch": display.playStartEpoch,
                            "items": items, "loop": display.loop}
            })
        response["PAYLOAD"] = "SUCCESS"

    elif(msg["REQUEST"] == "STOP"):
        display_id = msg["PAYLOAD"]["displayID"]
        display = settings.displays.get(display_id)
        if display:
            display.action = PlayState.STOP
            display.currentFrame = 0
        broadcast_to_display_group(display_id, {
            "REQUEST": "STOP", "PAYLOAD": {"displayID": display_id}
        })
        response["PAYLOAD"] = "SUCCESS"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_playback.py::TestPlayStop -c tests/pytest.ini -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_playback.py
git commit -m "feat(playback): add PLAY and STOP handlers

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Mid-playback join sync

**Files:**
- Modify: `server.py` (add `sync_new_client_to_group`; call it from the `REGISTER` branch of `msg_response`)
- Test: `tests/unit/test_playback.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_playback.py`:

```python
class TestMidJoinSync:
    def test_new_client_in_playing_group_receives_preload_and_play(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.file = "/media/server/a.jpg"; me.duration = 1000
        disp.mediaElements = [me]
        disp.loop = True
        disp.action = server.PlayState.PLAY
        disp.playStartEpoch = 1000000

        client = server.Client(); client.displayID = "Default"
        mock_settings.clients["newc"] = client

        server.sync_new_client_to_group("newc", client)

        # PRELOAD + PLAY sent directly to the joining client (2 broadcasts)
        assert server.socketmanager.broadcast.call_count == 2

    def test_new_client_in_idle_group_receives_nothing(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = mock_settings.displays["Default"]
        disp.action = server.PlayState.STOP

        client = server.Client(); client.displayID = "Default"
        server.sync_new_client_to_group("idlec", client)

        assert server.socketmanager.broadcast.call_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_playback.py::TestMidJoinSync -c tests/pytest.ini -q`
Expected: FAIL — `AttributeError: module 'server' has no attribute 'sync_new_client_to_group'`

- [ ] **Step 3: Implement the helper and wire it into REGISTER**

Add this function in `server.py` immediately after `playlist_index`:

```python
def sync_new_client_to_group(client_key, client):
    """If the client's display group is currently playing, send that one client
    PRELOAD + PLAY so it joins the in-progress playlist in sync."""
    display = settings.displays.get(client.displayID)
    if not display or display.action != PlayState.PLAY or not display.mediaElements:
        return
    items = [{"id": me.id, "file": me.file, "duration": me.duration}
             for me in display.mediaElements]
    broadcast_to_client(client_key, {"REQUEST": "PRELOAD", "PAYLOAD": {"items": items}})
    broadcast_to_client(client_key, {
        "REQUEST": "PLAY",
        "PAYLOAD": {"startEpoch": display.playStartEpoch, "items": items, "loop": display.loop}
    })
```

In `msg_response`, in the `REGISTER` branch, find the `if is_new_client:` block and add the sync call after `auto_configure_client(msg["SRC"], client)`:

```python
        if is_new_client:
            client.discoveryTime = time.time()
            auto_configure_client(msg["SRC"], client)
            sync_new_client_to_group(msg["SRC"], client)   # <-- add this line
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_playback.py::TestMidJoinSync -c tests/pytest.ini -q`
Expected: PASS

- [ ] **Step 5: Run the full suite for regressions**

Run: `python -m pytest tests/unit tests/integration -c tests/pytest.ini -q`
Expected: all prior tests still pass plus the new playback tests.

- [ ] **Step 6: Commit**

```bash
git add server.py tests/unit/test_playback.py
git commit -m "feat(playback): sync clients that join a playing group

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Client playback rendering (`index.html`, ES5)

**Files:**
- Modify: `index.html` (inline `<script>`: add `playlistIndex`, `playback` state, update `PRELOAD`, add `PLAY`/`STOP` handling, `showItem`, `renderPlayback`; gate TICK/TOCK)

No pytest harness exists for the inline client JS; this task is verified in the browser (Task 7). Keep everything ES5 (no `let`/`const`/arrow/template-literals).

- [ ] **Step 1: Add `playlistIndex` and `playback` state**

In `index.html`, just after `var mediaReady = false;`, add:

```javascript
	var playback = { items: [], startEpoch: 0, loop: false, active: false, timer: null };

	// JS mirror of server.playlist_index — keep the two in sync.
	function playlistIndex(elapsedMs, durations, loop) {
		var total = 0, i;
		for (i = 0; i < durations.length; i++) { total += durations[i]; }
		if (total <= 0) { return null; }
		if (loop) { elapsedMs = ((elapsedMs % total) + total) % total; }
		else if (elapsedMs >= total) { return null; }
		if (elapsedMs < 0) { elapsedMs = 0; }
		var cum = 0;
		for (i = 0; i < durations.length; i++) {
			if (elapsedMs < cum + durations[i]) { return { index: i, offsetMs: elapsedMs - cum }; }
			cum += durations[i];
		}
		return { index: durations.length - 1, offsetMs: durations[durations.length - 1] };
	}

	function showItem(i) {
		var item = playback.items[i];
		if (!item) { return; }
		$('#canvas').html('<img src="' + item.file + '" style="max-width:100%; max-height:100%;">');
	}

	function stopPlayback() {
		playback.active = false;
		if (playback.timer) { clearTimeout(playback.timer); playback.timer = null; }
		$('#canvas').empty();
	}

	// Frame-accurate, self-correcting render loop: each transition recomputes the
	// current item from the shared clock and schedules the exact next boundary.
	function renderPlayback() {
		if (!playback.active) { return; }
		if (playback.timer) { clearTimeout(playback.timer); playback.timer = null; }
		var durations = [], i;
		for (i = 0; i < playback.items.length; i++) { durations.push(playback.items[i].duration); }
		var elapsed = GoTime.now() - playback.startEpoch;
		var pos = playlistIndex(elapsed, durations, playback.loop);
		if (pos === null) { stopPlayback(); return; }
		showItem(pos.index);
		var msToNext = durations[pos.index] - pos.offsetMs;
		if (msToNext < 16) { msToNext = 16; } // floor to avoid a 0ms busy loop
		playback.timer = setTimeout(renderPlayback, msToNext);
	}
```

- [ ] **Step 2: Update the `PRELOAD` handler to the new item shape**

In `index.html`, replace the body of the `if(data_obj.REQUEST == "PRELOAD")` block so it reads `item.file` (not `.URL`):

```javascript
				if(data_obj.REQUEST == "PRELOAD")
				{
					mediaSequence = data_obj.PAYLOAD.items || [];
					mediaReady = false;
					var total = mediaSequence.length, settled = 0, j;
					if(total === 0) { mediaReady = true; updateHeartbeat(); }
					for(j = 0; j < mediaSequence.length; j++)
					{
						var img = new Image();
						img.onload = img.onerror = function() {
							settled++;
							if(settled >= total) { mediaReady = true; updateHeartbeat(); }
						};
						img.src = mediaSequence[j].file;
					}
				}
```

- [ ] **Step 3: Add `PLAY` and `STOP` handling**

In `index.html`, inside `mosiacMeshCallback`, after the existing `PRELOAD` / `IDENTIFY` / `CALIBRATE` branches (still inside the `if(data_obj.DEST == getUDID() || data_obj.DEST == 'ALL')` block), add:

```javascript
				else if(data_obj.REQUEST == "PLAY")
				{
					playback.items = data_obj.PAYLOAD.items || [];
					playback.startEpoch = data_obj.PAYLOAD.startEpoch;
					playback.loop = !!data_obj.PAYLOAD.loop;
					playback.active = true;
					renderPlayback();
				}
				else if(data_obj.REQUEST == "STOP")
				{
					stopPlayback();
				}
```

- [ ] **Step 4: Suppress TICK/TOCK while playing**

In `index.html`'s `tickcallback`, change the blink condition so the clock text does not clobber the playing image. Replace `if(blink)` (inside the `if(ProgrammableTimer.isSynced())` block) with:

```javascript
				if(blink && !playback.active)
```

- [ ] **Step 5: Syntax sanity check**

Run: `node --check index.html` is not valid (it's HTML). Instead verify by loading in Task 7. For now confirm no obvious typos by viewing the edited regions.
Expected: edited regions match the code above; all `var`, no ES6 tokens.

- [ ] **Step 6: Commit**

```bash
git add index.html
git commit -m "feat(playback): client-side synchronized playlist rendering

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: End-to-end verification (Playwright)

**Files:** none (verification only)

- [ ] **Step 1: Start the server**

Run (background): `python server.py -p 3000 -v`
Confirm: `curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/` prints `200`.

- [ ] **Step 2: Open the display client and inject a playlist**

Navigate Playwright to `http://localhost:3000/`. Wait ~4s for the clock to settle (heartbeat yellow). Then in the page, send SETPLAYLIST + PLAY over the existing socket and verify rendering. Use two short data-URI images so no real media files are needed:

```javascript
() => {
  var RED = 'data:image/gif;base64,R0lGODlhAQABAPAAAP8AAAAAACH5BAAAAAAALAAAAAABAAEAAAICRAEAOw==';
  var BLU = 'data:image/gif;base64,R0lGODlhAQABAPAAAAAA/wAAACH5BAAAAAAALAAAAAABAAEAAAICRAEAOw==';
  var items = [{id:'r', file:RED, duration:1000}, {id:'b', file:BLU, duration:1000}];
  // simulate the server PLAY arriving at this client
  playback.items = items; playback.startEpoch = GoTime.now(); playback.loop = true; playback.active = true;
  renderPlayback();
  return { active: playback.active, img: $('#canvas img').attr('src') ? 'shown' : 'none' };
}
```
Expected: `{ active: true, img: 'shown' }`.

- [ ] **Step 3: Verify the displayed item matches the clock-derived index**

Evaluate:

```javascript
() => {
  var durations = playback.items.map(function(x){return x.duration;});
  var elapsed = GoTime.now() - playback.startEpoch;
  var pos = playlistIndex(elapsed, durations, playback.loop);
  var shownSrc = $('#canvas img').attr('src');
  return { computedIndex: pos.index, shownMatches: shownSrc === playback.items[pos.index].file };
}
```
Expected: `shownMatches: true` (the rendered image is the one the clock says it should be).

- [ ] **Step 4: Verify it advances at the boundary**

Evaluate (waits across one 1s boundary and checks the index changed):

```javascript
async () => {
  function idx() {
    var d = playback.items.map(function(x){return x.duration;});
    return playlistIndex(GoTime.now() - playback.startEpoch, d, playback.loop).index;
  }
  var first = idx();
  await new Promise(function(r){ setTimeout(r, 1100); });
  var second = idx();
  var shown = $('#canvas img').attr('src');
  return { advanced: first !== second, shownMatchesSecond: shown === playback.items[second].file };
}
```
Expected: `advanced: true, shownMatchesSecond: true`.

- [ ] **Step 5: Verify STOP returns to idle**

Evaluate `() => { stopPlayback(); return { active: playback.active, canvasEmpty: $('#canvas').is(':empty') }; }`
Expected: `{ active: false, canvasEmpty: true }`.

- [ ] **Step 6: Shut down the server and record results**

Stop the background server (free port 3000). Note the observed values from Steps 2-5 in the task's completion comment. No commit (verification only).

---

## Self-review notes

- **Spec coverage:** clock-derived index (Task 1, 6), frame-accurate self-correcting scheduling (Task 6 `renderPlayback`), SETPLAYLIST/PLAY/STOP + PRELOAD fan-out (Tasks 3-4), mid-playback join (Task 5), persistence via existing `settings.dat` shape + `playStartEpoch` (Task 2), identical-mode images only / PAUSE+video+SEGMENT+SCRIPT deferred (not implemented — correct per scope), ES5 client (Task 6). Testing: pytest math + handlers (Tasks 1,3,4,5), Playwright sync (Task 7).
- **Type consistency:** item shape `{id, file, duration}` is identical in SETPLAYLIST/PLAY (server) and PRELOAD/PLAY (client); `playlist_index`/`playlistIndex` return `{index, offsetMs}` or `None`/`null` in both.
- **Known follow-ups (out of scope, by design):** the green heartbeat's `mediaReady` now keys off `PAYLOAD.items`; PAUSE, video, split (`SEGMENT`), `SCRIPT` animations, authoring UI, media-library listing, and scheduling are later slices.
