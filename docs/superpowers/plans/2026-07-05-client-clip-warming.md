# Client-Side Clip Warming — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make video-playlist clip transitions seamless on modern display devices by pre-loading the next clip on an idle `<video>` and flipping to it on advance, while iPad-1 keeps today's single-element behavior.

**Architecture:** A new ES5 client module `js/video-buffer.js` owns the `<video>` element(s) and exposes one "active" element. Modern devices (server flag `warmable=true`) get two elements ping-ponged; iPad-1 (`warmable=false`) gets one and the warm path is a no-op. The manager keeps `playback.pvid` pointed at the active element so the existing sync loop / render / HUD are unchanged. The server derives `warmable` from its device fingerprint and sends it per-client via a new `CONFIG` message.

**Tech Stack:** ES5 client JS (IIFE + `root.X` global, iPad-1 / iOS 5.1 compatible), Node `--test` unit tests, Python/aiohttp server, pytest.

## Global Constraints

- **Client JS touching the display client must be ES5** — no `let`/`const`/arrow/`class`/template-literals/`Promise`/`fetch`. Use the IIFE + `root.X = X` pattern (`root = typeof window !== 'undefined' ? window : globalThis`), matching `js/mesh-viewport.js`.
- **iPad-1 must remain byte-for-byte today's behavior when `warmable=false`** — verified constraint: the A4 cannot sustain two concurrent inline decodes (2026-07-05 test). Never create a second `<video>` when not warmable.
- **Warming is smoothness only** — do NOT touch the sync loop (`driftTick`) or GoTime; they target `playback.pvid`/`vbuf.active()` and must keep working unchanged.
- **`warmable` defaults to `false`** if the flag/message is absent (safe = today's behavior).
- Node JS tests run via `python pytest_runner.py --js` (or `node --test tests/unit/js/*.js`). Python tests via `python pytest_runner.py --unit`.

---

### Task 1: `nextPlaylistIndex` pure helper + node tests

**Files:**
- Create: `js/video-buffer.js`
- Test: `tests/unit/js/test_video_buffer.js`

**Interfaces:**
- Produces: global `nextPlaylistIndex(curIndex, itemCount, loop)` → integer next index, or `-1` when there is no next item.

- [ ] **Step 1: Write the failing test** — create `tests/unit/js/test_video_buffer.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/video-buffer.js');
const nextPlaylistIndex = globalThis.nextPlaylistIndex;

test('nextPlaylistIndex — middle advances by one', () => {
  assert.strictEqual(nextPlaylistIndex(0, 3, false), 1);
  assert.strictEqual(nextPlaylistIndex(1, 3, false), 2);
});
test('nextPlaylistIndex — last item, no loop => -1 (nothing to warm)', () => {
  assert.strictEqual(nextPlaylistIndex(2, 3, false), -1);
});
test('nextPlaylistIndex — last item, loop => wraps to 0', () => {
  assert.strictEqual(nextPlaylistIndex(2, 3, true), 0);
});
test('nextPlaylistIndex — single item, loop => -1 (no swap needed)', () => {
  assert.strictEqual(nextPlaylistIndex(0, 1, true), -1);
});
test('nextPlaylistIndex — empty / bad input => -1', () => {
  assert.strictEqual(nextPlaylistIndex(0, 0, true), -1);
  assert.strictEqual(nextPlaylistIndex(-1, 3, true), -1);
});
```

- [ ] **Step 2: Run it — expect FAIL** (`Cannot read properties of undefined` / not a function):

Run: `node --test tests/unit/js/test_video_buffer.js`
Expected: FAIL (`globalThis.nextPlaylistIndex` is undefined).

- [ ] **Step 3: Create `js/video-buffer.js` with the helper:**

```js
// js/video-buffer.js — client-side clip warming. ES5 only (iPad-1 / iOS 5.1). Loaded by index.html
// AND node-tested (attaches to window in the browser, globalThis in node).
(function (root) {
  'use strict';

  // Next playlist index to warm. Returns -1 when there is no distinct next item
  // (last item + no loop; single-item playlist; bad input). Mirrors the wrap
  // semantics of playlistIndex() in index.html.
  function nextPlaylistIndex(curIndex, itemCount, loop) {
    if (!(itemCount > 1) || !(curIndex >= 0) || curIndex >= itemCount) { return -1; }
    if (curIndex < itemCount - 1) { return curIndex + 1; }
    return loop ? 0 : -1;   // last item: wrap only if looping
  }

  root.nextPlaylistIndex = nextPlaylistIndex;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
```

- [ ] **Step 4: Run it — expect PASS:**

Run: `node --test tests/unit/js/test_video_buffer.js`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit:**

```bash
git add js/video-buffer.js tests/unit/js/test_video_buffer.js
git commit -m "feat(warming): nextPlaylistIndex helper + node tests"
```

---

### Task 2: `makeVideoBuffer` manager + node tests

**Files:**
- Modify: `js/video-buffer.js`
- Test: `tests/unit/js/test_video_buffer.js`

**Interfaces:**
- Consumes: `nextPlaylistIndex` (Task 1).
- Produces: global `makeVideoBuffer(deps)` where `deps = { mkVideo: function()->el, mount: function(el), isVideo: function(file)->bool }`. Returns an object:
  - `setup(warmable)` — create 1 (or 2 if warmable) elements via `deps.mkVideo`, mount them, hide the buffer.
  - `active()` — the current live element.
  - `warmNext(item)` — if warmable + `deps.isVideo(item.file)` + buffer not already holding it: `buffer.src = item.file; buffer.load()`. No-op otherwise.
  - `flipTo(file)` — if warmable + buffer is warm with `file`: reveal buffer, hide old active, swap roles, return the (warm, loaded) new active element. Else return `null` (caller cold-loads on `active()`).

- [ ] **Step 1: Add failing tests** to `tests/unit/js/test_video_buffer.js`:

```js
const makeVideoBuffer = globalThis.makeVideoBuffer;
function mkMock() {
  return { _src: null, loaded: 0, played: 0, style: {},
    get src() { return this._src; }, set src(v) { this._src = v; },
    load: function () { this.loaded++; }, play: function () { this.played++; return null; },
    pause: function () {} };
}
function makeDeps() {
  var made = [];
  return { made: made,
    mkVideo: function () { var m = mkMock(); made.push(m); return m; },
    mount: function () {}, isVideo: function (f) { return /\.mp4$/i.test(f); } };
}

test('setup(false) => one element, active is it, no buffer', () => {
  var d = makeDeps(); var vb = makeVideoBuffer(d); vb.setup(false);
  assert.strictEqual(d.made.length, 1);
  assert.strictEqual(vb.active(), d.made[0]);
});
test('setup(true) => two elements', () => {
  var d = makeDeps(); var vb = makeVideoBuffer(d); vb.setup(true);
  assert.strictEqual(d.made.length, 2);
});
test('warmNext no-op when not warmable', () => {
  var d = makeDeps(); var vb = makeVideoBuffer(d); vb.setup(false);
  vb.warmNext({ file: 'b.mp4' });
  assert.strictEqual(d.made.length, 1);        // no buffer created/loaded
});
test('warmNext loads next on buffer, idempotent, skips non-video', () => {
  var d = makeDeps(); var vb = makeVideoBuffer(d); vb.setup(true);
  var active = vb.active(), buffer = d.made[1];
  vb.warmNext({ file: 'b.mp4' });
  assert.strictEqual(buffer.src, 'b.mp4'); assert.strictEqual(buffer.loaded, 1);
  vb.warmNext({ file: 'b.mp4' });               // idempotent
  assert.strictEqual(buffer.loaded, 1);
  vb.warmNext({ file: 'anim.script' });         // non-video => skip
  assert.strictEqual(buffer.src, 'b.mp4');
  assert.strictEqual(active.loaded, 0);         // active untouched
});
test('flipTo warm => swaps active to the warm buffer; returns it', () => {
  var d = makeDeps(); var vb = makeVideoBuffer(d); vb.setup(true);
  var first = vb.active(), buf = d.made[1];
  vb.warmNext({ file: 'b.mp4' });
  var el = vb.flipTo('b.mp4');
  assert.strictEqual(el, buf);                  // returned the warm element
  assert.strictEqual(vb.active(), buf);         // active is now the warm one
  assert.notStrictEqual(vb.active(), first);
});
test('flipTo cold (buffer not warm / not warmable) => null', () => {
  var d = makeDeps(); var vb = makeVideoBuffer(d); vb.setup(true);
  assert.strictEqual(vb.flipTo('never-warmed.mp4'), null);
  var d2 = makeDeps(); var vb2 = makeVideoBuffer(d2); vb2.setup(false);
  assert.strictEqual(vb2.flipTo('b.mp4'), null); // not warmable
});
```

- [ ] **Step 2: Run — expect FAIL** (`makeVideoBuffer` undefined):

Run: `node --test tests/unit/js/test_video_buffer.js`
Expected: FAIL on the new tests.

- [ ] **Step 3: Add `makeVideoBuffer`** to `js/video-buffer.js` (inside the IIFE, before the `root.` exports):

```js
  // Video-buffer manager. Owns 1 (legacy) or 2 (warmable) <video> elements and
  // exposes one "active" element. deps injects element creation/mount + isVideo so
  // this is node-testable without a DOM.
  function makeVideoBuffer(deps) {
    var warmable = false, activeEl = null, bufferEl = null, bufferSrc = null;
    // Both elements stay display:block so the hidden buffer still BUFFERS/decodes (a
    // display:none video won't warm). Hidden = opacity 0, behind (zIndex 1).
    function show(el) { if (el && el.style) { el.style.display = 'block'; el.style.opacity = '1'; el.style.zIndex = '2'; } }
    function hide(el) { if (el && el.style) { el.style.display = 'block'; el.style.opacity = '0'; el.style.zIndex = '1'; } }
    return {
      setup: function (warm) {
        warmable = !!warm;
        activeEl = deps.mkVideo(); deps.mount(activeEl); show(activeEl);
        if (warmable) { bufferEl = deps.mkVideo(); deps.mount(bufferEl); hide(bufferEl); }
        else { bufferEl = null; }
        bufferSrc = null;
      },
      active: function () { return activeEl; },
      warmNext: function (item) {
        if (!warmable || !bufferEl || !item || !deps.isVideo(item.file)) { return; }
        if (bufferSrc === item.file) { return; }          // already warm
        bufferEl.src = item.file; bufferSrc = item.file;
        try { bufferEl.load(); } catch (e) {}
      },
      flipTo: function (file) {
        if (!warmable || !bufferEl || bufferSrc !== file) { return null; }  // cold-fallback
        show(bufferEl); hide(activeEl);
        try { activeEl.pause(); } catch (e) {}
        var t = activeEl; activeEl = bufferEl; bufferEl = t;   // old active becomes the free buffer
        bufferSrc = null;
        return activeEl;                                       // warm + loaded; caller seeks+plays
      }
    };
  }
```

Add to the exports block: `root.makeVideoBuffer = makeVideoBuffer;`

- [ ] **Step 4: Run — expect PASS** (all tests):

Run: `node --test tests/unit/js/test_video_buffer.js`
Expected: PASS.

- [ ] **Step 5: Commit:**

```bash
git add js/video-buffer.js tests/unit/js/test_video_buffer.js
git commit -m "feat(warming): makeVideoBuffer manager (1-vs-2 element, warm flip) + node tests"
```

---

### Task 3: Server `warmable` derivation + `CONFIG` message

**Files:**
- Modify: `mosaicmesh/websocket/legacy.py` (REGISTER handler, ~line 188+)
- Test: `tests/unit/test_warmable.py`

**Interfaces:**
- Produces: `client_warmable(client)` → `bool` (module-level in `legacy.py`); a `broadcast_to_client(client_key, {"REQUEST": "CONFIG", "PAYLOAD": {"warmable": bool}})` sent at the end of REGISTER handling.

- [ ] **Step 1: Write the failing test** — create `tests/unit/test_warmable.py`:

```python
import types
from mosaicmesh.websocket.legacy import client_warmable

def _c(**kw):
    c = types.SimpleNamespace(deviceBrand="", deviceType="", osName="", osVersion="", engine="")
    for k, v in kw.items(): setattr(c, k, v)
    return c

def test_legacy_ipad_not_warmable():
    # iOS 5 Safari 5 / old WebKit => the transplant can't double-decode
    assert client_warmable(_c(deviceType="tablet", osName="iOS", osVersion="5.1", engine="WebKit")) is False

def test_modern_ipad_warmable():
    assert client_warmable(_c(deviceType="tablet", osName="iOS", osVersion="15.0", engine="WebKit")) is True

def test_desktop_warmable():
    assert client_warmable(_c(deviceType="desktop", osName="Windows", osVersion="10", engine="Blink")) is True

def test_missing_fields_default_warmable():
    # unknown device => assume modern (warmable); the client no-ops safely if it can't
    assert client_warmable(_c()) is True
```

- [ ] **Step 2: Run — expect FAIL** (ImportError: no `client_warmable`):

Run: `python -m pytest tests/unit/test_warmable.py -c tests/pytest.ini -v`
Expected: FAIL (import error).

- [ ] **Step 3: Add `client_warmable`** to `mosaicmesh/websocket/legacy.py` (module level, near the top imports). It reuses the existing legacy-iPad classification where possible and adds an explicit iOS/Safari ≤5 guard:

```python
def client_warmable(client):
    """True if the device can pre-decode a second <video> for clip warming.
    False for the legacy iPad-1 class (iOS/Safari <= 5 / old WebKit), which cannot
    sustain two concurrent inline decodes (verified 2026-07-05). Unknown => True
    (assume modern; the client warm path no-ops safely if it can't actually warm)."""
    def _major(v):
        try:
            return int(str(v or "").split(".")[0])
        except (ValueError, TypeError):
            return None
    os_name = (getattr(client, "osName", "") or "").lower()
    os_major = _major(getattr(client, "osVersion", ""))
    if os_name in ("ios", "iphone os") and os_major is not None and os_major <= 5:
        return False
    return True
```

- [ ] **Step 4: Send `CONFIG` per-client at the end of the REGISTER branch.** In `legacy.py`, inside `elif msg["REQUEST"] == "REGISTER":` after `auto_configure_client` runs and `client_key` is known, add:

```python
        # Tell the client whether it may double-buffer video (clip warming). Per-client
        # (device-derived), so it can't ride the group-wide PREPARE payload.
        broadcast_to_client(client_key, {"REQUEST": "CONFIG",
                                         "PAYLOAD": {"warmable": client_warmable(client)}})
```

(Confirm `broadcast_to_client` and `client_key` are in scope in that branch — `broadcast_to_client` is already imported at legacy.py:46; `client_key` is the REGISTER handler's client identifier.)

- [ ] **Step 5: Run — expect PASS:**

Run: `python -m pytest tests/unit/test_warmable.py -c tests/pytest.ini -v`
Expected: PASS (4 tests). Then `python pytest_runner.py --unit` to confirm no regressions.

- [ ] **Step 6: Commit:**

```bash
git add mosaicmesh/websocket/legacy.py tests/unit/test_warmable.py
git commit -m "feat(warming): server derives warmable from fingerprint + sends per-client CONFIG"
```

---

### Task 4: Wire warming into the display client (`index.html`)

**Files:**
- Modify: `index.html` (script include ~line 25; `getPersistentVideo()` ~182-227; `renderPlayback` video branch ~1016-1056; message dispatch ~1488+)

**Interfaces:**
- Consumes: `makeVideoBuffer`, `nextPlaylistIndex` (globals from `js/video-buffer.js`).
- Produces: `playback.vbuf` (the manager); `playback.warmable`; `playback.pvid` kept pointed at `vbuf.active()`.

This task is integration glue — verified manually/on-device (no unit test). Keep each edit minimal; the sync loop and all `var v = playback.pvid` reads must keep working because `playback.pvid` always equals the active element.

- [ ] **Step 1: Load the module.** In `index.html` after line 25 (`<script src="/js/mesh-viewport.js"></script>`):

```html
  <script src="/js/video-buffer.js"></script>
```

- [ ] **Step 2: Create the buffer in `getPersistentVideo()`.** Replace the body of the `if (!playback.pvid) { ... }` block (index.html ~185-225) so element creation goes through `vbuf`, and `playback.pvid` tracks the active element. Keep ALL the existing per-element setup (the `position:fixed` full-size style, `preload='auto'`, muted, listeners) by moving it into the `mkVideo` factory so BOTH elements get identical setup:

```js
	function getPersistentVideo() {
		if (!playback.vbuf) {
			// mkVideo = the EXACT per-element setup the single-element block used (moved out of
			// getPersistentVideo verbatim), so both elements are identical to today's pvid. The
			// vbuf's setup() owns display/opacity/zIndex, so mkVideo does NOT set display:none.
			var mkVideo = function () {
				var v = document.createElement('video');
				v.muted = VIDEO_MUTED; // unmuted only when audio is wanted (needs a gesture)
				v.setAttribute('webkit-playsinline', '');
				v.setAttribute('playsinline', '');
				v.preload = 'auto';
				v.style.position = 'fixed';
				v.style.left = '0'; v.style.top = '0';
				v.style.width = '100%'; v.style.height = '100%';
				v.style.background = '#000';
				v.style.zIndex = '1';
				v.addEventListener('playing', function () { playback.activated = true; hideTapStart(); });
				v.addEventListener('error', function () {
					try {
						var s = '' + (v.currentSrc || v.src || '');
						if (s.indexOf('127.0.0.1') !== -1 && typeof sock !== 'undefined' && sock !== null) {
							sock.send(generateMessage("SRV", "ANNOUNCE_CACHE_MODE", {"mode": "none"}));
							if (typeof dbg === 'function') { dbg("cache-local-fail"); }
						}
					} catch (e) { /* best-effort */ }
				});
				return v;
			};
			playback.vbuf = makeVideoBuffer({
				mkVideo: mkVideo,
				mount: function (el) { document.body.appendChild(el); },
				isVideo: isVideoItem
			});
			playback.vbuf.setup(!!playback.warmable);
			playback.pvid = playback.vbuf.active();
		}
		return playback.pvid;
	}
```

This is the current index.html:186-225 body moved verbatim into `mkVideo` (minus the `display='none'` line, which `vbuf.setup`/`show`/`hide` now own). Delete the old `if (!playback.pvid) { ... }` body it replaces.

- [ ] **Step 3: Handle the `CONFIG` message.** In the message dispatch (index.html, alongside the `PREPARE`/`PLAY`/`PRELOAD` branches, ~1488+), add:

```js
			else if (data_obj.REQUEST == "CONFIG")
			{
				playback.warmable = !!(data_obj.PAYLOAD && data_obj.PAYLOAD.warmable);
				// If the buffer was already built single-element, upgrade to two elements now.
				if (playback.vbuf) { playback.vbuf.setup(playback.warmable); playback.pvid = playback.vbuf.active(); }
			}
```

Also add `warmable: false, vbuf: null,` to the `playback` object literal (near line 127 where `pvid: null` is declared).

- [ ] **Step 4: Warm the next clip + flip on advance.** In `renderPlayback`'s video branch (index.html ~1016-1056), make two edits:

(a) After `playback.videoIndex = i;` (~line 1033), warm the next item:

```js
				// Warm the next clip on the idle buffer (modern only; no-op on iPad-1).
				var _ni = nextPlaylistIndex(i, playback.items.length, playback.loop);
				if (_ni >= 0) { playback.vbuf.warmNext(playback.items[_ni]); }
```

(b) Replace the cold clip-change block (~1048-1056, the `if (item.file !== playback.videoSrc) { ... v.src = item.file; ... v.load(); }`) with a warm-flip-first version:

```js
				if (item.file !== playback.videoSrc) {
					if (playback.boundaryTimer) { clearTimeout(playback.boundaryTimer); playback.boundaryTimer = null; }
					playback.videoSrc = item.file;
					var warm = playback.vbuf.flipTo(item.file);   // non-null => already-loaded warm element
					if (warm) {
						playback.pvid = warm; v = warm;
						playback.video = v; playback.currentEl = v;
						seekAndPlay();                              // warm: no src/load, just seek+play (instant)
					} else {
						v.src = item.file;                          // cold path (unchanged): load then seek+play
						var once = function () { v.removeEventListener('loadedmetadata', once); seekAndPlay(); };
						v.addEventListener('loadedmetadata', once);
						try { v.load(); } catch (e) {}
					}
				} else if (v.readyState >= 1) {
```

- [ ] **Step 5: Verify — modern browser (Chrome/modern Safari).** Serve the app (`python server.py`), point a modern browser at a display client with a ≥2-video playlist. Confirm:
  - `?tdbg` HUD shows sync unaffected (drift/err numbers as before).
  - The clip→clip transition is **visually seamless** (no black flash / frozen frame).
  - DevTools: two `<video>` elements exist; the inactive one's `src` is the *next* clip during playback.

- [ ] **Step 6: Verify — iPad-1 (regression).** Load the display client on the iPad-1 (mmvideo transplant). Confirm:
  - Exactly **one** `<video>` element exists (no second element).
  - Playback + advance behave exactly as today (no new glitch, no crash).
  - Server sent `CONFIG {warmable:false}` (check the server log / client console).

- [ ] **Step 7: Run the full JS + unit suites, then commit:**

```bash
python pytest_runner.py --js
python pytest_runner.py --unit
git add index.html
git commit -m "feat(warming): wire video-buffer into display client (warm swap on modern, no-op on iPad-1)"
```

---

## Notes for the implementer

- **DRY:** `nextPlaylistIndex` is the single source of "what's next"; do not re-derive the wrap logic in index.html.
- **YAGNI:** two elements only (no pool); no MSE; no iPad-1 warming.
- **The cold path must stay identical** — warming only *adds* a warm-flip fast path in front of it. If `flipTo` returns null, behavior is exactly today's.
- **`playback.pvid === vbuf.active()` is an invariant** — every place that assigns a new active element must also update `playback.pvid`. Steps 2 and 4b are the only assignment sites.
