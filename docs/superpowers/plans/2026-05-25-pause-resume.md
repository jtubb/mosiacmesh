# PAUSE / Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add PAUSE (and resume-via-PLAY) to the synchronized playback engine — every display in a group freezes on the same frame and resumes from where it paused.

**Architecture:** Pure clock-offset math on the existing engine. The server records `pauseOffset = now − playStartEpoch` on PAUSE; resume shifts `playStartEpoch = now − pauseOffset` so the clock-derived index continues. The client freezes in place (clears timers, pauses video) and resumes on the next PLAY.

**Tech Stack:** Python 3 / aiohttp / sockjs 0.13 (server), vanilla ES5 client, pytest (server), Playwright (client verification).

**Spec:** `docs/superpowers/specs/2026-05-25-pause-resume-design.md`

**Constraints:** ES5 only in `index.html` (1st-gen iPad). Run tests: `python -m pytest tests/unit -c tests/pytest.ini -q`. Commit trailer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`. Tasks 1–2 edit `server.py` + `tests/unit/test_playback.py` (coupled). The `tests/unit/test_playback.py` file already has `_make_session`, and imports `MagicMock`/`patch` — append new classes, don't recreate those.

---

## Task 1: `Display.pauseOffset` field + `PAUSE` handler

**Files:** Modify `server.py`; Test `tests/unit/test_playback.py`.

- [ ] **Step 1: Add the field**

In `server.py`, in `class Display`'s `__init__`, add `self.pauseOffset = 0` right after `self.playStartEpoch = 0`:

```python
        self.playStartEpoch = 0   # server-time ms when playback last (re)started
        self.pauseOffset = 0      # ms into the playlist when paused
```

- [ ] **Step 2: Write the failing test**

Append to `tests/unit/test_playback.py`:

```python
class TestPause:
    def _playing_group(self, mock_settings):
        disp = mock_settings.displays["Default"]
        disp.mediaElements = []
        for f, d in [("/media/server/a.jpg", 1000), ("/media/server/b.jpg", 2000)]:
            me = server.MediaElement(); me.file = f; me.duration = d
            disp.mediaElements.append(me)
        disp.loop = True
        disp.action = server.PlayState.PLAY
        disp.playStartEpoch = 1000000
        client = server.Client(); client.displayID = "Default"
        mock_settings.clients["c1"] = client
        return disp

    def test_pause_sets_state_offset_and_broadcasts(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = self._playing_group(mock_settings)
        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "PAUSE",
               "PAYLOAD": {"displayID": "Default"}}
        with patch("time.time", return_value=1002.5):  # 1002500 ms
            server.msg_response(msg, _make_session())
        assert disp.action == server.PlayState.PAUSE
        assert disp.pauseOffset == 2500  # 1002500 - 1000000
        assert server.socketmanager.broadcast.call_count == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_playback.py::TestPause -c tests/pytest.ini -q`
Expected: FAIL — `PAUSE` falls through to the echo `else`, so `action`/`pauseOffset` unchanged and no broadcast.

- [ ] **Step 4: Implement the PAUSE handler**

In `server.py` `msg_response`, add this branch immediately before the final `else:`:

```python
    elif(msg["REQUEST"] == "PAUSE"):
        display_id = msg["PAYLOAD"]["displayID"]
        display = settings.displays.get(display_id)
        if display and display.action == PlayState.PLAY:
            display.pauseOffset = int(time.time() * 1000) - display.playStartEpoch
            display.action = PlayState.PAUSE
        broadcast_to_display_group(display_id, {
            "REQUEST": "PAUSE", "PAYLOAD": {"displayID": display_id}
        })
        response["PAYLOAD"] = "SUCCESS"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_playback.py::TestPause -c tests/pytest.ini -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add server.py tests/unit/test_playback.py
git commit -m "feat(playback): PAUSE handler records offset and freezes the group

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Resume-aware `PLAY`

**Files:** Modify `server.py`; Test `tests/unit/test_playback.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_playback.py`:

```python
class TestResume:
    def _group(self, mock_settings, action, pause_offset=0):
        disp = mock_settings.displays["Default"]
        disp.mediaElements = []
        for f, d in [("/media/server/a.jpg", 1000), ("/media/server/b.jpg", 2000)]:
            me = server.MediaElement(); me.file = f; me.duration = d
            disp.mediaElements.append(me)
        disp.loop = True
        disp.action = action
        disp.pauseOffset = pause_offset
        client = server.Client(); client.displayID = "Default"
        mock_settings.clients["c1"] = client
        return disp

    def test_play_resumes_from_pause(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = self._group(mock_settings, server.PlayState.PAUSE, pause_offset=2500)
        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "PLAY",
               "PAYLOAD": {"displayID": "Default"}}
        with patch("time.time", return_value=5000.0):  # 5000000 ms
            server.msg_response(msg, _make_session())
        assert disp.action == server.PlayState.PLAY
        assert disp.playStartEpoch == 4997500  # 5000000 - 2500 (resume)

    def test_play_from_stopped_starts_fresh(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        disp = self._group(mock_settings, server.PlayState.STOP, pause_offset=2500)
        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "PLAY",
               "PAYLOAD": {"displayID": "Default"}}
        with patch("time.time", return_value=5000.0):
            server.msg_response(msg, _make_session())
        assert disp.action == server.PlayState.PLAY
        assert disp.playStartEpoch == 5000000  # fresh, ignores pauseOffset
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_playback.py::TestResume -c tests/pytest.ini -q`
Expected: FAIL — the current PLAY always sets `playStartEpoch = now` (fresh), so the resume case mismatches.

- [ ] **Step 3: Make PLAY resume-aware**

In `server.py` `msg_response`, replace the existing `PLAY` branch with:

```python
    elif(msg["REQUEST"] == "PLAY"):
        display_id = msg["PAYLOAD"]["displayID"]
        display = settings.displays.get(display_id)
        if display and display.mediaElements:
            now_ms = int(time.time() * 1000)
            if display.action == PlayState.PAUSE:
                display.playStartEpoch = now_ms - display.pauseOffset  # resume
            else:
                display.playStartEpoch = now_ms                        # fresh start
            display.action = PlayState.PLAY
            items = [{"id": me.id, "file": me.file, "duration": me.duration}
                     for me in display.mediaElements]
            broadcast_to_display_group(display_id, {
                "REQUEST": "PLAY",
                "PAYLOAD": {"startEpoch": display.playStartEpoch,
                            "items": items, "loop": display.loop}
            })
        response["PAYLOAD"] = "SUCCESS"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_playback.py::TestResume -c tests/pytest.ini -q`
Expected: PASS

- [ ] **Step 5: Run the full suite for regressions**

Run: `python -m pytest tests/unit tests/integration -c tests/pytest.ini -q`
Expected: all prior tests pass plus the new pause/resume tests (the earlier `TestPlayStop` PLAY test still passes — fresh start path is unchanged for non-paused groups).

- [ ] **Step 6: Commit**

```bash
git add server.py tests/unit/test_playback.py
git commit -m "feat(playback): PLAY resumes a paused group from its offset

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Client freeze/resume (`index.html`, ES5)

**Files:** Modify `index.html` (inline `<script>`). No pytest harness; verified in Task 4.

- [ ] **Step 1: Add `paused` to the playback state**

Replace the `var playback = { ... };` declaration with (add `paused: false`):

```javascript
	var playback = { items: [], startEpoch: 0, loop: false, active: false, timer: null,
		video: null, videoIndex: -1, driftTimer: null, paused: false };
```

- [ ] **Step 2: Add `pausePlayback` and guard `renderPlayback`; reset `paused` in `stopPlayback`**

Add `pausePlayback` immediately before `stopPlayback`:

```javascript
	function pausePlayback() {
		playback.paused = true;
		if (playback.timer) { clearTimeout(playback.timer); playback.timer = null; }
		if (playback.driftTimer) { clearInterval(playback.driftTimer); playback.driftTimer = null; }
		if (playback.video) { try { playback.video.pause(); } catch (e) {} }
	}
```

In `stopPlayback`, add `playback.paused = false;` (after `playback.active = false;`):

```javascript
	function stopPlayback() {
		playback.active = false;
		playback.paused = false;
		clearVideo();
		if (playback.timer) { clearTimeout(playback.timer); playback.timer = null; }
		$('#canvas').empty();
	}
```

In `renderPlayback`, change the first guard line from `if (!playback.active) { return; }` to:

```javascript
		if (!playback.active || playback.paused) { return; }
```

- [ ] **Step 3: Wire PAUSE into the message handler and clear `paused` on PLAY**

In `mosiacMeshCallback`, in the `PLAY` branch, add `playback.paused = false;` right before `renderPlayback();`:

```javascript
				else if(data_obj.REQUEST == "PLAY")
				{
					playback.items = data_obj.PAYLOAD.items || [];
					playback.startEpoch = data_obj.PAYLOAD.startEpoch;
					playback.loop = !!data_obj.PAYLOAD.loop;
					playback.active = true;
					playback.paused = false;
					renderPlayback();
				}
```

Add a `PAUSE` branch immediately after the `STOP` branch (sibling `else if`):

```javascript
				else if(data_obj.REQUEST == "PAUSE")
				{
					pausePlayback();
				}
```

- [ ] **Step 4: ES5 self-review**

Confirm: only `var`/`function`; no `let`/`const`/arrow/template-literals; braces balanced; `renderPlayback` now returns early when `playback.paused`; PLAY clears `paused`.

- [ ] **Step 5: Commit**

```bash
git add index.html
git commit -m "feat(playback): client PAUSE freeze + resume on PLAY

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: End-to-end Playwright verification

**Files:** none (verification only). Done by the controller, over the real socket.

- [ ] **Step 1: Start the server and open the client**

Run (background): `python server.py -p 3000 -v`; confirm `curl http://localhost:3000/` → 200. Navigate Playwright to `http://localhost:3000/`; wait ~4s for the clock to settle.

- [ ] **Step 2: Set a looping playlist and PLAY, capture the playing frame**

```javascript
async () => {
  function sleep(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
  var dev = await fetch('/api/discovery/devices').then(function(r){ return r.json(); });
  var group = null, i;
  for (i = 0; i < dev.devices.length; i++) { if (dev.devices[i].clientKey === getUDID()) { group = dev.devices[i].displayID; } }
  if (!group) { return { ok:false, reason:'not registered' }; }
  function frame(label, bg){ var cv=document.createElement('canvas'); cv.width=300; cv.height=300; var x=cv.getContext('2d'); x.fillStyle=bg; x.fillRect(0,0,300,300); x.fillStyle='#fff'; x.font='bold 180px sans-serif'; x.textAlign='center'; x.textBaseline='middle'; x.fillText(label,150,160); return cv.toDataURL('image/png'); }
  var items=[{id:'1',file:frame('1','#c0392b'),duration:2000},{id:'2',file:frame('2','#27ae60'),duration:2000},{id:'3',file:frame('3','#2980b9'),duration:2000}];
  sock.send(generateMessage('SRV','SETPLAYLIST',{displayID:group, loop:true, items:items}));
  await sleep(400);
  sock.send(generateMessage('SRV','PLAY',{displayID:group}));
  await sleep(800);
  return { ok:true, group:group, active:playback.active, srcAtPlay: $('#canvas img').attr('src') ? 'shown' : 'none' };
}
```
Expected: `ok:true, active:true, srcAtPlay:'shown'`.

- [ ] **Step 3: PAUSE and verify the frame freezes across a boundary**

```javascript
async () => {
  function sleep(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
  var dev = await fetch('/api/discovery/devices').then(function(r){ return r.json(); });
  var group=null, i; for (i=0;i<dev.devices.length;i++){ if(dev.devices[i].clientKey===getUDID()){ group=dev.devices[i].displayID; } }
  var srcBefore = $('#canvas img').attr('src');
  sock.send(generateMessage('SRV','PAUSE',{displayID:group}));
  await sleep(300);
  var pausedFlag = playback.paused, timerNull = (playback.timer === null);
  await sleep(2500); // cross at least one 2s frame boundary
  var srcAfter = $('#canvas img').attr('src');
  return { pausedFlag:pausedFlag, timerNull:timerNull, frameUnchanged: srcBefore === srcAfter };
}
```
Expected: `pausedFlag:true, timerNull:true, frameUnchanged:true` (the displayed frame did not advance despite the clock moving past a boundary).

- [ ] **Step 4: Resume with PLAY and verify it continues (not frame 1) and advances**

```javascript
async () => {
  function sleep(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
  var dev = await fetch('/api/discovery/devices').then(function(r){ return r.json(); });
  var group=null, i; for (i=0;i<dev.devices.length;i++){ if(dev.devices[i].clientKey===getUDID()){ group=dev.devices[i].displayID; } }
  var srcAtResume0 = $('#canvas img').attr('src');
  sock.send(generateMessage('SRV','PLAY',{displayID:group}));
  await sleep(400);
  var resumedNotPaused = (playback.paused === false), timerSet = (playback.timer !== null);
  // index right after resume should match where we paused (continuation), then advance over time
  var d = playback.items.map(function(x){ return x.duration; });
  var idxResume = playlistIndex(GoTime.now() - playback.startEpoch, d, playback.loop).index;
  await sleep(2200);
  var idxLater = playlistIndex(GoTime.now() - playback.startEpoch, d, playback.loop).index;
  return { resumedNotPaused:resumedNotPaused, timerSet:timerSet, idxResume:idxResume, idxLater:idxLater, advanced: idxResume !== idxLater };
}
```
Expected: `resumedNotPaused:true, timerSet:true, advanced:true` (the index moved again after resume).

- [ ] **Step 5: STOP returns to idle**

```javascript
async () => {
  function sleep(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
  var dev = await fetch('/api/discovery/devices').then(function(r){ return r.json(); });
  var group=null, i; for (i=0;i<dev.devices.length;i++){ if(dev.devices[i].clientKey===getUDID()){ group=dev.devices[i].displayID; } }
  sock.send(generateMessage('SRV','STOP',{displayID:group}));
  await sleep(400);
  return { active:playback.active, paused:playback.paused, canvasImgGone: $('#canvas img').length === 0 };
}
```
Expected: `active:false, paused:false, canvasImgGone:true`.

- [ ] **Step 6: Shut down + clean up**

Stop the background server (free port 3000); close the browser; remove any `.playwright-mcp/` and a stray `settings.dat` left by the test. No commit (verification only).

---

## Self-review notes

- **Spec coverage:** `Display.pauseOffset` (Task 1); PAUSE handler records offset + freezes + broadcasts (Task 1); PLAY resume-aware vs fresh (Task 2, both cases tested); client `paused` flag, `pausePlayback` freeze, PLAY clears `paused`, `renderPlayback` guard, `stopPlayback` reset (Task 3); STOP-is-reset unchanged. Edge cases (pause-not-playing no-ops state but still broadcasts; resume-past-end → idle) follow from the guards. Deferred mid-join-during-pause: not implemented (correct per spec).
- **Placeholder scan:** none — concrete code/commands throughout.
- **Type/name consistency:** `pauseOffset`/`playStartEpoch` (server) and `playback.paused`/`pausePlayback`/`stopPlayback`/`renderPlayback` (client) used consistently; PLAY checks `action == PlayState.PAUSE` before reassigning `action`.
