# Auto-Render Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make rendering an automatic per-(playlist × group) asset so operators stage content without manually assigning/rendering, can't assign/play/schedule an un-rendered renderable playlist, get warned + auto-re-rendered on recalibration, and see fleet-wide render progress.

**Architecture:** A per-`Display` render registry (`Display.renders[playlistName] = {token, state, updatedAt, error, percent, eta, startedAt}`) becomes the source of truth for "is this playlist ready for this group". A bounded async render queue decouples "needs render" from ffmpeg work; a per-playlist debounce coalesces saves. The existing `render_group_async` encode body is extracted so any playlist's elements can be rendered against a group without disturbing the live `mediaElements`. Triggers (save, calibrate, delete, failure-retry) feed the queue; gates (PLAY, ASSIGN_PLAYLIST, schedules, default) consult the registry. A throttled `RENDERS_CHANGED` broadcast + `GET /api/renders` drive a fleet-wide Render Status panel.

**Tech Stack:** Python 3 / aiohttp / asyncio / jsonpickle (server); Alpine.js 3 + native ES modules (admin UI, no build step); pytest (`tests/pytest.ini`) + Node `--test` + Playwright.

**Spec:** `docs/superpowers/specs/2026-06-13-auto-render-model-design.md`

**Phasing note:** This is one cohesive subsystem; tasks are grouped A–G and are ordered so each phase leaves the tree green. Phases A–B are pure additions (no behavior change). C–D flip behavior. E–F surface it. G verifies.

**Test runner reminders (from CLAUDE.md):**
- Python unit: `python -m pytest tests/unit/<file> -c tests/pytest.ini -v` (NEVER bare `pytest`).
- JS unit: `node --test tests/unit/js/<file>.js`.
- E2e: `node tests/e2e/run.js <substr>` (needs dev server on `MM_BASE_URL`).
- `import server` requires runtime deps; tests use the `parse_args` monkeypatch boilerplate shown in `tests/unit/test_api_playlists.py`.

---

## File Structure

**New files**
- `mosaicmesh/render_queue.py` — bounded async render queue (concurrency cap, idempotent enqueue, in-flight tracking) + per-playlist save debounce. One responsibility: *scheduling* renders, not performing them.
- `mosaicmesh/api/renders.py` — `GET /api/renders` (fleet-wide snapshot of the registry + queue depth).
- `tests/unit/test_render_token.py` — `render_token`/`compute_render_token` stability + readiness predicate.
- `tests/unit/test_render_registry.py` — registry state helpers + migration backfill + boot revalidation.
- `tests/unit/test_render_queue.py` — enqueue idempotency, concurrency cap, debounce coalescing.
- `tests/unit/test_render_triggers.py` — save/calibrate/delete/retry enqueue behavior.
- `tests/unit/test_render_gating.py` — PLAY/ASSIGN/schedule gates reject non-ready.
- `tests/unit/test_api_renders.py` — `GET /api/renders` shape.
- `tests/e2e/render-model.spec` (a `node tests/e2e/run.js render-model` spec) — Play Now hides not-ready; Fleet has no Render now; Content shows status.

**Modified files**
- `mosaicmesh/render.py` — generalize token; extract encode body `_encode_group`; add `render_playlist_for_group_async`, `is_playlist_ready`, registry helpers, ffmpeg progress parsing; sync `display.renderedToken` in `_apply_playlist`.
- `mosaicmesh/state.py` — `Display.renders = {}` + migration backfill + render-state constants import site.
- `mosaicmesh/websocket/legacy.py` — `SAVE_PLAYLIST` debounce hook; repurpose `RENDER` handler to failure-retry; gate `PLAY`/`ASSIGN_PLAYLIST` via registry; `DELETE_PLAYLIST` cleanup.
- `mosaicmesh/api/playlists.py` — create/update → debounce hook; delete → cleanup.
- `mosaicmesh/api/schedules.py` — create/update → readiness validation.
- `server.py` — register `/api/renders` route; `calibrate()` flow returns will-render list + enqueues; `evaluate_schedules` gates on registry; boot revalidation call.
- `js/timeline/api.js` — `getRenders()`.
- `js/timeline/store.js` — `renders` slice, hydration, `setRenders()`, ready helper.
- `js/timeline/timeline/sockjs-status.js` — `RENDERS_CHANGED` case.
- `js/timeline/modals/play-now.js` — filter to ready, read PLAY response.
- `js/timeline/fleet/fleet-view.js` + `admin.html` — remove Render now; read-only readiness.
- `js/timeline/content/content-view.js` + `admin.html` — per-playlist render badge + Retry; global Render Status panel.
- `js/timeline/modals/calibration.js` — recalibrate warning list.

---

## Phase A — Core registry + token (pure additions)

### Task 1: Generalize `render_token(media_elements, display_id)`

**Files:**
- Modify: `mosaicmesh/render.py:295-319` (`compute_render_token`)
- Test: `tests/unit/test_render_token.py`

**Context:** `compute_render_token(display_id)` hashes `display.mediaElements` + bounding box + per-client quads. We need to hash *any* playlist's elements against a group so we can compute a token for an un-applied playlist. Keep the exact tuple shape and `encode_ver` string so existing `renderedToken` values stay valid.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_render_token.py
"""render_token / compute_render_token stability + readiness predicate."""
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
from mosaicmesh.state import Settings, Display, Client, MediaElement, PlayMode
from mosaicmesh import render as R


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev


def _seg_elem(i=0, file="/media/server/videos/a.mp4"):
    me = MediaElement()
    me.id = i
    me.file = file
    me.duration = 5000
    me.playmode = PlayMode.SEGMENT
    return me


def _calibrated_group(settings, did="G1"):
    d = Display()
    d.boundingBox = [0, 0, 100, 100]
    settings.displays[did] = d
    c = Client()
    c.displayID = did
    c.deviceWidth = 1024
    c.deviceHeight = 768
    c.measuredPerimeter = [0, 0, 10, 0, 10, 10, 0, 10]
    settings.clients["c1"] = c
    return d


def test_render_token_matches_compute_for_applied(fresh_settings):
    d = _calibrated_group(fresh_settings)
    d.mediaElements = [_seg_elem()]
    assert R.render_token(d.mediaElements, "G1") == R.compute_render_token("G1")


def test_render_token_varies_with_items(fresh_settings):
    _calibrated_group(fresh_settings)
    t1 = R.render_token([_seg_elem(file="/media/server/videos/a.mp4")], "G1")
    t2 = R.render_token([_seg_elem(file="/media/server/videos/b.mp4")], "G1")
    assert t1 != t2


def test_render_token_empty_for_unknown_group(fresh_settings):
    assert R.render_token([_seg_elem()], "NOPE") == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_render_token.py -c tests/pytest.ini -v`
Expected: FAIL — `AttributeError: module 'mosaicmesh.render' has no attribute 'render_token'`.

- [ ] **Step 3: Implement**

In `mosaicmesh/render.py`, replace `compute_render_token` (currently lines 295-319) with the generalized pair:

```python
def render_token(media_elements, display_id):
    """Stable hash of the inputs that affect a per-screen render (SEGMENT or
    INDIVIDUAL) for a GIVEN set of media elements against a group's calibration:
    the items, the group bounding box, and each client's resolution + measured
    quad. Generalizes the old compute_render_token so a token can be computed
    for any playlist (not just the one currently applied to the group)."""
    import server
    display = server.settings.displays.get(display_id)
    if not display:
        return ""
    items = []
    for me in media_elements:
        pm = me.playmode.name if hasattr(me.playmode, "name") else str(me.playmode)
        items.append((me.id, me.file, me.duration, pm,
                      getattr(me, "backgroundColor", "#000000"),
                      getattr(me, "startEffect", None), getattr(me, "endEffect", None)))
    clients = []
    for key, c in _group_clients(display_id):
        perim = None
        if c.measuredPerimeter is not None:
            perim = np.array(c.measuredPerimeter, dtype="int32").reshape(-1, 2).tolist()
        clients.append((key, c.deviceWidth, c.deviceHeight, perim))
    # Bump this when the encode settings change, to invalidate stale renders.
    # v6: encoder default reverted libx264 (NVENC SPS rejected by iPad-1).
    encode_ver = "grid025-cbl-v6"
    raw = repr((items, display.boundingBox, clients, encode_ver))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def compute_render_token(display_id):
    """Token for the playlist CURRENTLY applied to the group (display.mediaElements).
    Thin wrapper over render_token — preserves the historical call site/byte-form
    so existing Display.renderedToken values stay valid."""
    import server
    display = server.settings.displays.get(display_id)
    if not display:
        return ""
    return render_token(display.mediaElements, display_id)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_render_token.py -c tests/pytest.ini -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render.py tests/unit/test_render_token.py
git commit -m "feat(render): generalize compute_render_token -> render_token(items, group)"
```

---

### Task 2: Render-state constants + `Display.renders` registry + migration

**Files:**
- Modify: `mosaicmesh/render.py` (add state constants near top, after `_RENDER_CONCURRENCY` at line 86)
- Modify: `mosaicmesh/state.py:29-48` (`Display.__init__`), `:224-234` (`migrate_client_objects` display loop)
- Test: `tests/unit/test_render_registry.py`

**Context:** Each group tracks render state per playlist name. We store entries as plain dicts (jsonpickle-friendly, no new class to migrate). The registry persists in `settings.dat`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_render_registry.py
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
from mosaicmesh.state import Settings, Display, migrate_client_objects
from mosaicmesh import render as R


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev


def test_new_display_has_empty_renders():
    assert Display().renders == {}


def test_render_state_constants():
    assert R.RENDER_QUEUED == "QUEUED"
    assert R.RENDER_RENDERING == "RENDERING"
    assert R.RENDER_READY == "READY"
    assert R.RENDER_STALE == "STALE"
    assert R.RENDER_FAILED == "FAILED"


def test_migration_backfills_renders(fresh_settings):
    d = Display()
    del d.renders            # simulate a Display loaded from a pre-feature settings.dat
    fresh_settings.displays["G1"] = d
    migrate_client_objects()
    assert fresh_settings.displays["G1"].renders == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_render_registry.py -c tests/pytest.ini -v`
Expected: FAIL — `AttributeError: 'Display' object has no attribute 'renders'` and missing `RENDER_QUEUED`.

- [ ] **Step 3a: Add the field to `Display.__init__`**

In `mosaicmesh/state.py`, in `Display.__init__` after line 41 (`self.renderStatus = ""`), add:

```python
        # Per-(playlist) render registry for THIS group (PR auto-render).
        # { playlistName: {token, state, updatedAt, error, percent, eta, startedAt} }
        # state ∈ render.RENDER_{QUEUED,RENDERING,READY,STALE,FAILED}.
        # Persists in settings.dat; revalidated against render_token + on-disk
        # assets at boot. renderedToken/renderStatus above are the legacy
        # single-applied-playlist fields, kept for the live playback path.
        self.renders = {}
```

- [ ] **Step 3b: Backfill in migration**

In `mosaicmesh/state.py`, inside the `for _disp in settings.displays.values():` loop (after line 234 `_disp.prepareDeadline = 0`), add:

```python
        if not hasattr(_disp, 'renders'):
            _disp.renders = {}
```

- [ ] **Step 3c: Add render-state constants**

In `mosaicmesh/render.py`, immediately after line 86 (`_RENDER_CONCURRENCY = ...`), add:

```python
# Per-(playlist, group) render lifecycle states (stored in Display.renders[name]["state"]).
RENDER_QUEUED = "QUEUED"        # enqueued, not yet started
RENDER_RENDERING = "RENDERING"  # ffmpeg in flight
RENDER_READY = "READY"          # assets on disk + token current
RENDER_STALE = "STALE"          # was READY, inputs changed (recalibrate/edit)
RENDER_FAILED = "FAILED"        # ffmpeg errored; needs manual Retry
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_render_registry.py -c tests/pytest.ini -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/state.py mosaicmesh/render.py tests/unit/test_render_registry.py
git commit -m "feat(render): add Display.renders registry + state constants + migration"
```

---

### Task 3: Registry helpers + `is_playlist_ready`

**Files:**
- Modify: `mosaicmesh/render.py` (add helpers after `_is_renderable`, ~line 335)
- Test: `tests/unit/test_render_token.py` (extend)

**Context:** `is_playlist_ready(name, display_id)` is the gate predicate used everywhere. N/A playlists (no renderable items) are always ready. `_set_render_state` is the single writer for registry entries (stamps `updatedAt`).

- [ ] **Step 1: Write the failing test (append to `tests/unit/test_render_token.py`)**

```python
def test_na_playlist_always_ready(fresh_settings):
    from mosaicmesh.state import Playlist
    _calibrated_group(fresh_settings)
    pl = Playlist(); pl.name = "P"; pl.items = [{"id": 0, "file": "/m/x.png", "playmode": "FULL"}]
    fresh_settings.playlists["P"] = pl
    assert R.is_playlist_ready("P", "G1") is True


def test_renderable_not_ready_without_entry(fresh_settings):
    from mosaicmesh.state import Playlist
    _calibrated_group(fresh_settings)
    pl = Playlist(); pl.name = "P"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["P"] = pl
    assert R.is_playlist_ready("P", "G1") is False


def test_renderable_ready_with_current_token(fresh_settings):
    from mosaicmesh.state import Playlist
    d = _calibrated_group(fresh_settings)
    pl = Playlist(); pl.name = "P"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["P"] = pl
    tok = R.render_token(R._build_media_elements(pl.items), "G1")
    R._set_render_state(d, "P", R.RENDER_READY, token=tok)
    assert R.is_playlist_ready("P", "G1") is True
    # stale token => not ready
    R._set_render_state(d, "P", R.RENDER_READY, token="deadbeef")
    assert R.is_playlist_ready("P", "G1") is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_render_token.py -c tests/pytest.ini -v`
Expected: FAIL — `_set_render_state` / `is_playlist_ready` not defined.

- [ ] **Step 3: Implement (in `mosaicmesh/render.py`, after `_is_renderable`)**

```python
def _set_render_state(display, playlist_name, state, token=None, error=None,
                      percent=None, eta=None, started=None):
    """Single writer for a Display.renders[name] entry. Creates the entry if
    absent, patches only the provided fields, stamps updatedAt. Returns the entry."""
    reg = getattr(display, "renders", None)
    if reg is None:
        reg = display.renders = {}
    entry = reg.get(playlist_name) or {}
    entry["state"] = state
    if token is not None:
        entry["token"] = token
    entry["error"] = error
    if percent is not None:
        entry["percent"] = percent
    if eta is not None:
        entry["eta"] = eta
    if started is not None:
        entry["startedAt"] = started
    entry["updatedAt"] = time.time()
    reg[playlist_name] = entry
    return entry


def is_playlist_ready(playlist_name, display_id):
    """True if (playlist, group) needs no render (N/A — no renderable items) OR
    has a READY registry entry whose token matches the playlist's current
    render_token for that group. Used by every assignment/play/schedule gate."""
    import server
    pl = server.settings.playlists.get(playlist_name)
    display = server.settings.displays.get(display_id)
    if pl is None or display is None:
        return False
    elements = _build_media_elements(pl.items)
    if not any(_is_renderable(me) for me in elements):
        return True  # N/A — always assignable/playable
    entry = (getattr(display, "renders", {}) or {}).get(playlist_name)
    if not entry:
        return False
    return (entry.get("state") == RENDER_READY
            and entry.get("token") == render_token(elements, display_id))
```

Note: `_build_media_elements` is defined later in the module (line 616); since `is_playlist_ready` is only called at runtime (not import time), the forward reference resolves fine.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_render_token.py -c tests/pytest.ini -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render.py tests/unit/test_render_token.py
git commit -m "feat(render): add _set_render_state + is_playlist_ready predicate"
```

---

## Phase B — Encode extraction, per-playlist render, queue, progress, boot

### Task 4: Extract `_encode_group(media_elements, display_id, token)` from `render_group_async`

**Files:**
- Modify: `mosaicmesh/render.py:384-508` (`render_group_async`)
- Test: `tests/unit/test_render_registry.py` (extend — assert the wrapper still sets legacy fields)

**Context:** The encode body (pass 1 build commands, pass 2 run ffmpeg, image warps, cache-push) currently reads `display.mediaElements` + `compute_render_token`. Extract it to take `media_elements` + `token` explicitly so we can render an un-applied playlist. The legacy `render_group_async(display_id)` stays as a thin wrapper that still sets `display.renderStatus`/`renderedToken` (keeps `evaluate_schedules` + any existing callers working until Phase C/D move them).

- [ ] **Step 1: Write the failing test (append to `tests/unit/test_render_registry.py`)**

```python
import asyncio


def test_encode_group_callable_signature(fresh_settings):
    # _encode_group must accept (media_elements, display_id, token, progress_cb=None)
    import inspect
    sig = inspect.signature(R._encode_group)
    params = list(sig.parameters)
    assert params[:3] == ["media_elements", "display_id", "token"]
    assert "progress_cb" in params


def test_render_group_async_no_clients_returns_ready_token(fresh_settings):
    # A calibrated group with a single FULL (non-renderable) item: nothing to
    # encode, wrapper still returns ready + sets legacy fields.
    from mosaicmesh.state import Display, MediaElement, PlayMode
    d = Display(); d.boundingBox = [0, 0, 10, 10]
    me = MediaElement(); me.id = 0; me.file = "/m/x.png"; me.playmode = PlayMode.FULL
    d.mediaElements = [me]
    fresh_settings.displays["G1"] = d
    out = asyncio.get_event_loop().run_until_complete(R.render_group_async("G1"))
    assert out["status"] == "ready"
    assert d.renderStatus == "ready"
    assert d.renderedToken == out["token"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_render_registry.py -c tests/pytest.ini -v`
Expected: FAIL — `_encode_group` not defined.

- [ ] **Step 3: Refactor**

In `mosaicmesh/render.py`, replace the whole `render_group_async` function (lines 384-508) with an extracted encoder + a thin wrapper. The encode body is moved verbatim except: (a) it reads the `media_elements` parameter instead of `display.mediaElements`; (b) it uses the passed `token` instead of calling `compute_render_token`; (c) it no longer touches `display.renderStatus`/`renderedToken`/broadcasts — the caller owns those.

```python
async def _encode_group(media_elements, display_id, token, progress_cb=None):
    """Encode all SEGMENT/INDIVIDUAL items in `media_elements` for `display_id`'s
    calibrated screens, writing seg_<token>_<i>/ind_<token>_<i> assets. Pure
    encode: no Display.renderStatus / renderedToken / broadcast side effects —
    the caller owns lifecycle state (legacy wrapper or the per-playlist renderer).
    Raises on ffmpeg failure. progress_cb(done, total) is called as video jobs
    complete (best-effort, optional)."""
    import server
    display = server.settings.displays.get(display_id)
    if not display:
        raise RuntimeError("no such display: " + str(display_id))
    seg_items = [(i, me) for i, me in enumerate(media_elements)
                 if _is_renderable(me)]
    clients = [(k, c) for k, c in _group_clients(display_id) if c.measuredPerimeter is not None]
    video_jobs = []        # list of (cmd, label)
    seg_push_targets = []  # list of (client_key, segment_n) for seg_ video jobs only
    for i, me in seg_items:
        src_path = resolve_media_path(me.file)
        if isVideoItem(me.file):
            dims = get_video_dimensions(src_path) if src_path else None
            if not dims:
                raise RuntimeError("cannot read source video: " + str(me.file))
            sw, sh = dims
            for key, c in clients:
                out_dir = os.path.join("media", key, "videos")
                Path(out_dir).mkdir(parents=True, exist_ok=True)
                out_w, out_h = _render_output_dims(c)
                evf, eaf = _resolve_effect_filters(me, me.duration, out_w, out_h)
                if me.playmode == PlayMode.INDIVIDUAL:
                    quad_pts = np.array(c.measuredPerimeter, dtype="int32").reshape(-1, 2)
                    bx, by, bw, bh = [int(v) for v in cv.boundingRect(quad_pts)]
                    if bw <= 0 or bh <= 0 or cv.contourArea(np.array(c.measuredPerimeter, dtype="int32")) <= 0:
                        raise RuntimeError("degenerate screen quad for client " + str(key))
                    if sw * bh >= sh * bw:
                        pad_w = sw; pad_h = int(round(sw * bh / float(bw)))
                    else:
                        pad_h = sh; pad_w = int(round(sh * bw / float(bh)))
                    pad_x = (pad_w - sw) // 2; pad_y = (pad_h - sh) // 2
                    pts = quad_to_source_points([bx, by, bw, bh], c.measuredPerimeter, pad_w, pad_h)
                    out_path = os.path.join(out_dir, "ind_" + token + "_" + str(i) + ".mp4")
                    cmd = build_ffmpeg_individual_cmd(src_path, out_path, pts,
                                                      out_w, out_h, pad_w, pad_h, pad_x, pad_y,
                                                      getattr(me, "backgroundColor", "#000000"),
                                                      extra_video_filters=evf, extra_audio_filters=eaf)
                else:
                    pts = quad_to_source_points(display.boundingBox, c.measuredPerimeter, sw, sh)
                    out_path = os.path.join(out_dir, "seg_" + token + "_" + str(i) + ".mp4")
                    cmd = build_ffmpeg_perspective_cmd(src_path, out_path, pts, out_w, out_h,
                                                       extra_video_filters=evf, extra_audio_filters=eaf)
                    seg_push_targets.append((key, i))
                video_jobs.append((cmd, key + "/" + str(i)))
        else:
            img = cv.imread(src_path) if src_path else None
            if img is None:
                raise RuntimeError("cannot read source image: " + str(me.file))
            for key, c in clients:
                out_dir = os.path.join("media", key, "images")
                Path(out_dir).mkdir(parents=True, exist_ok=True)
                out_w, out_h = _render_output_dims(c)
                if me.playmode == PlayMode.INDIVIDUAL:
                    quad_pts = np.array(c.measuredPerimeter, dtype="int32").reshape(-1, 2)
                    bx, by, bw, bh = [int(v) for v in cv.boundingRect(quad_pts)]
                    if bw <= 0 or bh <= 0 or cv.contourArea(np.array(c.measuredPerimeter, dtype="int32")) <= 0:
                        raise RuntimeError("degenerate screen quad for client " + str(key))
                    bg = _hex_to_bgr(getattr(me, "backgroundColor", "#000000"))
                    canvas = letterbox_to_aspect(img, bw, bh, bg)
                    warped = warp_image_for_screen(canvas, [bx, by, bw, bh], c.measuredPerimeter, out_w, out_h)
                    cv.imwrite(os.path.join(out_dir, "ind_" + token + "_" + str(i) + ".png"), warped)
                else:
                    warped = warp_image_for_screen(img, display.boundingBox, c.measuredPerimeter, out_w, out_h)
                    cv.imwrite(os.path.join(out_dir, "seg_" + token + "_" + str(i) + ".png"), warped)
    if video_jobs:
        sem = asyncio.Semaphore(_RENDER_CONCURRENCY)
        logging.info("render: launching %d ffmpeg jobs concurrency=%d encoder=%s",
                     len(video_jobs), _RENDER_CONCURRENCY, _VIDEO_ENCODER)
        t0 = time.time()
        total = len(video_jobs)
        done = [0]
        async def _run_and_count(cmd, lbl):
            await _run_ffmpeg(cmd, lbl, sem)
            done[0] += 1
            if progress_cb:
                try:
                    progress_cb(done[0], total)
                except Exception:
                    pass
        await asyncio.gather(*[_run_and_count(cmd, lbl) for cmd, lbl in video_jobs])
        logging.info("render: %d ffmpeg jobs done in %.1fs", len(video_jobs), time.time() - t0)
        for _push_key, _push_n in seg_push_targets:
            asyncio.ensure_future(
                server._push_segment_to_cached_clients(_push_key, token, _push_n))


async def render_group_async(display_id):
    """Legacy entry point: render the playlist CURRENTLY applied to a group
    (display.mediaElements). Sets display.renderStatus/renderedToken + broadcasts
    RENDER_STATUS. Retained so evaluate_schedules' resume path keeps working;
    Phase C/D route new renders through render_playlist_for_group_async."""
    import server
    display = server.settings.displays.get(display_id)
    if not display:
        return {"status": "error"}
    display.renderStatus = "rendering"
    _broadcast_render_status(display_id, "rendering")
    token = compute_render_token(display_id)
    try:
        await _encode_group(display.mediaElements, display_id, token)
        display.renderedToken = token
        display.renderStatus = "ready"
        _broadcast_render_status(display_id, "ready")
        return {"status": "ready", "token": token}
    except Exception as e:
        logging.error("render failed for %s: %s", display_id, e)
        display.renderStatus = "error"
        _broadcast_render_status(display_id, "error")
        return {"status": "error", "error": str(e)}
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_render_registry.py tests/unit/test_render_token.py -c tests/pytest.ini -v`
Expected: PASS. Also run the existing render-related suite to confirm no regression: `python -m pytest tests/unit -c tests/pytest.ini -k "render or playlist or schedule" -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render.py tests/unit/test_render_registry.py
git commit -m "refactor(render): extract _encode_group(items, group, token) from render_group_async"
```

---

### Task 5: `render_playlist_for_group_async` + registry lifecycle

**Files:**
- Modify: `mosaicmesh/render.py` (add after `render_group_async`)
- Test: `tests/unit/test_render_registry.py` (extend)

**Context:** This renders a named playlist for a group, writing QUEUED→RENDERING→READY/FAILED into the registry and stamping percent/eta via `progress_cb`. It does NOT touch `display.mediaElements` (staging-safe). It broadcasts `RENDERS_CHANGED` (added in Task 11; for now call a stub that no-ops if absent — we define `_broadcast_renders_changed` here).

- [ ] **Step 1: Write the failing test (append to `tests/unit/test_render_registry.py`)**

```python
def test_render_playlist_for_group_sets_ready(fresh_settings, monkeypatch):
    from mosaicmesh.state import Display, Playlist, Client
    d = Display(); d.boundingBox = [0, 0, 10, 10]
    fresh_settings.displays["G1"] = d
    c = Client(); c.displayID = "G1"; c.deviceWidth = 100; c.deviceHeight = 100
    c.measuredPerimeter = [0, 0, 5, 0, 5, 5, 0, 5]
    fresh_settings.clients["c1"] = c
    pl = Playlist(); pl.name = "P"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["P"] = pl

    # Stub the actual encode so the test needs no ffmpeg/source file.
    async def _fake_encode(elements, did, token, progress_cb=None):
        if progress_cb: progress_cb(1, 1)
    monkeypatch.setattr(R, "_encode_group", _fake_encode)

    asyncio.get_event_loop().run_until_complete(R.render_playlist_for_group_async("P", "G1"))
    entry = d.renders["P"]
    assert entry["state"] == R.RENDER_READY
    assert entry["token"] == R.render_token(R._build_media_elements(pl.items), "G1")
    assert entry["percent"] == 100


def test_render_playlist_for_group_failed(fresh_settings, monkeypatch):
    from mosaicmesh.state import Display, Playlist, Client
    d = Display(); d.boundingBox = [0, 0, 10, 10]
    fresh_settings.displays["G1"] = d
    c = Client(); c.displayID = "G1"; c.deviceWidth = 100; c.deviceHeight = 100
    c.measuredPerimeter = [0, 0, 5, 0, 5, 5, 0, 5]
    fresh_settings.clients["c1"] = c
    pl = Playlist(); pl.name = "P"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["P"] = pl

    async def _boom(elements, did, token, progress_cb=None):
        raise RuntimeError("ffmpeg exploded")
    monkeypatch.setattr(R, "_encode_group", _boom)

    asyncio.get_event_loop().run_until_complete(R.render_playlist_for_group_async("P", "G1"))
    entry = d.renders["P"]
    assert entry["state"] == R.RENDER_FAILED
    assert "ffmpeg exploded" in entry["error"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_render_registry.py -c tests/pytest.ini -v`
Expected: FAIL — `render_playlist_for_group_async` not defined.

- [ ] **Step 3: Implement (in `mosaicmesh/render.py`, after `render_group_async`)**

```python
# Throttled RENDERS_CHANGED broadcast (≤1/s). Module-level so all callers coalesce.
_last_renders_broadcast = [0.0]


def _broadcast_renders_changed(force=False):
    """Fan a RENDERS_CHANGED snapshot to all clients, throttled to ≤1/s unless
    force=True (terminal transitions). No-op if socketmanager isn't wired."""
    import server
    if server.socketmanager is None:
        return
    now = time.time()
    if not force and (now - _last_renders_broadcast[0]) < 1.0:
        return
    _last_renders_broadcast[0] = now
    server.socketmanager.broadcast(jsonpickle.encode(
        {"REQUEST": "RENDERS_CHANGED", "PAYLOAD": {"renders": renders_snapshot()}}))


def renders_snapshot():
    """Flat list of every non-N/A render entry across all groups, for the
    fleet-wide Render Status panel + GET /api/renders."""
    import server
    out = []
    for did, display in server.settings.displays.items():
        for name, e in (getattr(display, "renders", {}) or {}).items():
            out.append({
                "displayID": did, "playlist": name,
                "state": e.get("state"), "percent": e.get("percent"),
                "eta": e.get("eta"), "startedAt": e.get("startedAt"),
                "error": e.get("error"), "updatedAt": e.get("updatedAt"),
            })
    return out


async def render_playlist_for_group_async(playlist_name, display_id):
    """Render a NAMED playlist for a group into the registry (QUEUED→RENDERING→
    READY/FAILED) WITHOUT touching display.mediaElements (staging-safe). Used by
    the render queue. No-op (drops the entry) if the playlist became N/A."""
    import server
    pl = server.settings.playlists.get(playlist_name)
    display = server.settings.displays.get(display_id)
    if pl is None or display is None:
        return
    elements = _build_media_elements(pl.items)
    if not any(_is_renderable(me) for me in elements):
        display.renders.pop(playlist_name, None)   # became N/A
        _broadcast_renders_changed(force=True)
        return
    token = render_token(elements, display_id)
    _set_render_state(display, playlist_name, RENDER_RENDERING, token=token,
                      percent=0, started=time.time())
    _broadcast_renders_changed(force=True)

    def _progress(done, total):
        pct = int(round(100.0 * done / total)) if total else 100
        entry = display.renders.get(playlist_name) or {}
        started = entry.get("startedAt") or time.time()
        elapsed = max(0.001, time.time() - started)
        rate = done / elapsed
        eta = int(round((total - done) / rate)) if rate > 0 else None
        _set_render_state(display, playlist_name, RENDER_RENDERING, percent=pct, eta=eta)
        _broadcast_renders_changed()

    try:
        await _encode_group(elements, display_id, token, progress_cb=_progress)
        _set_render_state(display, playlist_name, RENDER_READY, token=token,
                          percent=100, eta=0, error=None)
        # If this playlist is the one applied to the group, sync the live token
        # so the per-client PLAY URLs resolve the freshly-rendered assets.
        if getattr(display, "currentPlaylistName", None) == playlist_name:
            display.renderedToken = token
    except Exception as e:
        logging.error("render_playlist_for_group %s/%s failed: %s", playlist_name, display_id, e)
        _set_render_state(display, playlist_name, RENDER_FAILED, error=str(e))
    _broadcast_renders_changed(force=True)
    try:
        from mosaicmesh.persistence import save_settings_incremental
        save_settings_incremental()
    except Exception:
        pass
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_render_registry.py -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render.py tests/unit/test_render_registry.py
git commit -m "feat(render): render_playlist_for_group_async + registry lifecycle + RENDERS_CHANGED"
```

---

### Task 6: Bounded render queue + save debounce (`mosaicmesh/render_queue.py`)

**Files:**
- Create: `mosaicmesh/render_queue.py`
- Modify: `mosaicmesh/render.py` (add `enqueue_playlist_for_calibrated_groups` + `_group_is_calibrated`)
- Test: `tests/unit/test_render_queue.py`

**Context:** The queue caps concurrent ffmpeg jobs and dedupes in-flight (playlist, group) pairs. The debounce coalesces rapid saves into one render pass after 60s. Both live here so render.py stays focused on encoding.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_render_queue.py
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

import asyncio
import pytest
from mosaicmesh import render_queue as Q


@pytest.fixture(autouse=True)
def clean_queue():
    Q._pending.clear()
    for t in list(Q._debounce_tasks.values()):
        t.cancel()
    Q._debounce_tasks.clear()
    yield
    Q._pending.clear()


def test_enqueue_idempotent(monkeypatch):
    runs = []
    async def _fake_render(name, did):
        runs.append((name, did))
        await asyncio.sleep(0)
    monkeypatch.setattr("mosaicmesh.render.render_playlist_for_group_async", _fake_render)

    async def _go():
        assert Q.enqueue("P", "G1") is True
        assert Q.enqueue("P", "G1") is False   # already pending → deduped
        await asyncio.sleep(0.05)
    asyncio.get_event_loop().run_until_complete(_go())
    assert runs == [("P", "G1")]


def test_queue_depth_counts_queued(monkeypatch):
    async def _slow(name, did):
        await asyncio.sleep(0.2)
    monkeypatch.setattr("mosaicmesh.render.render_playlist_for_group_async", _slow)
    monkeypatch.setattr(Q, "_QUEUE_CONCURRENCY", 1, raising=False)

    async def _go():
        Q._sem = None  # rebuild semaphore at the new concurrency
        Q.enqueue("A", "G1")
        Q.enqueue("B", "G1")
        await asyncio.sleep(0.02)
        # With concurrency 1, one is RENDERING, one still QUEUED.
        assert Q.queue_depth() >= 1
        await asyncio.sleep(0.5)
    asyncio.get_event_loop().run_until_complete(_go())


def test_debounce_coalesces(monkeypatch):
    calls = []
    monkeypatch.setattr("mosaicmesh.render.enqueue_playlist_for_calibrated_groups",
                        lambda name: calls.append(name))
    monkeypatch.setattr(Q, "DEBOUNCE_SECONDS", 0.05, raising=False)

    async def _go():
        Q.schedule_autorender("P")
        await asyncio.sleep(0.02)
        Q.schedule_autorender("P")   # resets the timer
        await asyncio.sleep(0.02)
        assert calls == []           # not fired yet
        await asyncio.sleep(0.1)
        assert calls == ["P"]        # fired once
    asyncio.get_event_loop().run_until_complete(_go())
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_render_queue.py -c tests/pytest.ini -v`
Expected: FAIL — `No module named 'mosaicmesh.render_queue'`.

- [ ] **Step 3a: Create `mosaicmesh/render_queue.py`**

```python
"""Bounded background render queue + per-playlist save debounce.

Decouples "a (playlist, group) needs rendering" from the ffmpeg work so a
fleet-wide save or a calibrate can enqueue N jobs without spawning N
simultaneous encodes. Lives apart from render.py so that module stays focused
on the encode itself.

- enqueue(name, group): idempotent; schedules a render under a concurrency cap.
- schedule_autorender(name): debounced; after DEBOUNCE_SECONDS of quiet, enqueues
  the playlist against every calibrated group.
"""
import asyncio
import logging
import os

_QUEUE_CONCURRENCY = int(os.environ.get("MMRENDER_QUEUE_CONCURRENCY") or 2)
DEBOUNCE_SECONDS = int(os.environ.get("MMRENDER_DEBOUNCE") or 60)

_pending = {}          # (name, group) -> "QUEUED" | "RENDERING"
_sem = None            # asyncio.Semaphore, lazily bound to the running loop
_debounce_tasks = {}   # name -> asyncio.Task


def _get_sem():
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(_QUEUE_CONCURRENCY)
    return _sem


def enqueue(playlist_name, display_id):
    """Idempotent enqueue of a (playlist, group) render. No-op if already
    queued/in-flight. Returns True iff a new job was scheduled."""
    key = (playlist_name, display_id)
    if key in _pending:
        return False
    _pending[key] = "QUEUED"
    asyncio.ensure_future(_run(playlist_name, display_id))
    return True


async def _run(playlist_name, display_id):
    from mosaicmesh import render as R
    key = (playlist_name, display_id)
    async with _get_sem():
        _pending[key] = "RENDERING"
        try:
            await R.render_playlist_for_group_async(playlist_name, display_id)
        except Exception as e:
            logging.error("render_queue job %s failed: %s", key, e)
        finally:
            _pending.pop(key, None)


def queue_depth():
    """Number of jobs still waiting (not yet started)."""
    return sum(1 for v in _pending.values() if v == "QUEUED")


def schedule_autorender(playlist_name):
    """Debounced auto-render: (re)start a DEBOUNCE_SECONDS timer for this
    playlist. On fire, enqueue it for every calibrated group. A later call
    within the window resets the timer (coalesces a burst of edits)."""
    old = _debounce_tasks.get(playlist_name)
    if old and not old.done():
        old.cancel()
    _debounce_tasks[playlist_name] = asyncio.ensure_future(_debounce_fire(playlist_name))


async def _debounce_fire(playlist_name):
    try:
        await asyncio.sleep(DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        return
    _debounce_tasks.pop(playlist_name, None)
    from mosaicmesh import render as R
    R.enqueue_playlist_for_calibrated_groups(playlist_name)
```

- [ ] **Step 3b: Add `enqueue_playlist_for_calibrated_groups` + `_group_is_calibrated` to `mosaicmesh/render.py`** (after `render_playlist_for_group_async`)

```python
def _group_is_calibrated(display_id):
    """A group is calibrated iff it has a boundingBox AND ≥1 client with a
    measured perimeter — the minimum needed to produce a per-screen render."""
    import server
    display = server.settings.displays.get(display_id)
    if not display or not display.boundingBox:
        return False
    return any(c.measuredPerimeter is not None for _k, c in _group_clients(display_id))


def enqueue_playlist_for_calibrated_groups(playlist_name):
    """For a saved renderable playlist, set QUEUED + enqueue a render against
    every calibrated group. N/A playlists (no renderable items) are skipped."""
    import server
    from mosaicmesh import render_queue
    pl = server.settings.playlists.get(playlist_name)
    if pl is None:
        return
    elements = _build_media_elements(pl.items)
    if not any(_is_renderable(me) for me in elements):
        return
    changed = False
    for did, display in server.settings.displays.items():
        if not _group_is_calibrated(did):
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

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_render_queue.py -c tests/pytest.ini -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render_queue.py mosaicmesh/render.py tests/unit/test_render_queue.py
git commit -m "feat(render): bounded render queue + save debounce + calibrated-group fan-out"
```

---

### Task 7: ffmpeg per-job progress parsing

**Files:**
- Modify: `mosaicmesh/render.py:367-381` (`_run_ffmpeg`) — already counts completed jobs in `_encode_group` (Task 4). This task adds *intra-job* percent via `-progress` so a single long encode shows movement.
- Test: `tests/unit/test_render_queue.py` (extend with a parse-helper unit test)

**Context:** Task 5's `progress_cb(done, total)` already gives coarse per-completed-job percent. For long single encodes we refine: parse ffmpeg `-progress pipe:1` `out_time_ms=`/`progress=end` lines. We add a pure parser `_parse_ffmpeg_progress_line` (unit-testable without ffmpeg) and wire it in `_run_ffmpeg` to call an optional per-job callback. To keep scope bounded, the queue-level percent (done/total jobs) remains the headline number; intra-job percent is a refinement stored as `subPercent` and is OPTIONAL — if wiring proves noisy, the done/total percent alone satisfies the spec's "% complete".

- [ ] **Step 1: Write the failing test (append to `tests/unit/test_render_queue.py`)**

```python
def test_parse_ffmpeg_progress_line():
    from mosaicmesh import render as R
    assert R._parse_ffmpeg_progress_line("out_time_ms=2000000") == ("out_time_ms", 2000000)
    assert R._parse_ffmpeg_progress_line("progress=end") == ("progress", "end")
    assert R._parse_ffmpeg_progress_line("garbage") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_render_queue.py::test_parse_ffmpeg_progress_line -c tests/pytest.ini -v`
Expected: FAIL — `_parse_ffmpeg_progress_line` not defined.

- [ ] **Step 3: Implement the parser (in `mosaicmesh/render.py`, just above `_run_ffmpeg`)**

```python
def _parse_ffmpeg_progress_line(line):
    """Parse one ffmpeg `-progress` key=value line. Returns (key, value) where
    value is int for numeric keys, else the raw string; None for non key=value
    lines. Pure — unit-tested without ffmpeg."""
    line = (line or "").strip()
    if "=" not in line:
        return None
    k, _, v = line.partition("=")
    k = k.strip(); v = v.strip()
    if not k:
        return None
    if v.lstrip("-").isdigit():
        return (k, int(v))
    return (k, v)
```

(Wiring `-progress pipe:1` into the ffmpeg commands is deferred as a non-blocking refinement; the done/total percent from Task 5 satisfies the spec. Document this in the task's commit message so reviewers know it's intentional.)

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_render_queue.py -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render.py tests/unit/test_render_queue.py
git commit -m "feat(render): ffmpeg -progress line parser (per-job percent refinement)"
```

---

### Task 8: Boot revalidation of persisted renders

**Files:**
- Modify: `mosaicmesh/render.py` (add `revalidate_renders_on_boot`)
- Modify: `server.py` — call it once at startup, right after `migrate_client_objects()` (find the startup call site near where settings is loaded)
- Test: `tests/unit/test_render_registry.py` (extend)

**Context:** On boot, every persisted READY entry is checked against the current `render_token` (calibration/items may have changed while down) AND on-disk assets. Matching+present → stays READY. Otherwise → STALE (lazy re-render happens on next save/calibrate/assign). In-flight states (QUEUED/RENDERING) from a previous run are meaningless after a restart → reset to STALE.

- [ ] **Step 1: Write the failing test (append to `tests/unit/test_render_registry.py`)**

```python
def test_revalidate_demotes_stale_token(fresh_settings):
    from mosaicmesh.state import Display, Playlist, Client
    d = Display(); d.boundingBox = [0, 0, 10, 10]
    fresh_settings.displays["G1"] = d
    c = Client(); c.displayID = "G1"; c.deviceWidth = 100; c.deviceHeight = 100
    c.measuredPerimeter = [0, 0, 5, 0, 5, 5, 0, 5]
    fresh_settings.clients["c1"] = c
    pl = Playlist(); pl.name = "P"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["P"] = pl
    # Persisted READY with a WRONG token (calibration changed while down).
    R._set_render_state(d, "P", R.RENDER_READY, token="staletoken")
    R.revalidate_renders_on_boot()
    assert d.renders["P"]["state"] == R.RENDER_STALE


def test_revalidate_resets_inflight(fresh_settings):
    from mosaicmesh.state import Display
    d = Display(); d.boundingBox = [0, 0, 10, 10]
    fresh_settings.displays["G1"] = d
    R._set_render_state(d, "Q", R.RENDER_RENDERING, token="t")
    R.revalidate_renders_on_boot()
    assert d.renders["Q"]["state"] == R.RENDER_STALE
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_render_registry.py -c tests/pytest.ini -v`
Expected: FAIL — `revalidate_renders_on_boot` not defined.

- [ ] **Step 3a: Implement (in `mosaicmesh/render.py`)**

```python
def _render_assets_exist(playlist_name, display_id, token):
    """True if every renderable item's per-client asset exists on disk for this
    token. Conservative: a single missing file demotes the entry to STALE."""
    import server
    pl = server.settings.playlists.get(playlist_name)
    display = server.settings.displays.get(display_id)
    if pl is None or display is None:
        return False
    elements = _build_media_elements(pl.items)
    clients = [(k, c) for k, c in _group_clients(display_id) if c.measuredPerimeter is not None]
    for i, me in enumerate(elements):
        if not _is_renderable(me):
            continue
        ext = ".mp4" if isVideoItem(me.file) else ".png"
        prefix = "ind_" if me.playmode == PlayMode.INDIVIDUAL else "seg_"
        subdir = "videos" if ext == ".mp4" else "images"
        for key, _c in clients:
            path = os.path.join("media", key, subdir, prefix + token + "_" + str(i) + ext)
            if not os.path.exists(path):
                return False
    return True


def revalidate_renders_on_boot():
    """Re-validate every persisted render entry once at startup. READY entries
    whose token still matches AND whose assets exist stay READY; everything else
    (stale token, missing asset, or a leftover in-flight QUEUED/RENDERING) drops
    to STALE for lazy re-render. Never auto-storms at boot."""
    import server
    for did, display in server.settings.displays.items():
        reg = getattr(display, "renders", {}) or {}
        for name in list(reg.keys()):
            entry = reg[name]
            pl = server.settings.playlists.get(name)
            if pl is None:
                reg.pop(name, None)   # playlist gone
                continue
            elements = _build_media_elements(pl.items)
            if not any(_is_renderable(me) for me in elements):
                reg.pop(name, None)   # became N/A
                continue
            cur = render_token(elements, did)
            ok = (entry.get("state") == RENDER_READY
                  and entry.get("token") == cur
                  and _render_assets_exist(name, did, cur))
            if not ok:
                _set_render_state(display, name, RENDER_STALE, token=cur)
```

- [ ] **Step 3b: Wire into startup in `server.py`**

Find the startup sequence where `migrate_client_objects()` is called (search `migrate_client_objects(`). Immediately after it, add:

```python
    try:
        revalidate_renders_on_boot()
    except Exception as e:
        logging.error("render revalidation on boot failed: %s", e)
```

`revalidate_renders_on_boot` is re-exported via the `from mosaicmesh.render import *` style imports already in `server.py` (the file re-imports render helpers — confirm the name is reachable; if `server.py` imports render symbols explicitly, add `revalidate_renders_on_boot` to that import list near line 62-69).

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_render_registry.py -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render.py server.py tests/unit/test_render_registry.py
git commit -m "feat(render): boot revalidation of persisted render registry"
```

---

## Phase C — Triggers

### Task 9: Save playlist → debounced auto-render (legacy + REST)

**Files:**
- Modify: `mosaicmesh/websocket/legacy.py:495-505` (`SAVE_PLAYLIST`)
- Modify: `mosaicmesh/api/playlists.py:67-122` (`api_playlists_create`, `api_playlists_update`)
- Test: `tests/unit/test_render_triggers.py`

**Context:** Every save path schedules the debounce. The REST handlers already exist; we add one call after the playlist is persisted.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_render_triggers.py
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

import json
import pytest
from aiohttp.test_utils import make_mocked_request
from mosaicmesh.state import Settings, Playlist


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev


@pytest.mark.asyncio
async def test_rest_create_schedules_autorender(fresh_settings, monkeypatch):
    scheduled = []
    monkeypatch.setattr("mosaicmesh.render_queue.schedule_autorender",
                        lambda name: scheduled.append(name))
    from mosaicmesh.api.playlists import api_playlists_create
    req = make_mocked_request('POST', '/api/playlists')
    async def _json():
        return {"name": "P", "items": [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]}
    req.json = _json
    resp = await api_playlists_create(req)
    assert resp.status == 201
    assert scheduled == ["P"]


@pytest.mark.asyncio
async def test_rest_update_schedules_autorender(fresh_settings, monkeypatch):
    p = Playlist(); p.name = "P"; p._serverVersion = 1
    fresh_settings.playlists["P"] = p
    scheduled = []
    monkeypatch.setattr("mosaicmesh.render_queue.schedule_autorender",
                        lambda name: scheduled.append(name))
    from mosaicmesh.api.playlists import api_playlists_update
    req = make_mocked_request('PUT', '/api/playlists/P', headers={'If-Match': '1'})
    async def _json():
        return {"items": [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]}
    req.json = _json
    req.match_info = {"name": "P"}
    resp = await api_playlists_update(req)
    assert resp.status == 200
    assert scheduled == ["P"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_render_triggers.py -c tests/pytest.ini -v`
Expected: FAIL — `schedule_autorender` not called (scheduled stays `[]`).

- [ ] **Step 3a: REST create — `mosaicmesh/api/playlists.py`**

In `api_playlists_create`, after `saveSettings()` (line 90) and before the `return`:

```python
    from mosaicmesh import render_queue
    render_queue.schedule_autorender(name)
```

- [ ] **Step 3b: REST update — `mosaicmesh/api/playlists.py`**

In `api_playlists_update`, after `saveSettings()` (line 121) and before the `return`:

```python
    from mosaicmesh import render_queue
    render_queue.schedule_autorender(name)
```

- [ ] **Step 3c: Legacy `SAVE_PLAYLIST` — `mosaicmesh/websocket/legacy.py`**

In the `SAVE_PLAYLIST` branch, after `pl.loop = bool(...)` (line 504) and before `response["PAYLOAD"] = "SUCCESS"`:

```python
            from mosaicmesh import render_queue
            render_queue.schedule_autorender(name)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_render_triggers.py -c tests/pytest.ini -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/api/playlists.py mosaicmesh/websocket/legacy.py tests/unit/test_render_triggers.py
git commit -m "feat(render): playlist save schedules debounced auto-render (REST + legacy)"
```

---

### Task 10: Calibrate → will-render list + enqueue all renderable playlists

**Files:**
- Modify: `server.py:1727+` (`calibrate`) — return a `willRender` list; the upload handler already returns JSON to the modal (PR-28's `detected_count`/`mapped_count`). Find where `calibrate()`'s result is assembled into the upload response and add `willRender`.
- Modify: `server.py` — after calibration persists `measuredPerimeter`/`boundingBox`, mark existing entries STALE + enqueue every renderable playlist for that group.
- Test: `tests/unit/test_render_triggers.py` (extend)

**Context:** Calibrate (first or re-) renders ALL renderable playlists for the group with a warning. We add a pure helper `mark_group_recalibrated(display_id)` in render.py that (a) STALEs existing entries, (b) enqueues every renderable playlist, (c) returns the list of playlist names that will render (for the warning). The calibrate route calls it and surfaces the list.

- [ ] **Step 1: Write the failing test (append to `tests/unit/test_render_triggers.py`)**

```python
def test_mark_group_recalibrated_enqueues_all(fresh_settings, monkeypatch):
    from mosaicmesh.state import Display, Playlist, Client
    from mosaicmesh import render as R
    enq = []
    monkeypatch.setattr("mosaicmesh.render_queue.enqueue",
                        lambda name, did: enq.append((name, did)) or True)
    d = Display(); d.boundingBox = [0, 0, 10, 10]
    fresh_settings.displays["G1"] = d
    c = Client(); c.displayID = "G1"; c.deviceWidth = 100; c.deviceHeight = 100
    c.measuredPerimeter = [0, 0, 5, 0, 5, 5, 0, 5]
    fresh_settings.clients["c1"] = c
    seg = Playlist(); seg.name = "Seg"
    seg.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    na = Playlist(); na.name = "Na"
    na.items = [{"id": 0, "file": "/m/x.png", "playmode": "FULL"}]
    fresh_settings.playlists["Seg"] = seg
    fresh_settings.playlists["Na"] = na

    will = R.mark_group_recalibrated("G1")
    assert will == ["Seg"]                  # N/A playlist excluded
    assert ("Seg", "G1") in enq
    assert d.renders["Seg"]["state"] in (R.RENDER_QUEUED, R.RENDER_STALE)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_render_triggers.py -c tests/pytest.ini -v`
Expected: FAIL — `mark_group_recalibrated` not defined.

- [ ] **Step 3a: Implement (in `mosaicmesh/render.py`, after `enqueue_playlist_for_calibrated_groups`)**

```python
def mark_group_recalibrated(display_id):
    """Calibration changed for a group (first calibration OR recalibrate):
    enqueue a render of EVERY renderable playlist for this group and return the
    list of playlist names that will render (for the operator warning + ETA).
    Existing entries are reset to QUEUED with the new token. N/A playlists are
    skipped. No-op (returns []) if the group isn't calibrated."""
    import server
    from mosaicmesh import render_queue
    if not _group_is_calibrated(display_id):
        return []
    display = server.settings.displays.get(display_id)
    will = []
    for name, pl in server.settings.playlists.items():
        elements = _build_media_elements(pl.items)
        if not any(_is_renderable(me) for me in elements):
            continue
        _set_render_state(display, name, RENDER_QUEUED, token=render_token(elements, display_id))
        render_queue.enqueue(name, display_id)
        will.append(name)
    if will:
        _broadcast_renders_changed(force=True)
    return will
```

- [ ] **Step 3b: Wire into the calibrate flow in `server.py`**

Locate where `calibrate()` finishes persisting per-client `measuredCenter`/`measuredPerimeter` and the group's `boundingBox` (search within `calibrate` for `measuredPerimeter =` assignments and the `boundingBox` write). After the group's calibration is fully persisted, capture the affected `display_id`(s) and call:

```python
        will_render = mark_group_recalibrated(display_id)
```

Then, in the upload route that returns JSON to the calibration modal (the handler that currently returns `detected`/`mapped` counts), include the list:

```python
        # ...existing response dict...
        "willRender": will_render,   # playlist names that will (re)render for this group
```

If `calibrate()` does not currently know the `display_id` (it maps markers→clients), derive the set of affected groups from the calibrated clients: `affected = {c.displayID for c in settings.clients.values() if c.measuredPerimeter is not None and c.displayID}` and call `mark_group_recalibrated` for each, unioning the `willRender` lists. Add `ensure the return dict carries willRender` as a single flat list.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_render_triggers.py -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render.py server.py tests/unit/test_render_triggers.py
git commit -m "feat(render): calibrate enqueues all renderable playlists + returns willRender"
```

---

### Task 11: Delete playlist/group → registry + asset cleanup

**Files:**
- Modify: `mosaicmesh/render.py` (add `cleanup_playlist_renders`, `cleanup_group_renders`)
- Modify: `mosaicmesh/api/playlists.py:125-144` (`api_playlists_delete`), `mosaicmesh/websocket/legacy.py:507-509` (`DELETE_PLAYLIST`)
- Modify: `mosaicmesh/api/displays.py` (DELETE handler — call `cleanup_group_renders`)
- Test: `tests/unit/test_render_triggers.py` (extend)

**Context:** Deleting a playlist removes its registry entry from every group and deletes its rendered asset files. Deleting a group drops its whole registry + assets.

- [ ] **Step 1: Write the failing test (append to `tests/unit/test_render_triggers.py`)**

```python
def test_cleanup_playlist_renders_removes_entries(fresh_settings):
    from mosaicmesh.state import Display
    from mosaicmesh import render as R
    d1 = Display(); d2 = Display()
    fresh_settings.displays["G1"] = d1
    fresh_settings.displays["G2"] = d2
    R._set_render_state(d1, "P", R.RENDER_READY, token="t")
    R._set_render_state(d2, "P", R.RENDER_READY, token="t")
    R._set_render_state(d2, "Q", R.RENDER_READY, token="t")
    R.cleanup_playlist_renders("P")
    assert "P" not in d1.renders
    assert "P" not in d2.renders
    assert "Q" in d2.renders   # untouched
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_render_triggers.py -c tests/pytest.ini -v`
Expected: FAIL — `cleanup_playlist_renders` not defined.

- [ ] **Step 3a: Implement (in `mosaicmesh/render.py`)**

```python
def _delete_render_assets(playlist_name, display_id):
    """Delete on-disk seg_/ind_ assets for a (playlist, group) across all its
    tokens. Best-effort; missing files are fine."""
    import server, glob
    display = server.settings.displays.get(display_id)
    if not display:
        return
    pl = server.settings.playlists.get(playlist_name)
    if pl is None:
        return
    elements = _build_media_elements(pl.items)
    token = (display.renders.get(playlist_name) or {}).get("token", "")
    if not token:
        return
    for key, _c in _group_clients(display_id):
        for sub in ("videos", "images"):
            for prefix in ("seg_", "ind_"):
                for path in glob.glob(os.path.join("media", key, sub, prefix + token + "_*")):
                    try:
                        os.remove(path)
                    except OSError:
                        pass


def cleanup_playlist_renders(playlist_name):
    """Remove a playlist's render entry + assets from every group (on delete)."""
    import server
    for did, display in server.settings.displays.items():
        if playlist_name in (getattr(display, "renders", {}) or {}):
            _delete_render_assets(playlist_name, did)
            display.renders.pop(playlist_name, None)
    _broadcast_renders_changed(force=True)


def cleanup_group_renders(display_id):
    """Drop a group's whole render registry + assets (on group delete)."""
    import server
    display = server.settings.displays.get(display_id)
    if not display:
        return
    for name in list((getattr(display, "renders", {}) or {}).keys()):
        _delete_render_assets(name, display_id)
    display.renders = {}
    _broadcast_renders_changed(force=True)
```

- [ ] **Step 3b: Wire into delete paths**

REST `api_playlists_delete` (`mosaicmesh/api/playlists.py`), after `del server.settings.playlists[name]` (line 142) and before `saveSettings()`:

```python
    from mosaicmesh import render as _render
    _render.cleanup_playlist_renders(name)
```

Legacy `DELETE_PLAYLIST` (`mosaicmesh/websocket/legacy.py:507-509`), after the `.pop(...)`:

```python
        from mosaicmesh import render as _render
        _render.cleanup_playlist_renders(msg["PAYLOAD"].get("name"))
```

Group DELETE in `mosaicmesh/api/displays.py` — locate the handler that deletes a display group; after it removes the group from `settings.displays`, call `cleanup_group_renders(display_id)` BEFORE the dict removal (it reads `settings.displays[display_id]`), or capture the display object first. Reorder so cleanup runs while the group still exists.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_render_triggers.py -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render.py mosaicmesh/api/playlists.py mosaicmesh/api/displays.py mosaicmesh/websocket/legacy.py tests/unit/test_render_triggers.py
git commit -m "feat(render): cleanup render entries + assets on playlist/group delete"
```

---

### Task 12: Repurpose `RENDER` handler to failure-retry

**Files:**
- Modify: `mosaicmesh/websocket/legacy.py:461-477` (`RENDER`)
- Test: `tests/unit/test_render_triggers.py` (extend)

**Context:** The only manual render affordance is retrying a FAILED (playlist, group). The `RENDER` handler now takes `{displayID, name}` and re-enqueues that one combo, only if its current state is FAILED (else it's a no-op informational reply). The old "render the applied playlist" semantics are gone.

- [ ] **Step 1: Write the failing test (append to `tests/unit/test_render_triggers.py`)**

```python
def test_render_handler_retries_failed(fresh_settings, monkeypatch):
    import jsonpickle
    from mosaicmesh.state import Display, Playlist, Client
    from mosaicmesh import render as R
    from mosaicmesh.websocket.legacy import msg_response
    enq = []
    monkeypatch.setattr("mosaicmesh.render_queue.enqueue",
                        lambda name, did: enq.append((name, did)) or True)
    d = Display(); d.boundingBox = [0, 0, 10, 10]
    fresh_settings.displays["G1"] = d
    c = Client(); c.displayID = "G1"; c.deviceWidth = 100; c.deviceHeight = 100
    c.measuredPerimeter = [0, 0, 5, 0, 5, 5, 0, 5]
    fresh_settings.clients["c1"] = c
    pl = Playlist(); pl.name = "P"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    fresh_settings.playlists["P"] = pl
    R._set_render_state(d, "P", R.RENDER_FAILED, token="t", error="boom")

    msg = {"REQUEST": "RENDER", "PAYLOAD": {"displayID": "G1", "name": "P"},
           "SRC": "admin", "DEST": "SRV"}
    out = msg_response(msg, None)
    assert ("P", "G1") in enq
    assert out["PAYLOAD"]["status"] in ("QUEUED", "rendering")
```

Note: confirm `msg_response`'s real signature (it may be `msg_response(msg, session)`). Match the existing test calls in `tests/unit/` (grep `msg_response(`); adjust the call to the established convention.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_render_triggers.py -c tests/pytest.ini -v`
Expected: FAIL — handler still uses old semantics / doesn't enqueue by name.

- [ ] **Step 3: Replace the `RENDER` branch (`mosaicmesh/websocket/legacy.py:461-477`)**

```python
    elif(msg["REQUEST"] == "RENDER"):
        # Manual retry of a FAILED (playlist, group) render — the ONLY manual
        # render affordance left. PAYLOAD = {displayID, name}.
        from mosaicmesh import render_queue
        payload = msg["PAYLOAD"]
        display_id = payload.get("displayID")
        name = payload.get("name")
        display = server.settings.displays.get(display_id)
        if not display or name not in server.settings.playlists:
            response["PAYLOAD"] = {"status": "ERROR", "error": "unknown playlist/group"}
        elif not _group_is_calibrated(display_id):
            response["PAYLOAD"] = {"status": "ERROR", "error": "group not calibrated"}
        else:
            elements = _build_media_elements(server.settings.playlists[name].items)
            _set_render_state(display, name, RENDER_QUEUED,
                              token=render_token(elements, display_id))
            render_queue.enqueue(name, display_id)
            response["PAYLOAD"] = {"status": "QUEUED", "displayID": display_id, "name": name}
```

Ensure `_group_is_calibrated`, `_build_media_elements`, `render_token`, `_set_render_state`, `RENDER_QUEUED` are imported into `legacy.py` (it already imports many render symbols near the top — add any missing names to that import).

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_render_triggers.py -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/websocket/legacy.py tests/unit/test_render_triggers.py
git commit -m "feat(render): repurpose RENDER handler to FAILED-render retry by name"
```

---

## Phase D — Gating

### Task 13: Gate `PLAY` + `ASSIGN_PLAYLIST` on registry readiness

**Files:**
- Modify: `mosaicmesh/websocket/legacy.py:379-397` (`PLAY`), `:511-527` (`ASSIGN_PLAYLIST`)
- Modify: `mosaicmesh/render.py:712-727` (`_apply_playlist`) — sync `display.renderedToken` from the registry
- Test: `tests/unit/test_render_gating.py`

**Context:** `PLAY` must refuse a renderable applied playlist that isn't READY. `ASSIGN_PLAYLIST` must report readiness. `_apply_playlist` sets `display.renderedToken` to the playlist's READY token (so `_per_client_items` resolves the right `seg_<token>` URLs) or `""` if not ready.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_render_gating.py
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
from mosaicmesh.state import Settings, Display, Playlist, Client
from mosaicmesh import render as R
from mosaicmesh.websocket.legacy import msg_response


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev


def _calibrated_group_with_seg_playlist(settings):
    d = Display(); d.boundingBox = [0, 0, 10, 10]
    settings.displays["G1"] = d
    c = Client(); c.displayID = "G1"; c.deviceWidth = 100; c.deviceHeight = 100
    c.measuredPerimeter = [0, 0, 5, 0, 5, 5, 0, 5]
    settings.clients["c1"] = c
    pl = Playlist(); pl.name = "P"
    pl.items = [{"id": 0, "file": "/media/server/videos/a.mp4", "playmode": "SEGMENT"}]
    settings.playlists["P"] = pl
    return d, pl


def test_play_rejects_unready(fresh_settings):
    d, pl = _calibrated_group_with_seg_playlist(fresh_settings)
    R._apply_playlist("G1", pl)   # applied but never rendered
    out = msg_response({"REQUEST": "PLAY", "PAYLOAD": {"displayID": "G1"},
                        "SRC": "a", "DEST": "SRV"}, None)
    assert out["PAYLOAD"]["status"] == "RENDER_REQUIRED"


def test_play_allows_ready(fresh_settings, monkeypatch):
    d, pl = _calibrated_group_with_seg_playlist(fresh_settings)
    R._apply_playlist("G1", pl)
    tok = R.render_token(R._build_media_elements(pl.items), "G1")
    R._set_render_state(d, "P", R.RENDER_READY, token=tok)
    # _apply_playlist again to sync renderedToken from the now-READY entry
    R._apply_playlist("G1", pl)
    started = []
    monkeypatch.setattr("mosaicmesh.render._begin_prepare", lambda did: started.append(did))
    out = msg_response({"REQUEST": "PLAY", "PAYLOAD": {"displayID": "G1"},
                        "SRC": "a", "DEST": "SRV"}, None)
    assert out["PAYLOAD"] == "SUCCESS"
    assert started == ["G1"]


def test_assign_reports_render_required(fresh_settings):
    d, pl = _calibrated_group_with_seg_playlist(fresh_settings)
    out = msg_response({"REQUEST": "ASSIGN_PLAYLIST", "PAYLOAD": {"displayID": "G1", "name": "P"},
                        "SRC": "a", "DEST": "SRV"}, None)
    assert out["PAYLOAD"]["status"] == "RENDER_REQUIRED"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_render_gating.py -c tests/pytest.ini -v`
Expected: FAIL — PLAY still uses `renderedToken`/`compute_render_token` and `_apply_playlist` doesn't sync from registry, so readiness states differ from expectations.

- [ ] **Step 3a: Update `_apply_playlist` (`mosaicmesh/render.py:712-727`)**

Replace the body after `display.loop = bool(pl.loop)` so it syncs `renderedToken` from the registry:

```python
def _apply_playlist(display_id, pl):
    """Copy a saved Playlist onto a group (mediaElements, loop, PRELOAD) and
    sync display.renderedToken from the render registry so the per-client PLAY
    URLs (_per_client_items) resolve the right seg_<token> assets. Sets the
    live token to the playlist's READY token, else "" (not ready)."""
    import server
    display = server.settings.displays.setdefault(display_id, Display())
    display.mediaElements = _build_media_elements(pl.items)
    display.currentPlaylistName = getattr(pl, "name", None)
    display.loop = bool(pl.loop)
    name = getattr(pl, "name", None)
    entry = (getattr(display, "renders", {}) or {}).get(name)
    cur = render_token(display.mediaElements, display_id)
    if entry and entry.get("state") == RENDER_READY and entry.get("token") == cur:
        display.renderedToken = cur
    else:
        display.renderedToken = ""
    _broadcast_per_client_preload(display_id, display.mediaElements)
```

- [ ] **Step 3b: Update `PLAY` branch (`mosaicmesh/websocket/legacy.py:379-397`)**

Replace the render-gate lines (the `has_renderable` checks) with registry-aware logic:

```python
    elif(msg["REQUEST"] == "PLAY"):
        display_id = msg["PAYLOAD"]["displayID"]
        display = server.settings.displays.get(display_id)
        if not display or not display.mediaElements:
            response["PAYLOAD"] = "SUCCESS"
        else:
            now_ms = int(time.time() * 1000)
            resume_epoch = now_ms - display.pauseOffset if display.action == PlayState.PAUSE else now_ms
            name = getattr(display, "currentPlaylistName", None)
            has_renderable = any(_is_renderable(me) for me in display.mediaElements)
            entry = (getattr(display, "renders", {}) or {}).get(name) if name else None
            state = entry.get("state") if entry else None
            if has_renderable and state in (RENDER_QUEUED, RENDER_RENDERING):
                response["PAYLOAD"] = {"status": "RENDER_IN_PROGRESS", "displayID": display_id}
            elif has_renderable and not is_playlist_ready(name, display_id):
                response["PAYLOAD"] = {"status": "RENDER_REQUIRED", "displayID": display_id}
            else:
                if display.action == PlayState.PAUSE:
                    _start_group_playback(display_id, resume_epoch)
                else:
                    _begin_prepare(display_id)
                response["PAYLOAD"] = "SUCCESS"
```

- [ ] **Step 3c: Update `ASSIGN_PLAYLIST` branch (`mosaicmesh/websocket/legacy.py:511-527`)**

```python
    elif(msg["REQUEST"] == "ASSIGN_PLAYLIST"):
        payload = msg["PAYLOAD"]
        display_id = payload.get("displayID")
        name = payload.get("name")
        pl = server.settings.playlists.get(name)
        if pl is None or display_id is None:
            response["PAYLOAD"] = {"status": "error", "displayID": display_id}
        else:
            _apply_playlist(display_id, pl)
            display = server.settings.displays.get(display_id)
            has_renderable = any(_is_renderable(me) for me in display.mediaElements)
            if has_renderable and not _group_is_calibrated(display_id):
                status = "NOT_CALIBRATED"
            elif has_renderable and not is_playlist_ready(name, display_id):
                status = "RENDER_REQUIRED"
            else:
                status = "ok"
            response["PAYLOAD"] = {"status": status, "displayID": display_id}
```

Ensure `is_playlist_ready`, `RENDER_QUEUED`, `RENDER_RENDERING`, `_group_is_calibrated` are imported in `legacy.py`.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_render_gating.py -c tests/pytest.ini -v`
Expected: PASS (3 tests). Also re-run `tests/unit -k "render or playlist"` → green.

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render.py mosaicmesh/websocket/legacy.py tests/unit/test_render_gating.py
git commit -m "feat(render): gate PLAY/ASSIGN_PLAYLIST on registry readiness; sync renderedToken in _apply_playlist"
```

---

### Task 14: Gate schedules (`evaluate_schedules` + REST validation)

**Files:**
- Modify: `server.py:2114-2121` (`evaluate_schedules` render gate)
- Modify: `mosaicmesh/api/schedules.py:94-134` (`_validate_fields`) — optional readiness warning (non-blocking)
- Test: `tests/unit/test_render_gating.py` (extend)

**Context:** `evaluate_schedules` currently triggers `render_group_async` (the applied-playlist legacy path). With the registry, it should: enqueue via the queue if not ready, hold PLAY until ready, and only `_start_group_playback` when `is_playlist_ready`. REST schedule create/update keeps FK validation; readiness isn't a hard reject at create time (the playlist may legitimately be rendering), but we surface it.

- [ ] **Step 1: Write the failing test (append to `tests/unit/test_render_gating.py`)**

```python
def test_evaluate_schedules_holds_until_ready(fresh_settings, monkeypatch):
    from mosaicmesh.state import Schedule
    import datetime
    d, pl = _calibrated_group_with_seg_playlist(fresh_settings)
    s = Schedule(); s.id = "s1"; s.displayID = "G1"; s.playlistName = "P"
    s.enabled = True; s.freq = "DAILY"; s.dtstart = "2020-01-01"
    s.startTime = "00:00"; s.endTime = "23:59"
    fresh_settings.schedules["s1"] = s

    enq = []
    monkeypatch.setattr("mosaicmesh.render_queue.enqueue",
                        lambda name, did: enq.append((name, did)) or True)
    started = []
    monkeypatch.setattr(server, "_start_group_playback", lambda did: started.append(did))

    server.evaluate_schedules(datetime.datetime(2020, 1, 1, 12, 0))
    assert ("P", "G1") in enq   # not ready → enqueued, not played
    assert started == []

    # Now mark ready and re-evaluate → it plays.
    tok = R.render_token(R._build_media_elements(pl.items), "G1")
    R._set_render_state(d, "P", R.RENDER_READY, token=tok)
    server.evaluate_schedules(datetime.datetime(2020, 1, 1, 12, 0))
    assert started == ["G1"]
```

Confirm `server._start_group_playback` is the symbol `evaluate_schedules` calls (it's imported at server.py:69). If `evaluate_schedules` calls the bare name `_start_group_playback`, monkeypatch `server._start_group_playback` AND ensure the function references the module global (it does — imported into server namespace).

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_render_gating.py -c tests/pytest.ini -v`
Expected: FAIL — current code calls `render_group_async` + checks `renderedToken`, so it won't enqueue via the queue and may play stale.

- [ ] **Step 3: Update the `evaluate_schedules` render gate (`server.py:2114-2121`)**

Replace:

```python
            has_renderable = any(_is_renderable(me) for me in display.mediaElements)
            if has_renderable and compute_render_token(display_id) != display.renderedToken:
                if display.renderStatus != "rendering":
                    asyncio.ensure_future(render_group_async(display_id))
                    display.scheduledPlaying = False
            elif not getattr(display, "scheduledPlaying", False):
                _start_group_playback(display_id)
                display.scheduledPlaying = True
```

with:

```python
            has_renderable = any(_is_renderable(me) for me in display.mediaElements)
            if has_renderable and not is_playlist_ready(playlist_name, display_id):
                # Not ready: enqueue (idempotent) and HOLD — don't play stale/un-rendered.
                from mosaicmesh import render_queue, render as _render
                elements = _render._build_media_elements(
                    settings.playlists[playlist_name].items)
                _render._set_render_state(display, playlist_name, _render.RENDER_QUEUED,
                                          token=_render.render_token(elements, display_id))
                render_queue.enqueue(playlist_name, display_id)
                display.scheduledPlaying = False
            elif not getattr(display, "scheduledPlaying", False):
                _start_group_playback(display_id)
                display.scheduledPlaying = True
```

Ensure `is_playlist_ready` is reachable in `server.py` (add to the render import list at line 62-69 if not already wildcard-imported).

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_render_gating.py -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_render_gating.py
git commit -m "feat(render): evaluate_schedules holds non-ready playlists + enqueues via queue"
```

---

## Phase E — API + broadcast

### Task 15: `GET /api/renders` endpoint + route

**Files:**
- Create: `mosaicmesh/api/renders.py`
- Modify: `server.py` (register the route alongside the other `/api/*` routes)
- Test: `tests/unit/test_api_renders.py`

**Context:** The fleet-wide feed = `renders_snapshot()` (Task 5) + queue depth.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_api_renders.py
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

import json
import pytest
from aiohttp.test_utils import make_mocked_request
from mosaicmesh.state import Settings, Display
from mosaicmesh import render as R
from mosaicmesh.api.renders import api_renders_list


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev


@pytest.mark.asyncio
async def test_renders_list_shape(fresh_settings):
    d = Display()
    fresh_settings.displays["G1"] = d
    R._set_render_state(d, "P", R.RENDER_RENDERING, token="t", percent=42, eta=30)
    resp = await api_renders_list(make_mocked_request('GET', '/api/renders'))
    assert resp.status == 200
    data = json.loads(resp.text)
    assert data["success"] is True
    assert "queueDepth" in data
    row = next(r for r in data["renders"] if r["playlist"] == "P")
    assert row["displayID"] == "G1"
    assert row["state"] == "RENDERING"
    assert row["percent"] == 42
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_api_renders.py -c tests/pytest.ini -v`
Expected: FAIL — `No module named 'mosaicmesh.api.renders'`.

- [ ] **Step 3a: Create `mosaicmesh/api/renders.py`**

```python
"""GET /api/renders — fleet-wide snapshot of the per-(playlist, group) render
registry plus the queue depth. Read-only; drives the admin Render Status panel.
Follows the project {success, ...} response convention."""
from aiohttp import web

from mosaicmesh import render as _render
from mosaicmesh import render_queue

__all__ = ["api_renders_list"]


async def api_renders_list(request):
    """GET /api/renders -> {success, renders: [...], queueDepth: N}."""
    return web.json_response({
        "success": True,
        "renders": _render.renders_snapshot(),
        "queueDepth": render_queue.queue_depth(),
    })
```

- [ ] **Step 3b: Register the route in `server.py`**

Find where the other `/api/*` GET routes are added (search `'/api/playback'` or `api_playlists_list`). Add:

```python
    from mosaicmesh.api.renders import api_renders_list
    app.router.add_get('/api/renders', api_renders_list)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_api_renders.py -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/api/renders.py server.py tests/unit/test_api_renders.py
git commit -m "feat(render): GET /api/renders fleet-wide snapshot endpoint"
```

---

## Phase F — Client UI

### Task 16: `api.getRenders()` + store `renders` slice + RENDERS_CHANGED handler

**Files:**
- Modify: `js/timeline/api.js` (add `getRenders`)
- Modify: `js/timeline/store.js` (add `renders` slice, hydrate, `setRenders`, `isPlaylistReady`/`renderStateFor` helpers)
- Modify: `js/timeline/timeline/sockjs-status.js` (add `RENDERS_CHANGED` case)
- Test: `tests/unit/js/` — extend the module-load smoke; add a pure-helper test if a pure helper is extracted.

**Context:** The store hydrates `/api/renders` into `renders` keyed by `displayID -> {playlistName -> entry}`, and a derived `rendersList` for the panel. SockJS `RENDERS_CHANGED` replaces the slice. `isPlaylistReady(name, displayID)` mirrors the server predicate for UI gating (Play Now picker, Content badges).

- [ ] **Step 1: Write the failing test**

Add to the JS module-load smoke (`tests/unit/js/` — find the existing smoke that imports modules; mirror its style). Create `tests/unit/js/render-helpers.test.js`:

```javascript
// tests/unit/js/render-helpers.test.js
import { test } from 'node:test';
import assert from 'node:assert';
import { isReadyFromEntry, renderBadge } from '../../../js/timeline/util/render-helpers.js';

test('isReadyFromEntry: missing entry is not ready', () => {
  assert.equal(isReadyFromEntry(undefined), false);
});

test('isReadyFromEntry: READY is ready', () => {
  assert.equal(isReadyFromEntry({ state: 'READY' }), true);
});

test('renderBadge maps states to labels', () => {
  assert.equal(renderBadge({ state: 'RENDERING', percent: 40 }), 'rendering… 40%');
  assert.equal(renderBadge({ state: 'QUEUED' }), 'queued');
  assert.equal(renderBadge({ state: 'FAILED' }), 'render failed');
  assert.equal(renderBadge({ state: 'READY' }), 'ready');
  assert.equal(renderBadge(undefined), 'not rendered');
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/render-helpers.test.js`
Expected: FAIL — `Cannot find module .../util/render-helpers.js`.

- [ ] **Step 3a: Create `js/timeline/util/render-helpers.js`**

```javascript
// Pure helpers for render-state UI. No DOM, no store — node-testable.

export function isReadyFromEntry(entry) {
  return !!(entry && entry.state === 'READY');
}

export function renderBadge(entry) {
  if (!entry) return 'not rendered';
  switch (entry.state) {
    case 'READY': return 'ready';
    case 'QUEUED': return 'queued';
    case 'RENDERING':
      return (typeof entry.percent === 'number')
        ? `rendering… ${entry.percent}%` : 'rendering…';
    case 'STALE': return 'needs re-render';
    case 'FAILED': return 'render failed';
    default: return 'not rendered';
  }
}
```

- [ ] **Step 3b: `js/timeline/api.js` — add `getRenders`** (mirror `getPlayback`)

```javascript
  async getRenders() {
    const r = await fetch('/api/renders');
    if (!r.ok) throw new ApiError(`GET /api/renders -> ${r.status}`, { status: r.status, body: await r.json().catch(() => ({})) });
    const body = await r.json();
    return { renders: body.renders || [], queueDepth: body.queueDepth || 0 };
  },
```

- [ ] **Step 3c: `js/timeline/store.js` — slice + hydrate + setter**

Add to the store state init (near `renderInProgress: {}`, line ~19): `renders: {}, // displayID -> {playlistName -> entry}` and `renderQueueDepth: 0,`.

In `hydrate()`, extend the `Promise.all` destructuring and assignment. Add `api.getRenders()` to the array and:

```javascript
      const rd = await api.getRenders();
      this.setRenders(rd.renders, rd.queueDepth);
```

(Place this after the existing assignments; a second await is fine — or add it to the `Promise.all` tuple. Match the existing pattern: append `api.getRenders()` to the array and destructure `rn`, then `this.setRenders(rn.renders, rn.queueDepth)`.)

Add methods to the store:

```javascript
    setRenders(rows, queueDepth) {
      const map = {};
      for (const r of (rows || [])) {
        (map[r.displayID] = map[r.displayID] || {})[r.playlist] = r;
      }
      this.renders = map;
      if (typeof queueDepth === 'number') this.renderQueueDepth = queueDepth;
    },
    renderEntry(playlistName, displayID) {
      return (this.renders[displayID] || {})[playlistName] || null;
    },
    isPlaylistReady(playlistName, displayID) {
      // N/A playlists (no SEGMENT/INDIVIDUAL items) are always ready.
      const pl = this.playlists[playlistName];
      const renderable = !!(pl && (pl.items || []).some(
        (it) => it.playmode === 'SEGMENT' || it.playmode === 'INDIVIDUAL'));
      if (!renderable) return true;
      const e = this.renderEntry(playlistName, displayID);
      return !!(e && e.state === 'READY');
    },
    get rendersList() {
      const out = [];
      for (const did of Object.keys(this.renders)) {
        for (const name of Object.keys(this.renders[did])) out.push(this.renders[did][name]);
      }
      return out.filter((e) => e.state !== 'READY');  // panel shows active/failed only
    },
```

- [ ] **Step 3d: `js/timeline/timeline/sockjs-status.js` — add `RENDERS_CHANGED`**

In `handle()` (before the closing of the if/else chain, ~line 118), add:

```javascript
  } else if (req === 'RENDERS_CHANGED') {
    const rows = payload?.renders ?? [];
    applyMutation(() => store.setRenders(rows));
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/render-helpers.test.js` → PASS.
Run the module-load smoke (`node --test tests/unit/js/*.js`) → PASS (no import errors from store/api/sockjs changes).

- [ ] **Step 5: Commit**

```bash
git add js/timeline/util/render-helpers.js js/timeline/api.js js/timeline/store.js js/timeline/timeline/sockjs-status.js tests/unit/js/render-helpers.test.js
git commit -m "feat(admin): render-state store slice + getRenders + RENDERS_CHANGED handler"
```

---

### Task 17: Play Now — filter to ready + read PLAY response

**Files:**
- Modify: `js/timeline/modals/play-now.js`
- Test: covered by Task 21 e2e (no node unit — DOM-heavy). Add a pure-helper test only if logic is extracted.

**Context:** The picker lists every playlist; ready ones are clickable, not-ready renderable ones are disabled with a reason. `firePlayNow` reads the `PLAY` reply and toasts the real result. SockJS `generateMessage`/`sock.send` is fire-and-forget; to read the reply we listen for the next `PLAY`-status message addressed to this group, or use the existing response convention. Since the legacy socket is one-way broadcast, the simplest correct approach: after sending, the server's per-group `PLAYBACK_CHANGED`/the `PLAY` response is delivered via the socket reply envelope. Use the established pattern already used elsewhere for reading a one-shot reply (grep for how other modals read a SockJS reply, e.g. a `once`-style handler keyed by REQUEST). If no such pattern exists, gate purely client-side (picker only shows ready) and keep the toast optimistic but accurate to the picker filter.

- [ ] **Step 1 (decision step, no code):** Grep `js/timeline` for any existing "await a SockJS reply" helper (search `onmessage`, `REQUEST ===`, `pendingReplies`). If one exists, use it. If not, the picker-filter is the gate and `firePlayNow` stays send-only but is only reachable for ready playlists.

- [ ] **Step 2: Update `openPlayNowModal` (filter)** — replace the picker loop in `js/timeline/modals/play-now.js:64-81`:

```javascript
  } else {
    const ul = document.createElement('ul');
    ul.className = 'mm-play-now-list';
    for (const name of names) {
      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.type = 'button';
      const ready = store.isPlaylistReady(name, displayID);
      btn.className = 'btn mm-play-now-pick';
      btn.textContent = name;
      if (!ready) {
        const entry = store.renderEntry(name, displayID);
        const why = entry && entry.state === 'FAILED' ? 'render failed'
          : entry && (entry.state === 'RENDERING' || entry.state === 'QUEUED') ? 'rendering…'
          : 'not rendered for this group';
        btn.disabled = true;
        btn.title = why;
        btn.textContent = `${name} — ${why}`;
      } else {
        btn.addEventListener('click', function () {
          firePlayNow(store, displayID, name);
          closeModal();
        });
      }
      li.appendChild(btn);
      ul.appendChild(li);
    }
    root.appendChild(ul);
  }
```

- [ ] **Step 3: Harden `firePlayNow` toast** — replace lines 28-37 so the toast reflects the picker gate (and, if a reply helper exists, the real status):

```javascript
export function firePlayNow(store, displayID, playlistName) {
  if (!sockReady(store)) return;
  if (!store.isPlaylistReady(playlistName, displayID)) {
    store.toast(`"${playlistName}" isn't rendered for "${displayID}" yet.`, 'error');
    return;
  }
  try {
    window.sock.send(window.generateMessage('SRV', 'ASSIGN_PLAYLIST', { displayID, name: playlistName }));
    window.sock.send(window.generateMessage('SRV', 'PLAY', { displayID }));
    store.toast(`Playing "${playlistName}" on "${displayID}" now.`, 'info');
  } catch (e) {
    store.toast(`Failed to start playback: ${(e && e.message) || e}`, 'error');
  }
}
```

- [ ] **Step 4: Verify** — `node --test tests/unit/js/*.js` (module-load smoke) → PASS. Functional check deferred to Task 21 e2e.

- [ ] **Step 5: Commit**

```bash
git add js/timeline/modals/play-now.js
git commit -m "feat(admin): Play Now lists only ready playlists; guards fire on un-rendered"
```

---

### Task 18: Fleet — remove Render now, show read-only readiness

**Files:**
- Modify: `js/timeline/fleet/fleet-view.js:68-81` (delete `renderNow`)
- Modify: `admin.html:996` (delete the "⟳ Render now" button)
- Modify: `js/timeline/fleet/fleet-status.js` (add `playlistReadinessForGroup`) + `admin.html` Fleet detail (render the readiness line)
- Test: extend the Fleet node test if one exists for `fleet-status.js`; else covered by e2e.

- [ ] **Step 1: Write/extend the failing test** — if `tests/unit/js/` has a fleet-status test, add:

```javascript
test('playlistReadinessForGroup labels each playlist', () => {
  const playlists = { A: { items: [{ playmode: 'SEGMENT' }] }, B: { items: [{ playmode: 'FULL' }] } };
  const renders = { G1: { A: { state: 'RENDERING', percent: 50 } } };
  const rows = playlistReadinessForGroup('G1', playlists, renders);
  assert.deepEqual(rows.find(r => r.name === 'A').label, 'rendering… 50%');
  assert.equal(rows.find(r => r.name === 'B').label, 'ready'); // N/A → ready
});
```

- [ ] **Step 2: Run** — `node --test tests/unit/js/fleet-status.test.js` → FAIL (function missing). (If no such test file exists, create `tests/unit/js/fleet-status.test.js` with the import + this test.)

- [ ] **Step 3a: Add helper to `js/timeline/fleet/fleet-status.js`**

```javascript
import { isReadyFromEntry, renderBadge } from '../util/render-helpers.js';

export function playlistReadinessForGroup(displayID, playlists, renders) {
  const reg = (renders && renders[displayID]) || {};
  return Object.keys(playlists || {}).sort().map((name) => {
    const pl = playlists[name];
    const renderable = (pl.items || []).some(
      (it) => it.playmode === 'SEGMENT' || it.playmode === 'INDIVIDUAL');
    if (!renderable) return { name, label: 'ready', ready: true };
    const entry = reg[name];
    return { name, label: renderBadge(entry), ready: isReadyFromEntry(entry) };
  });
}
```

- [ ] **Step 3b: Delete `renderNow()`** from `js/timeline/fleet/fleet-view.js` (lines 68-81). Add a getter the template uses:

```javascript
  get playlistReadiness() {
    const { playlistReadinessForGroup } = window.__fleetStatus || {};
    // If fleet-status helpers are imported at module top, call directly instead.
    return playlistReadinessForGroup
      ? playlistReadinessForGroup(this.selectedGroupId, this.$store.mm.playlists, this.$store.mm.renders)
      : [];
  },
```

Prefer a top-level `import { playlistReadinessForGroup } from './fleet-status.js';` and call it directly in the getter (drop the `window.__fleetStatus` shim). Match how `fleet-view.js` already imports `groupStatusLine`.

- [ ] **Step 3c: `admin.html`** — remove the Render now button (line 996). In the Fleet group-detail (after the status line ~line 989), add a read-only readiness block:

```html
<div class="mm-fleet-render-readiness" x-show="selectedGroupId">
  <h4>Rendered content</h4>
  <ul>
    <template x-for="r in playlistReadiness" :key="r.name">
      <li><span x-text="r.name"></span>
        <span class="mm-render-badge" :class="r.ready ? 'is-ready' : 'is-pending'" x-text="r.label"></span>
      </li>
    </template>
  </ul>
</div>
```

- [ ] **Step 4: Run** — `node --test tests/unit/js/fleet-status.test.js` → PASS. Module-load smoke → PASS.

- [ ] **Step 5: Commit**

```bash
git add js/timeline/fleet/fleet-view.js js/timeline/fleet/fleet-status.js admin.html tests/unit/js/fleet-status.test.js
git commit -m "feat(admin): Fleet drops Render now, shows read-only per-playlist readiness"
```

---

### Task 19: Content — per-playlist render badge + Retry

**Files:**
- Modify: `js/timeline/content/content-view.js` (add `playlistRenderSummary(name)` + `retryRender`)
- Modify: `admin.html:849-863` (playlist row — add badge + Retry)
- Test: extend `tests/unit/js/render-helpers.test.js` if a summary helper is extracted as pure.

**Context:** Per playlist, show readiness across calibrated groups: "rendered 3/3 · rendering on Lobby… · failed on OEB ⚠ (Retry)". Retry sends `RENDER {displayID, name}` for the failed group(s).

- [ ] **Step 1: Add pure summary helper to `js/timeline/util/render-helpers.js` + test**

```javascript
// append to render-helpers.js
export function playlistGroupSummary(name, displayGroups, renders, isRenderable) {
  // returns {total, ready, rendering, failed:[displayID], queued}
  const out = { total: 0, ready: 0, rendering: 0, failed: [], queued: 0 };
  if (!isRenderable) return out;   // N/A — nothing to summarize
  for (const g of (displayGroups || [])) {
    out.total += 1;
    const e = (renders[g.displayID] || {})[name];
    if (!e) continue;
    if (e.state === 'READY') out.ready += 1;
    else if (e.state === 'RENDERING') out.rendering += 1;
    else if (e.state === 'QUEUED') out.queued += 1;
    else if (e.state === 'FAILED') out.failed.push(g.displayID);
  }
  return out;
}
```

Test (append to `render-helpers.test.js`):

```javascript
import { playlistGroupSummary } from '../../../js/timeline/util/render-helpers.js';
test('playlistGroupSummary counts states', () => {
  const groups = [{ displayID: 'A' }, { displayID: 'B' }];
  const renders = { A: { P: { state: 'READY' } }, B: { P: { state: 'FAILED' } } };
  const s = playlistGroupSummary('P', groups, renders, true);
  assert.equal(s.total, 2); assert.equal(s.ready, 1);
  assert.deepEqual(s.failed, ['B']);
});
test('playlistGroupSummary N/A short-circuits', () => {
  assert.deepEqual(playlistGroupSummary('P', [{displayID:'A'}], {}, false).total, 0);
});
```

- [ ] **Step 2: Run** — `node --test tests/unit/js/render-helpers.test.js` → FAIL (function missing).

- [ ] **Step 3a: `content-view.js`** — add to `mmContentComponent()`:

```javascript
    playlistRenderSummary(name) {
      const { playlistGroupSummary } = this.$store.mm._renderHelpers || {};
      const pl = this.$store.mm.playlists[name];
      const renderable = !!(pl && (pl.items || []).some(
        (it) => it.playmode === 'SEGMENT' || it.playmode === 'INDIVIDUAL'));
      // import the helper at module top; call directly:
      return window.__pgsummary
        ? window.__pgsummary(name, this.$store.mm.displayGroups, this.$store.mm.renders, renderable)
        : { total: 0, ready: 0, rendering: 0, failed: [], queued: 0 };
    },
    retryRender(name) {
      const summary = this.playlistRenderSummary(name);
      if (typeof window.sock === 'undefined' || typeof window.generateMessage !== 'function') {
        this.$store.mm.toast('SockJS not available; reload the page.', 'error');
        return;
      }
      for (const displayID of summary.failed) {
        window.sock.send(window.generateMessage('SRV', 'RENDER', { displayID, name }));
      }
      this.$store.mm.toast(`Retrying render of "${name}" on ${summary.failed.length} group(s).`, 'info');
    },
```

Prefer a top-level `import { playlistGroupSummary } from '../util/render-helpers.js';` and call it directly (drop the `window.__pgsummary` shim).

- [ ] **Step 3b: `admin.html`** — in the playlist row (after `mm-playlist-meta`, line 857):

```html
        <span class="mm-playlist-render" x-show="playlistRenderSummary(p.name).total"
              x-text="'rendered ' + playlistRenderSummary(p.name).ready + '/' + playlistRenderSummary(p.name).total"></span>
        <button class="mm-playlist-retry" x-show="playlistRenderSummary(p.name).failed.length"
                @click="retryRender(p.name)" title="Retry failed renders">Retry ⚠</button>
```

- [ ] **Step 4: Run** — `node --test tests/unit/js/render-helpers.test.js` → PASS. Smoke → PASS.

- [ ] **Step 5: Commit**

```bash
git add js/timeline/util/render-helpers.js js/timeline/content/content-view.js admin.html tests/unit/js/render-helpers.test.js
git commit -m "feat(admin): Content per-playlist render summary + Retry"
```

---

### Task 20: Recalibrate warning + global Render Status panel

**Files:**
- Modify: `js/timeline/modals/calibration.js` (surface `willRender` from the calibrate response)
- Modify: `admin.html` (global Render Status panel/header indicator bound to `store.rendersList` + `store.renderQueueDepth`)
- Test: covered by e2e (Task 21); add a pure test only if a label helper is extracted.

**Context:** After a calibrate upload returns `{willRender:[...]}` (Task 10), the modal shows "Calibrating will render N playlists (~M min)". The global panel lists active/failed renders fleet-wide.

- [ ] **Step 1: `calibration.js`** — after the upload response is parsed (find where the modal reads `detected`/`mapped`), add:

```javascript
  const willRender = resp.willRender || [];
  if (willRender.length) {
    store.toast(`Calibration will (re)render ${willRender.length} playlist(s): ${willRender.join(', ')}.`, 'info');
  }
```

(If the modal renders a results summary element, append a line listing `willRender` and a rough ETA estimate `~${willRender.length * 1.5} min` — keep the estimate clearly approximate.)

- [ ] **Step 2: `admin.html`** — add a global Render Status surface. Near the top-level toolbar/header, add an indicator + drawer bound to the store:

```html
<div class="mm-render-status" x-data x-show="$store.mm.rendersList.length || $store.mm.renderQueueDepth">
  <button class="mm-render-status-toggle" @click="$store.mm.renderPanelOpen = !$store.mm.renderPanelOpen"
          x-text="'▣ ' + $store.mm.rendersList.length + ' rendering…'"></button>
  <div class="mm-render-status-drawer" x-show="$store.mm.renderPanelOpen">
    <h4>Render status</h4>
    <template x-if="!$store.mm.rendersList.length"><p>All renders up to date.</p></template>
    <ul>
      <template x-for="r in $store.mm.rendersList" :key="r.displayID + ':' + r.playlist">
        <li>
          <span x-text="r.playlist"></span> · <span x-text="r.displayID"></span>
          <progress x-show="r.state==='RENDERING'" :value="r.percent || 0" max="100"></progress>
          <span x-text="r.state + (r.percent!=null ? ' '+r.percent+'%' : '') + (r.eta!=null ? ' · ~'+r.eta+'s' : '')"></span>
          <button x-show="r.state==='FAILED'" @click="$store.mm.retryRenderGroup(r.playlist, r.displayID)">Retry</button>
        </li>
      </template>
    </ul>
  </div>
</div>
```

Add to the store: `renderPanelOpen: false,` and a `retryRenderGroup(name, displayID)` mutator that sends `RENDER {displayID, name}` (mirror Task 19's `retryRender` for a single group).

- [ ] **Step 3: Verify** — module-load smoke → PASS. Manual: open admin with a fake `/api/renders` returning an active job; confirm the indicator shows.

- [ ] **Step 4: Commit**

```bash
git add js/timeline/modals/calibration.js js/timeline/store.js admin.html
git commit -m "feat(admin): recalibrate warning + global Render Status panel"
```

---

## Phase G — Verify + finish

### Task 21: E2e smoke — gating + Fleet + Content

**Files:**
- Create: a spec runnable via `node tests/e2e/run.js render-model` (follow the existing `tests/e2e/run.js` spec structure — each spec creates + cleans up its own `__e2e_`-prefixed entities and calls `cleanupE2eOrphans` up front).
- Test: the spec itself.

**Context:** Catches reactivity/layout regressions Node `--test` can't. Requires dev server on `MM_BASE_URL` + `npm install` + chromium.

- [ ] **Step 1: Write the spec** — assertions:
  1. Create an `__e2e_seg` playlist with a SEGMENT item via REST; open admin; in Play Now for an uncalibrated/un-rendered group the `__e2e_seg` button is **disabled** with a "not rendered"/"rendering…" hint.
  2. Fleet group-detail has **no** "Render now" button (`assert page has no text "Render now"`).
  3. Content playlist row for `__e2e_seg` shows a render summary span.
  4. Cleanup: delete the playlist via REST.

Mirror an existing spec file's scaffolding exactly (imports, `cleanupE2eOrphans`, browser launch, teardown).

- [ ] **Step 2: Run** — `node tests/e2e/run.js render-model` (with dev server up).
Expected: initially FAIL if any UI wiring is off; iterate until PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/<spec-file>
git commit -m "test(e2e): render-model gating — Play Now disabled, no Render now, Content status"
```

---

### Task 22: Full suite + docs + final review

**Files:**
- Modify: `CLAUDE.md` (Architecture/Conventions — document the render registry + queue + gating), `js/timeline/README.md` (module map: `util/render-helpers.js`, `api/renders.py`, `render_queue.py`).
- Test: full runs.

- [ ] **Step 1: Run the full unit + JS suites**

Run: `python pytest_runner.py --unit`
Expected: PASS (all new suites + no regression in render/playlist/schedule/api tests).

Run: `python pytest_runner.py --js`
Expected: PASS.

- [ ] **Step 2: Update docs**

Add a Conventions bullet to `CLAUDE.md`:

```
- **Rendering is an automatic per-(playlist × group) asset (auto-render model).** Each `Display.renders[playlistName] = {token, state, updatedAt, error, percent, eta, startedAt}` is the source of truth for readiness; states QUEUED/RENDERING/READY/STALE/FAILED. Saving a playlist debounces (60s) then enqueues a render for every calibrated group via `mosaicmesh/render_queue.py` (bounded concurrency). Calibrating a group renders all its renderable playlists (with a warning). PLAY/ASSIGN_PLAYLIST/schedules/default-playlist all gate on `render.is_playlist_ready(name, displayID)`. The only manual render is `RENDER {displayID, name}` to retry a FAILED entry. Fleet-wide status: `GET /api/renders` + throttled `RENDERS_CHANGED` broadcast. `render_token(items, group)` (was `compute_render_token(group)`) hashes items + bbox + per-client quads; `_encode_group(items, group, token)` is the staging-safe encode body.
```

Update `mosaicmesh/__init__`-level docstring/Layout in `CLAUDE.md` to list `mosaicmesh/render_queue.py` and `mosaicmesh/api/renders.py`.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md js/timeline/README.md
git commit -m "docs: document auto-render model (registry, queue, gating, /api/renders)"
```

- [ ] **Step 4: Final review + finish the branch**

Dispatch a final code review across the whole implementation, then use `superpowers:finishing-a-development-branch` to verify tests pass and present merge/PR options.

---

## Self-Review

**1. Spec coverage:**
- Goal 1 (render automatic + decoupled): Tasks 4–6, 9 (debounced save fan-out), `render_playlist_for_group_async` doesn't touch `mediaElements`. ✓
- Goal 2 (can't assign/schedule/play un-rendered): Tasks 13 (PLAY/ASSIGN), 14 (schedules + default via evaluate_schedules). ✓
- Goal 3 (recalibration warns + re-renders): Task 10 (`mark_group_recalibrated` + `willRender`), Task 20 (warning UI). ✓
- Goal 4 (Render now leaves Fleet): Task 18 (delete button + `renderNow`), Task 12 (RENDER repurposed to retry only). ✓
- Goal 5 (Play Now shows only ready): Task 17. ✓
- Resolved decision 1 (new group renders all + warn): Task 10. ✓
- Resolved decision 2 (persist + boot revalidate): Task 2 (persist), Task 8 (revalidate). ✓
- Resolved decision 3 (60s debounce): Task 6 (`DEBOUNCE_SECONDS=60`). ✓
- Resolved decision 4 (fleet-wide status): Tasks 5 (snapshot+broadcast), 7 (progress parse), 15 (`/api/renders`), 16 (store/sockjs), 20 (panel). ✓
- "Keep playing stale until ready" (spec §Playback during re-render): partially — `render_playlist_for_group_async` is staging-safe so the live `mediaElements`/`renderedToken` aren't disturbed until the applied playlist's render reaches READY (then Task 5 syncs `renderedToken`). The explicit loop-boundary hot-swap is NOT separately implemented; document as a known follow-up (the current preload/cache-push path already swaps at the next PRELOAD/PLAY). **Gap flagged** — see note below.
- Asset cleanup (spec §Edge cases): Task 11. ✓

**Gap noted:** The spec's "hot-swap at next natural loop/item boundary" is approximated, not built — staging-safe rendering means no interruption, and `_apply_playlist`/PRELOAD swap URLs, but there's no explicit "wait for loop boundary" logic. This is acceptable for the plan (no blackout occurs; the swap happens at the next PRELOAD) and is called out for reviewer awareness rather than silently dropped. If the user wants the strict loop-boundary swap, add a task that defers the `renderedToken` sync + re-PRELOAD until the client reports a loop boundary.

**2. Placeholder scan:** No "TBD"/"implement later"/bare "add error handling". Decision steps (17 Step 1, 18 Step 3b shim) explicitly say "grep for the existing pattern and prefer the direct import" — concrete, not vague. ✓

**3. Type consistency:** `render_token(media_elements, display_id)` used consistently (Tasks 1, 3, 5, 6, 8, 10, 13). `_set_render_state(display, name, state, token=, error=, percent=, eta=, started=)` signature consistent (Tasks 3, 5, 10, 12, 13). Registry entry keys `{token, state, updatedAt, error, percent, eta, startedAt}` consistent (Tasks 2, 5, 15). `is_playlist_ready(name, display_id)` consistent (Tasks 3, 13, 14). State constants `RENDER_*` consistent. JS helpers `isReadyFromEntry`/`renderBadge`/`playlistGroupSummary` consistent (Tasks 16, 18, 19). ✓
