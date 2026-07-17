# meshCells De-duplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop sending the display's mesh warp grid (`meshCells`) once per playlist item; send it once per PLAY/PREPARE/PRELOAD message so the per-client payload no longer overflows the mmws 128 KB websocket rx buffer, restoring the real-websocket transport fleet-wide.

**Architecture:** `meshCells` is display-level geometry duplicated onto every item by `_per_client_items` (render.py). Move it to the message PAYLOAD level in all per-client broadcasters; on the client, hydrate it back onto each item at message intake via a small pure helper, leaving every existing `it.meshCells` render site untouched.

**Tech Stack:** Python 3 (aiohttp/sockjs server, `mosaicmesh/render.py`, `mosaicmesh/api/discovery.py`); ES5 client JS (`index.html`, `js/mesh-viewport.js`) — 1st-gen iPad / Safari 5.1, no `let`/`const`/arrow/`class`; Node `--test` for JS units; pytest for Python (`tests/pytest.ini`).

## Global Constraints

- **Client JS is ES5 only** — no `let`/`const`, arrow functions, template literals, `class`, `Promise`, `fetch`. `index.html` + `js/mesh-viewport.js` must stay Safari-5.1 safe.
- **Pure client helpers are exported via `root.mmX = mmX`** in an IIFE `(function(root){…})(typeof window!=='undefined'?window:globalThis)` so classic `<script>` and Node `await import()` both work. Call sites guard with `typeof mmX === 'function'`.
- **jsonpickle back-ref hazard:** a Python list attached to two places in ONE message is emitted as `{"py/id":N}`, which the iPad's `JSON.parse` cannot read. `meshCells` must appear at exactly ONE place per message (the payload), built as a fresh nested list.
- **Run tests via runners:** `python pytest_runner.py --js` (Node) and `python -m pytest <file> -c tests/pytest.ini` (Python). Never bare `pytest`.
- **`meshCells` content is unchanged** — only its position in the message moves. The mesh render must look identical.

---

### Task 1: Client helper `mmHydrateMeshCells` (pure, node-tested)

**Files:**
- Modify: `js/mesh-viewport.js` (add function + export, before the closing `})(…)` at line ~65–67)
- Test: `tests/unit/js/test_mesh_hydrate.js` (create)

**Interfaces:**
- Produces: `mmHydrateMeshCells(items, meshCells)` — sets `items[i].meshCells = meshCells` for every item when `meshCells` is truthy; no-op when `meshCells` is falsy or `items` is falsy. Returns `items`. Exposed as `root.mmHydrateMeshCells`.

- [ ] **Step 1: Write the failing test** — create `tests/unit/js/test_mesh_hydrate.js`:

```js
/**
 * mmHydrateMeshCells re-attaches the de-duplicated meshCells grid (sent once at the
 * PLAY/PREPARE/PRELOAD payload level) onto each item, so the per-item render sites that
 * read it.meshCells are unchanged. Pure; no DOM.
 */
import { test } from 'node:test';
import assert from 'node:assert';
await import('../../../js/mesh-viewport.js');
const g = globalThis;

test('mmHydrateMeshCells: attaches meshCells to every item (shared ref)', () => {
  const cells = [[[0, 0], [1, 0], [1, 1], [0, 1]]];
  const items = [{ file: 'a' }, { file: 'b' }, { file: 'c' }];
  const out = g.mmHydrateMeshCells(items, cells);
  assert.strictEqual(out, items);
  for (let i = 0; i < items.length; i++) assert.strictEqual(items[i].meshCells, cells);
});

test('mmHydrateMeshCells: no-op when meshCells is null/undefined', () => {
  const items = [{ file: 'a' }];
  g.mmHydrateMeshCells(items, null);
  assert.strictEqual('meshCells' in items[0], false);
  g.mmHydrateMeshCells(items, undefined);
  assert.strictEqual('meshCells' in items[0], false);
});

test('mmHydrateMeshCells: tolerates falsy/empty items', () => {
  assert.doesNotThrow(() => g.mmHydrateMeshCells(undefined, [[[0, 0]]]));
  assert.doesNotThrow(() => g.mmHydrateMeshCells(null, [[[0, 0]]]));
  assert.deepStrictEqual(g.mmHydrateMeshCells([], [[[0, 0]]]), []);
});
```

- [ ] **Step 2: Run the test, verify it fails**

Run: `node --test tests/unit/js/test_mesh_hydrate.js`
Expected: FAIL — `g.mmHydrateMeshCells is not a function`.

- [ ] **Step 3: Implement the helper** — in `js/mesh-viewport.js`, add before `root.mmStampSprite = mmStampSprite;` (line ~65):

```js
  // Re-attach the de-duplicated mesh warp grid (sent ONCE at the PLAY/PREPARE/PRELOAD
  // payload level) onto each playlist item, so the per-item render sites reading
  // it.meshCells stay unchanged. The server stopped sending meshCells per-item because
  // a per-item copy blew the per-client message past the mmws 128KB ws rx buffer. The
  // shared reference is safe: meshCells is read-only in the render path. No-op when the
  // payload carries no grid (non-mesh playlists). Returns items.
  function mmHydrateMeshCells(items, meshCells) {
    if (items && meshCells) {
      for (var i = 0; i < items.length; i++) { items[i].meshCells = meshCells; }
    }
    return items;
  }
```

And add the export next to the existing ones (line ~65–66):

```js
  root.mmHydrateMeshCells = mmHydrateMeshCells;
```

- [ ] **Step 4: Run the test, verify it passes**

Run: `node --test tests/unit/js/test_mesh_hydrate.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full JS suite (no regressions)**

Run: `python pytest_runner.py --js`
Expected: all pass (existing count + 3).

- [ ] **Step 6: Commit**

```bash
git add js/mesh-viewport.js tests/unit/js/test_mesh_hydrate.js
git commit -m "feat(mesh): mmHydrateMeshCells helper — re-attach de-duped grid client-side"
```

---

### Task 2: Server — send `meshCells` once at payload level

**Files:**
- Modify: `mosaicmesh/render.py` — add `_mesh_cells_payload(display)` near `_per_client_items` (~line 1371); remove the per-item `meshCells` attach (~lines 1442–1444); add `"meshCells"` to the PAYLOAD in `_broadcast_per_client_play` (~1459), `_broadcast_per_client_preload` (~1478), `_begin_prepare` (~1596), `_prepare_unsynced_clients` (~1642)
- Modify: `mosaicmesh/api/discovery.py` — add `"meshCells"` to the PRELOAD + PLAY payloads in `sync_new_client_to_group` (~lines 235–237)
- Test: `tests/unit/test_meshcells_dedup.py` (create)

**Interfaces:**
- Consumes: `display.meshCells` (list of cells, each a list of `[x,y]` points) — unchanged.
- Produces: `_mesh_cells_payload(display) -> list | None` (fresh nested `float` list, or `None`); every per-client PLAY/PREPARE/PRELOAD PAYLOAD gains a `"meshCells"` key; `_per_client_items` items NO LONGER contain a `meshCells` key.

- [ ] **Step 1: Write the failing test** — create `tests/unit/test_meshcells_dedup.py`:

```python
"""meshCells is sent ONCE at the payload level, not per item — a per-item copy
overflowed the mmws 128KB ws rx buffer and regressed the fleet to XHR."""
import types
import numpy as np
import pytest
import server
from mosaicmesh import render, calibration
from mosaicmesh.state import Display, Client, MediaElement, PlayMode


def _mesh_display_and_client():
    d = Display()
    d.displayID = "G"
    d.boundingBox = [0, 0, 1000, 800]
    d.meshGlobal = [1000, 800]
    d.meshGlobalRect = [1000, 800]
    d.meshGrid = [2, 2]
    d.meshCells = [[[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]],
                   [[0.5, 0.0], [1.0, 0.0], [1.0, 0.5], [0.5, 0.5]]]
    # three mesh SCRIPT items
    els = []
    for i in range(3):
        me = MediaElement()
        me.id = "i%d" % i
        me.file = "bouncingBalls"
        me.playmode = PlayMode.SCRIPT
        me.scriptSpan = "mesh"
        me.duration = 10
        els.append(me)
    d.mediaElements = els
    c = Client()
    c.displayID = "G"
    c.measuredPerimeter = np.array([[0, 0], [500, 0], [500, 400], [0, 400]])
    c.meshCellQuad = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]]
    return d, c


def test_per_client_items_no_meshcells(monkeypatch):
    monkeypatch.setattr(calibration, "MESH_RECTIFY", True)
    d, c = _mesh_display_and_client()
    items = render._per_client_items(d, "k", c)
    assert len(items) == 3
    for it in items:
        assert "meshCells" not in it, "meshCells must NOT be attached per-item"
        # small per-item mesh fields stay
        assert "meshGlobal" in it and "meshQuad" in it


def test_mesh_cells_payload_is_fresh_grid():
    d, _ = _mesh_display_and_client()
    mc = render._mesh_cells_payload(d)
    assert mc == [[[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]],
                  [[0.5, 0.0], [1.0, 0.0], [1.0, 0.5], [0.5, 0.5]]]
    assert mc is not d.meshCells  # fresh list, not the stored ref


def test_mesh_cells_payload_none_without_grid():
    d = Display(); d.meshCells = None
    assert render._mesh_cells_payload(d) is None


def test_payload_size_is_O_one_grid(monkeypatch):
    """The grid appears ONCE regardless of item count (guards against regression)."""
    import json
    monkeypatch.setattr(calibration, "MESH_RECTIFY", True)
    d, c = _mesh_display_and_client()
    items = render._per_client_items(d, "k", c)
    grid = render._mesh_cells_payload(d)
    payload = {"items": items, "meshCells": grid}
    blob = json.dumps(payload)
    # one grid's worth of cell coordinates, not three
    assert blob.count("0.5") <= len(json.dumps(grid).split("0.5")) + 2
    assert json.dumps(items).count("meshCells") == 0
```

- [ ] **Step 2: Run the test, verify it fails**

Run: `python -m pytest tests/unit/test_meshcells_dedup.py -c tests/pytest.ini -v`
Expected: FAIL — `_per_client_items` still attaches `meshCells` (first test) and `render._mesh_cells_payload` does not exist (`AttributeError`).

- [ ] **Step 3: Add `_mesh_cells_payload`** — in `mosaicmesh/render.py`, immediately BEFORE `def _per_client_items(display, key, c):` (~line 1371):

```python
def _mesh_cells_payload(display):
    """The display's mesh warp grid as a FRESH nested float list, or None. Sent ONCE at
    the PLAY/PREPARE/PRELOAD payload level (de-duplicated) instead of on every item: a
    per-item copy made the per-client message ~83KB -> ~166KB SockJS-framed, overflowing
    the mmws 128KB ws rx buffer and dropping the fleet back to XHR. Fresh list so
    jsonpickle can't emit a {"py/id"} back-ref the iPad JSON.parse can't read."""
    mc = getattr(display, "meshCells", None)
    if not mc:
        return None
    return [[[float(pt[0]), float(pt[1])] for pt in cell] for cell in mc]
```

- [ ] **Step 4: Remove the per-item `meshCells` attach** — in `_per_client_items`, DELETE these three lines (~1442–1444) inside the `me.playmode == PlayMode.SCRIPT and scriptSpan == "mesh"` rectify branch, keeping the `meshGrid` line above them:

```python
                if getattr(display, "meshCells", None):
                    item["meshCells"] = [[[float(pt[0]), float(pt[1])] for pt in cell]
                                         for cell in display.meshCells]
```

(Leave `item["meshGlobal"]` and the `item["meshGrid"]` lines intact.)

- [ ] **Step 5: Add `meshCells` to each broadcaster PAYLOAD** — five edits, all the same shape: compute the grid once per function (before the client loop), add it to each payload.

In `_broadcast_per_client_play` (~1457):
```python
def _broadcast_per_client_play(display_id, display):
    """Send each client its own PLAY with its per-client (warped) media URLs."""
    mesh_cells = _mesh_cells_payload(display)
    for key, c in _group_clients(display_id):
        broadcast_to_client(key, {"REQUEST": "PLAY",
            "PAYLOAD": {"startEpoch": display.playStartEpoch,
                        "items": _per_client_items(display, key, c), "loop": display.loop,
                        "seed": getattr(display, "playSeed", 0),
                        "meshCells": mesh_cells}})
```

In `_broadcast_per_client_preload` (~1478), after `for key, c in _group_clients(display_id):` compute once above the loop and add the key:
```python
    mesh_cells = _mesh_cells_payload(display)
    for key, c in _group_clients(display_id):
        items = _per_client_items(display, key, c)
        broadcast_to_client(key, {"REQUEST": "PRELOAD",
                                  "PAYLOAD": {"items": items, "meshCells": mesh_cells}})
```

In `_begin_prepare` (the PREPARE at ~1596) add `"meshCells": _mesh_cells_payload(display)` to that PAYLOAD dict (grid is small to recompute; a single client-loop here may inline it):
```python
        broadcast_to_client(key, {
            "REQUEST": "PREPARE",
            "PAYLOAD": {"prepareId": display.prepareId,
                        "items": _per_client_items(display, key, c), "loop": display.loop,
                        "meshCells": _mesh_cells_payload(display)}})
```

In `_prepare_unsynced_clients` (the PREPARE at ~1642) likewise:
```python
            broadcast_to_client(key, {
                "REQUEST": "PREPARE",
                "PAYLOAD": {"prepareId": prepare_id,
                            "items": _per_client_items(display, key, c),
                            "meshCells": _mesh_cells_payload(display)}})
```

In `mosaicmesh/api/discovery.py` `sync_new_client_to_group` (~235), compute once and add to BOTH messages:
```python
    items = _per_client_items(display, client_key, client)
    mesh_cells = _mesh_cells_payload(display)
    broadcast_to_client(client_key, {"REQUEST": "PRELOAD",
                                     "PAYLOAD": {"items": items, "meshCells": mesh_cells}})
    broadcast_to_client(client_key, {
        "REQUEST": "PLAY",
        "PAYLOAD": {"startEpoch": display.playStartEpoch, "items": items, "loop": display.loop,
                    "meshCells": mesh_cells,
```
(add `_mesh_cells_payload` to the existing `from mosaicmesh.render import …` line at the top of discovery.py.)

- [ ] **Step 6: Run the tests, verify they pass**

Run: `python -m pytest tests/unit/test_meshcells_dedup.py -c tests/pytest.ini -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Run the unit suite (no regressions)**

Run: `python pytest_runner.py --unit`
Expected: the reliably-passing unit set still passes.

- [ ] **Step 8: Commit**

```bash
git add mosaicmesh/render.py mosaicmesh/api/discovery.py tests/unit/test_meshcells_dedup.py
git commit -m "fix(mesh): send meshCells once at payload level (was per-item) — stops mmws ws overflow"
```

---

### Task 3: Client — hydrate `meshCells` at message intake

**Files:**
- Modify: `index.html` — PRELOAD handler (~1637), PREPARE handler (~1736), PLAY handler (~1751)

**Interfaces:**
- Consumes: `mmHydrateMeshCells(items, meshCells)` from Task 1 (loaded via `<script src="/js/mesh-viewport.js">`, already in `index.html`).

- [ ] **Step 1: Hydrate in the PLAY handler** — in `index.html`, in the `else if(data_obj.REQUEST == "PLAY")` block, replace the items assignment (~1751):

```js
				playback.items = data_obj.PAYLOAD.items || [];
```
with:
```js
				playback.items = (typeof mmHydrateMeshCells === 'function')
					? mmHydrateMeshCells(data_obj.PAYLOAD.items || [], data_obj.PAYLOAD.meshCells)
					: (data_obj.PAYLOAD.items || []);
```

- [ ] **Step 2: Hydrate in the PREPARE handler** — in the `else if(data_obj.REQUEST == "PREPARE")` block, replace the items assignment (~1736) with the identical pattern:

```js
				playback.items = (typeof mmHydrateMeshCells === 'function')
					? mmHydrateMeshCells(data_obj.PAYLOAD.items || [], data_obj.PAYLOAD.meshCells)
					: (data_obj.PAYLOAD.items || []);
```

- [ ] **Step 3: Hydrate in the PRELOAD handler** — in the `if(data_obj.REQUEST == "PRELOAD")` block, replace the `mediaSequence` assignment (~1637):

```js
				mediaSequence = data_obj.PAYLOAD.items || [];
```
with:
```js
				mediaSequence = (typeof mmHydrateMeshCells === 'function')
					? mmHydrateMeshCells(data_obj.PAYLOAD.items || [], data_obj.PAYLOAD.meshCells)
					: (data_obj.PAYLOAD.items || []);
```

- [ ] **Step 4: Syntax sanity-check** — confirm no ES5 violations were introduced (the edits use only `var`-free ternaries and existing globals):

Run: `node -e "require('fs').readFileSync('index.html','utf8')" && echo "read ok"`
(Full parse isn't possible for an HTML file; visually confirm the three edits match the pattern and use no `let`/`const`/arrow.)
Expected: `read ok`, edits use only ES5.

- [ ] **Step 5: Commit**

```bash
git add index.html
git commit -m "feat(client): hydrate payload-level meshCells onto items at PLAY/PREPARE/PRELOAD"
```

---

### Task 4: Deploy + on-wall verification

**Files:** none (operational).

- [ ] **Step 1: Restart the server** (picks up `render.py`/`discovery.py`). Detached so it survives the session:

```
Start-Process python -ArgumentList "server.py","-p","3000","-v" -WorkingDirectory <repo> -RedirectStandardError mm_server.err -RedirectStandardOutput mm_server.out
```
- [ ] **Step 2: Verify config survived the restart** — `GET /api/displays`: OEB Sign 1 present with its clients; `settings.dat` decode shows `meshGlobal`/`meshCells` intact (a restart has previously appeared to reset group config — confirm before proceeding).

- [ ] **Step 3: RELOAD the group** so webclips re-fetch `index.html` + `js/mesh-viewport.js`:

Send `RELOAD {displayID:"OEB Sign 1"}` over SockJS (`tools/send_command.py RELOAD "OEB Sign 1"`).

- [ ] **Step 4: Play the mesh playlist** — ASSIGN_PLAYLIST + PLAY `All Effects Demo (Mesh)` on OEB Sign 1.

- [ ] **Step 5: Verify the transport recovered** — in the server access log, `xhr_send`→~0 and `/websocket` 101 upgrades climb toward the online count; on 2–3 devices, `mmws.log`: `overflow=0`, `OPEN` stops looping. Confirm the mesh animation renders correctly (screens show their warped portion, not black).

- [ ] **Step 6: Final commit / PR prep** — the three feature commits are on `feat/ws-meshcells-dedup`; open a PR referencing the spec.

---

## Self-Review

**Spec coverage:** Part A of the spec = de-dupe `meshCells` server-side + hydrate client-side + tests + on-wall verify. Task 1 = client helper + node test; Task 2 = server de-dup (all 5 broadcasters — superset of the spec's "PLAY+PRELOAD", corrected during planning) + Python size guard; Task 3 = client wiring at all 3 intake handlers; Task 4 = deploy + verify. Part B (dynamic rx buffer) explicitly deferred, not in this plan. ✔

**Placeholder scan:** all steps carry real code/commands; no TBD/TODO/"handle edge cases". ✔

**Type consistency:** `mmHydrateMeshCells(items, meshCells)` signature identical in Task 1 (def), Task 3 (calls); `_mesh_cells_payload(display)` identical in Task 2 (def + all call sites). Payload key `"meshCells"` consistent server↔client. ✔
