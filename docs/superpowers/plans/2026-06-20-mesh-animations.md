# Mesh-Display Animations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a SCRIPT animation item span the whole calibrated wall (`scriptSpan: 'mesh'`) — each screen renders its affine slice of one global wall canvas — while keeping today's per-screen mirror behavior as the default.

**Architecture:** Server adds a per-item `scriptSpan` flag and a per-group `Display.meshGlobal` device-pixel canvas size (computed at calibration from the ArUco bbox + screen resolutions). For mesh SCRIPT items it attaches each calibrated client's normalized quad (`meshQuad`) + `meshGlobal` to the per-client PLAY payload. The iPad-1 client derives an affine (`mmMeshTransform`) mapping global wall coords → its canvas pixels, then draws the animation in global coords; an uncalibrated screen in a mesh group paints black; mirror items are unchanged. Coordination is free from the shared clock + shared `mmLoopItemSeed`.

**Tech Stack:** Python (aiohttp server, `mosaicmesh/` package), ES5-only browser JS (`js/animations.js`, `index.html` — Safari 5.1: no let/const/arrow/template-literals/Math.imul; `setTransform`/`fillRect` are fine), Alpine/modern JS admin (`js/timeline/modals/playlist-editor.js`), pytest (`tests/unit/`, run with `-c tests/pytest.ini`), Node `--test` (`tests/unit/js/`).

**Reference spec:** `docs/superpowers/specs/2026-06-20-mesh-animations-design.md`
**Branch:** `feature/mesh-animations` (off clean main; `mmLoopItemSeed` present).

---

## File Structure

- `mosaicmesh/state.py` — `MediaElement` gains `scriptSpan='mirror'`; `Display` gains `meshGlobal=None`. (Data model.)
- `mosaicmesh/render.py` — `_build_media_elements` copies `scriptSpan` from the item dict; `_media_item_payload` echoes it; `_per_client_items` attaches `meshQuad`+`meshGlobal` for mesh SCRIPT items on calibrated clients.
- `mosaicmesh/calibration.py` — `assign_group_bounding_boxes` additionally computes `Display.meshGlobal`.
- `js/animations.js` — new pure `mmMeshTransform` helper, exposed on `root`.
- `index.html` — `runScriptLoop` three-way branch (mesh / black / mirror).
- `js/timeline/modals/playlist-editor.js` — a Mirror/Mesh select for SCRIPT items.
- Tests: `tests/unit/test_mesh_animations.py` (server), `tests/unit/js/test_animations_mesh.js` (client math).

No persistence migration code is needed: `MediaElement`/`Display` defaults + `dict.get`/`getattr` fallbacks cover objects and playlist item dicts loaded from an older `settings.dat` (playlists persist as item *dicts*; `display.mediaElements` are rebuilt fresh each play). The playlist REST API stores `items` verbatim (`p.items = list(body["items"])`, `api/playlists.py`), so `scriptSpan` round-trips with no API change.

---

## Task 1: Data model + payload threading

**Files:**
- Modify: `mosaicmesh/state.py` (`MediaElement.__init__` ~line 73, `Display.__init__` ~line 31)
- Modify: `mosaicmesh/render.py` (`_build_media_elements` ~line 1155, `_media_item_payload` ~line 1085)
- Test: `tests/unit/test_mesh_animations.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_mesh_animations.py`:

```python
# tests/unit/test_mesh_animations.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import argparse
_orig = argparse.ArgumentParser.parse_args
class _MockArgs:
    Port = 3000
    Verbose = False
argparse.ArgumentParser.parse_args = lambda self, a=None, n=None: _MockArgs()
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

import pytest
from unittest.mock import MagicMock
from mosaicmesh.state import Settings, Display, Client, MediaElement, PlayMode
from mosaicmesh import render as R


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    server.socketmanager = MagicMock()
    yield server.settings
    server.settings = prev


def test_mediaelement_defaults_scriptspan_mirror():
    assert MediaElement().scriptSpan == 'mirror'


def test_display_defaults_meshglobal_none():
    assert Display().meshGlobal is None


def test_build_media_elements_reads_scriptspan():
    els = R._build_media_elements([
        {"id": "a", "file": "plasma", "playmode": "SCRIPT", "scriptSpan": "mesh"},
        {"id": "b", "file": "plasma", "playmode": "SCRIPT"},  # no scriptSpan -> mirror
    ])
    assert els[0].scriptSpan == "mesh"
    assert els[1].scriptSpan == "mirror"


def test_media_item_payload_echoes_scriptspan():
    me = MediaElement(); me.id = "a"; me.file = "plasma"
    me.playmode = PlayMode.SCRIPT; me.duration = 1.0; me.scriptSpan = "mesh"
    assert R._media_item_payload(me)["scriptSpan"] == "mesh"


def test_media_item_payload_scriptspan_defaults_mirror_on_old_object():
    me = MediaElement(); me.id = "a"; me.file = "plasma"
    me.playmode = PlayMode.SCRIPT; me.duration = 1.0
    del me.scriptSpan  # simulate an object from an older settings.dat
    assert R._media_item_payload(me)["scriptSpan"] == "mirror"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_mesh_animations.py -c tests/pytest.ini -v`
Expected: FAIL — `AttributeError: 'MediaElement' object has no attribute 'scriptSpan'` / `'Display' object has no attribute 'meshGlobal'` / `KeyError: 'scriptSpan'`.

- [ ] **Step 3: Add the fields**

In `mosaicmesh/state.py`, `MediaElement.__init__`, after `self.endEffect = None` (line ~75):

```python
        self.startEffect = None
        self.endEffect = None
        # 'mirror' (default, every screen draws the full animation) | 'mesh'
        # (animation spans the calibrated wall; each screen draws its slice).
        self.scriptSpan = 'mirror'
```

In `mosaicmesh/state.py`, `Display.__init__`, after `self.boundingBoxCenter = None` (line ~32):

```python
        self.boundingBox = None
        self.boundingBoxCenter = None
        # [GW, GH] device-pixel global wall canvas size for mesh animations,
        # computed at calibration (assign_group_bounding_boxes). None until
        # the group is calibrated; a mesh item with no meshGlobal -> black.
        self.meshGlobal = None
```

- [ ] **Step 4: Thread it through render.py**

In `mosaicmesh/render.py`, `_build_media_elements`, after `me.endEffect = item.get("endEffect")` (line ~1157):

```python
        me.endEffect = item.get("endEffect")
        me.scriptSpan = item.get("scriptSpan", "mirror")
```

In `mosaicmesh/render.py`, `_media_item_payload`, add `scriptSpan` to the returned dict (after the `endEffect` line ~1089):

```python
            "startEffect": getattr(me, "startEffect", None),
            "endEffect": getattr(me, "endEffect", None),
            "scriptSpan": getattr(me, "scriptSpan", "mirror")}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_mesh_animations.py -c tests/pytest.ini -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add mosaicmesh/state.py mosaicmesh/render.py tests/unit/test_mesh_animations.py
git commit -m "feat(mesh): MediaElement.scriptSpan + Display.meshGlobal data model

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Compute `Display.meshGlobal` at calibration

**Files:**
- Modify: `mosaicmesh/calibration.py` (`assign_group_bounding_boxes` ~lines 372-385)
- Test: `tests/unit/test_mesh_animations.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mesh_animations.py`:

```python
from mosaicmesh import calibration as CAL


def _client_with_quad(did, quad, dw, dh):
    c = Client(); c.displayID = did; c.measuredPerimeter = quad
    c.deviceWidth = dw; c.deviceHeight = dh
    return c


def test_meshglobal_preserves_bbox_aspect_and_scales(fresh_settings):
    # Two 100x100 (photo px) screens side by side -> bbox 200x100 (aspect 2:1).
    # Each screen is 1024x768 device px. quad bbox = 100x100 photo px.
    # ratio per screen = sqrt((1024/100)*(768/100)) = sqrt(78.64) ~= 8.868
    # GW = 200*k, GH = 100*k -> aspect stays 2:1.
    fresh_settings.clients["a"] = _client_with_quad(
        "G", [[0, 0], [100, 0], [100, 100], [0, 100]], 1024, 768)
    fresh_settings.clients["b"] = _client_with_quad(
        "G", [[100, 0], [200, 0], [200, 100], [100, 100]], 1024, 768)
    CAL.assign_group_bounding_boxes()
    d = fresh_settings.displays["G"]
    assert d.boundingBox == [0, 0, 200, 100]
    gw, gh = d.meshGlobal
    assert gw > 0 and gh > 0
    assert abs(gw / float(gh) - 2.0) < 0.02          # aspect preserved
    assert abs(gh - round(100 * 8.868)) <= 2          # scaled by median ratio


def test_meshglobal_fallback_when_no_resolution(fresh_settings):
    # Clients calibrated but with no device resolution -> k=1 -> meshGlobal == bbox dims.
    fresh_settings.clients["a"] = _client_with_quad(
        "G", [[0, 0], [100, 0], [100, 100], [0, 100]], 0, 0)
    CAL.assign_group_bounding_boxes()
    d = fresh_settings.displays["G"]
    bx, by, bw, bh = d.boundingBox
    assert d.meshGlobal == [bw, bh]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_mesh_animations.py -c tests/pytest.ini -k meshglobal -v`
Expected: FAIL — `d.meshGlobal` is `None` (not yet computed).

- [ ] **Step 3: Implement the computation**

In `mosaicmesh/calibration.py`, add `import statistics` and `import math` near the top imports if absent, then replace the body of `assign_group_bounding_boxes` (lines ~372-385) with:

```python
def assign_group_bounding_boxes():
    """Per display group, set boundingBox/boundingBoxCenter from the ArUco
    screens' quads (photo coords), plus meshGlobal = [GW, GH] — the device-pixel
    global wall canvas for mesh animations. GW/GH preserve the bbox aspect and
    scale by the median device-px-per-photo-px ratio across calibrated screens
    (one global unit ~= one device pixel). Call after calibration."""
    import server
    groups = {}
    members = {}
    for key, client in server.settings.clients.items():
        if client.measuredPerimeter is not None and client.displayID:
            groups.setdefault(client.displayID, []).append(client.measuredPerimeter)
            members.setdefault(client.displayID, []).append(client)
    for display_id, quads in groups.items():
        display = server.settings.displays.setdefault(display_id, server.Display())
        bbox = group_bounding_box(quads)
        display.boundingBox = bbox
        if not bbox:
            display.meshGlobal = None
            continue
        bx, by, bw, bh = bbox
        display.boundingBoxCenter = [bx + bw // 2, by + bh // 2]
        ratios = []
        for c in members.get(display_id, []):
            q = c.measuredPerimeter
            xs = [p[0] for p in q]; ys = [p[1] for p in q]
            qw = max(xs) - min(xs); qh = max(ys) - min(ys)
            dw = getattr(c, "deviceWidth", 0) or 0
            dh = getattr(c, "deviceHeight", 0) or 0
            if qw > 0 and qh > 0 and dw > 0 and dh > 0:
                ratios.append(math.sqrt((dw / float(qw)) * (dh / float(qh))))
        k = statistics.median(ratios) if ratios else 1.0
        display.meshGlobal = [int(round(bw * k)), int(round(bh * k))]
```

(Confirm `import math` and `import statistics` are present at the top of `mosaicmesh/calibration.py`; add whichever is missing.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_mesh_animations.py -c tests/pytest.ini -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/calibration.py tests/unit/test_mesh_animations.py
git commit -m "feat(mesh): compute Display.meshGlobal from bbox + screen resolution

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Attach `meshQuad`+`meshGlobal` in `_per_client_items`

**Files:**
- Modify: `mosaicmesh/render.py` (`_per_client_items` loop, before `items.append(item)` ~line 1205)
- Test: `tests/unit/test_mesh_animations.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_mesh_animations.py`:

```python
def _mesh_group(fresh_settings, did="G"):
    d = Display()
    d.boundingBox = [0, 0, 200, 100]
    d.meshGlobal = [1774, 887]
    me = MediaElement(); me.id = "a"; me.file = "plasma"
    me.playmode = PlayMode.SCRIPT; me.duration = 5.0; me.scriptSpan = "mesh"
    d.mediaElements = [me]
    fresh_settings.displays[did] = d
    return d


def test_per_client_items_mesh_attaches_quad_for_calibrated(fresh_settings):
    _mesh_group(fresh_settings)
    c = Client(); c.displayID = "G"
    c.measuredPerimeter = [[0, 0], [100, 0], [100, 100], [0, 100]]
    items = R._per_client_items(fresh_settings.displays["G"], "c1", c)
    assert items[0]["meshGlobal"] == [1774, 887]
    # left-half screen of a 200-wide bbox -> normalized x in {0, 0.5}
    q = items[0]["meshQuad"]
    assert q[0] == [0.0, 0.0] and q[1] == [0.5, 0.0]
    assert q[2] == [0.5, 1.0] and q[3] == [0.0, 1.0]


def test_per_client_items_mesh_black_for_uncalibrated(fresh_settings):
    _mesh_group(fresh_settings)
    c = Client(); c.displayID = "G"; c.measuredPerimeter = None  # uncalibrated
    items = R._per_client_items(fresh_settings.displays["G"], "c1", c)
    assert "meshQuad" not in items[0] and "meshGlobal" not in items[0]


def test_per_client_items_mirror_has_no_mesh_fields(fresh_settings):
    d = _mesh_group(fresh_settings)
    d.mediaElements[0].scriptSpan = "mirror"
    c = Client(); c.displayID = "G"
    c.measuredPerimeter = [[0, 0], [100, 0], [100, 100], [0, 100]]
    items = R._per_client_items(d, "c1", c)
    assert "meshQuad" not in items[0] and "meshGlobal" not in items[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_mesh_animations.py -c tests/pytest.ini -k per_client -v`
Expected: FAIL — `KeyError: 'meshGlobal'` (fields not attached yet).

- [ ] **Step 3: Implement the attachment**

In `mosaicmesh/render.py`, inside `_per_client_items`'s `for i, me in enumerate(display.mediaElements):` loop, change the tail from:

```python
        item = _media_item_payload(me)
        item["file"] = f
        items.append(item)
```

to:

```python
        item = _media_item_payload(me)
        item["file"] = f
        # Mesh animation geometry: a SCRIPT item set to span the wall gets this
        # client's quad (normalized into the group bbox) + the global canvas size,
        # but ONLY when the client is calibrated and the group has a bbox+meshGlobal.
        # Omitted otherwise -> the client goes black (mesh) or mirrors (mirror).
        if (me.playmode == PlayMode.SCRIPT
                and getattr(me, "scriptSpan", "mirror") == "mesh"
                and c.measuredPerimeter is not None
                and display.boundingBox and getattr(display, "meshGlobal", None)):
            bx, by, bw, bh = display.boundingBox
            item["meshQuad"] = [[(px - bx) / float(bw), (py - by) / float(bh)]
                                for (px, py) in c.measuredPerimeter]
            item["meshGlobal"] = list(display.meshGlobal)
        items.append(item)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_mesh_animations.py -c tests/pytest.ini -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Run the full unit suite (no regressions)**

Run: `python pytest_runner.py --unit`
Expected: PASS (existing suite still green).

- [ ] **Step 6: Commit**

```bash
git add mosaicmesh/render.py tests/unit/test_mesh_animations.py
git commit -m "feat(mesh): attach meshQuad+meshGlobal to per-client SCRIPT mesh items

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: `mmMeshTransform` client math + node tests

**Files:**
- Modify: `js/animations.js` (add function near `mmLoopItemSeed` ~line 42; expose on `root` ~line 619)
- Test: `tests/unit/js/test_animations_mesh.js` (new)

> `js/animations.js` is ES5-only (Safari 5.1). The node test file may use modern JS.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/js/test_animations_mesh.js`:

```js
/**
 * mmMeshTransform maps GLOBAL wall coords -> a screen's canvas pixels via an
 * affine fixed by 3 corner correspondences (canvas TL/TR/BL <-> the quad's
 * TL/TR/BL). The op is the cross-screen continuity guarantee for mesh
 * animations: adjacent screens map the shared global edge to their touching
 * canvas edges, so content flows continuously across the seam.
 */
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/animations.js');
const { mmMeshTransform } = globalThis;

// Apply {a,b,c,d,e,f} to a global point -> canvas pixel.
function apply(m, x, y) { return [m.a * x + m.c * y + m.e, m.b * x + m.d * y + m.f]; }
function near(p, q) { return Math.abs(p[0] - q[0]) < 1e-6 && Math.abs(p[1] - q[1]) < 1e-6; }

test('mmMeshTransform — full-bbox quad maps wall corners to canvas corners', () => {
  const m = mmMeshTransform([[0,0],[1,0],[1,1],[0,1]], 1000, 1000, 100, 100);
  assert.ok(near(apply(m, 0, 0), [0, 0]));        // TL
  assert.ok(near(apply(m, 1000, 0), [100, 0]));   // TR
  assert.ok(near(apply(m, 0, 1000), [0, 100]));   // BL
});

test('mmMeshTransform — right-half quad offsets+scales correctly', () => {
  const m = mmMeshTransform([[0.5,0],[1,0],[1,1],[0.5,1]], 1000, 1000, 100, 100);
  assert.ok(near(apply(m, 500, 0), [0, 0]));      // quad TL -> canvas (0,0)
  assert.ok(near(apply(m, 1000, 0), [100, 0]));   // quad TR -> canvas (100,0)
  assert.ok(near(apply(m, 500, 1000), [0, 100])); // quad BL -> canvas (0,100)
});

test('mmMeshTransform — adjacent screens are continuous across the seam', () => {
  const left  = mmMeshTransform([[0,0],[0.5,0],[0.5,1],[0,1]], 1000, 1000, 100, 100);
  const right = mmMeshTransform([[0.5,0],[1,0],[1,1],[0.5,1]], 1000, 1000, 100, 100);
  // The shared global edge is x=500. On the LEFT screen it maps to the right
  // edge (cx=100); on the RIGHT screen to the left edge (cx=0). Same global
  // point -> touching canvas edges -> continuous content.
  assert.ok(Math.abs(apply(left, 500, 300)[0] - 100) < 1e-6);
  assert.ok(Math.abs(apply(right, 500, 300)[0] - 0) < 1e-6);
});

test('mmMeshTransform — deterministic for same inputs', () => {
  const a = mmMeshTransform([[0.1,0.2],[0.6,0.15],[0.62,0.9],[0.08,0.85]], 1280, 960, 1024, 768);
  const b = mmMeshTransform([[0.1,0.2],[0.6,0.15],[0.62,0.9],[0.08,0.85]], 1280, 960, 1024, 768);
  assert.deepStrictEqual(a, b);
});

test('mmMeshTransform — degenerate quad (collinear edges) returns null', () => {
  // index-3 corner == index-0 corner -> left edge vector is zero -> det 0.
  assert.equal(mmMeshTransform([[0,0],[1,0],[1,0],[0,0]], 1000, 1000, 100, 100), null);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/unit/js/test_animations_mesh.js`
Expected: FAIL — `mmMeshTransform is not a function`.

- [ ] **Step 3: Implement the helper + exposure**

In `js/animations.js`, immediately after the `mmLoopItemSeed` function (it ends ~line 44), add:

```js
  // Affine mapping GLOBAL wall coords -> this screen's canvas pixels for mesh
  // animations. Fixed by 3 corner correspondences (canvas TL/TR/BL <-> the
  // quad's TL/TR/BL scaled into the GW x GH global canvas; BR is ignored, as
  // an affine is determined by 3 points). meshQuad: [[u,v]x4] normalized 0..1
  // in TL,TR,BR,BL order. Returns {a,b,c,d,e,f} for ctx.setTransform, or null
  // for a degenerate (collinear-edge) quad so the caller can go black.
  function mmMeshTransform(meshQuad, GW, GH, canvasW, canvasH) {
    var g0x = meshQuad[0][0] * GW, g0y = meshQuad[0][1] * GH;   // TL -> (0,0)
    var g1x = meshQuad[1][0] * GW, g1y = meshQuad[1][1] * GH;   // TR -> (W,0)
    var g3x = meshQuad[3][0] * GW, g3y = meshQuad[3][1] * GH;   // BL -> (0,H)
    var e1x = g1x - g0x, e1y = g1y - g0y;
    var e3x = g3x - g0x, e3y = g3y - g0y;
    var det = e1x * e3y - e3x * e1y;
    if (det > -1e-9 && det < 1e-9) { return null; }
    var W = canvasW, H = canvasH;
    var a = (W * e3y) / det;
    var c = (-W * e3x) / det;
    var b = (-H * e1y) / det;
    var d = (H * e1x) / det;
    var e = -(a * g0x + c * g0y);
    var f = -(b * g0x + d * g0y);
    return { a: a, b: b, c: c, d: d, e: e, f: f };
  }
```

Then, in the `root.*` exposure block (after `root.mmLoopItemSeed = mmLoopItemSeed;` ~line 619):

```js
  root.mmLoopItemSeed = mmLoopItemSeed;
  root.mmMeshTransform = mmMeshTransform;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/unit/js/test_animations_mesh.js`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the full JS suite (no regressions, ES5 guard)**

Run: `python pytest_runner.py --js`
Expected: PASS — all suites green, including `test_animations_rng.js`'s no-`Math.imul` guard scanning `js/animations.js`.

- [ ] **Step 6: Commit**

```bash
git add js/animations.js tests/unit/js/test_animations_mesh.js
git commit -m "feat(mesh): mmMeshTransform affine-from-quad helper + node tests

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: `runScriptLoop` three-way branch (mesh / black / mirror)

**Files:**
- Modify: `index.html` (`runScriptLoop`'s `if (animations[name]) { ... }` block ~lines 487-499)

> No automated test: `runScriptLoop` is the inline RAF/GoTime display loop. The sync-critical math (`mmMeshTransform`, `mmLoopItemSeed`) is unit-tested; correctness here is verified by reading the diff against the spec + the iPad-1 sign-off (Task 7). ES5 only (tabs, `var`, no let/const/arrow/template-literals).

- [ ] **Step 1: Read the current block to confirm exact text**

Read `index.html` around the `runScriptLoop` `frame()` body (~lines 481-499). Confirm the post-#47 block matches:

```js
			ctx.clearRect(0, 0, canvas.width, canvas.height);
			if (animations[name]) {
				var total = 0, t;
				for (t = 0; t < durations.length; t++) { total += durations[t]; }
				var loopIdx = (total > 0 && rawElapsed > 0) ? Math.floor(rawElapsed / total) : 0;
				var itemSeed = (typeof mmLoopItemSeed === 'function')
					? mmLoopItemSeed(playback.seed || 0, loopIdx, pos.index) : 0;
				animations[name](ctx, pos.offsetMs, canvas.width, canvas.height, GoTime.now(), itemSeed);
			}
```

If it differs materially, STOP and report what was found.

- [ ] **Step 2: Replace the block with the three-way branch**

Replace the `if (animations[name]) { ... }` block above with:

```js
			if (animations[name]) {
				var total = 0, t;
				for (t = 0; t < durations.length; t++) { total += durations[t]; }
				var loopIdx = (total > 0 && rawElapsed > 0) ? Math.floor(rawElapsed / total) : 0;
				var itemSeed = (typeof mmLoopItemSeed === 'function')
					? mmLoopItemSeed(playback.seed || 0, loopIdx, pos.index) : 0;
				var it = playback.items[pos.index];
				var span = (it && it.scriptSpan) || 'mirror';
				if (span === 'mesh') {
					// Mesh: render this screen's affine slice of the global wall canvas.
					var m = (it && it.meshQuad && it.meshGlobal && typeof mmMeshTransform === 'function')
						? mmMeshTransform(it.meshQuad, it.meshGlobal[0], it.meshGlobal[1], canvas.width, canvas.height)
						: null;
					if (m) {
						ctx.save();
						ctx.setTransform(m.a, m.b, m.c, m.d, m.e, m.f);
						animations[name](ctx, pos.offsetMs, it.meshGlobal[0], it.meshGlobal[1], GoTime.now(), itemSeed);
						ctx.restore();
					} else {
						// Uncalibrated screen in a mesh group (or degenerate quad): go black.
						ctx.setTransform(1, 0, 0, 1, 0, 0);
						ctx.fillStyle = '#000';
						ctx.fillRect(0, 0, canvas.width, canvas.height);
					}
				} else {
					// Mirror (default): full animation on this screen — unchanged behavior.
					animations[name](ctx, pos.offsetMs, canvas.width, canvas.height, GoTime.now(), itemSeed);
				}
			}
```

Notes for the implementer:
- `ctx.clearRect(...)` already runs above this block at the identity transform; `ctx.restore()` (mesh) / explicit `setTransform(1,0,0,1,0,0)` (black) leave the transform at identity for the next frame's clear.
- `playback.items[pos.index]` is the item carrying `scriptSpan`/`meshQuad`/`meshGlobal` from the PLAY payload. `playlistIndex` is unchanged.
- The `typeof mmMeshTransform === 'function'` guard tolerates an old cached `animations.js`; without it, mesh degrades to black, which is safe.

- [ ] **Step 3: ES5 + portability self-check**

Confirm the new lines use only `var` (no let/const/arrow/template-literals), tabs preserved, no `Math.imul`. As a sanity check nothing regressed in the shared module: `node --test tests/unit/js/test_animations_mesh.js` → PASS.

- [ ] **Step 4: Commit**

```bash
git add index.html
git commit -m "feat(mesh): runScriptLoop renders mesh slice / black / mirror per scriptSpan

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Playlist editor Mirror/Mesh toggle for SCRIPT items

**Files:**
- Modify: `js/timeline/modals/playlist-editor.js` (selected-item settings block ~lines 151-205)

> Modern admin JS (not iPad-1). No node unit test (DOM-bound); covered by the persisted-dict round-trip (Tasks 1/3) + manual check. `it.scriptSpan` is written into the playlist item dict and persists verbatim through `/api/playlists`.

- [ ] **Step 1: Add the toggle for animations**

In `js/timeline/modals/playlist-editor.js`, the selected-item block currently shows a "Play type" select only for media (`if (!isAnim(it)) { ... }`, ~lines 158-176). Add an `else` branch (for SCRIPT items) immediately after that `if` block closes (after line ~176), before the Duration `label`:

```js
      } else {
        // Animation (SCRIPT): mirror (every screen) vs mesh (span the wall).
        const wmWrap = document.createElement('label'); wmWrap.textContent = 'Wall mode ';
        const wm = document.createElement('select');
        const wopts = [['mirror', 'Mirror (same on every screen)'],
                       ['mesh', 'Mesh (span across the wall)']];
        for (const [val, label] of wopts) {
          const o = document.createElement('option');
          o.value = val; o.textContent = label;
          if ((it.scriptSpan || 'mirror') === val) o.selected = true;
          wm.appendChild(o);
        }
        wm.addEventListener('change', () => {
          if (wm.value === 'mesh') it.scriptSpan = 'mesh'; else delete it.scriptSpan;
          render();
        });
        wmWrap.appendChild(wm); box.appendChild(wmWrap);
        const wmHint = document.createElement('span'); wmHint.className = 'mm-ple-hint';
        wmHint.textContent = 'Mesh needs a calibrated group; uncalibrated screens go black';
        wmWrap.appendChild(wmHint);
      }
```

The existing media block is `if (!isAnim(it)) { ...play type... }` — change it to `if (!isAnim(it)) { ...play type... } else { ...wall mode... }` by attaching the `else` above to that `if`. (Default `mirror` deletes the key to keep item dicts clean; `mesh` sets `it.scriptSpan = 'mesh'`.)

- [ ] **Step 2: Manual verification**

With a dev server running, open the admin playlist editor, select a SCRIPT (animation) item, confirm a "Wall mode" dropdown appears (Mirror default), switch it to Mesh, Save, reopen the playlist, and confirm the selection persisted (the item dict has `scriptSpan: 'mesh'`). Media items still show "Play type", not "Wall mode".

- [ ] **Step 3: Commit**

```bash
git add js/timeline/modals/playlist-editor.js
git commit -m "feat(mesh): playlist editor Wall mode (mirror/mesh) toggle for animations

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: iPad-1 manual sign-off

**Files:** none (hardware verification).

> No code change. Automated tests can't judge the actual wall; only the calibrated OEB group can. The fleet is 23/24 calibrated, so this is exercisable now.

- [ ] **Step 1: Mesh spans the wall in lockstep**

On the calibrated OEB group, set a SCRIPT item (e.g. `plasma` or `starfield`) to **Mesh**, play on loop. Verify: the animation appears as ONE image spanning the wall (each screen showing its slice), screens are in lockstep, and the look re-randomizes each loop (per-loop reseed still applies in global coords).

- [ ] **Step 2: Uncalibrated screen is black**

Confirm the one uncalibrated screen renders **black** during the mesh item (not a stray full animation).

- [ ] **Step 3: Mirror unchanged**

Switch the same item to **Mirror**; confirm every screen shows the full animation as before. Record outcomes (and any seam/alignment notes) in the PR description.

---

## Self-Review

**Spec coverage:**
- `MediaElement.scriptSpan` default mirror + `Display.meshGlobal` + payload echo → Task 1. ✅
- `Display.meshGlobal` computed at calibration (median device-px-per-photo-px, aspect from bbox, k=1 fallback) → Task 2. ✅
- `_per_client_items` attaches `meshQuad`+`meshGlobal` for calibrated mesh SCRIPT clients only (uncalibrated → omitted → black; mirror → omitted) → Task 3. ✅
- `mmMeshTransform` affine-from-quad + degenerate→null, node-tested (full-bbox / right-half / seam continuity / determinism / degenerate) → Task 4. ✅
- `runScriptLoop` three-way mesh/black/mirror using `playback.items[pos.index]` → Task 5. ✅
- Editor Mirror/Mesh toggle for SCRIPT items → Task 6. ✅
- iPad-1 sign-off → Task 7. ✅
- Migration: covered by field defaults + `dict.get`/`getattr` fallbacks (no migrate code needed); noted in File Structure + tested (`..._defaults_..._on_old_object`). ✅

**Placeholder scan:** No TBD/TODO/"add validation"; every code step shows full code. ✅

**Type/name consistency:** `scriptSpan` (str 'mirror'|'mesh') and `meshGlobal` (`[GW,GH]`) consistent across state.py / render.py / calibration.py / payload / client. `meshQuad` is `[[u,v]×4]` 0..1 TL/TR/BR/BL everywhere. `mmMeshTransform(meshQuad, GW, GH, canvasW, canvasH)` signature identical in Task 4 definition, Task 4 tests, and Task 5 call site. ✅
