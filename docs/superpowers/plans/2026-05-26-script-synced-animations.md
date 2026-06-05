# SCRIPT Synced Animations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a built-in `bouncingBalls` JS animation full-screen on every display in a group, synchronized via the shared clock, as a `SCRIPT` playlist item.

**Architecture:** A SCRIPT item carries `playmode = SCRIPT` and `file` = the animation name. The server tags PLAY items with `playmode` so the client recognizes SCRIPT (no server render — client-computed). The client runs a clock-driven `<canvas>` loop calling a pure-function-of-time animation, so every display draws the same frame at the same instant.

**Tech Stack:** Python/aiohttp (small server change), vanilla ES5 + Canvas 2D + `webkit`-rAF (client). pytest + Playwright.

**Spec:** `docs/superpowers/specs/2026-05-26-script-synced-animations-design.md`

**Conventions:** `server.py` imports cleanly. Tests: `python -m pytest tests/unit -c tests/pytest.ini -q`. Commit trailer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`. ES5-only in `index.html`. Server SCRIPT tests go in `tests/unit/test_playback.py` (which already has the `_make_session` helper and imports `MagicMock`/`patch`).

---

## Task 1: Server — recognize SCRIPT + tag PLAY items with playmode

**Files:** Modify `server.py`; Test `tests/unit/test_playback.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_playback.py`:

```python
class TestScriptPlayback:
    def _script_group(self, mock_settings):
        disp = mock_settings.displays["Default"]
        me = server.MediaElement(); me.id = "anim"; me.file = "bouncingBalls"
        me.duration = 10000; me.playmode = server.PlayMode.SCRIPT
        disp.mediaElements = [me]; disp.loop = True; disp.action = server.PlayState.STOP
        client = server.Client(); client.displayID = "Default"
        mock_settings.clients["c1"] = client
        return disp

    def test_setplaylist_maps_script_playmode(self, mock_settings):
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        msg = {"SRC": "admin", "DEST": "SRV", "REQUEST": "SETPLAYLIST", "PAYLOAD": {
            "displayID": "Default", "loop": False,
            "items": [{"id": "a", "file": "bouncingBalls", "duration": 10000, "playmode": "SCRIPT"}]}}
        server.msg_response(msg, _make_session())
        assert mock_settings.displays["Default"].mediaElements[0].playmode == server.PlayMode.SCRIPT

    def test_play_script_broadcasts_with_playmode_and_no_render_gate(self, mock_settings):
        import jsonpickle
        server.settings = mock_settings
        server.socketmanager = MagicMock()
        self._script_group(mock_settings)
        ret = server.msg_response({"SRC": "a", "DEST": "SRV", "REQUEST": "PLAY",
                                   "PAYLOAD": {"displayID": "Default"}}, _make_session())
        assert jsonpickle.decode(ret)["PAYLOAD"] == "SUCCESS"        # not RENDER_REQUIRED
        assert server.socketmanager.broadcast.call_count == 1        # group path, one client
        sent = jsonpickle.decode(server.socketmanager.broadcast.call_args_list[0].args[0])
        assert sent["PAYLOAD"]["items"][0]["playmode"] == "SCRIPT"
        assert sent["PAYLOAD"]["items"][0]["file"] == "bouncingBalls"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_playback.py::TestScriptPlayback -c tests/pytest.ini -q`
Expected: FAIL — SETPLAYLIST stores SCRIPT items as `FULL`, and PLAY items omit `playmode`.

- [ ] **Step 3: Map SCRIPT in SETPLAYLIST**

In `server.py` `msg_response`, in the `SETPLAYLIST` branch, replace the playmode line:

```python
            me.playmode = PlayMode.SEGMENT if item.get("playmode") == "SEGMENT" else PlayMode.FULL
```

with:

```python
            _pm = item.get("playmode")
            me.playmode = (PlayMode.SEGMENT if _pm == "SEGMENT"
                           else PlayMode.SCRIPT if _pm == "SCRIPT"
                           else PlayMode.FULL)
```

- [ ] **Step 4: Add `playmode` to PLAY items in all three builders**

(a) In the PLAY branch's FULL/group path, change:

```python
                    items = [{"id": me.id, "file": me.file, "duration": me.duration}
                             for me in display.mediaElements]
```

to:

```python
                    items = [{"id": me.id, "file": me.file, "duration": me.duration,
                              "playmode": me.playmode.name} for me in display.mediaElements]
```

(b) In `_broadcast_segment_play`, change the append:

```python
            items.append({"id": me.id, "file": f, "duration": me.duration})
```

to:

```python
            items.append({"id": me.id, "file": f, "duration": me.duration,
                          "playmode": me.playmode.name})
```

(c) In `sync_new_client_to_group`, change:

```python
    items = [{"id": me.id, "file": me.file, "duration": me.duration}
             for me in display.mediaElements]
```

to:

```python
    items = [{"id": me.id, "file": me.file, "duration": me.duration,
              "playmode": me.playmode.name} for me in display.mediaElements]
```

- [ ] **Step 5: Run to verify pass + full regression**

Run: `python -m pytest tests/unit/test_playback.py::TestScriptPlayback -c tests/pytest.ini -q`
Expected: PASS
Then: `python -m pytest tests/unit tests/integration -c tests/pytest.ini -q`
Expected: all pass (the added `playmode` field is additive; existing item-shape assertions check specific keys, not exact dict equality).

- [ ] **Step 6: Commit**

```bash
git add server.py tests/unit/test_playback.py
git commit -m "feat(script): map SCRIPT playmode; tag PLAY items with playmode

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Client — animation registry, rAF shim, state, teardown

**Files:** Modify `index.html` (inline `<script>`). ES5 only; verified in Task 4.

- [ ] **Step 1: Add SCRIPT fields to the playback state**

Replace the `var playback = { ... };` declaration (it currently ends `..., paused: false };`) with the same plus two fields:

```javascript
	var playback = { items: [], startEpoch: 0, loop: false, active: false, timer: null,
		video: null, videoIndex: -1, driftTimer: null, paused: false,
		scriptRaf: null, scriptIndex: -1 };
```

- [ ] **Step 2: Add the rAF shim, animation registry, and `clearScript`**

Immediately AFTER the `clearVideo` function, insert:

```javascript
	var _raf = window.requestAnimationFrame || window.webkitRequestAnimationFrame ||
		function(cb){ return setTimeout(cb, 16); };
	var _caf = window.cancelAnimationFrame || window.webkitCancelAnimationFrame ||
		function(id){ clearTimeout(id); };

	// Built-in animations: each is a PURE function of elapsed time (tMs) and canvas
	// size, so every display draws the same frame at the same shared-clock instant.
	var animations = {
		bouncingBalls: function(ctx, tMs, w, h) {
			var colors = ['#e74c3c', '#27ae60', '#2980b9', '#f1c40f'];
			var r = Math.max(12, Math.min(w, h) * 0.06), n = 4, i;
			for (i = 0; i < n; i++) {
				var px = (Math.sin(tMs / (900 + i * 220) + i) + 1) / 2;        // 0..1
				var py = (Math.sin(tMs / (700 + i * 180) + i * 1.7) + 1) / 2;  // 0..1
				ctx.fillStyle = colors[i % colors.length];
				ctx.beginPath();
				ctx.arc(r + px * (w - 2 * r), r + py * (h - 2 * r), r, 0, Math.PI * 2);
				ctx.fill();
			}
		}
	};

	function clearScript() {
		if (playback.scriptRaf) { _caf(playback.scriptRaf); playback.scriptRaf = null; }
		playback.scriptIndex = -1;
	}
```

- [ ] **Step 3: ES5 self-review**

Confirm only `var`/`function`, no ES6; `_raf`/`_caf` fall back through the `webkit` prefix to `setTimeout`; `animations.bouncingBalls` reads only `tMs`/`w`/`h` (no external state).

- [ ] **Step 4: Commit**

```bash
git add index.html
git commit -m "feat(script): animation registry (bouncingBalls), rAF shim, clearScript

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Client — SCRIPT render path (`showItem`, loop, teardown, PRELOAD)

**Files:** Modify `index.html` (inline `<script>`). ES5 only.

- [ ] **Step 1: Add `runScriptLoop` and branch `showItem`**

Add `runScriptLoop` immediately after `clearScript` (from Task 2):

```javascript
	function runScriptLoop(canvas, name) {
		var ctx = canvas.getContext('2d');
		function frame() {
			if (!playback.active) { return; }
			var durations = [], k;
			for (k = 0; k < playback.items.length; k++) { durations.push(playback.items[k].duration); }
			var pos = playlistIndex(GoTime.now() - playback.startEpoch, durations, playback.loop);
			if (pos === null) { stopPlayback(); return; }
			if (pos.index !== playback.scriptIndex) { return; } // transition; renderPlayback handles it
			ctx.clearRect(0, 0, canvas.width, canvas.height);
			if (animations[name]) { animations[name](ctx, pos.offsetMs, canvas.width, canvas.height); }
			playback.scriptRaf = _raf(frame);
		}
		playback.scriptRaf = _raf(frame);
	}
```

In `showItem(i, offsetMs)`, add `clearScript();` next to `clearVideo();` at the top, and add a SCRIPT branch as the first case. The function's top + branching becomes:

```javascript
	function showItem(i, offsetMs) {
		clearVideo();
		clearScript();
		var item = playback.items[i];
		if (!item) { return; }
		if (item.playmode === 'SCRIPT') {
			var cnv = document.createElement('canvas');
			cnv.width = window.innerWidth; cnv.height = window.innerHeight;
			cnv.style.width = '100%'; cnv.style.height = '100%';
			$('#canvas').empty();
			document.getElementById('canvas').appendChild(cnv);
			playback.scriptIndex = i;
			runScriptLoop(cnv, item.file);
		} else if (isVideoItem(item.file)) {
```

(Leave the existing video branch body and the final image `else` exactly as they are — you are only adding the `clearScript();` line, the `if (item.playmode === 'SCRIPT') { … } else if` wrapper, and turning the former `if (isVideoItem(item.file)) {` into `} else if (isVideoItem(item.file)) {`.)

- [ ] **Step 2: Tear down script in `stopPlayback`**

In `stopPlayback`, add `clearScript();` right after the existing `clearVideo();`:

```javascript
	function stopPlayback() {
		playback.active = false;
		playback.paused = false;
		clearVideo();
		clearScript();
		if (playback.timer) { clearTimeout(playback.timer); playback.timer = null; }
		$('#canvas').empty();
	}
```

- [ ] **Step 3: Skip SCRIPT items in PRELOAD**

In the `PRELOAD` branch's `for` loop, add a leading SCRIPT case and make the video check an `else if`. Change:

```javascript
					for(j = 0; j < mediaSequence.length; j++)
					{
						if(isVideoItem(mediaSequence[j].file))
						{
```

to:

```javascript
					for(j = 0; j < mediaSequence.length; j++)
					{
						if(mediaSequence[j].playmode === 'SCRIPT')
						{
							onSettled(); // a script item has nothing to fetch
						}
						else if(isVideoItem(mediaSequence[j].file))
						{
```

- [ ] **Step 4: ES5 self-review**

Confirm: only `var`/`function`; `showItem` now has three branches (SCRIPT / video / image) with `clearScript()` at the top; `runScriptLoop` re-arms via `_raf` only while the current item is the SCRIPT item; `stopPlayback` and PRELOAD updated; braces balanced.

- [ ] **Step 5: Commit**

```bash
git add index.html
git commit -m "feat(script): clock-driven canvas loop for SCRIPT items

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: End-to-end Playwright verification

**Files:** none (verification only). Controller-run.

- [ ] **Step 1: Start server + open client**

Run (background): remove stale `settings.dat`, then `python server.py -p 3000 -v`; confirm `curl http://localhost:3000/` → 200. Navigate Playwright to `http://localhost:3000/`; wait ~4s for clock settle.

- [ ] **Step 2: Determinism (the sync guarantee)**

```javascript
() => {
  // bouncingBalls is a pure function of tMs -> same tMs gives identical pixels
  function sample(tMs) {
    var c = document.createElement('canvas'); c.width = 200; c.height = 200;
    var x = c.getContext('2d');
    animations.bouncingBalls(x, tMs, 200, 200);
    return c.toDataURL();
  }
  return {
    sameTimeIdentical: sample(1234) === sample(1234),
    differentTimeDiffers: sample(1234) !== sample(5678)
  };
}
```
Expected: `{ sameTimeIdentical: true, differentTimeDiffers: true }` — identical render for the same clock time (so all displays match), different over time (it animates).

- [ ] **Step 3: A SCRIPT item builds a canvas and runs the loop**

```javascript
() => {
  playback.items = [{ id: 'a', file: 'bouncingBalls', duration: 10000, playmode: 'SCRIPT' }];
  playback.startEpoch = GoTime.now(); playback.loop = true; playback.active = true;
  showItem(0, 0);
  return {
    canvasBuilt: !!document.querySelector('#canvas canvas'),
    loopRunning: playback.scriptRaf !== null,
    scriptIndex: playback.scriptIndex
  };
}
```
Expected: `{ canvasBuilt: true, loopRunning: true, scriptIndex: 0 }`.

- [ ] **Step 4: STOP tears it down**

```javascript
() => { stopPlayback(); return { raf: playback.scriptRaf, canvasGone: document.querySelector('#canvas canvas') === null, active: playback.active }; }
```
Expected: `{ raf: null, canvasGone: true, active: false }`.

- [ ] **Step 5: Shut down + clean up**

Stop the background server (free port 3000); close the browser; remove `.playwright-mcp/` and any `settings.dat`. No commit.

---

## Self-review notes

- **Spec coverage:** SCRIPT item model (Task 1 SETPLAYLIST mapping); `playmode` added to PLAY items so the client recognizes SCRIPT (Task 1, all three builders); no render gate for SCRIPT (Task 1 test asserts SUCCESS not RENDER_REQUIRED); animation registry + pure-function `bouncingBalls` + rAF shim (Task 2); clock-driven canvas loop + `showItem` branch + `clearScript` teardown + PRELOAD skip (Task 3); determinism = sync guarantee (Task 4). Mosaic-spanning / params / multiple animations correctly absent.
- **Placeholder scan:** none — complete code/commands throughout.
- **Type/name consistency:** `playback.scriptRaf`/`scriptIndex`, `clearScript`, `runScriptLoop`, `animations.bouncingBalls`, `_raf`/`_caf`, `item.playmode === 'SCRIPT'`, server `me.playmode.name` used consistently. The client reads `playmode` strings (`'SCRIPT'`) that match the server's `me.playmode.name` (`PlayMode.SCRIPT.name == 'SCRIPT'`).
