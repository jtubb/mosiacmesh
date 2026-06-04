# Server Module Split — Implementation Plan (PR-1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the 5151-line `server.py` into focused modules under `mosaicmesh/` without changing any behavior.

**Architecture:** Pure relocation. Class definitions move to `mosaicmesh/state.py`; helpers move to topic-named sibling modules; the `settings = Settings()` singleton **stays in `server.py`** so the existing `server.settings = mock_settings` test pattern continues to work unchanged. Extracted modules access settings via `import server; server.settings.X` (lazy at call time, never at import time — circular-import safe). After this plan, `server.py` contains only the entry point, route table, top-level handlers that bind routes, and the singleton.

**Tech Stack:** Python 3, `aiohttp`, `sockjs`, `jsonpickle`, `numpy`, `opencv` (`cv2`), `device_detector`. Tests: pytest + `python pytest_runner.py --unit`. No new runtime dependencies.

---

## Source spec

[`docs/superpowers/specs/2026-06-04-admin-timeline-redesign-design.md`](../specs/2026-06-04-admin-timeline-redesign-design.md) — Section 5 (Server module split) and Section 12 PR-1.

---

## File structure (target)

```
server.py                                  -- entry: route table, startup,
                                              process() loop, settings = Settings(),
                                              __main__. Re-imports the moved
                                              classes/functions for backward compat.
mosaicmesh/__init__.py                     -- empty
mosaicmesh/state.py                        -- Settings, Scripts, Display, PlayState,
                                              MediaElement, Playlist, Schedule,
                                              PlayMode, Client; _apply_default_scripts;
                                              migrate_client_objects
mosaicmesh/persistence.py                  -- save_settings_incremental, saveSettings,
                                              cleanup_old_clients, jsonpickle setup
mosaicmesh/cache.py                        -- init_json_cache, get_pooled_file_handle,
                                              close_file_pool, prewarm_static_cache,
                                              get_cached_file, file-handle pool
mosaicmesh/broadcast.py                    -- _send_to_session, _deliver,
                                              broadcast_to_client,
                                              broadcast_to_display_group
mosaicmesh/calibration.py                  -- order_points, _draw_fitted_label,
                                              group_bounding_box, reconstruct_screen_quad,
                                              _quad_box, _quad_iou, _quad_aspect,
                                              _aspect_in_marker_frame,
                                              reconcile_screen_quad,
                                              warp_image_for_screen, _hex_to_bgr,
                                              letterbox_to_aspect,
                                              assign_group_bounding_boxes, _group_clients,
                                              find_squares, angle_cos (helpers later
                                              in file)
mosaicmesh/render.py                       -- _keyframe_grid_args, _video_input_args,
                                              _video_encoder_args, _get_push_sem,
                                              compute_render_token,
                                              _broadcast_render_status, _is_renderable,
                                              _normalize_effect, _resolve_effect_filters,
                                              _run_ffmpeg, render_group_async,
                                              _render_output_dims, isVideoItem,
                                              quad_to_source_points,
                                              build_ffmpeg_perspective_cmd,
                                              build_ffmpeg_individual_cmd,
                                              get_video_dimensions, resolve_media_path,
                                              _duration_ms, _media_item_payload,
                                              _resolve_media_url, _build_media_elements,
                                              _apply_playlist, _start_group_playback,
                                              _stop_group_playback, _group_online_keys,
                                              _begin_prepare, _prepare_unsynced_clients,
                                              _per_client_items,
                                              _broadcast_per_client_play,
                                              _broadcast_per_client_preload
mosaicmesh/device_scripts.py               -- DEFAULT_DEVICE_SCRIPTS, WEBCLIP_BUNDLE_ID,
                                              WEBAPP_ICON_FBX/Y, SSH_*, _run_device_script,
                                              _launch_webapp_via_vnc
mosaicmesh/scheduling.py                   -- playlist_index, _parse_date, _hhmm_to_min,
                                              schedule_active_at
mosaicmesh/websocket/__init__.py           -- empty
mosaicmesh/websocket/legacy.py             -- msg_response (the big if/elif on REQUEST;
                                              moved BYTE-IDENTICAL)
mosaicmesh/websocket/typed.py              -- handle_websocket_message
mosaicmesh/websocket/dispatch.py           -- ws_handler, handle_client_disconnect
mosaicmesh/api/__init__.py                 -- empty
mosaicmesh/api/discovery.py                -- auto_configure_client, get_discovered_devices,
                                              _expected_seg_keys_for_display,
                                              _expected_segments_for_client,
                                              _propagation_percent_for_client,
                                              sync_new_client_to_group,
                                              and the /api/discovery/* aiohttp handlers
```

---

## Key constraint: keep `settings` in `server.py`

**Why:** `tests/unit/test_*.py` (12 files) and `tests/integration/*.py` use the pattern `server.settings = mock_settings`. Moving the singleton to `mosaicmesh.state` breaks that — assigning to `server.settings` would rebind only in server's namespace, and other modules importing from `mosaicmesh.state` would still see the real one. Moving it requires touching every test file.

**Pattern for extracted modules:** at the top of the moved module:

```python
# mosaicmesh/<name>.py
import server   # the singleton lives there; reference via server.settings.X
```

Inside functions, use `server.settings.clients[key]`, `server.settings.displays[id]`, etc. This is lazy — `server.settings` is looked up *when the function runs*, not at import time, so circular imports between `server` and `mosaicmesh.<name>` resolve cleanly.

For functions that take a `client`/`display` as a parameter and never need `server.settings`, no `import server` is required.

---

## Task 1: Bootstrap `mosaicmesh` package + move data classes to `state.py`

**Files:**
- Create: `mosaicmesh/__init__.py` (empty)
- Create: `mosaicmesh/state.py`
- Modify: `server.py` (delete class blocks; add imports)
- Test: `tests/unit/test_module_layout.py` (NEW — verifies imports land at the new location)

- [ ] **Step 1: Write the failing test for new module layout**

Create `tests/unit/test_module_layout.py`:

```python
"""Smoke tests confirming the mosaicmesh module split landed.
Each module's existence is verified by importing one canonical symbol from it."""
import pytest

def test_state_classes_importable():
    from mosaicmesh.state import (
        Settings, Scripts, Display, PlayState, MediaElement,
        Playlist, Schedule, PlayMode, Client,
        migrate_client_objects, _apply_default_scripts,
    )
    s = Settings()
    assert hasattr(s, 'clients')
    assert hasattr(s, 'displays')
    assert hasattr(s, 'playlists')
    assert hasattr(s, 'schedules')
    assert hasattr(s, 'scripts')

def test_server_reexports_state_classes():
    """server.py still exposes the classes for backward compat with tests
    that do `from server import Client, Settings, etc.`"""
    import server
    assert server.Settings is __import__('mosaicmesh.state', fromlist=['Settings']).Settings
    assert server.Client is __import__('mosaicmesh.state', fromlist=['Client']).Client
    assert server.Playlist is __import__('mosaicmesh.state', fromlist=['Playlist']).Playlist
    assert server.Schedule is __import__('mosaicmesh.state', fromlist=['Schedule']).Schedule
    assert server.PlayMode is __import__('mosaicmesh.state', fromlist=['PlayMode']).PlayMode
```

- [ ] **Step 2: Run the new test, see it fail**

```bash
python -m pytest tests/unit/test_module_layout.py::test_state_classes_importable -c tests/pytest.ini -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'mosaicmesh'`.

- [ ] **Step 3: Create `mosaicmesh/__init__.py`**

```bash
mkdir -p mosaicmesh
```

Create `mosaicmesh/__init__.py` (empty file).

- [ ] **Step 4: Create `mosaicmesh/state.py` with the classes moved from server.py**

Read `server.py` lines 1534-1676 (the class definitions: `Settings`, `Scripts`, `Display`, `PlayState`, `MediaElement`, `Playlist`, `Schedule`, `PlayMode`, `Client`). Also read `server.py` lines around `_apply_default_scripts` (currently ~160) and `migrate_client_objects` (search for it).

Write `mosaicmesh/state.py`:

```python
"""Data classes for the server's in-memory state and the singleton's structure.

The singleton `settings = Settings()` itself stays in `server.py` (see PR-1
plan rationale): the existing test pattern `server.settings = mock_settings`
requires `server` to be the canonical namespace for the instance binding.
This module owns the CLASS definitions and stateless helpers.
"""
from enum import Enum
from datetime import datetime


# <Paste Scripts, Display, PlayState, MediaElement, Playlist, Schedule, PlayMode,
#  Client class definitions from server.py lines 1534-1676 verbatim.>

# <Paste Settings class verbatim (currently server.py ~1534).>

# <Paste _apply_default_scripts(client) verbatim (currently server.py ~160).>

# <Paste migrate_client_objects function verbatim (search server.py for `def migrate_client_objects`).>
```

The `<Paste …>` markers are because the engineer must verify line numbers in case of drift; copy the function bodies exactly. **Do not modify any logic — pure relocation.**

- [ ] **Step 5: Modify `server.py` to import the moved classes + remove the inline definitions**

Replace the class definition block (lines ~1534-1676) in `server.py` with:

```python
# Data classes live in mosaicmesh.state; re-imported here so existing code
# (and tests that do `from server import Client`) keeps working.
from mosaicmesh.state import (
    Settings, Scripts, Display, PlayState, MediaElement,
    Playlist, Schedule, PlayMode, Client,
    _apply_default_scripts, migrate_client_objects,
)
```

Also remove the original `_apply_default_scripts(client):` definition and `migrate_client_objects` definition from `server.py`.

The line `settings = Settings()` **stays in `server.py`** — do not move it.

- [ ] **Step 6: Run the new layout test — should pass**

```bash
python -m pytest tests/unit/test_module_layout.py::test_state_classes_importable tests/unit/test_module_layout.py::test_server_reexports_state_classes -c tests/pytest.ini -v
```

Expected: both PASS.

- [ ] **Step 7: Run the full unit test suite — confirm no regressions**

```bash
python pytest_runner.py --unit
```

Expected: same pass/fail counts as before this task (i.e. no NEW failures). The 12 test files using `server.settings = mock_settings` continue to work because the singleton is still in server.py.

- [ ] **Step 8: Commit**

```bash
git add mosaicmesh/__init__.py mosaicmesh/state.py server.py tests/unit/test_module_layout.py
git commit -m "refactor(server): move data classes to mosaicmesh/state.py

Pure relocation of Settings, Scripts, Display, PlayState, MediaElement,
Playlist, Schedule, PlayMode, Client class definitions plus the
_apply_default_scripts and migrate_client_objects helpers. The singleton
settings = Settings() stays in server.py so the existing
'server.settings = mock_settings' test pattern works unchanged.

server.py re-imports the classes for backward compat with any test or
external caller using 'from server import Client'.

Part of PR-1 of the admin-timeline-redesign spec (server module split).
"
```

---

## Task 2: Extract persistence helpers to `mosaicmesh/persistence.py`

**Files:**
- Create: `mosaicmesh/persistence.py`
- Modify: `server.py` (delete moved code; add imports)
- Modify: `tests/unit/test_module_layout.py` (add persistence smoke test)

- [ ] **Step 1: Add the smoke test for the new module**

Append to `tests/unit/test_module_layout.py`:

```python
def test_persistence_helpers_importable():
    from mosaicmesh.persistence import (
        save_settings_incremental, saveSettings, cleanup_old_clients,
    )
    assert callable(save_settings_incremental)
    assert callable(saveSettings)
    assert callable(cleanup_old_clients)
```

- [ ] **Step 2: Run the new test, see it fail**

```bash
python -m pytest tests/unit/test_module_layout.py::test_persistence_helpers_importable -c tests/pytest.ini -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'mosaicmesh.persistence'`.

- [ ] **Step 3: Create `mosaicmesh/persistence.py`**

Read `server.py` for `save_settings_incremental`, `saveSettings`, `cleanup_old_clients` (search by name). Also identify any jsonpickle-setup code (`init_json_cache` is its own thing — that's cache, not persistence; `save_settings_incremental` is the jsonpickle-based save).

Write `mosaicmesh/persistence.py`:

```python
"""Persistent storage of the global settings object to settings.dat.

Uses jsonpickle to serialize the Settings instance and only writes when
the encoded form's hash has changed (save_settings_incremental). The
singleton itself lives in server.settings; this module references it
lazily via `server.settings` so it's resolved at call time, not import time.
"""
import server  # lazy reference: server.settings is the live singleton
import hashlib
import jsonpickle
import logging
import time

# <Paste save_settings_incremental, saveSettings, cleanup_old_clients verbatim
#  from server.py. Replace any reference to `settings` (the local module-level
#  name in server.py) with `server.settings` so the late-binding works.>
```

**Substitution rule:** every reference to `settings` (bare name) inside the moved functions becomes `server.settings`. References to classes (e.g. `Settings`, `Client`) become imports: `from mosaicmesh.state import Settings, Client` at the top of the module.

- [ ] **Step 4: Modify `server.py` to import + re-export**

Replace the inline definitions in `server.py` with:

```python
from mosaicmesh.persistence import (
    save_settings_incremental, saveSettings, cleanup_old_clients,
)
```

Delete the original definitions.

- [ ] **Step 5: Run the new layout test — passes**

```bash
python -m pytest tests/unit/test_module_layout.py::test_persistence_helpers_importable -c tests/pytest.ini -v
```

Expected: PASS.

- [ ] **Step 6: Run the full unit test suite**

```bash
python pytest_runner.py --unit
```

Expected: no NEW failures.

- [ ] **Step 7: Commit**

```bash
git add mosaicmesh/persistence.py server.py tests/unit/test_module_layout.py
git commit -m "refactor(server): extract persistence helpers to mosaicmesh/persistence.py

Pure relocation of save_settings_incremental, saveSettings,
cleanup_old_clients. Functions reference the singleton via server.settings
(lazy at call time) so circular import is harmless.

Part of PR-1 of the admin-timeline-redesign spec.
"
```

---

## Task 3: Extract cache + file-handle pool to `mosaicmesh/cache.py`

**Files:**
- Create: `mosaicmesh/cache.py`
- Modify: `server.py`
- Modify: `tests/unit/test_module_layout.py`

- [ ] **Step 1: Add the smoke test**

Append to `tests/unit/test_module_layout.py`:

```python
def test_cache_helpers_importable():
    from mosaicmesh.cache import (
        init_json_cache, get_pooled_file_handle, close_file_pool,
        prewarm_static_cache, get_cached_file,
    )
    assert callable(get_cached_file)
```

- [ ] **Step 2: Run, see fail**

```bash
python -m pytest tests/unit/test_module_layout.py::test_cache_helpers_importable -c tests/pytest.ini -v
```

Expected: FAIL.

- [ ] **Step 3: Create `mosaicmesh/cache.py`**

Identify in `server.py`: `init_json_cache`, `get_pooled_file_handle`, `close_file_pool`, `prewarm_static_cache`, `get_cached_file` (around line 322, 334, 421, 1464, 1494). Also any module-level cache dicts (`_FILE_POOL`, `_JSON_CACHE`, etc.).

Write `mosaicmesh/cache.py`:

```python
"""File-content cache + file-handle pool used by static and media handlers.

Module-level state (the cache dict, file-handle pool dict) is co-located
with the functions that manage it.
"""
import os
import logging
import time
from collections import OrderedDict

# <Paste the module-level state variables (cache dict, file-handle pool dict, etc.)
#  exactly as they appear in server.py.>

# <Paste init_json_cache, get_pooled_file_handle, close_file_pool,
#  prewarm_static_cache, get_cached_file verbatim. No server.settings access
#  is expected in these — they operate on filesystem paths. If any reference
#  exists, replace with `import server; server.settings.X` at call site.>
```

- [ ] **Step 4: Modify `server.py`**

Replace inline definitions with:

```python
from mosaicmesh.cache import (
    init_json_cache, get_pooled_file_handle, close_file_pool,
    prewarm_static_cache, get_cached_file,
)
```

Also update any `/debug/cache` route handler in `server.py` if it references the pool dict directly — it should now go through `mosaicmesh.cache` accessors. If the cache dicts are accessed directly anywhere outside cache.py, expose them as module-level attributes of `mosaicmesh.cache` and access as `mosaicmesh.cache.<name>` from the route handler.

- [ ] **Step 5: Run layout test — pass**

```bash
python -m pytest tests/unit/test_module_layout.py::test_cache_helpers_importable -c tests/pytest.ini -v
```

Expected: PASS.

- [ ] **Step 6: Run full unit suite — no regressions**

```bash
python pytest_runner.py --unit
```

In particular, `tests/unit/test_media_cache.py` (which extensively tests this code) must pass.

Expected: same pass/fail counts as before.

- [ ] **Step 7: Commit**

```bash
git add mosaicmesh/cache.py server.py tests/unit/test_module_layout.py
git commit -m "refactor(server): extract file cache + handle pool to mosaicmesh/cache.py

Pure relocation of init_json_cache, get_pooled_file_handle, close_file_pool,
prewarm_static_cache, get_cached_file plus their module-level state dicts.
All tests/unit/test_media_cache.py tests pass unchanged.

Part of PR-1 of the admin-timeline-redesign spec.
"
```

---

## Task 4: Extract broadcast helpers to `mosaicmesh/broadcast.py`

**Files:**
- Create: `mosaicmesh/broadcast.py`
- Modify: `server.py`
- Modify: `tests/unit/test_module_layout.py`

- [ ] **Step 1: Add smoke test**

```python
def test_broadcast_helpers_importable():
    from mosaicmesh.broadcast import (
        _send_to_session, _deliver,
        broadcast_to_client, broadcast_to_display_group,
    )
    assert callable(broadcast_to_client)
    assert callable(broadcast_to_display_group)
```

- [ ] **Step 2: Run, see fail**

```bash
python -m pytest tests/unit/test_module_layout.py::test_broadcast_helpers_importable -c tests/pytest.ini -v
```

Expected: FAIL.

- [ ] **Step 3: Create `mosaicmesh/broadcast.py`**

Functions at `server.py` lines ~343, 370, 395, 407. They reference `socketmanager` and `server.settings.clients`.

Write `mosaicmesh/broadcast.py`:

```python
"""SockJS broadcast helpers. Reference the shared socketmanager via
server.socketmanager (lazy at call time)."""
import server
import logging
import json

# <Paste _send_to_session, _deliver, broadcast_to_client,
#  broadcast_to_display_group verbatim.>
# Replace `settings.clients` with `server.settings.clients`.
# Replace `socketmanager` with `server.socketmanager`.
```

- [ ] **Step 4: Modify `server.py`**

```python
from mosaicmesh.broadcast import (
    _send_to_session, _deliver,
    broadcast_to_client, broadcast_to_display_group,
)
```

Delete the inline definitions.

- [ ] **Step 5: Layout test passes**

```bash
python -m pytest tests/unit/test_module_layout.py::test_broadcast_helpers_importable -c tests/pytest.ini -v
```

Expected: PASS.

- [ ] **Step 6: Full unit suite**

```bash
python pytest_runner.py --unit
```

Expected: same pass/fail counts.

- [ ] **Step 7: Commit**

```bash
git add mosaicmesh/broadcast.py server.py tests/unit/test_module_layout.py
git commit -m "refactor(server): extract broadcast helpers to mosaicmesh/broadcast.py

Pure relocation of _send_to_session, _deliver, broadcast_to_client,
broadcast_to_display_group. Late-binds server.settings + server.socketmanager
at call time.

Part of PR-1 of the admin-timeline-redesign spec.
"
```

---

## Task 5: Extract calibration code to `mosaicmesh/calibration.py`

**Files:**
- Create: `mosaicmesh/calibration.py`
- Modify: `server.py`
- Modify: `tests/unit/test_module_layout.py`

- [ ] **Step 1: Smoke test**

```python
def test_calibration_helpers_importable():
    from mosaicmesh.calibration import (
        order_points, reconstruct_screen_quad, reconcile_screen_quad,
        warp_image_for_screen, assign_group_bounding_boxes,
        group_bounding_box, letterbox_to_aspect,
    )
    assert callable(order_points)
```

- [ ] **Step 2: Run, see fail**

```bash
python -m pytest tests/unit/test_module_layout.py::test_calibration_helpers_importable -c tests/pytest.ini -v
```

Expected: FAIL.

- [ ] **Step 3: Create `mosaicmesh/calibration.py`**

Functions in `server.py`: `order_points` (~665), `_draw_fitted_label` (~678), `group_bounding_box` (~787), `reconstruct_screen_quad` (~796), `_quad_box` (~811), `_quad_iou` (~817), `_quad_aspect` (~826), `_aspect_in_marker_frame` (~836), `reconcile_screen_quad` (~872), `_render_output_dims` (~959), `warp_image_for_screen` (~975), `_hex_to_bgr` (~992), `letterbox_to_aspect` (~1000), `assign_group_bounding_boxes` (~1015), `_group_clients` (~1030). Plus `find_squares` and `angle_cos` if they exist (search the file).

The `generateAruco` and `calibrate` HTTP handlers stay in `server.py` for now (they're route handlers — they move to a later module split). They'll call into `mosaicmesh.calibration` for the math.

Write `mosaicmesh/calibration.py`:

```python
"""Physical-layout calibration: detect ArUco markers in an uploaded photo,
map each marker ID back to a client, compute per-screen quads + group bounding
boxes for perspective rendering."""
import server  # late-bind server.settings.clients/displays
import logging
import math
import os
import numpy as np
import cv2

# <Paste functions verbatim from server.py.>
# Replace `settings.X` with `server.settings.X` throughout.
```

- [ ] **Step 4: Modify `server.py`**

```python
from mosaicmesh.calibration import (
    order_points, _draw_fitted_label, group_bounding_box,
    reconstruct_screen_quad, _quad_box, _quad_iou, _quad_aspect,
    _aspect_in_marker_frame, reconcile_screen_quad, _render_output_dims,
    warp_image_for_screen, _hex_to_bgr, letterbox_to_aspect,
    assign_group_bounding_boxes, _group_clients,
)
```

Delete the original definitions.

- [ ] **Step 5: Layout test passes**

```bash
python -m pytest tests/unit/test_module_layout.py::test_calibration_helpers_importable -c tests/pytest.ini -v
```

Expected: PASS.

- [ ] **Step 6: Full unit suite**

```bash
python pytest_runner.py --unit
```

Expected: same pass/fail counts.

- [ ] **Step 7: Commit**

```bash
git add mosaicmesh/calibration.py server.py tests/unit/test_module_layout.py
git commit -m "refactor(server): extract calibration math to mosaicmesh/calibration.py

Pure relocation. The generateAruco/calibrate aiohttp route handlers stay
in server.py for this PR; they'll import into mosaicmesh/calibration.py
for the math.

Part of PR-1 of the admin-timeline-redesign spec.
"
```

---

## Task 6: Extract render pipeline to `mosaicmesh/render.py`

**Files:**
- Create: `mosaicmesh/render.py`
- Modify: `server.py`
- Modify: `tests/unit/test_module_layout.py`

- [ ] **Step 1: Smoke test**

```python
def test_render_pipeline_importable():
    from mosaicmesh.render import (
        render_group_async, compute_render_token,
        build_ffmpeg_perspective_cmd, build_ffmpeg_individual_cmd,
        get_video_dimensions, resolve_media_path,
        _apply_playlist, _start_group_playback, _stop_group_playback,
        _broadcast_per_client_play, _broadcast_per_client_preload,
    )
    assert callable(render_group_async)
```

- [ ] **Step 2: Run, see fail**

```bash
python -m pytest tests/unit/test_module_layout.py::test_render_pipeline_importable -c tests/pytest.ini -v
```

Expected: FAIL.

- [ ] **Step 3: Create `mosaicmesh/render.py`**

This is the biggest extracted module. From `server.py` (line numbers approximate):

- `_keyframe_grid_args` (~164), `_video_input_args` (~268), `_video_encoder_args` (~278)
- `_get_push_sem` (~258)
- `compute_render_token` (~1091), `_broadcast_render_status` (~1117), `_is_renderable` (~1123)
- `_normalize_effect` (~1128), `_resolve_effect_filters` (~1139)
- `_run_ffmpeg` (~1157), `render_group_async` (~1174)
- `_per_client_items` (~1300), `_broadcast_per_client_play` (~1336), `_broadcast_per_client_preload` (~1344)
- `isVideoItem` (~1367), `quad_to_source_points` (~1373)
- `build_ffmpeg_perspective_cmd` (~1384), `build_ffmpeg_individual_cmd` (~1409)
- `get_video_dimensions` (~1440), `resolve_media_path` (~1453)
- `_duration_ms` (~1726), `_media_item_payload` (~1738)
- `_resolve_media_url` (~1769), `_build_media_elements` (~1806)
- `_apply_playlist` (~1828), `_start_group_playback` (~1844), `_stop_group_playback` (~1864)
- `_group_online_keys` (~1877), `_begin_prepare` (~1882), `_prepare_unsynced_clients` (~1923)

Write `mosaicmesh/render.py`:

```python
"""ffmpeg-driven render pipeline: per-screen perspective warp, segment slicing,
video mosaic encoding, plus the playback orchestration (PRELOAD/PLAY,
preparation barriers, push concurrency)."""
import server  # late-bind server.settings + server.socketmanager
import os
import json
import asyncio
import logging
import subprocess
import time
import threading
from mosaicmesh.calibration import (
    _render_output_dims, warp_image_for_screen, group_bounding_box,
)

# Module-level concurrency primitives (move from server.py):
# - any global `_render_token_lock`, `_push_sem`, `_VIDEO_ENCODER` constants etc.
# Inspect server.py for these and paste verbatim.

# <Paste all listed functions verbatim. Substitute `settings` -> `server.settings`,
#  socketmanager -> server.socketmanager.>
```

- [ ] **Step 4: Modify `server.py`**

```python
from mosaicmesh.render import (
    _keyframe_grid_args, _video_input_args, _video_encoder_args,
    _get_push_sem, compute_render_token, _broadcast_render_status,
    _is_renderable, _normalize_effect, _resolve_effect_filters,
    _run_ffmpeg, render_group_async,
    _per_client_items, _broadcast_per_client_play, _broadcast_per_client_preload,
    isVideoItem, quad_to_source_points,
    build_ffmpeg_perspective_cmd, build_ffmpeg_individual_cmd,
    get_video_dimensions, resolve_media_path,
    _duration_ms, _media_item_payload,
    _resolve_media_url, _build_media_elements,
    _apply_playlist, _start_group_playback, _stop_group_playback,
    _group_online_keys, _begin_prepare, _prepare_unsynced_clients,
)
```

Delete the originals.

- [ ] **Step 5: Layout test passes**

```bash
python -m pytest tests/unit/test_module_layout.py::test_render_pipeline_importable -c tests/pytest.ini -v
```

Expected: PASS.

- [ ] **Step 6: Full unit suite**

```bash
python pytest_runner.py --unit
```

Expected: same pass/fail counts. `tests/unit/test_mosaic.py`, `tests/unit/test_playback.py`, `tests/unit/test_coordinated_start.py` are the relevant ones — confirm they still pass.

- [ ] **Step 7: Commit**

```bash
git add mosaicmesh/render.py server.py tests/unit/test_module_layout.py
git commit -m "refactor(server): extract render pipeline to mosaicmesh/render.py

Largest extracted module. Pure relocation of the ffmpeg perspective/segment
encoders, playback orchestration (PRELOAD/PLAY, _start_group_playback,
_prepare_unsynced_clients), per-client item resolution, and concurrency
primitives.

Part of PR-1 of the admin-timeline-redesign spec.
"
```

---

## Task 7: Extract device-script execution to `mosaicmesh/device_scripts.py`

**Files:**
- Create: `mosaicmesh/device_scripts.py`
- Modify: `server.py`
- Modify: `tests/unit/test_module_layout.py`

- [ ] **Step 1: Smoke test**

```python
def test_device_scripts_importable():
    from mosaicmesh.device_scripts import (
        DEFAULT_DEVICE_SCRIPTS, WEBCLIP_BUNDLE_ID,
        WEBAPP_ICON_FBX, WEBAPP_ICON_FBY,
        _run_device_script, _launch_webapp_via_vnc,
    )
    assert isinstance(DEFAULT_DEVICE_SCRIPTS, dict)
    assert 'loginScript' in DEFAULT_DEVICE_SCRIPTS
    assert 'startScript' in DEFAULT_DEVICE_SCRIPTS
    assert isinstance(WEBAPP_ICON_FBX, int)
```

- [ ] **Step 2: Run, see fail**

```bash
python -m pytest tests/unit/test_module_layout.py::test_device_scripts_importable -c tests/pytest.ini -v
```

Expected: FAIL.

- [ ] **Step 3: Create `mosaicmesh/device_scripts.py`**

Move from `server.py`: `SSH_KEY_PATH`, `SSH_USER`, `SSH_LEGACY_OPTS` (top of file ~66-73), `DISPLAY_URL` (~75), `WEBCLIP_BUNDLE_ID` (~83), `DEFAULT_DEVICE_SCRIPTS` (~84-142), `WEBAPP_ICON_FBX/Y` (search for these), `_launch_webapp_via_vnc` (search by name), `_run_device_script` (search by name).

```python
"""Execution of per-device lifecycle scripts over SSH plus the Veency VNC-tap
launch helper. This is the *current* (PR-1) layout: scripts and constants are
still hardcoded here. PR-3 will replace this module's contents with the
ScriptingProfile-driven dispatcher.

The shared SSH options live here too — onboard_devices.ps1 has its own copy
of these in PowerShell array form for the bootstrap phase."""
import server  # late-bind server.settings for client lookups
import os
import logging
import asyncio
import subprocess

SSH_KEY_PATH = os.path.expanduser(os.path.join("~", ".ssh", "mosaic_ipad"))
SSH_USER = "root"
SSH_LEGACY_OPTS = [
    "-o", "HostKeyAlgorithms=+ssh-rsa",
    "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
    "-o", "IdentitiesOnly=yes",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=10",
    "-o", "BatchMode=yes",
]

DISPLAY_URL = "http://192.168.1.60:3000/"

WEBCLIP_BUNDLE_ID = "com.apple.webapp-4D6F736169634D6573684B696F736B31"

# <Paste DEFAULT_DEVICE_SCRIPTS dict verbatim from server.py.>

# <Paste WEBAPP_ICON_FBX, WEBAPP_ICON_FBY verbatim.>

# <Paste _launch_webapp_via_vnc(client_key) verbatim.>
# <Paste _run_device_script(client_key, which) verbatim.>
# Substitute `settings.clients` -> `server.settings.clients`.
```

- [ ] **Step 4: Modify `server.py`**

```python
from mosaicmesh.device_scripts import (
    SSH_KEY_PATH, SSH_USER, SSH_LEGACY_OPTS, DISPLAY_URL,
    WEBCLIP_BUNDLE_ID, WEBAPP_ICON_FBX, WEBAPP_ICON_FBY,
    DEFAULT_DEVICE_SCRIPTS,
    _launch_webapp_via_vnc, _run_device_script,
)
```

Delete originals.

- [ ] **Step 5: Layout test passes**

```bash
python -m pytest tests/unit/test_module_layout.py::test_device_scripts_importable -c tests/pytest.ini -v
```

Expected: PASS.

- [ ] **Step 6: Full unit suite — tests/unit/test_device_scripts.py is the focus**

```bash
python pytest_runner.py --unit
```

Expected: `test_device_scripts.py` passes unchanged (it presumably uses `server.DEFAULT_DEVICE_SCRIPTS` which still works via the re-import).

- [ ] **Step 7: Commit**

```bash
git add mosaicmesh/device_scripts.py server.py tests/unit/test_module_layout.py
git commit -m "refactor(server): extract device-script execution to mosaicmesh/device_scripts.py

Pure relocation of DEFAULT_DEVICE_SCRIPTS, WEBCLIP_BUNDLE_ID,
WEBAPP_ICON_FBX/FBY, _launch_webapp_via_vnc, _run_device_script, plus
the SSH option constants. Module contents are unchanged in PR-1; PR-3
replaces them with the ScriptingProfile dispatcher.

Part of PR-1 of the admin-timeline-redesign spec.
"
```

---

## Task 8: Extract scheduling helpers to `mosaicmesh/scheduling.py`

**Files:**
- Create: `mosaicmesh/scheduling.py`
- Modify: `server.py`
- Modify: `tests/unit/test_module_layout.py`

- [ ] **Step 1: Smoke test**

```python
def test_scheduling_helpers_importable():
    from mosaicmesh.scheduling import (
        playlist_index, _parse_date, _hhmm_to_min, schedule_active_at,
    )
    assert callable(schedule_active_at)
    # smoke check
    assert _hhmm_to_min("09:30") == 9 * 60 + 30
    assert _hhmm_to_min("00:00") == 0
    assert _hhmm_to_min("23:59") == 23 * 60 + 59
```

- [ ] **Step 2: Run, see fail**

```bash
python -m pytest tests/unit/test_module_layout.py::test_scheduling_helpers_importable -c tests/pytest.ini -v
```

Expected: FAIL.

- [ ] **Step 3: Create `mosaicmesh/scheduling.py`**

```python
"""Schedule recurrence evaluation: given a Schedule and a datetime, is the
schedule active? Plus playlist-index calculation for in-progress catch-up.

Uses python-dateutil's rrule under the hood (already a project dependency).
"""
from datetime import datetime, timedelta
from dateutil.rrule import rrule, DAILY, WEEKLY, MONTHLY, YEARLY

# <Paste _FREQ_MAP module-level dict from server.py (search for _FREQ_MAP near
#  schedule_active_at).>

# <Paste playlist_index, _parse_date, _hhmm_to_min, schedule_active_at verbatim.>
# These functions take Schedule/Playlist as parameters — no server.settings
# dependency. No `import server` needed in this module.
```

- [ ] **Step 4: Modify `server.py`**

```python
from mosaicmesh.scheduling import (
    playlist_index, _parse_date, _hhmm_to_min, schedule_active_at,
)
```

Delete originals.

- [ ] **Step 5: Layout test passes**

```bash
python -m pytest tests/unit/test_module_layout.py::test_scheduling_helpers_importable -c tests/pytest.ini -v
```

Expected: PASS.

- [ ] **Step 6: Full unit suite — `tests/unit/test_scheduling.py` focus**

```bash
python pytest_runner.py --unit
```

Expected: same pass/fail counts.

- [ ] **Step 7: Commit**

```bash
git add mosaicmesh/scheduling.py server.py tests/unit/test_module_layout.py
git commit -m "refactor(server): extract scheduling helpers to mosaicmesh/scheduling.py

Pure relocation of playlist_index, _parse_date, _hhmm_to_min, schedule_active_at.
No server.settings dependency — these are pure data-in / data-out functions.

Part of PR-1 of the admin-timeline-redesign spec.
"
```

---

## Task 9: Extract discovery helpers to `mosaicmesh/api/discovery.py`

**Files:**
- Create: `mosaicmesh/api/__init__.py` (empty)
- Create: `mosaicmesh/api/discovery.py`
- Modify: `server.py`
- Modify: `tests/unit/test_module_layout.py`

- [ ] **Step 1: Smoke test**

```python
def test_api_discovery_importable():
    from mosaicmesh.api.discovery import (
        auto_configure_client, get_discovered_devices,
        sync_new_client_to_group,
        _expected_seg_keys_for_display, _expected_segments_for_client,
        _propagation_percent_for_client,
        api_discovery_devices, api_discovery_stats, api_discovery_configure,
    )
    assert callable(auto_configure_client)
```

- [ ] **Step 2: Run, see fail**

```bash
python -m pytest tests/unit/test_module_layout.py::test_api_discovery_importable -c tests/pytest.ini -v
```

Expected: FAIL.

- [ ] **Step 3: Create the `mosaicmesh/api/` package + `discovery.py`**

```bash
mkdir -p mosaicmesh/api
```

Create `mosaicmesh/api/__init__.py` (empty).

Identify in `server.py`:
- `auto_configure_client` (~442)
- `get_discovered_devices` (~528)
- `_expected_seg_keys_for_display` (~481)
- `_expected_segments_for_client` (~498)
- `_propagation_percent_for_client` (~508)
- `sync_new_client_to_group` (~650)
- `handle_client_disconnect` (~430) — stays in `websocket/dispatch.py` per the spec; do NOT move here.
- The aiohttp handlers `api_discovery_devices`, `api_discovery_stats`, `api_discovery_configure` (search by name).

Write `mosaicmesh/api/discovery.py`:

```python
"""Discovery REST API + the client-registration / group-sync helpers.

The REST handlers (api_discovery_devices, etc.) are aiohttp request handlers.
The non-handler helpers (auto_configure_client, sync_new_client_to_group,
get_discovered_devices) are called from both the REST surface AND from the
legacy SockJS message handler.
"""
import server  # late-bind server.settings + server.socketmanager
import logging
from aiohttp import web
from mosaicmesh.state import Client

# <Paste auto_configure_client, get_discovered_devices,
#  _expected_seg_keys_for_display, _expected_segments_for_client,
#  _propagation_percent_for_client, sync_new_client_to_group,
#  api_discovery_devices, api_discovery_stats, api_discovery_configure
#  verbatim. Substitute `settings.X` -> `server.settings.X`.>
```

- [ ] **Step 4: Modify `server.py`**

```python
from mosaicmesh.api.discovery import (
    auto_configure_client, get_discovered_devices,
    _expected_seg_keys_for_display, _expected_segments_for_client,
    _propagation_percent_for_client, sync_new_client_to_group,
    api_discovery_devices, api_discovery_stats, api_discovery_configure,
)
```

Delete originals.

In the route table near `__main__`, ensure routes still bind:

```python
app.router.add_get('/api/discovery/devices', api_discovery_devices)
app.router.add_get('/api/discovery/stats', api_discovery_stats)
app.router.add_post('/api/discovery/configure', api_discovery_configure)
```

(These bindings were already present; they just bind to the re-imported names now.)

- [ ] **Step 5: Layout test passes**

```bash
python -m pytest tests/unit/test_module_layout.py::test_api_discovery_importable -c tests/pytest.ini -v
```

Expected: PASS.

- [ ] **Step 6: Full unit suite — `tests/unit/test_api_endpoints.py` focus**

```bash
python pytest_runner.py --unit
```

Expected: same pass/fail counts.

- [ ] **Step 7: Commit**

```bash
git add mosaicmesh/api/__init__.py mosaicmesh/api/discovery.py server.py tests/unit/test_module_layout.py
git commit -m "refactor(server): extract discovery REST + helpers to mosaicmesh/api/discovery.py

Pure relocation of auto_configure_client, get_discovered_devices,
sync_new_client_to_group, the propagation helpers, and the three
/api/discovery/* aiohttp handlers. handle_client_disconnect stays for
the websocket dispatch task.

Part of PR-1 of the admin-timeline-redesign spec.
"
```

---

## Task 10: Create `websocket/` package + extract `msg_response` to `legacy.py`

**Files:**
- Create: `mosaicmesh/websocket/__init__.py` (empty)
- Create: `mosaicmesh/websocket/legacy.py`
- Modify: `server.py`
- Modify: `tests/unit/test_module_layout.py`

- [ ] **Step 1: Smoke test**

```python
def test_websocket_legacy_importable():
    from mosaicmesh.websocket.legacy import msg_response
    assert callable(msg_response)
```

- [ ] **Step 2: Run, see fail**

```bash
python -m pytest tests/unit/test_module_layout.py::test_websocket_legacy_importable -c tests/pytest.ini -v
```

Expected: FAIL.

- [ ] **Step 3: Create the package + module**

```bash
mkdir -p mosaicmesh/websocket
```

Create `mosaicmesh/websocket/__init__.py` (empty).

Find `msg_response` in `server.py` (search for `def msg_response`). It's a large function with a long if/elif chain on `REQUEST` types.

**Critical:** this function is the iPad-facing protocol surface. Move BYTE-IDENTICALLY — do not refactor, do not collapse cases, do not rename variables. The 24 iPad-1 displays depend on this exact behavior. CLAUDE.md says "do not remove" the legacy protocol — and "do not modify" applies equally.

Write `mosaicmesh/websocket/legacy.py`:

```python
"""Legacy SockJS REQUEST-based message dispatch (msg_response).

This is the protocol surface for the iPad-1 ES5 display clients. It MUST
NOT change semantics — clients in production depend on byte-identical
behavior. Moved here byte-identically from server.py.
"""
import server  # late-bind server.settings + everything else
import json
import logging
import time
from mosaicmesh.broadcast import broadcast_to_client, broadcast_to_display_group
from mosaicmesh.api.discovery import (
    auto_configure_client, get_discovered_devices,
    sync_new_client_to_group,
)
from mosaicmesh.device_scripts import _run_device_script
from mosaicmesh.persistence import saveSettings
# any other dependencies — import them based on what msg_response references.

# <Paste msg_response verbatim. Replace `settings.X` -> `server.settings.X`.
#  Any function references that have moved to extracted modules — leave them
#  as bare names; the imports above bring them into scope.>
```

- [ ] **Step 4: Modify `server.py`**

```python
from mosaicmesh.websocket.legacy import msg_response
```

Delete the original `msg_response` definition.

- [ ] **Step 5: Layout test passes**

```bash
python -m pytest tests/unit/test_module_layout.py::test_websocket_legacy_importable -c tests/pytest.ini -v
```

Expected: PASS.

- [ ] **Step 6: Full unit suite — `tests/unit/test_websocket_handlers.py` focus**

```bash
python pytest_runner.py --unit
```

Expected: same pass/fail counts. If `test_websocket_handlers.py` imports `msg_response` from `server`, it still works via the re-import.

- [ ] **Step 7: Manual smoke (DO NOT SKIP)**

The legacy protocol is the iPad-facing surface. Start the server locally and connect at least one display client (or replay a session log) to confirm no regressions in the message switch.

```bash
python server.py -v
# In another terminal: open the discovery page in a browser at http://localhost:3000/discovery
# Confirm devices register normally; no errors in the server log.
```

Expected: discovery page renders the fleet; clients connect without errors.

- [ ] **Step 8: Commit**

```bash
git add mosaicmesh/websocket/__init__.py mosaicmesh/websocket/legacy.py server.py tests/unit/test_module_layout.py
git commit -m "refactor(server): extract legacy SockJS msg_response to mosaicmesh/websocket/legacy.py

CRITICAL: the function body is byte-identical. The 24 iPad-1 displays
depend on this protocol surface. Only the file location changes.

Imports for moved helpers (broadcast, discovery, device_scripts, persistence)
re-establish the names that msg_response references.

Part of PR-1 of the admin-timeline-redesign spec.
"
```

---

## Task 11: Extract `handle_websocket_message` to `mosaicmesh/websocket/typed.py`

**Files:**
- Create: `mosaicmesh/websocket/typed.py`
- Modify: `server.py`
- Modify: `tests/unit/test_module_layout.py`

- [ ] **Step 1: Smoke test**

```python
def test_websocket_typed_importable():
    from mosaicmesh.websocket.typed import handle_websocket_message
    assert callable(handle_websocket_message)
```

- [ ] **Step 2: Run, see fail**

```bash
python -m pytest tests/unit/test_module_layout.py::test_websocket_typed_importable -c tests/pytest.ini -v
```

Expected: FAIL.

- [ ] **Step 3: Create `mosaicmesh/websocket/typed.py`**

Find `handle_websocket_message` in `server.py` (search by name). It's the newer async typed protocol.

```python
"""Async type-based websocket message handler — intended replacement for the
legacy REQUEST-based protocol. Currently coexists with msg_response in legacy.py;
the migration to consolidate them is a separate PR per the spec."""
import server  # late-bind server.settings
import asyncio
import json
import logging
from mosaicmesh.broadcast import broadcast_to_client, broadcast_to_display_group

# <Paste handle_websocket_message verbatim. Substitute `settings.X` -> `server.settings.X`.>
```

- [ ] **Step 4: Modify `server.py`**

```python
from mosaicmesh.websocket.typed import handle_websocket_message
```

Delete the original.

- [ ] **Step 5: Layout test passes**

```bash
python -m pytest tests/unit/test_module_layout.py::test_websocket_typed_importable -c tests/pytest.ini -v
```

Expected: PASS.

- [ ] **Step 6: Full unit suite**

```bash
python pytest_runner.py --unit
```

Expected: same pass/fail counts.

- [ ] **Step 7: Commit**

```bash
git add mosaicmesh/websocket/typed.py server.py tests/unit/test_module_layout.py
git commit -m "refactor(server): extract handle_websocket_message to mosaicmesh/websocket/typed.py

Pure relocation of the newer async typed-message handler. Coexists with
legacy.py per the dual-protocol convention (legacy stays for iPad-1 clients).

Part of PR-1 of the admin-timeline-redesign spec.
"
```

---

## Task 12: Extract `ws_handler` + `handle_client_disconnect` to `mosaicmesh/websocket/dispatch.py`

**Files:**
- Create: `mosaicmesh/websocket/dispatch.py`
- Modify: `server.py`
- Modify: `tests/unit/test_module_layout.py`

- [ ] **Step 1: Smoke test**

```python
def test_websocket_dispatch_importable():
    from mosaicmesh.websocket.dispatch import ws_handler, handle_client_disconnect
    assert callable(ws_handler)
    assert callable(handle_client_disconnect)
```

- [ ] **Step 2: Run, see fail**

```bash
python -m pytest tests/unit/test_module_layout.py::test_websocket_dispatch_importable -c tests/pytest.ini -v
```

Expected: FAIL.

- [ ] **Step 3: Create `mosaicmesh/websocket/dispatch.py`**

Find in `server.py`:
- `ws_handler` (~1678) — the SockJS connection lifecycle handler that dispatches to `msg_response`.
- `handle_client_disconnect` (~430)

```python
"""SockJS connection lifecycle + dispatch to legacy or typed handlers based
on message shape. Owns the connect/disconnect callbacks registered on the
sockjs manager."""
import server  # late-bind server.settings + server.socketmanager
import json
import logging
from mosaicmesh.websocket.legacy import msg_response
from mosaicmesh.websocket.typed import handle_websocket_message

# <Paste ws_handler verbatim.>
# <Paste handle_client_disconnect verbatim.>
# Substitute `settings.X` -> `server.settings.X`.
```

- [ ] **Step 4: Modify `server.py`**

```python
from mosaicmesh.websocket.dispatch import ws_handler, handle_client_disconnect
```

Delete the originals.

The sockjs manager registration in the main block (`socketmanager = sockjs.add_endpoint(...)` or similar — search `server.py`) needs to register `ws_handler` — confirm it still does after the import change.

- [ ] **Step 5: Layout test passes**

```bash
python -m pytest tests/unit/test_module_layout.py::test_websocket_dispatch_importable -c tests/pytest.ini -v
```

Expected: PASS.

- [ ] **Step 6: Full unit suite + manual smoke**

```bash
python pytest_runner.py --unit
python server.py -v
# Confirm clients can connect, REGISTER, send messages, disconnect cleanly.
```

Expected: same pass/fail counts; server starts and accepts connections normally.

- [ ] **Step 7: Commit**

```bash
git add mosaicmesh/websocket/dispatch.py server.py tests/unit/test_module_layout.py
git commit -m "refactor(server): extract ws_handler + handle_client_disconnect to dispatch.py

Pure relocation. ws_handler is the SockJS connection lifecycle entry point
that dispatches each message to either msg_response (legacy) or
handle_websocket_message (typed) based on message shape.

Part of PR-1 of the admin-timeline-redesign spec.
"
```

---

## Task 13: Verify `server.py` is now just entry + routes + singletons

**Files:**
- Modify: `server.py` (cleanup of any straggler helpers; ensure shape is right)

- [ ] **Step 1: Check `server.py` line count**

```bash
wc -l server.py
```

Expected: well under 1000 lines (started at 5151). Mostly: imports, the `settings = Settings()` instantiation, the aiohttp app + route table, the SockJS manager setup, the `process()` background loop, the `__main__` block, plus the static file handlers (`index_handler`, `javascript_handler`, `image_handler`, `media_handler`) that bind to filesystem routes.

- [ ] **Step 2: Find any straggler top-level functions**

```bash
grep -nE '^(def |async def |class )' server.py
```

Expected: ~10-30 entries — only what belongs in server.py (route handlers like `index_handler`, plus `process()`, plus `parse_args()`). If you see any leftover business-logic helpers that should have been extracted, do one more move.

- [ ] **Step 3: Verify the re-imports cover everything tests rely on**

```bash
python -c "import server; print([s for s in dir(server) if not s.startswith('_')][:50])"
```

Expected: Settings, Client, Playlist, Schedule, settings, msg_response, broadcast_to_client, etc. all visible — backward-compat for tests doing `from server import X`.

- [ ] **Step 4: Run the FULL test suite one more time**

```bash
python pytest_runner.py --unit --verbose
```

Expected: identical pass/fail counts to the state before Task 1.

- [ ] **Step 5: Run integration tests too (per project README, some are WIP — note new failures vs pre-existing)**

```bash
python pytest_runner.py --integration --verbose 2>&1 | tee /tmp/integration_after.log
```

If any test FAILS that wasn't failing before, investigate before continuing. Compare to a pre-refactor baseline if needed: stash changes, run integration, save log, unstash.

- [ ] **Step 6: Commit (no-op or final cleanup)**

If straggler moves were needed in step 2:

```bash
git add server.py
git commit -m "refactor(server): final cleanup of straggler helpers post-module-split

Confirms server.py is now ~<N> lines (down from 5151) and contains only:
- imports + re-exports for backward compat
- settings = Settings() singleton
- aiohttp app + route table
- SockJS manager setup
- process() background loop
- parse_args() + __main__

Part of PR-1 of the admin-timeline-redesign spec.
"
```

If no stragglers, skip the commit and move to Task 14.

---

## Task 14: Update `CLAUDE.md` to reflect the new layout

**Files:**
- Modify: `CLAUDE.md` (Architecture + Layout sections)

- [ ] **Step 1: Read the current CLAUDE.md layout section**

```bash
grep -n -B2 -A20 '^## Layout' CLAUDE.md
```

Note the current shape — single-paragraph descriptions of `server.py` and `js/mosiacmesh.js`.

- [ ] **Step 2: Update the Architecture section**

In `CLAUDE.md`, locate the paragraph beginning *"**Single-process async monolith.** Everything server-side is in `server.py` (~1000 lines)…"* and replace it with:

```markdown
**Single-process async monolith.** Server-side code lives in `mosaicmesh/`
under topic-named modules (state, persistence, cache, broadcast, calibration,
render, device_scripts, scheduling, websocket/{legacy,typed,dispatch},
api/discovery). `server.py` is the entry point (~600 lines): it owns the
`settings = Settings()` singleton, builds the aiohttp app + route table,
registers the SockJS endpoint, and runs `process()` every 5 seconds for the
life of the process.

**State lives in one global `settings` object** (`Settings` → `displays`,
`scripts`, `clients` dicts) — owned by `server.py` so the existing test
pattern `server.settings = mock_settings` continues to work. Sub-modules
reference it lazily via `import server; server.settings.X` (call-time
lookup avoids circular-import issues at import time). State is persisted
to `settings.dat` (gitignored) via `jsonpickle` in `mosaicmesh.persistence.
save_settings_incremental()`. On startup, `mosaicmesh.state.migrate_client_
objects()` backfills newer fields onto `Client` objects loaded from an older
`settings.dat`.
```

- [ ] **Step 3: Update the Layout section**

Locate the bullet beginning *"- `server.py` — entire backend…"* and replace with:

```markdown
- `server.py` — entry point: route table, SockJS manager registration,
  `settings = Settings()`, `process()` loop, `__main__`.
- `mosaicmesh/` — server-side code split by responsibility:
  - `state.py` — Settings, Client, Playlist, Schedule, PlayMode, etc. + migration
  - `persistence.py` — jsonpickle save/load + cleanup_old_clients
  - `cache.py` — file-content cache + file-handle pool + /debug/cache
  - `broadcast.py` — SockJS broadcast helpers
  - `calibration.py` — ArUco math + perspective warps
  - `render.py` — ffmpeg pipeline + playback orchestration (PRELOAD/PLAY)
  - `device_scripts.py` — DEFAULT_DEVICE_SCRIPTS + SSH/VNC script execution
                          (will be replaced by ScriptingProfile in PR-3)
  - `scheduling.py` — schedule_active_at + recurrence helpers
  - `websocket/legacy.py` — msg_response (the iPad-facing if/elif protocol)
  - `websocket/typed.py` — handle_websocket_message (async typed protocol)
  - `websocket/dispatch.py` — ws_handler + handle_client_disconnect
  - `api/discovery.py` — /api/discovery/* handlers + REGISTER helpers
- `js/mosiacmesh.js` — client connection logic, UDID cookie, SockJS wiring,
  message construction (`generateMessage`).
- `js/GoTime.js` — clock-sync library used for synchronized playback.
- `*.html` (root) — `index` (display client), `admin` (control),
  `discovery` (device management).
- `media/<client>/{images,videos}/` — per-client media and generated ArUco
  markers (created at runtime).
- `tests/unit` reliably pass; `tests/integration` and several unit suites are
  partially implemented (see `tests/README.md` "Test Status").
```

- [ ] **Step 4: Verify CLAUDE.md still parses as valid Markdown**

```bash
head -10 CLAUDE.md
```

Eyeball check — heading levels intact, lists formatted, no broken markdown.

- [ ] **Step 5: Run the full test suite once more (paranoid final check)**

```bash
python pytest_runner.py --unit
```

Expected: same counts.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude-md): update Architecture + Layout for mosaicmesh module split

server.py is now the entry point + singleton owner; all behavior lives in
mosaicmesh/{state,persistence,cache,broadcast,calibration,render,
device_scripts,scheduling,websocket/*,api/*}. The global settings pattern
and the dual SockJS protocols (legacy + typed) are preserved exactly.

Closes PR-1 of the admin-timeline-redesign spec.
"
```

---

## Self-review checklist (run before opening the PR)

- [ ] `wc -l server.py` reports a substantial drop (target: <1000 lines, was 5151)
- [ ] `python pytest_runner.py --unit` passes with same counts as the pre-refactor baseline
- [ ] `python server.py -v` starts cleanly; opening http://localhost:3000/discovery in a browser shows the fleet
- [ ] At least one display client can connect, REGISTER, and exchange messages without errors in the server log
- [ ] `git log --oneline -n 15` shows ~13-14 commits, one per task, each independently reviewable
- [ ] `tests/unit/test_module_layout.py` has one passing test per extracted module
- [ ] No file in `mosaicmesh/` imports `from server import settings` (that would break the lazy pattern); all sub-modules use `import server; server.settings.X` inside functions

---

## Notes for the implementing engineer

1. **The class moves change names, not behavior.** Resist the urge to rename, refactor, or "clean up" while moving. PR-1 must pass the same tests with the same behavior. Refactors land in later PRs.

2. **Settings is a singleton.** Inside extracted modules, `import server` at the top, then access `server.settings.X` inside function bodies. **Never** `from server import settings` — that captures the value at import time and won't pick up the test pattern `server.settings = mock_settings`.

3. **The 24 iPad-1 displays are in production.** PR-1 touches `mosaicmesh/websocket/legacy.py` — that's the iPad-facing protocol. Move byte-identically. After Task 10, do the manual smoke per Step 7.

4. **Watch for module-level side effects.** A few extracted modules have module-level setup (jsonpickle's `set_decoder_options`, the file-handle pool dict). Keep that setup at module level in the new location; don't lazy-init it inside functions.

5. **Test discovery in subdirectories.** `tests/pytest.ini` already includes `tests/unit` recursively, but verify after Task 1 that the new `test_module_layout.py` file is picked up. If pytest doesn't find it, check the file is named `test_*.py` and the `python_files` glob in `pytest.ini` matches.

6. **Watch the `_*` prefix on functions.** Many extracted helpers start with `_` (private convention). They keep that prefix in the new location; we don't promote them to public just because they're cross-module now. Import them as `from mosaicmesh.X import _helper` — Python allows underscore imports.

7. **After Task 14, the spec's PR-2 (new REST endpoints) becomes much cleaner to build** — `mosaicmesh/api/` is already a package, the pattern for sub-module + lazy-server-reference is established.
