# FULL renders for uncalibrated display groups — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let FULL/SCRIPT-only playlists render, assign, and auto-PRECACHE for uncalibrated display groups, so modern/desktop groups get local caching without ArUco calibration.

**Architecture:** One pure predicate `_playlist_needs_calibration(items)` (true iff any item is SEGMENT/INDIVIDUAL) gates three calibration checks in the request/enqueue paths. The FULL encode already works without calibration (`_encode_group`); only the trigger/status gates block it. No change to the encode pipeline, the PRECACHE two-gate logic, or any calibrated-group behavior.

**Tech Stack:** Python 3.14, aiohttp, pytest (via `python -m pytest -c tests/pytest.ini`).

## Global Constraints

- Server-side Python only. No client JS, no ffmpeg/browser needed for tests.
- `import server` must stay side-effect-free (arg parsing only under `__main__`).
- The predicate operates on **raw playlist item dicts** (`item["playmode"]` is the string `"SEGMENT"`/`"INDIVIDUAL"`/`"FULL"`/`"SCRIPT"`), matching the existing `legacy.py:602` idiom `it.get("playmode") in ("SEGMENT","INDIVIDUAL")`.
- Do NOT change: `_encode_group`, `_client_is_push_eligible`/URL-rewrite two-gate, `_group_is_calibrated` itself, or any calibrated-group behavior (calibrated groups still render every playmode).
- A "needs calibration" playlist on an uncalibrated group keeps the EXISTING refusal responses verbatim (`RENDER` → `{"status":"ERROR","error":"group not calibrated"}`; `ASSIGN_PLAYLIST` → `status "NOT_CALIBRATED"`).
- Run tests with `python -m pytest <file> -c tests/pytest.ini -q`.

---

### Task 1: `_playlist_needs_calibration` predicate

**Files:**
- Modify: `mosaicmesh/render.py` (add helper next to `_group_is_calibrated`, which is at `render.py:976`)
- Test: `tests/unit/test_render_gating.py`

**Interfaces:**
- Produces: `_playlist_needs_calibration(items) -> bool` where `items` is a list of raw playlist-item dicts. Re-exported through `server` automatically (server re-imports all of `mosaicmesh.render`), so both `render._playlist_needs_calibration` and `server._playlist_needs_calibration` resolve.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_render_gating.py`:

```python
def test_needs_calibration_full_only_is_false():
    assert R._playlist_needs_calibration([{"playmode": "FULL"}]) is False

def test_needs_calibration_script_only_is_false():
    assert R._playlist_needs_calibration([{"playmode": "SCRIPT"}]) is False

def test_needs_calibration_full_plus_script_is_false():
    assert R._playlist_needs_calibration(
        [{"playmode": "FULL"}, {"playmode": "SCRIPT"}]) is False

def test_needs_calibration_any_segment_is_true():
    assert R._playlist_needs_calibration(
        [{"playmode": "FULL"}, {"playmode": "SEGMENT"}]) is True

def test_needs_calibration_any_individual_is_true():
    assert R._playlist_needs_calibration([{"playmode": "INDIVIDUAL"}]) is True

def test_needs_calibration_empty_is_false():
    assert R._playlist_needs_calibration([]) is False

def test_needs_calibration_none_items_is_false():
    assert R._playlist_needs_calibration(None) is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/unit/test_render_gating.py -c tests/pytest.ini -q -k needs_calibration`
Expected: FAIL with `AttributeError: module 'mosaicmesh.render' has no attribute '_playlist_needs_calibration'`

- [ ] **Step 3: Implement** — add to `mosaicmesh/render.py` immediately after the `_group_is_calibrated` function (ends near line 982):

```python
def _playlist_needs_calibration(items):
    """True iff the playlist contains a SEGMENT or INDIVIDUAL item — the two
    playmodes that warp per-screen and read measuredPerimeter/boundingBox.
    FULL (mirror) and SCRIPT need no calibration, so a FULL/SCRIPT-only playlist
    can render on an uncalibrated group. Operates on raw playlist item dicts."""
    return any((it or {}).get("playmode") in ("SEGMENT", "INDIVIDUAL")
               for it in (items or []))
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/unit/test_render_gating.py -c tests/pytest.ini -q -k needs_calibration`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render.py tests/unit/test_render_gating.py
git commit -m "feat(render): _playlist_needs_calibration predicate (SEGMENT/INDIVIDUAL only)"
```

---

### Task 2: `RENDER` handler gate (spec trigger point 1)

**Files:**
- Modify: `mosaicmesh/websocket/legacy.py` — the `from mosaicmesh.render import (...)` block (line 65-74, which imports `_group_is_calibrated`) and the `RENDER` handler (line 589)
- Test: `tests/unit/test_render_gating.py`

**Interfaces:**
- Consumes: `_playlist_needs_calibration(items)` from Task 1.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_render_gating.py`. Add this shared helper first (near `_calibrated_group_with_seg_playlist` at line 38):

```python
def _uncalibrated_group_with_full_playlist(settings):
    d = Display()   # no boundingBox, no calibrated client -> _group_is_calibrated False
    settings.displays["U1"] = d
    pl = Playlist(); pl.name = "F"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "FULL"}]
    settings.playlists["F"] = pl
    return d, pl
```

Then the tests:

```python
def test_render_full_only_on_uncalibrated_group_queues(fresh_settings, monkeypatch):
    d, pl = _uncalibrated_group_with_full_playlist(fresh_settings)
    calls = []
    monkeypatch.setattr("mosaicmesh.render_queue.enqueue",
                        lambda name, did: calls.append((name, did)))
    import jsonpickle
    out = jsonpickle.decode(server.msg_response(
        {"REQUEST": "RENDER", "PAYLOAD": {"displayID": "U1", "name": "F"},
         "SRC": "a", "DEST": "SRV"}, _MockSession()))
    assert out["PAYLOAD"]["status"] == "QUEUED"
    assert calls == [("F", "U1")]

def test_render_segment_on_uncalibrated_group_still_refused(fresh_settings, monkeypatch):
    d = Display(); fresh_settings.displays["U1"] = d      # uncalibrated
    pl = Playlist(); pl.name = "S"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["S"] = pl
    called = []
    monkeypatch.setattr("mosaicmesh.render_queue.enqueue",
                        lambda name, did: called.append((name, did)))
    import jsonpickle
    out = jsonpickle.decode(server.msg_response(
        {"REQUEST": "RENDER", "PAYLOAD": {"displayID": "U1", "name": "S"},
         "SRC": "a", "DEST": "SRV"}, _MockSession()))
    assert out["PAYLOAD"]["status"] == "ERROR"
    assert out["PAYLOAD"]["error"] == "group not calibrated"
    assert called == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/unit/test_render_gating.py -c tests/pytest.ini -q -k "render_full_only_on_uncalibrated or render_segment_on_uncalibrated"`
Expected: `test_render_full_only_...` FAILS (currently returns `ERROR "group not calibrated"`, not `QUEUED`). `test_render_segment_...` passes already.

- [ ] **Step 3: Implement** — two edits in `mosaicmesh/websocket/legacy.py`:

(a) Add `_playlist_needs_calibration,` to the `from mosaicmesh.render import (...)` block (line 65-74), on its own line next to `_group_is_calibrated,`.

(b) Change the `RENDER` handler guard (currently line 589):

```python
        elif not _group_is_calibrated(display_id):
            response["PAYLOAD"] = {"status": "ERROR", "error": "group not calibrated"}
```

to:

```python
        elif (_playlist_needs_calibration(server.settings.playlists[name].items)
              and not _group_is_calibrated(display_id)):
            response["PAYLOAD"] = {"status": "ERROR", "error": "group not calibrated"}
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/unit/test_render_gating.py -c tests/pytest.ini -q -k "render_full_only_on_uncalibrated or render_segment_on_uncalibrated"`
Expected: PASS (2 passed)

- [ ] **Step 5: Regression + commit**

Run: `python -m pytest tests/unit/test_render_gating.py -c tests/pytest.ini -q` (all pass) then:

```bash
git add mosaicmesh/websocket/legacy.py tests/unit/test_render_gating.py
git commit -m "feat(render): RENDER handler allows FULL-only playlists on uncalibrated groups"
```

---

### Task 3: `ASSIGN_PLAYLIST` handler gate (third gate — found during grounding)

**Files:**
- Modify: `mosaicmesh/websocket/legacy.py` — the `ASSIGN_PLAYLIST` handler (line 646)
- Test: `tests/unit/test_render_gating.py`

**Interfaces:**
- Consumes: `_playlist_needs_calibration(items)` (imported into `legacy.py` in Task 2).

**Context:** the spec named two trigger points; grounding found a third calibration gate in `ASSIGN_PLAYLIST` (`legacy.py:646`) that returns `NOT_CALIBRATED` for any renderable playlist on an uncalibrated group. Without this, an operator cannot assign a FULL playlist to the Desktop group. Same predicate, same intent.

- [ ] **Step 1: Write the failing test** — append to `tests/unit/test_render_gating.py`:

```python
def test_assign_full_only_on_uncalibrated_group_not_blocked(fresh_settings, monkeypatch):
    d, pl = _uncalibrated_group_with_full_playlist(fresh_settings)
    # A FULL-only playlist on an uncalibrated group must NOT be NOT_CALIBRATED.
    # It will be RENDER_REQUIRED (no render yet) or ok, but never NOT_CALIBRATED.
    import jsonpickle
    out = jsonpickle.decode(server.msg_response(
        {"REQUEST": "ASSIGN_PLAYLIST", "PAYLOAD": {"displayID": "U1", "name": "F"},
         "SRC": "a", "DEST": "SRV"}, _MockSession()))
    assert out["PAYLOAD"]["status"] != "NOT_CALIBRATED"
    assert out["PAYLOAD"]["status"] in ("RENDER_REQUIRED", "ok")

def test_assign_segment_on_uncalibrated_group_still_not_calibrated(fresh_settings):
    d = Display(); fresh_settings.displays["U1"] = d      # uncalibrated
    pl = Playlist(); pl.name = "S"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["S"] = pl
    import jsonpickle
    out = jsonpickle.decode(server.msg_response(
        {"REQUEST": "ASSIGN_PLAYLIST", "PAYLOAD": {"displayID": "U1", "name": "S"},
         "SRC": "a", "DEST": "SRV"}, _MockSession()))
    assert out["PAYLOAD"]["status"] == "NOT_CALIBRATED"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_render_gating.py -c tests/pytest.ini -q -k "assign_full_only_on_uncalibrated or assign_segment_on_uncalibrated"`
Expected: `test_assign_full_only_...` FAILS (currently `NOT_CALIBRATED`). `test_assign_segment_...` passes already.

- [ ] **Step 3: Implement** — change the `ASSIGN_PLAYLIST` guard (currently line 646):

```python
            if has_renderable and not _group_is_calibrated(display_id):
                status = "NOT_CALIBRATED"
```

to:

```python
            if (has_renderable and _playlist_needs_calibration(pl.items)
                    and not _group_is_calibrated(display_id)):
                status = "NOT_CALIBRATED"
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/unit/test_render_gating.py -c tests/pytest.ini -q -k "assign_full_only_on_uncalibrated or assign_segment_on_uncalibrated"`
Expected: PASS (2 passed)

- [ ] **Step 5: Regression + commit**

Run: `python -m pytest tests/unit/test_render_gating.py -c tests/pytest.ini -q` (all pass) then:

```bash
git add mosaicmesh/websocket/legacy.py tests/unit/test_render_gating.py
git commit -m "feat(render): ASSIGN_PLAYLIST allows FULL-only playlists on uncalibrated groups"
```

---

### Task 4: auto-enqueue on save (spec trigger point 2) — rename + widen

**Files:**
- Modify: `mosaicmesh/render.py:1060` (`enqueue_playlist_for_calibrated_groups`)
- Modify: `mosaicmesh/render_queue.py:90` (caller)
- Modify: `tests/unit/test_render_queue.py:74` (monkeypatch target)
- Test: `tests/unit/test_render_gating.py`

**Interfaces:**
- Consumes: `_playlist_needs_calibration(items)` from Task 1.
- Produces: `enqueue_playlist_for_eligible_groups(playlist_name)` (renamed from `enqueue_playlist_for_calibrated_groups`).

- [ ] **Step 1: Write the failing test** — append to `tests/unit/test_render_gating.py`:

```python
def test_enqueue_eligible_includes_uncalibrated_for_full_only(fresh_settings, monkeypatch):
    d, pl = _uncalibrated_group_with_full_playlist(fresh_settings)   # group "U1", playlist "F"
    enq = []
    monkeypatch.setattr("mosaicmesh.render_queue.enqueue",
                        lambda name, did: enq.append((name, did)))
    R.enqueue_playlist_for_eligible_groups("F")
    assert ("F", "U1") in enq

def test_enqueue_eligible_skips_uncalibrated_for_segment(fresh_settings, monkeypatch):
    d = Display(); fresh_settings.displays["U1"] = d      # uncalibrated
    pl = Playlist(); pl.name = "S"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["S"] = pl
    enq = []
    monkeypatch.setattr("mosaicmesh.render_queue.enqueue",
                        lambda name, did: enq.append((name, did)))
    R.enqueue_playlist_for_eligible_groups("S")
    assert ("S", "U1") not in enq
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_render_gating.py -c tests/pytest.ini -q -k "enqueue_eligible"`
Expected: FAIL with `AttributeError: module 'mosaicmesh.render' has no attribute 'enqueue_playlist_for_eligible_groups'`

- [ ] **Step 3: Implement** — three edits.

(a) In `mosaicmesh/render.py`, rename `enqueue_playlist_for_calibrated_groups` (line 1060) to `enqueue_playlist_for_eligible_groups` and widen the group loop. Replace the whole function with:

```python
def enqueue_playlist_for_eligible_groups(playlist_name):
    """For a saved renderable playlist, set QUEUED + enqueue a render against
    every ELIGIBLE group: a calibrated group (any playmode), OR any group when
    the playlist needs no calibration (FULL/SCRIPT-only). N/A playlists (no
    renderable items) are skipped."""
    import server
    from mosaicmesh import render_queue
    pl = server.settings.playlists.get(playlist_name)
    if pl is None:
        return
    elements = _build_media_elements(pl.items)
    if not any(_is_renderable(me) for me in elements):
        return
    needs_cal = _playlist_needs_calibration(pl.items)
    changed = False
    for did, display in server.settings.displays.items():
        if needs_cal and not _group_is_calibrated(did):
            continue
        if is_playlist_ready(playlist_name, did):
            continue   # already current — don't re-encode
        _set_render_state(display, playlist_name, RENDER_QUEUED,
                          token=render_token(elements, did))
        render_queue.enqueue(playlist_name, did)
        changed = True
    if changed:
        _broadcast_renders_changed(force=True)
```

(b) In `mosaicmesh/render_queue.py:90`, change the caller:

```python
    R.enqueue_playlist_for_calibrated_groups(playlist_name)
```

to:

```python
    R.enqueue_playlist_for_eligible_groups(playlist_name)
```

(c) In `tests/unit/test_render_queue.py:74`, change the monkeypatch target string:

```python
    monkeypatch.setattr("mosaicmesh.render.enqueue_playlist_for_calibrated_groups",
                        lambda name: calls.append(name))
```

to:

```python
    monkeypatch.setattr("mosaicmesh.render.enqueue_playlist_for_eligible_groups",
                        lambda name: calls.append(name))
```

- [ ] **Step 4: Run to verify they pass** — the new tests, the debounce test, and no dangling old name:

Run: `python -m pytest tests/unit/test_render_gating.py tests/unit/test_render_queue.py -c tests/pytest.ini -q -k "enqueue_eligible or debounce"`
Expected: PASS
Run: `grep -rn "enqueue_playlist_for_calibrated_groups" server.py mosaicmesh tests` → no matches (ignore `__pycache__`).

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render.py mosaicmesh/render_queue.py tests/unit/test_render_queue.py tests/unit/test_render_gating.py
git commit -m "feat(render): auto-enqueue FULL-only playlists for uncalibrated groups (rename ->eligible)"
```

---

## Final verification (after all tasks)

- [ ] `python -c "import server; print('OK')"`
- [ ] `python -m pytest tests/unit/ -c tests/pytest.ini -q` — all green.
- [ ] `grep -rn "enqueue_playlist_for_calibrated_groups" server.py mosaicmesh tests` — empty (rename complete).

## Self-Review (plan author)

- **Spec coverage:** predicate (Task 1) = spec "The predicate"; RENDER gate (Task 2) = spec trigger point 1; auto-enqueue rename+widen (Task 4) = spec trigger point 2; ASSIGN gate (Task 3) = the third gate grounding revealed beyond the spec's two (flagged to the user). Decisions honored: mixed playlists refused (predicate returns True if ANY SEGMENT/INDIVIDUAL → keeps the calibrated requirement); both auto + manual covered.
- **Placeholder scan:** none — every code step carries complete code; tests carry real assertions; the one grep step is a concrete bounded check.
- **Type consistency:** `_playlist_needs_calibration(items)->bool` used identically in Tasks 2/3/4; the renamed `enqueue_playlist_for_eligible_groups` name matches across render.py def, render_queue.py caller, and the test monkeypatch string.
- **Unchanged guardrails:** `_encode_group`, the PRECACHE two-gate, and `_group_is_calibrated` are untouched; calibrated groups still take the same path (needs_cal True or the group is calibrated → identical behavior).
