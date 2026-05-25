# Video Playlist Items Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `.mp4` items play in the existing synchronized playlist engine, kept aligned across displays by a `playbackRate` controller with a hard-seek fallback.

**Architecture:** Pure client-side extension of the playback MVP in `index.html`. `showItem` builds a `<video>` for `.mp4` items, seeks to the clock-derived offset, and plays; a ~500ms drift loop nudges `playbackRate` (or hard-seeks on large drift). The server, `playlist_index`, and `SETPLAYLIST`/`PLAY`/`STOP` are unchanged.

**Tech Stack:** Vanilla ES5 + jQuery 1.x + SockJS client + GoTime (display client). Playwright for verification. No pytest changes (the new logic is client-side; server already passes video items through).

**Spec:** `docs/superpowers/specs/2026-05-25-video-playlist-items-design.md`

**Constraints:** ES5 ONLY (1st-gen iPad / iOS 5 / Safari 5.1) — `var`, `function`, string concat; no `let`/`const`/arrow/template-literals/`class`. Commit messages end with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`. All edits are in `index.html`.

> **Note on tasks 1–3:** they all edit the same `index.html` inline script and are tightly coupled. There is no unit harness for this inline JS, so each task is edit → ES5 self-review → commit; behavioral verification is Task 4 (Playwright). An executor may implement 1–3 in a single pass, but keep the three commits distinct.

---

## Task 1: Video playback state + drift controller helpers

**Files:** Modify `index.html` (inline `<script>`).

- [ ] **Step 1: Extend the `playback` state object**

Find the line `var playback = { items: [], startEpoch: 0, loop: false, active: false, timer: null };` and replace it with:

```javascript
	var playback = { items: [], startEpoch: 0, loop: false, active: false, timer: null,
		video: null, videoIndex: -1, driftTimer: null };
	var HARD_SEEK_MS = 400; // drift beyond this is corrected with a seek, not a rate nudge
```

- [ ] **Step 2: Add the video helpers**

Immediately AFTER the `playlistIndex` function (and before `showItem`), insert:

```javascript
	function isVideoItem(file) {
		return /\.mp4(\?|$)/i.test(file || '');
	}

	function clearVideo() {
		if (playback.driftTimer) { clearInterval(playback.driftTimer); playback.driftTimer = null; }
		if (playback.video) {
			try { playback.video.pause(); } catch (e) {}
			playback.video = null;
		}
		playback.videoIndex = -1;
	}

	// Keep the active video aligned to the shared clock: a proportional playbackRate
	// controller for small drift, a hard seek for large drift (the latter is also the
	// iOS-5 path, where playbackRate may be ignored so error grows until it is seeked).
	function driftTick() {
		var v = playback.video;
		if (!v || !playback.active) { return; }
		var durations = [], i;
		for (i = 0; i < playback.items.length; i++) { durations.push(playback.items[i].duration); }
		var pos = playlistIndex(GoTime.now() - playback.startEpoch, durations, playback.loop);
		if (pos === null || pos.index !== playback.videoIndex) { return; } // transition handled by renderPlayback
		var errorMs = v.currentTime * 1000 - pos.offsetMs;
		if (Math.abs(errorMs) > HARD_SEEK_MS) {
			try { v.currentTime = pos.offsetMs / 1000; } catch (e) {}
			v.playbackRate = 1;
		} else {
			var rate = 1 - errorMs / 2000;
			if (rate < 0.85) { rate = 0.85; }
			if (rate > 1.15) { rate = 1.15; }
			v.playbackRate = rate;
		}
	}

	function startDriftLoop() {
		if (playback.driftTimer) { clearInterval(playback.driftTimer); }
		playback.driftTimer = setInterval(driftTick, 500);
	}
```

- [ ] **Step 3: ES5 self-review**

Re-read the inserted code. Confirm: only `var`/`function`, no `=>`/backticks/`let`/`const`; `clearInterval`/`setInterval` used (not `clearTimeout`); braces balanced.

- [ ] **Step 4: Commit**

```bash
git add index.html
git commit -m "feat(playback): video state + drift controller helpers

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `showItem` video branch, offset plumbing, teardown

**Files:** Modify `index.html` (inline `<script>`).

- [ ] **Step 1: Replace `showItem` with the type-branching version**

Replace the entire existing `showItem` function with:

```javascript
	function showItem(i, offsetMs) {
		clearVideo();
		var item = playback.items[i];
		if (!item) { return; }
		if (isVideoItem(item.file)) {
			var v = document.createElement('video');
			v.muted = true;
			v.setAttribute('webkit-playsinline', '');
			v.setAttribute('playsinline', '');
			v.preload = 'auto';
			v.style.maxWidth = '100%';
			v.style.maxHeight = '100%';
			v.src = item.file;
			$('#canvas').empty();
			document.getElementById('canvas').appendChild(v);
			playback.video = v;
			playback.videoIndex = i;
			var seekAndPlay = function() {
				try { v.currentTime = (offsetMs || 0) / 1000; } catch (e) {}
				var p = v.play();
				if (p && p['catch']) { p['catch'](function() {}); } // ignore autoplay rejection
			};
			if (v.readyState >= 1) { seekAndPlay(); }
			else { v.addEventListener('loadedmetadata', seekAndPlay); }
			startDriftLoop();
		} else {
			$('#canvas').html('<img src="' + item.file + '" style="max-width:100%; max-height:100%;">');
		}
	}
```

- [ ] **Step 2: Pass the offset from `renderPlayback`**

In `renderPlayback`, change the line `showItem(pos.index);` to:

```javascript
		showItem(pos.index, pos.offsetMs);
```

- [ ] **Step 3: Tear down video in `stopPlayback`**

Replace the existing `stopPlayback` function with:

```javascript
	function stopPlayback() {
		playback.active = false;
		clearVideo();
		if (playback.timer) { clearTimeout(playback.timer); playback.timer = null; }
		$('#canvas').empty();
	}
```

- [ ] **Step 4: ES5 self-review**

Confirm ES5-only; `showItem` now takes `(i, offsetMs)`; image branch unchanged in behavior; `clearVideo()` runs at the top of `showItem` so advancing off a video tears it down before the next item renders.

- [ ] **Step 5: Commit**

```bash
git add index.html
git commit -m "feat(playback): render video items, seek to clock offset, teardown on transition

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: PRELOAD readiness for video (`canplaythrough`)

**Files:** Modify `index.html` (inline `<script>`).

- [ ] **Step 1: Replace the PRELOAD branch body**

Replace the body of the `if(data_obj.REQUEST == "PRELOAD")` block with:

```javascript
				if(data_obj.REQUEST == "PRELOAD")
				{
					mediaSequence = data_obj.PAYLOAD.items || [];
					mediaReady = false;
					var total = mediaSequence.length, settled = 0, j;
					if(total === 0) { mediaReady = true; updateHeartbeat(); }
					var onSettled = function() {
						settled++;
						if(settled >= total) { mediaReady = true; updateHeartbeat(); }
					};
					for(j = 0; j < mediaSequence.length; j++)
					{
						if(isVideoItem(mediaSequence[j].file))
						{
							var vp = document.createElement('video');
							vp.preload = 'auto';
							vp.muted = true;
							// count the FIRST terminal-ish signal once per item so a bad/large
							// URL can't wedge the green heartbeat forever
							var onceV = function() {
								if (this._hbDone) { return; }
								this._hbDone = true;
								onSettled();
							};
							vp.addEventListener('canplaythrough', onceV);
							vp.addEventListener('error', onceV);
							vp.addEventListener('stalled', onceV);
							vp.src = mediaSequence[j].file;
						}
						else
						{
							var img = new Image();
							img.onload = img.onerror = onSettled;
							img.src = mediaSequence[j].file;
						}
					}
				}
```

- [ ] **Step 2: ES5 self-review**

Confirm: `isVideoItem` (Task 1) is in scope; `onceV` uses `this._hbDone` (the event target element); ES5-only; images keep `img.onload/onerror`.

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "feat(playback): PRELOAD video readiness via canplaythrough

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: End-to-end Playwright verification

**Files:** none (verification only). Done by the controller.

The drift controller is tested deterministically with a **fake video object** (so no H.264 codec is required in headless Chromium); DOM creation and transitions use a real `.mp4`-named item.

- [ ] **Step 1: Start the server and open the client**

Run (background): `python server.py -p 3000 -v`; confirm `curl http://localhost:3000/` is 200. Navigate Playwright to `http://localhost:3000/` and wait ~3s.

- [ ] **Step 2: Verify `showItem` builds a `<video>` for an mp4 item**

```javascript
() => {
  playback.items = [{id:'v', file:'/media/server/clip.mp4', duration:10000}];
  playback.startEpoch = GoTime.now();
  playback.loop = false;
  playback.active = true;
  showItem(0, 0);
  var el = document.querySelector('#canvas video');
  return {
    isVideoEl: !!el,
    muted: el ? el.muted : null,
    playsinline: el ? el.hasAttribute('playsinline') : null,
    srcEndsMp4: el ? /clip\.mp4$/.test(el.getAttribute('src')) : null,
    videoIndex: playback.videoIndex,
    driftRunning: playback.driftTimer !== null
  };
}
```
Expected: `isVideoEl:true, muted:true, playsinline:true, srcEndsMp4:true, videoIndex:0, driftRunning:true`.

- [ ] **Step 3: Verify the drift controller (fake video, small drift → rate nudge)**

```javascript
() => {
  // controlled state: 3s into a 10s clip
  playback.items = [{id:'v', file:'/x.mp4', duration:10000}];
  playback.startEpoch = GoTime.now() - 3000;
  playback.loop = false; playback.active = true; playback.videoIndex = 0;
  if (playback.driftTimer) { clearInterval(playback.driftTimer); playback.driftTimer = null; }
  // video is 100ms AHEAD of the clock (3.1s vs ~3.0s target)
  playback.video = { currentTime: 3.1, playbackRate: 1, pause: function(){} };
  driftTick();
  var rateAfterAhead = playback.video.playbackRate;
  // video is 100ms BEHIND
  playback.video = { currentTime: 2.9, playbackRate: 1, pause: function(){} };
  driftTick();
  var rateAfterBehind = playback.video.playbackRate;
  return { rateAfterAhead: rateAfterAhead, rateAfterBehind: rateAfterBehind };
}
```
Expected: `rateAfterAhead` < 1 and >= 0.85 (ahead → slow down); `rateAfterBehind` > 1 and <= 1.15 (behind → speed up).

- [ ] **Step 4: Verify the hard-seek fallback (large drift)**

```javascript
() => {
  playback.items = [{id:'v', file:'/x.mp4', duration:10000}];
  playback.startEpoch = GoTime.now() - 3000;
  playback.loop = false; playback.active = true; playback.videoIndex = 0;
  if (playback.driftTimer) { clearInterval(playback.driftTimer); playback.driftTimer = null; }
  // video is 2s ahead -> exceeds HARD_SEEK_MS (400)
  playback.video = { currentTime: 5.0, playbackRate: 1, pause: function(){} };
  driftTick();
  return {
    seekedNearTargetSec: Math.abs(playback.video.currentTime - 3.0) < 0.2,
    rateReset: playback.video.playbackRate === 1
  };
}
```
Expected: `seekedNearTargetSec:true, rateReset:true`.

- [ ] **Step 5: Verify transition video→image tears the video down**

```javascript
() => {
  playback.items = [{id:'v', file:'/x.mp4', duration:1000}, {id:'i', file:'/y.jpg', duration:1000}];
  playback.startEpoch = GoTime.now(); playback.loop = false; playback.active = true;
  showItem(0, 0);                      // video
  var hadVideo = playback.video !== null && playback.driftTimer !== null;
  showItem(1, 0);                      // advance to image
  return {
    hadVideo: hadVideo,
    videoCleared: playback.video === null && playback.driftTimer === null,
    canvasIsImg: !!document.querySelector('#canvas img') && !document.querySelector('#canvas video')
  };
}
```
Expected: `hadVideo:true, videoCleared:true, canvasIsImg:true`.

- [ ] **Step 6: Verify STOP tears down the video**

```javascript
() => {
  playback.items = [{id:'v', file:'/x.mp4', duration:10000}];
  playback.startEpoch = GoTime.now(); playback.loop = false; playback.active = true;
  showItem(0, 0);
  stopPlayback();
  return {
    active: playback.active,
    videoCleared: playback.video === null && playback.driftTimer === null,
    canvasEmpty: document.getElementById('canvas').children.length === 0
  };
}
```
Expected: `active:false, videoCleared:true, canvasEmpty:true`.

- [ ] **Step 7: Shut down the server and record results**

Stop the background server (free port 3000); close the browser. Note the observed values. No commit (verification only). Real video decode/`currentTime` advance on actual hardware (and the iOS-5 autoplay-gesture path via SSH device prep) is left for on-device validation per the spec.

---

## Self-review notes

- **Spec coverage:** type detection via `.mp4` (Task 1 `isVideoItem`); `<video muted webkit-playsinline playsinline preload=auto>` + seek-to-offset + play (Task 2); `playbackRate` proportional controller + hard-seek/iOS-5 fallback (Task 1 `driftTick`, verified Task 4 steps 3–4); teardown on transition/STOP (Task 2 + Task 4 steps 5–6); `canplaythrough` readiness with error/stalled once-guard (Task 3); item shape `{id,file,duration}` unchanged; no server changes (correct). PAUSE/SEGMENT/SCRIPT/audio/authoring/scheduling deferred (correct).
- **Placeholder scan:** none — every step has concrete code/commands.
- **Type/name consistency:** `playback.video`/`videoIndex`/`driftTimer`, `isVideoItem`, `clearVideo`, `driftTick`, `startDriftLoop`, `HARD_SEEK_MS`, `showItem(i, offsetMs)` used consistently across tasks.
- **Known minor:** counting transient `stalled` as "settled" can mark green slightly early on a buffering hiccup; acceptable for this slice and tunable (constants/event list).
