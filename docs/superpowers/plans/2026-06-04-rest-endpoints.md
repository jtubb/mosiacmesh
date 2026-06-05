# REST Endpoints (Playlists/Schedules/Profiles/Media) Implementation Plan — PR-2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add REST CRUD endpoints for Playlists, Schedules, Scripting Profiles, and Media — the server-side surface the new admin timeline UI (PR-4) will hydrate from. All mutating endpoints use `If-Match: <_serverVersion>` for optimistic concurrency; DELETE on referenced resources returns 409 with a `refs` list.

**Architecture:** Each resource gets its own module under `mosaicmesh/api/` (mirroring the `discovery.py` pattern established in PR-1). All endpoints follow the `{success: true, …}` / `{success: false, error: …}` response shape already used by `/api/discovery/configure`. Existing `Playlist`, `Schedule` classes in `mosaicmesh/state.py` gain a `_serverVersion: int` field bumped server-side on each mutation; a new `ScriptingProfile` class is added (shape only — PR-3 wires the dispatcher behavior).

**Tech Stack:** Python 3, `aiohttp` for REST handlers, `jsonpickle` for serialization, `dateutil.rrule` for schedule recurrence validation (already a project dependency). Tests: pytest + `aiohttp.test_utils.make_mocked_request` (existing pattern from `tests/unit/test_api_endpoints.py`).

---

## Source spec

[`docs/superpowers/specs/2026-06-04-admin-timeline-redesign-design.md`](../specs/2026-06-04-admin-timeline-redesign-design.md) — Section 6 (New REST endpoints) and Section 7 (Scripting Profiles — data model only; PR-3 ships the dispatcher).

## Stacking

This PR stacks on top of **PR-1** (`refactor/server-module-split`). Branch from there:

```bash
git checkout refactor/server-module-split
git checkout -b feature/pr2-rest-endpoints
```

When PR-1 merges to main, this PR's base auto-updates.

---

## File structure (target)

```
mosaicmesh/state.py                -- MODIFY: add _serverVersion to Playlist, Schedule;
                                     add ScriptingProfile class (shape only); add
                                     Settings.profiles = {} initialization
mosaicmesh/api/_concurrency.py     -- NEW: shared helpers for If-Match parsing +
                                     412 responses + version bumping. ~40 lines.
mosaicmesh/api/playlists.py        -- NEW: GET/POST/PUT/DELETE /api/playlists
mosaicmesh/api/schedules.py        -- NEW: GET/POST/PUT/DELETE /api/schedules
mosaicmesh/api/profiles.py        -- NEW: GET/POST/PUT/DELETE /api/profiles +
                                     POST /api/clients/{clientKey}/profile
mosaicmesh/api/media.py           -- NEW: GET /api/media (relocated from server.py),
                                     POST /api/upload (relocated)

server.py                          -- MODIFY:
                                     - Add 4 import blocks for the new api modules
                                     - Register 14 new route handlers in __main__
                                     - DELETE inline api_media + upload_handler
                                       (relocated to media.py)

tests/unit/test_api_playlists.py        -- NEW: ~12 pytest cases
tests/unit/test_api_schedules.py        -- NEW: ~15 pytest cases (extra for validation)
tests/unit/test_api_profiles.py         -- NEW: ~12 pytest cases + client-assign
tests/unit/test_api_media.py            -- NEW: ~6 pytest cases
tests/unit/test_version_concurrency.py  -- NEW: cross-resource If-Match handling
```

---

## Key conventions

### Response shapes

Success (most endpoints):
```json
{"success": true, "playlist": {...}}        // or schedules / profiles / etc.
{"success": true, "playlists": [...]}        // list endpoints
```

Error:
```json
{"success": false, "error": "Schedule not found"}
```

HTTP status codes:
- `200` — successful GET / PUT
- `201` — successful POST (creation)
- `204` — successful DELETE
- `400` — validation error
- `404` — resource not found
- `409` — conflict (e.g., delete-with-refs)
- `412` — stale `_serverVersion` (If-Match precondition failed)
- `500` — unexpected server error

### Concurrency contract

Every mutating endpoint (POST, PUT, DELETE) on a versioned resource:
1. On `POST`, the new resource is created with `_serverVersion = 1`.
2. On `PUT`, the client MUST send `If-Match: <current _serverVersion>`. If missing, 428 (Precondition Required); if stale, 412 (Precondition Failed). On success, `_serverVersion` increments by 1.
3. On `DELETE`, `If-Match` is recommended but optional (we accept missing for now to keep delete simple; deletes are usually less contended).
4. Response always echoes the new `_serverVersion` on the returned resource.

### Reference checks on DELETE

- Deleting a `Playlist` returns 409 if any `Schedule.playlistName` references it.
- Deleting a `ScriptingProfile` returns 409 if any `Client.profileName` references it.
- Schedule and Media DELETEs have no reference checks.

The 409 body includes `refs: [...]` so the UI can show "Used by N schedules" with names.

---

## Task 1: Data model changes in `mosaicmesh/state.py`

Adds `_serverVersion` to `Playlist` and `Schedule`, adds the `ScriptingProfile` class (shape only — PR-3 wires the dispatcher), adds `Settings.profiles = {}` initialization.

**Files:**
- Modify: `mosaicmesh/state.py`
- Modify: `tests/unit/test_module_layout.py` (extend `test_state_classes_importable`)

- [ ] **Step 1: Add failing test for new state shape**

Edit `tests/unit/test_module_layout.py`. Find `test_state_classes_importable` and add to its body:

```python
def test_state_classes_importable():
    from mosaicmesh.state import (
        Settings, Scripts, Display, PlayState, MediaElement,
        Playlist, Schedule, PlayMode, Client, ScriptingProfile,
        migrate_client_objects, _apply_default_scripts,
    )
    s = Settings()
    assert hasattr(s, 'clients')
    assert hasattr(s, 'displays')
    assert hasattr(s, 'playlists')
    assert hasattr(s, 'schedules')
    assert hasattr(s, 'scripts')
    assert hasattr(s, 'profiles')              # NEW
    # New _serverVersion fields:
    p = Playlist()
    assert hasattr(p, '_serverVersion')
    assert p._serverVersion == 0               # 0 means "not yet persisted"
    sc = Schedule()
    assert hasattr(sc, '_serverVersion')
    assert sc._serverVersion == 0
    # ScriptingProfile shape:
    prof = ScriptingProfile()
    assert hasattr(prof, 'name')
    assert hasattr(prof, 'label')
    assert hasattr(prof, 'matchDeviceType')
    assert hasattr(prof, 'scripts')
    assert hasattr(prof, 'launch')
    assert hasattr(prof, 'webclip')
    assert hasattr(prof, 'ssh')
    assert hasattr(prof, '_serverVersion')
    assert prof._serverVersion == 0
```

- [ ] **Step 2: Run the test — see it fail**

```bash
python -m pytest tests/unit/test_module_layout.py::test_state_classes_importable -c tests/pytest.ini -v
```

Expected: FAIL with `ImportError: cannot import name 'ScriptingProfile' from 'mosaicmesh.state'`.

- [ ] **Step 3: Add `_serverVersion` to `Playlist` and `Schedule` in `mosaicmesh/state.py`**

Find `class Playlist():` (around line 65). Replace its `__init__` with:

```python
class Playlist():
    def __init__(self):
        self.name = ""
        self.items = []      # list of item dicts: id, file, duration, playmode, backgroundColor, startEffect, endEffect
        self.loop = False
        # Monotonic version bumped on each PUT via the REST API. 0 = never persisted
        # via the REST surface (e.g. instances created in pre-PR-2 code paths or
        # loaded from older settings.dat). Used for If-Match optimistic
        # concurrency in mosaicmesh/api/playlists.py.
        self._serverVersion = 0
```

Find `class Schedule():` (around line 71). Replace its `__init__` with:

```python
class Schedule():
    def __init__(self):
        self.id = ""
        self.name = ""
        self.playlistName = ""
        self.displayID = ""
        self.priority = 0
        self.enabled = True
        self.freq = "DAILY"          # DAILY | WEEKLY | MONTHLY | YEARLY
        self.interval = 1
        self.byweekday = []          # ints 0=Mon..6=Sun (WEEKLY)
        self.dtstart = ""            # "YYYY-MM-DD"
        self.end = {"type": "never"} # or {"type":"until","untilDate":...} / {"type":"count","count":N}
        self.exdates = []            # ["YYYY-MM-DD", ...]
        self.startTime = "00:00"
        self.endTime = "23:59"
        # Monotonic version bumped on each PUT via the REST API. See Playlist
        # for rationale.
        self._serverVersion = 0
```

- [ ] **Step 4: Add `ScriptingProfile` class to `mosaicmesh/state.py`**

After the `Schedule` class, before `class PlayMode(Enum):`, add:

```python
class ScriptingProfile():
    """Per-device-type bundle of lifecycle scripts + launch configuration +
    webclip identity + SSH options. Auto-matched to clients by deviceType
    on REGISTER (matchDeviceType field). Template variables in script
    strings (e.g. {webclipBundleId}, {displayUrl}, {ip}) are substituted
    at run time via SafeDict.

    PR-2 ships the class shape + REST CRUD + Settings.profiles dict.
    PR-3 ships the launch dispatcher (_exec_ssh / _vnc_tap_sequence /
    _ssh_then_vnc in mosaicmesh.device_scripts) + the bootstrap migration
    that seeds the ipad1-ios5 default profile and removes the hardcoded
    DEFAULT_DEVICE_SCRIPTS from server.py.
    """
    def __init__(self):
        self.name = ""                # unique key (e.g. "ipad1-ios5")
        self.label = ""               # human label ("iPad 1 — iOS 5.1.1")
        self.matchDeviceType = ""     # auto-assign on REGISTER (e.g. "Tablet"); "" = manual only
        self.scripts = {              # template-variable shell commands
            "login": "",
            "start": "",
            "stop":  "",
            "test":  "",
            "reboot": "",
        }
        self.launch = {               # how to actually launch the display
            "method": "shell",        # "shell" | "vnc-tap" | "ssh-then-vnc"
        }
        self.webclip = {              # iOS-5 webclip metadata
            "bundleId": "",
            "title":    "",
        }
        self.ssh = {                  # SSH connection options
            "legacyCrypto": False,
            "user": "root",
            "keyPath": "",
        }
        # Monotonic version bumped on each PUT via the REST API.
        self._serverVersion = 0
```

- [ ] **Step 5: Add `Settings.profiles = {}` initialization**

Find `class Settings():` (around line 14). Locate its `__init__` body. After the existing `self.scripts = {}` line, add:

```python
        self.profiles = {}     # {name: ScriptingProfile} — populated by REST or
                               # PR-3's bootstrap; empty dict on first ever start
```

- [ ] **Step 6: Run the layout test — should pass now**

```bash
python -m pytest tests/unit/test_module_layout.py::test_state_classes_importable -c tests/pytest.ini -v
```

Expected: PASS.

- [ ] **Step 7: Run full unit suite — confirm no regressions**

```bash
python -m pytest tests/unit/ -c tests/pytest.ini --tb=no -q
```

Expected: same pass/fail counts as the baseline post-PR-1 (`13 failed / 296 passed / 2 skipped`).

- [ ] **Step 8: Commit**

```bash
git add mosaicmesh/state.py tests/unit/test_module_layout.py
git commit -m "feat(state): add _serverVersion to Playlist/Schedule, add ScriptingProfile class

PR-2 prep: REST endpoints need a monotonic version field for If-Match
optimistic concurrency. _serverVersion = 0 on construction (means
'never persisted via REST'); the new endpoints bump it on each PUT.

ScriptingProfile class added with shape-only fields (name, label,
matchDeviceType, scripts, launch, webclip, ssh, _serverVersion).
PR-3 will populate it with bootstrap defaults and wire the launch
dispatcher; PR-2 only needs the shape so the /api/profiles endpoints
can CRUD it.

Settings.profiles = {} added for the new entity collection.

Test count unchanged: 13 failed (pre-existing) / 296 passed / 2 skipped.

Part of PR-2 of the admin-timeline-redesign spec."
```

---

## Task 2: `mosaicmesh/api/_concurrency.py` — shared If-Match helpers

Tiny helper module used by playlists, schedules, and profiles. Centralizes the If-Match parsing logic so all three resources behave identically.

**Files:**
- Create: `mosaicmesh/api/_concurrency.py`
- Modify: `tests/unit/test_module_layout.py` (add smoke test)

- [ ] **Step 1: Add failing smoke test**

Append to `tests/unit/test_module_layout.py`:

```python
def test_api_concurrency_importable():
    from mosaicmesh.api._concurrency import (
        parse_if_match,
        precondition_required_response,
        precondition_failed_response,
        bump_version,
    )
    assert callable(parse_if_match)
    assert callable(precondition_required_response)
    assert callable(precondition_failed_response)
    assert callable(bump_version)


def test_parse_if_match_returns_int_or_none():
    from aiohttp.test_utils import make_mocked_request
    from mosaicmesh.api._concurrency import parse_if_match
    # Missing header
    req = make_mocked_request('PUT', '/x')
    assert parse_if_match(req) is None
    # Valid integer
    req = make_mocked_request('PUT', '/x', headers={'If-Match': '42'})
    assert parse_if_match(req) == 42
    # Non-integer (return None so the handler can decide 428/400)
    req = make_mocked_request('PUT', '/x', headers={'If-Match': 'bad'})
    assert parse_if_match(req) is None


def test_bump_version_increments_monotonically():
    from types import SimpleNamespace
    from mosaicmesh.api._concurrency import bump_version
    obj = SimpleNamespace(_serverVersion=0)
    bump_version(obj)
    assert obj._serverVersion == 1
    bump_version(obj)
    assert obj._serverVersion == 2
```

- [ ] **Step 2: Run, see fail**

```bash
python -m pytest tests/unit/test_module_layout.py -c tests/pytest.ini -v -k 'concurrency or if_match or bump_version'
```

Expected: 3 tests FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `mosaicmesh/api/_concurrency.py`**

```python
"""Shared optimistic-concurrency helpers for the PR-2 REST endpoints.

All mutating endpoints on versioned resources (Playlists, Schedules,
ScriptingProfiles) follow the same If-Match protocol:
  - PUT requires an If-Match header carrying the current _serverVersion.
  - Missing header -> 428 Precondition Required.
  - Stale version  -> 412 Precondition Failed.
  - Success        -> bump_version() increments the object's _serverVersion.

Centralizing the parsing + response shape keeps the three resource
modules trivially consistent. The response bodies follow the project's
{success: false, error: ...} convention.
"""
from aiohttp import web


def parse_if_match(request):
    """Return the integer If-Match version from the request headers, or
    None if the header is absent or non-integer. Handlers decide how
    to respond (428 for missing, 412 for stale, 400 for malformed).

    Note: HTTP allows If-Match to wrap the value in quotes (RFC 9110).
    We accept both '42' and '"42"' for friendliness.
    """
    raw = request.headers.get('If-Match')
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def precondition_required_response(resource_kind):
    """428 response for a mutating request that didn't send If-Match."""
    return web.json_response({
        "success": False,
        "error": f"If-Match header required for {resource_kind} update",
    }, status=428)


def precondition_failed_response(resource_kind, current_version):
    """412 response for a mutating request with a stale If-Match. Returns
    the current_version in the body so the client can resync."""
    return web.json_response({
        "success": False,
        "error": f"{resource_kind} was modified by another writer",
        "currentVersion": current_version,
    }, status=412)


def bump_version(obj):
    """Increment obj._serverVersion by 1. Used after every successful PUT
    so subsequent If-Match comparisons reflect the new state."""
    obj._serverVersion = int(getattr(obj, '_serverVersion', 0)) + 1
```

- [ ] **Step 4: Run, see pass**

```bash
python -m pytest tests/unit/test_module_layout.py -c tests/pytest.ini -v -k 'concurrency or if_match or bump_version'
```

Expected: all 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/api/_concurrency.py tests/unit/test_module_layout.py
git commit -m "feat(api): add shared If-Match concurrency helpers (mosaicmesh/api/_concurrency.py)

Centralizes the If-Match header parsing + 412/428 response shapes for
the upcoming /api/playlists, /api/schedules, /api/profiles endpoints.
Keeps the three resource modules consistent without duplication.

Accepts both '42' and '\"42\"' If-Match forms per RFC 9110.

Part of PR-2 of the admin-timeline-redesign spec."
```

---

## Task 3: `mosaicmesh/api/playlists.py` — Playlist CRUD

GET/POST/PUT/DELETE for the `Playlist` resource. Delete-with-refs returns 409 listing schedules that reference the playlist.

**Files:**
- Create: `mosaicmesh/api/playlists.py`
- Create: `tests/unit/test_api_playlists.py`
- Modify: `server.py` (import + route registration)

- [ ] **Step 1: Create the test file with failing GET test**

Create `tests/unit/test_api_playlists.py`:

```python
"""Unit tests for /api/playlists CRUD."""
import json
import pytest
from aiohttp.test_utils import make_mocked_request

# Boilerplate to import server with parse_args mocked (matches the existing
# pattern from test_api_endpoints.py).
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import argparse
_orig = argparse.ArgumentParser.parse_args

class _MockArgs:
    Port = 3000
    Verbose = False

argparse.ArgumentParser.parse_args = lambda self, args=None, namespace=None: _MockArgs()
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

from mosaicmesh.state import Settings, Playlist, Schedule
from mosaicmesh.api.playlists import (
    api_playlists_list, api_playlists_create,
    api_playlists_update, api_playlists_delete,
)


@pytest.fixture
def fresh_settings():
    """Replace server.settings with a fresh Settings object for each test."""
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev


class TestPlaylistsList:
    @pytest.mark.asyncio
    async def test_empty_list(self, fresh_settings):
        resp = await api_playlists_list(make_mocked_request('GET', '/api/playlists'))
        assert resp.status == 200
        data = json.loads(resp.text)
        assert data['success'] is True
        assert data['playlists'] == []

    @pytest.mark.asyncio
    async def test_lists_existing(self, fresh_settings):
        p = Playlist()
        p.name = "Morning News"
        p.items = [{"file": "/media/a.mp4"}]
        p.loop = True
        p._serverVersion = 5
        fresh_settings.playlists["Morning News"] = p
        resp = await api_playlists_list(make_mocked_request('GET', '/api/playlists'))
        data = json.loads(resp.text)
        assert len(data['playlists']) == 1
        out = data['playlists'][0]
        assert out['name'] == "Morning News"
        assert out['loop'] is True
        assert out['_serverVersion'] == 5
        assert out['items'] == [{"file": "/media/a.mp4"}]
```

- [ ] **Step 2: Run, see fail**

```bash
python -m pytest tests/unit/test_api_playlists.py -c tests/pytest.ini -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'mosaicmesh.api.playlists'`.

- [ ] **Step 3: Create `mosaicmesh/api/playlists.py`** with the GET handler first

```python
"""REST CRUD for Playlists. Backed by Settings.playlists (dict keyed by name).

All endpoints follow the project's {success, ...} response shape. Mutating
endpoints use If-Match for optimistic concurrency (see
mosaicmesh/api/_concurrency.py).

A Playlist is referenced by Schedules via Schedule.playlistName. DELETE
returns 409 with a refs list when the playlist is in use by any schedule,
so the admin UI can show 'Used by N schedules' before forcing the user
to disconnect them.
"""
import logging

from aiohttp import web

from mosaicmesh.state import Playlist
from mosaicmesh.persistence import saveSettings
from mosaicmesh.api._concurrency import (
    parse_if_match,
    precondition_required_response,
    precondition_failed_response,
    bump_version,
)

__all__ = [
    "api_playlists_list",
    "api_playlists_create",
    "api_playlists_update",
    "api_playlists_delete",
]


def _serialize(p):
    """Playlist -> dict. Mirrors what jsonpickle would emit but stripped of
    pickle metadata, which the timeline-UI client doesn't need."""
    return {
        "name": p.name,
        "items": list(p.items),
        "loop": bool(p.loop),
        "_serverVersion": int(getattr(p, "_serverVersion", 0)),
    }


async def api_playlists_list(request):
    """GET /api/playlists — list every playlist with its current version."""
    import server
    out = [_serialize(p) for p in server.settings.playlists.values()]
    return web.json_response({"success": True, "playlists": out})


async def api_playlists_create(request):
    """POST /api/playlists — create a new playlist. Body: {name, items?, loop?}.
    Returns 201 + {playlist}; 409 if a playlist with the same name exists."""
    import server
    try:
        body = await request.json()
    except Exception as e:
        return web.json_response({"success": False,
                                  "error": f"Invalid JSON: {e}"}, status=400)
    name = (body.get("name") or "").strip()
    if not name:
        return web.json_response({"success": False,
                                  "error": "name is required"}, status=400)
    if name in server.settings.playlists:
        return web.json_response({"success": False,
                                  "error": f"playlist '{name}' already exists"},
                                 status=409)
    p = Playlist()
    p.name = name
    p.items = list(body.get("items") or [])
    p.loop = bool(body.get("loop", False))
    p._serverVersion = 1   # first persistence
    server.settings.playlists[name] = p
    saveSettings()
    return web.json_response({"success": True, "playlist": _serialize(p)},
                             status=201)


async def api_playlists_update(request):
    """PUT /api/playlists/{name} — update items + loop. If-Match required.
    Returns 200 + {playlist}; 404 if missing; 412 if stale; 428 if no If-Match."""
    import server
    name = request.match_info.get("name", "")
    p = server.settings.playlists.get(name)
    if p is None:
        return web.json_response({"success": False,
                                  "error": f"playlist '{name}' not found"},
                                 status=404)
    if_match = parse_if_match(request)
    if if_match is None:
        return precondition_required_response("playlist")
    if if_match != p._serverVersion:
        return precondition_failed_response("playlist", p._serverVersion)
    try:
        body = await request.json()
    except Exception as e:
        return web.json_response({"success": False,
                                  "error": f"Invalid JSON: {e}"}, status=400)
    if "items" in body:
        p.items = list(body["items"])
    if "loop" in body:
        p.loop = bool(body["loop"])
    bump_version(p)
    saveSettings()
    return web.json_response({"success": True, "playlist": _serialize(p)})


async def api_playlists_delete(request):
    """DELETE /api/playlists/{name} — remove. Returns 204; 404 if missing;
    409 with refs list if any schedule references the playlist."""
    import server
    name = request.match_info.get("name", "")
    if name not in server.settings.playlists:
        return web.json_response({"success": False,
                                  "error": f"playlist '{name}' not found"},
                                 status=404)
    refs = [s.id for s in server.settings.schedules.values()
            if getattr(s, "playlistName", "") == name]
    if refs:
        return web.json_response({
            "success": False,
            "error": f"playlist '{name}' is referenced by {len(refs)} schedule(s)",
            "refs": refs,
        }, status=409)
    del server.settings.playlists[name]
    saveSettings()
    return web.Response(status=204)
```

- [ ] **Step 4: Run the GET tests — should pass**

```bash
python -m pytest tests/unit/test_api_playlists.py::TestPlaylistsList -c tests/pytest.ini -v
```

Expected: both `test_empty_list` and `test_lists_existing` PASS.

- [ ] **Step 5: Add POST tests + run**

Append to `tests/unit/test_api_playlists.py`:

```python
class TestPlaylistsCreate:
    @pytest.mark.asyncio
    async def test_create_minimal(self, fresh_settings):
        req = make_mocked_request('POST', '/api/playlists')
        req._payload = None  # not used; we override .json below
        async def _json():
            return {"name": "Lunch Menu"}
        req.json = _json
        resp = await api_playlists_create(req)
        assert resp.status == 201
        data = json.loads(resp.text)
        assert data['success'] is True
        assert data['playlist']['name'] == "Lunch Menu"
        assert data['playlist']['_serverVersion'] == 1
        assert "Lunch Menu" in fresh_settings.playlists

    @pytest.mark.asyncio
    async def test_create_with_items_and_loop(self, fresh_settings):
        req = make_mocked_request('POST', '/api/playlists')
        async def _json():
            return {"name": "X", "items": [{"file": "/m/a.mp4"}], "loop": True}
        req.json = _json
        resp = await api_playlists_create(req)
        assert resp.status == 201
        data = json.loads(resp.text)
        assert data['playlist']['loop'] is True
        assert data['playlist']['items'] == [{"file": "/m/a.mp4"}]

    @pytest.mark.asyncio
    async def test_create_requires_name(self, fresh_settings):
        req = make_mocked_request('POST', '/api/playlists')
        async def _json():
            return {"loop": True}
        req.json = _json
        resp = await api_playlists_create(req)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_create_rejects_duplicate(self, fresh_settings):
        fresh_settings.playlists["dup"] = Playlist()
        req = make_mocked_request('POST', '/api/playlists')
        async def _json():
            return {"name": "dup"}
        req.json = _json
        resp = await api_playlists_create(req)
        assert resp.status == 409
```

Run:

```bash
python -m pytest tests/unit/test_api_playlists.py::TestPlaylistsCreate -c tests/pytest.ini -v
```

Expected: 4 tests PASS.

- [ ] **Step 6: Add PUT tests + run**

Append to `tests/unit/test_api_playlists.py`:

```python
class TestPlaylistsUpdate:
    def _req(self, name, body, if_match=None):
        headers = {'If-Match': str(if_match)} if if_match is not None else {}
        req = make_mocked_request('PUT', f'/api/playlists/{name}',
                                  headers=headers,
                                  match_info={"name": name})
        async def _json():
            return body
        req.json = _json
        return req

    @pytest.mark.asyncio
    async def test_update_with_matching_if_match(self, fresh_settings):
        p = Playlist()
        p.name = "x"
        p._serverVersion = 3
        fresh_settings.playlists["x"] = p
        resp = await api_playlists_update(self._req("x", {"loop": True}, if_match=3))
        assert resp.status == 200
        data = json.loads(resp.text)
        assert data['playlist']['_serverVersion'] == 4
        assert data['playlist']['loop'] is True

    @pytest.mark.asyncio
    async def test_update_missing_if_match_returns_428(self, fresh_settings):
        p = Playlist()
        p._serverVersion = 1
        fresh_settings.playlists["x"] = p
        resp = await api_playlists_update(self._req("x", {"loop": True}))
        assert resp.status == 428

    @pytest.mark.asyncio
    async def test_update_stale_if_match_returns_412(self, fresh_settings):
        p = Playlist()
        p._serverVersion = 5
        fresh_settings.playlists["x"] = p
        resp = await api_playlists_update(self._req("x", {"loop": True}, if_match=2))
        assert resp.status == 412
        data = json.loads(resp.text)
        assert data['currentVersion'] == 5

    @pytest.mark.asyncio
    async def test_update_missing_returns_404(self, fresh_settings):
        resp = await api_playlists_update(self._req("nope", {"loop": True}, if_match=1))
        assert resp.status == 404
```

Run:

```bash
python -m pytest tests/unit/test_api_playlists.py::TestPlaylistsUpdate -c tests/pytest.ini -v
```

Expected: 4 tests PASS.

- [ ] **Step 7: Add DELETE tests + run**

Append to `tests/unit/test_api_playlists.py`:

```python
class TestPlaylistsDelete:
    @pytest.mark.asyncio
    async def test_delete_existing(self, fresh_settings):
        fresh_settings.playlists["x"] = Playlist()
        req = make_mocked_request('DELETE', '/api/playlists/x',
                                  match_info={"name": "x"})
        resp = await api_playlists_delete(req)
        assert resp.status == 204
        assert "x" not in fresh_settings.playlists

    @pytest.mark.asyncio
    async def test_delete_missing_returns_404(self, fresh_settings):
        req = make_mocked_request('DELETE', '/api/playlists/nope',
                                  match_info={"name": "nope"})
        resp = await api_playlists_delete(req)
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_delete_referenced_returns_409_with_refs(self, fresh_settings):
        fresh_settings.playlists["pl"] = Playlist()
        s1 = Schedule(); s1.id = "sch-1"; s1.playlistName = "pl"
        s2 = Schedule(); s2.id = "sch-2"; s2.playlistName = "pl"
        s3 = Schedule(); s3.id = "sch-other"; s3.playlistName = "other"
        fresh_settings.schedules = {"sch-1": s1, "sch-2": s2, "sch-other": s3}
        req = make_mocked_request('DELETE', '/api/playlists/pl',
                                  match_info={"name": "pl"})
        resp = await api_playlists_delete(req)
        assert resp.status == 409
        data = json.loads(resp.text)
        assert set(data['refs']) == {"sch-1", "sch-2"}
        assert "pl" in fresh_settings.playlists   # not removed
```

Run:

```bash
python -m pytest tests/unit/test_api_playlists.py::TestPlaylistsDelete -c tests/pytest.ini -v
```

Expected: 3 tests PASS.

- [ ] **Step 8: Register routes in `server.py`**

Add to the import block in `server.py` (after `from mosaicmesh.api.discovery import …`):

```python
from mosaicmesh.api.playlists import (
    api_playlists_list, api_playlists_create,
    api_playlists_update, api_playlists_delete,
)
```

Find the route table near the bottom of `server.py` (inside `if __name__ == '__main__':`; locate via `grep -n "/api/discovery/devices" server.py` and look for the cluster of `app.router.add_*` calls). Add:

```python
app.router.add_get('/api/playlists', api_playlists_list)
app.router.add_post('/api/playlists', api_playlists_create)
app.router.add_put('/api/playlists/{name}', api_playlists_update)
app.router.add_delete('/api/playlists/{name}', api_playlists_delete)
```

- [ ] **Step 9: Run the full suite — confirm new tests + no regressions**

```bash
python -m pytest tests/unit/ -c tests/pytest.ini --tb=no -q
```

Expected: 13 failed (pre-existing) / 296 + 12 (Task-1 new) + 11 (this task) = 319 passed / 2 skipped. (Exact number depends on cleanup commits during Tasks 1-2; the regression is "no NEW failures vs baseline".)

- [ ] **Step 10: Commit**

```bash
git add mosaicmesh/api/playlists.py tests/unit/test_api_playlists.py server.py
git commit -m "feat(api/playlists): add GET/POST/PUT/DELETE /api/playlists CRUD

11 pytest cases covering: empty list, populated list, create-minimal,
create-with-items, missing-name 400, duplicate-name 409, update with
matching If-Match (200 + version bump), missing If-Match 428, stale
If-Match 412, missing playlist 404, delete success, delete missing 404,
delete-with-schedule-refs 409 + refs list.

Routes registered in server.py's __main__ route table.

Part of PR-2 of the admin-timeline-redesign spec."
```

---

## Task 4: `mosaicmesh/api/schedules.py` — Schedule CRUD with validation

Similar shape to playlists but with extra validation: `freq ∈ {DAILY,WEEKLY,MONTHLY,YEARLY}`, `playlistName` must reference an existing playlist, `displayID` must reference an existing display, time format `HH:MM`.

**Files:**
- Create: `mosaicmesh/api/schedules.py`
- Create: `tests/unit/test_api_schedules.py`
- Modify: `server.py`

- [ ] **Step 1: Create the test file skeleton + GET tests**

Create `tests/unit/test_api_schedules.py`:

```python
"""Unit tests for /api/schedules CRUD + validation."""
import json
import pytest
from aiohttp.test_utils import make_mocked_request

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import argparse
_orig = argparse.ArgumentParser.parse_args

class _MockArgs:
    Port = 3000
    Verbose = False

argparse.ArgumentParser.parse_args = lambda self, args=None, namespace=None: _MockArgs()
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

from mosaicmesh.state import Settings, Schedule, Playlist, Display
from mosaicmesh.api.schedules import (
    api_schedules_list, api_schedules_create,
    api_schedules_update, api_schedules_delete,
)


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    server.settings.playlists["Morning"] = Playlist()
    server.settings.playlists["Morning"].name = "Morning"
    server.settings.displays["Lobby"] = Display()
    yield server.settings
    server.settings = prev


class TestSchedulesList:
    @pytest.mark.asyncio
    async def test_empty_list(self, fresh_settings):
        resp = await api_schedules_list(make_mocked_request('GET', '/api/schedules'))
        assert resp.status == 200
        assert json.loads(resp.text)['schedules'] == []

    @pytest.mark.asyncio
    async def test_lists_existing(self, fresh_settings):
        s = Schedule()
        s.id = "abc-123"
        s.playlistName = "Morning"
        s.displayID = "Lobby"
        s.freq = "WEEKLY"
        s.byweekday = [0, 1, 2, 3, 4]
        s.startTime = "08:00"
        s.endTime = "11:00"
        s._serverVersion = 2
        fresh_settings.schedules[s.id] = s
        resp = await api_schedules_list(make_mocked_request('GET', '/api/schedules'))
        data = json.loads(resp.text)
        assert len(data['schedules']) == 1
        out = data['schedules'][0]
        assert out['id'] == "abc-123"
        assert out['_serverVersion'] == 2
        assert out['byweekday'] == [0, 1, 2, 3, 4]
```

- [ ] **Step 2: Run, see fail**

```bash
python -m pytest tests/unit/test_api_schedules.py -c tests/pytest.ini -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'mosaicmesh.api.schedules'`.

- [ ] **Step 3: Create `mosaicmesh/api/schedules.py` with full CRUD**

```python
"""REST CRUD for Schedules. Backed by Settings.schedules (dict keyed by id).

Validation is stricter than Playlists because Schedules have foreign-key-
style links (playlistName -> Playlists, displayID -> Displays) plus
recurrence-rule semantics that must be parseable by dateutil.rrule (the
same code path mosaicmesh.scheduling.schedule_active_at uses).

Schedule.id is server-generated on POST (uuid4) — clients never need to
mint one.
"""
import logging
import uuid

from aiohttp import web

from mosaicmesh.state import Schedule
from mosaicmesh.persistence import saveSettings
from mosaicmesh.api._concurrency import (
    parse_if_match,
    precondition_required_response,
    precondition_failed_response,
    bump_version,
)

__all__ = [
    "api_schedules_list",
    "api_schedules_create",
    "api_schedules_update",
    "api_schedules_delete",
]

_VALID_FREQ = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}


def _serialize(s):
    return {
        "id": s.id,
        "name": s.name,
        "playlistName": s.playlistName,
        "displayID": s.displayID,
        "priority": int(s.priority),
        "enabled": bool(s.enabled),
        "freq": s.freq,
        "interval": int(s.interval),
        "byweekday": list(s.byweekday or []),
        "dtstart": s.dtstart,
        "end": dict(s.end or {"type": "never"}),
        "exdates": list(s.exdates or []),
        "startTime": s.startTime,
        "endTime": s.endTime,
        "_serverVersion": int(getattr(s, "_serverVersion", 0)),
    }


def _validate_time_str(s):
    """HH:MM with 0-23 hours and 0-59 minutes. Returns (ok, error_msg)."""
    if not isinstance(s, str) or len(s) != 5 or s[2] != ':':
        return False, f"time '{s}' must be HH:MM"
    try:
        hh, mm = int(s[:2]), int(s[3:])
    except ValueError:
        return False, f"time '{s}' must be HH:MM with numeric values"
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return False, f"time '{s}' out of range (00:00 to 23:59)"
    return True, None


def _validate_fields(body, settings, partial=False):
    """Validate body fields against Schedule's contract.
    partial=True (PUT) skips presence checks; partial=False (POST) requires
    playlistName + displayID. Returns (ok, error_msg)."""
    if not partial:
        if not body.get("playlistName"):
            return False, "playlistName is required"
        if not body.get("displayID"):
            return False, "displayID is required"
    if "playlistName" in body and body["playlistName"] not in settings.playlists:
        return False, f"playlist '{body['playlistName']}' not found"
    if "displayID" in body and body["displayID"] not in settings.displays:
        return False, f"display '{body['displayID']}' not found"
    if "freq" in body and body["freq"] not in _VALID_FREQ:
        return False, f"freq '{body['freq']}' must be one of {sorted(_VALID_FREQ)}"
    if "interval" in body:
        try:
            if int(body["interval"]) < 1:
                return False, "interval must be >= 1"
        except (TypeError, ValueError):
            return False, "interval must be an integer"
    if "byweekday" in body:
        if not isinstance(body["byweekday"], list):
            return False, "byweekday must be a list of integers 0-6"
        for d in body["byweekday"]:
            if not isinstance(d, int) or not (0 <= d <= 6):
                return False, "byweekday entries must be integers 0-6 (Mon=0)"
    if "startTime" in body:
        ok, err = _validate_time_str(body["startTime"])
        if not ok:
            return False, err
    if "endTime" in body:
        ok, err = _validate_time_str(body["endTime"])
        if not ok:
            return False, err
    return True, None


def _apply_fields(s, body):
    """Copy provided fields from body to the Schedule object. Skips id and
    _serverVersion (those are managed server-side)."""
    for field in ("name", "playlistName", "displayID", "priority",
                  "enabled", "freq", "interval", "byweekday",
                  "dtstart", "end", "exdates", "startTime", "endTime"):
        if field in body:
            setattr(s, field, body[field])


async def api_schedules_list(request):
    """GET /api/schedules — list every schedule."""
    import server
    out = [_serialize(s) for s in server.settings.schedules.values()]
    return web.json_response({"success": True, "schedules": out})


async def api_schedules_create(request):
    """POST /api/schedules — create a new schedule. id auto-generated.
    Body: at minimum {playlistName, displayID}; other fields take Schedule
    defaults. Returns 201 + {schedule}; 400 on validation; 404 if
    referenced playlist/display missing."""
    import server
    try:
        body = await request.json()
    except Exception as e:
        return web.json_response({"success": False,
                                  "error": f"Invalid JSON: {e}"}, status=400)
    ok, err = _validate_fields(body, server.settings, partial=False)
    if not ok:
        return web.json_response({"success": False, "error": err}, status=400)
    s = Schedule()
    s.id = uuid.uuid4().hex[:16]
    _apply_fields(s, body)
    s._serverVersion = 1
    server.settings.schedules[s.id] = s
    saveSettings()
    return web.json_response({"success": True, "schedule": _serialize(s)},
                             status=201)


async def api_schedules_update(request):
    """PUT /api/schedules/{id} — update any subset of fields. If-Match required.
    Returns 200 + {schedule}; 404 if missing; 412 if stale; 428 if no If-Match."""
    import server
    sid = request.match_info.get("id", "")
    s = server.settings.schedules.get(sid)
    if s is None:
        return web.json_response({"success": False,
                                  "error": f"schedule '{sid}' not found"},
                                 status=404)
    if_match = parse_if_match(request)
    if if_match is None:
        return precondition_required_response("schedule")
    if if_match != s._serverVersion:
        return precondition_failed_response("schedule", s._serverVersion)
    try:
        body = await request.json()
    except Exception as e:
        return web.json_response({"success": False,
                                  "error": f"Invalid JSON: {e}"}, status=400)
    ok, err = _validate_fields(body, server.settings, partial=True)
    if not ok:
        return web.json_response({"success": False, "error": err}, status=400)
    _apply_fields(s, body)
    bump_version(s)
    saveSettings()
    return web.json_response({"success": True, "schedule": _serialize(s)})


async def api_schedules_delete(request):
    """DELETE /api/schedules/{id} — remove. Returns 204; 404 if missing.
    No reference check (schedules aren't referenced by other entities)."""
    import server
    sid = request.match_info.get("id", "")
    if sid not in server.settings.schedules:
        return web.json_response({"success": False,
                                  "error": f"schedule '{sid}' not found"},
                                 status=404)
    del server.settings.schedules[sid]
    saveSettings()
    return web.Response(status=204)
```

- [ ] **Step 4: Run GET tests — should pass**

```bash
python -m pytest tests/unit/test_api_schedules.py::TestSchedulesList -c tests/pytest.ini -v
```

Expected: 2 tests PASS.

- [ ] **Step 5: Add POST + validation tests + run**

Append to `tests/unit/test_api_schedules.py`:

```python
class TestSchedulesCreate:
    def _post(self, body):
        req = make_mocked_request('POST', '/api/schedules')
        async def _json(): return body
        req.json = _json
        return req

    @pytest.mark.asyncio
    async def test_minimal_create(self, fresh_settings):
        resp = await api_schedules_create(self._post(
            {"playlistName": "Morning", "displayID": "Lobby"}))
        assert resp.status == 201
        data = json.loads(resp.text)
        sid = data['schedule']['id']
        assert sid and len(sid) == 16
        assert data['schedule']['_serverVersion'] == 1
        assert sid in fresh_settings.schedules

    @pytest.mark.asyncio
    async def test_missing_playlist_400(self, fresh_settings):
        resp = await api_schedules_create(self._post({"displayID": "Lobby"}))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_missing_display_400(self, fresh_settings):
        resp = await api_schedules_create(self._post({"playlistName": "Morning"}))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_unknown_playlist_400(self, fresh_settings):
        resp = await api_schedules_create(self._post(
            {"playlistName": "Ghost", "displayID": "Lobby"}))
        assert resp.status == 400
        assert "Ghost" in json.loads(resp.text)['error']

    @pytest.mark.asyncio
    async def test_unknown_display_400(self, fresh_settings):
        resp = await api_schedules_create(self._post(
            {"playlistName": "Morning", "displayID": "Ghost"}))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_invalid_freq_400(self, fresh_settings):
        resp = await api_schedules_create(self._post(
            {"playlistName": "Morning", "displayID": "Lobby", "freq": "HOURLY"}))
        assert resp.status == 400
        assert "HOURLY" in json.loads(resp.text)['error']

    @pytest.mark.asyncio
    async def test_invalid_time_400(self, fresh_settings):
        resp = await api_schedules_create(self._post(
            {"playlistName": "Morning", "displayID": "Lobby",
             "startTime": "25:00"}))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_invalid_byweekday_400(self, fresh_settings):
        resp = await api_schedules_create(self._post(
            {"playlistName": "Morning", "displayID": "Lobby",
             "freq": "WEEKLY", "byweekday": [0, 7]}))
        assert resp.status == 400
```

Run:

```bash
python -m pytest tests/unit/test_api_schedules.py::TestSchedulesCreate -c tests/pytest.ini -v
```

Expected: 8 tests PASS.

- [ ] **Step 6: Add PUT + DELETE tests + run**

Append to `tests/unit/test_api_schedules.py`:

```python
class TestSchedulesUpdate:
    def _put(self, sid, body, if_match=None):
        headers = {'If-Match': str(if_match)} if if_match is not None else {}
        req = make_mocked_request('PUT', f'/api/schedules/{sid}',
                                  headers=headers,
                                  match_info={"id": sid})
        async def _json(): return body
        req.json = _json
        return req

    @pytest.mark.asyncio
    async def test_update_partial_with_if_match(self, fresh_settings):
        s = Schedule(); s.id = "abc"; s._serverVersion = 1
        s.playlistName = "Morning"; s.displayID = "Lobby"
        fresh_settings.schedules["abc"] = s
        resp = await api_schedules_update(
            self._put("abc", {"enabled": False}, if_match=1))
        assert resp.status == 200
        data = json.loads(resp.text)
        assert data['schedule']['enabled'] is False
        assert data['schedule']['_serverVersion'] == 2

    @pytest.mark.asyncio
    async def test_update_stale_412(self, fresh_settings):
        s = Schedule(); s.id = "abc"; s._serverVersion = 5
        fresh_settings.schedules["abc"] = s
        resp = await api_schedules_update(
            self._put("abc", {"enabled": False}, if_match=2))
        assert resp.status == 412

    @pytest.mark.asyncio
    async def test_update_missing_if_match_428(self, fresh_settings):
        s = Schedule(); s.id = "abc"; s._serverVersion = 1
        fresh_settings.schedules["abc"] = s
        resp = await api_schedules_update(self._put("abc", {"enabled": False}))
        assert resp.status == 428

    @pytest.mark.asyncio
    async def test_update_unknown_playlist_400(self, fresh_settings):
        s = Schedule(); s.id = "abc"; s._serverVersion = 1
        s.playlistName = "Morning"; s.displayID = "Lobby"
        fresh_settings.schedules["abc"] = s
        resp = await api_schedules_update(
            self._put("abc", {"playlistName": "Ghost"}, if_match=1))
        assert resp.status == 400


class TestSchedulesDelete:
    @pytest.mark.asyncio
    async def test_delete_existing(self, fresh_settings):
        s = Schedule(); s.id = "abc"
        fresh_settings.schedules["abc"] = s
        req = make_mocked_request('DELETE', '/api/schedules/abc',
                                  match_info={"id": "abc"})
        resp = await api_schedules_delete(req)
        assert resp.status == 204
        assert "abc" not in fresh_settings.schedules

    @pytest.mark.asyncio
    async def test_delete_missing_404(self, fresh_settings):
        req = make_mocked_request('DELETE', '/api/schedules/nope',
                                  match_info={"id": "nope"})
        resp = await api_schedules_delete(req)
        assert resp.status == 404
```

Run:

```bash
python -m pytest tests/unit/test_api_schedules.py -c tests/pytest.ini -v
```

Expected: all 14 tests in the file PASS.

- [ ] **Step 7: Register routes in `server.py`**

Add to the import block:

```python
from mosaicmesh.api.schedules import (
    api_schedules_list, api_schedules_create,
    api_schedules_update, api_schedules_delete,
)
```

In the route table:

```python
app.router.add_get('/api/schedules', api_schedules_list)
app.router.add_post('/api/schedules', api_schedules_create)
app.router.add_put('/api/schedules/{id}', api_schedules_update)
app.router.add_delete('/api/schedules/{id}', api_schedules_delete)
```

- [ ] **Step 8: Run the full suite — confirm no regressions**

```bash
python -m pytest tests/unit/ -c tests/pytest.ini --tb=no -q
```

Expected: 13 failed (pre-existing) / (previous + 14) passed / 2 skipped.

- [ ] **Step 9: Commit**

```bash
git add mosaicmesh/api/schedules.py tests/unit/test_api_schedules.py server.py
git commit -m "feat(api/schedules): add GET/POST/PUT/DELETE /api/schedules CRUD + validation

14 pytest cases. Validation: required playlistName + displayID on POST,
both must reference existing entities; freq restricted to {DAILY, WEEKLY,
MONTHLY, YEARLY}; interval >= 1; byweekday entries 0-6; startTime/endTime
in HH:MM format. id auto-generated server-side (uuid4 16-hex).

Routes registered in server.py.

Part of PR-2 of the admin-timeline-redesign spec."
```

---

## Task 5: `mosaicmesh/api/profiles.py` — ScriptingProfile CRUD + client assignment

CRUD for `ScriptingProfile` plus `POST /api/clients/{clientKey}/profile` for assigning a profile to a single client.

**Files:**
- Create: `mosaicmesh/api/profiles.py`
- Create: `tests/unit/test_api_profiles.py`
- Modify: `server.py`

- [ ] **Step 1: Create the test file with GET + smoke tests**

Create `tests/unit/test_api_profiles.py`:

```python
"""Unit tests for /api/profiles CRUD + POST /api/clients/{key}/profile."""
import json
import pytest
from aiohttp.test_utils import make_mocked_request

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import argparse
_orig = argparse.ArgumentParser.parse_args

class _MockArgs:
    Port = 3000
    Verbose = False

argparse.ArgumentParser.parse_args = lambda self, args=None, namespace=None: _MockArgs()
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

from mosaicmesh.state import Settings, ScriptingProfile, Client
from mosaicmesh.api.profiles import (
    api_profiles_list, api_profiles_create,
    api_profiles_update, api_profiles_delete,
    api_clients_assign_profile,
)


@pytest.fixture
def fresh_settings():
    prev = getattr(server, 'settings', None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev


class TestProfilesList:
    @pytest.mark.asyncio
    async def test_empty_list(self, fresh_settings):
        resp = await api_profiles_list(make_mocked_request('GET', '/api/profiles'))
        assert resp.status == 200
        assert json.loads(resp.text)['profiles'] == []

    @pytest.mark.asyncio
    async def test_lists_existing(self, fresh_settings):
        p = ScriptingProfile()
        p.name = "ipad1-ios5"
        p.label = "iPad 1"
        p.matchDeviceType = "Tablet"
        p._serverVersion = 3
        fresh_settings.profiles["ipad1-ios5"] = p
        resp = await api_profiles_list(make_mocked_request('GET', '/api/profiles'))
        data = json.loads(resp.text)
        assert len(data['profiles']) == 1
        assert data['profiles'][0]['name'] == "ipad1-ios5"
        assert data['profiles'][0]['_serverVersion'] == 3
```

- [ ] **Step 2: Run, see fail**

```bash
python -m pytest tests/unit/test_api_profiles.py -c tests/pytest.ini -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `mosaicmesh/api/profiles.py`**

```python
"""REST CRUD for ScriptingProfiles plus per-client profile assignment.

PR-2 implements the CRUD shell. PR-3 will populate Settings.profiles with
the bootstrapped 'ipad1-ios5' default + wire device_scripts.py to actually
USE these profiles for script execution. Until PR-3 lands, profiles are
inert data — clients still execute scripts via the hardcoded
DEFAULT_DEVICE_SCRIPTS.

A ScriptingProfile is referenced by Clients via Client.profileName.
DELETE returns 409 with a refs list (clientKeys) when the profile is
assigned to any client.
"""
import logging

from aiohttp import web

from mosaicmesh.state import ScriptingProfile
from mosaicmesh.persistence import saveSettings
from mosaicmesh.api._concurrency import (
    parse_if_match,
    precondition_required_response,
    precondition_failed_response,
    bump_version,
)

__all__ = [
    "api_profiles_list",
    "api_profiles_create",
    "api_profiles_update",
    "api_profiles_delete",
    "api_clients_assign_profile",
]


def _serialize(p):
    return {
        "name": p.name,
        "label": p.label,
        "matchDeviceType": p.matchDeviceType,
        "scripts": dict(p.scripts or {}),
        "launch": dict(p.launch or {}),
        "webclip": dict(p.webclip or {}),
        "ssh": dict(p.ssh or {}),
        "_serverVersion": int(getattr(p, "_serverVersion", 0)),
    }


def _apply_fields(p, body):
    """Copy provided fields onto the ScriptingProfile. Skips name and
    _serverVersion (name is the key; version is server-managed)."""
    if "label" in body:
        p.label = body["label"]
    if "matchDeviceType" in body:
        p.matchDeviceType = body["matchDeviceType"]
    if "scripts" in body and isinstance(body["scripts"], dict):
        p.scripts = dict(body["scripts"])
    if "launch" in body and isinstance(body["launch"], dict):
        p.launch = dict(body["launch"])
    if "webclip" in body and isinstance(body["webclip"], dict):
        p.webclip = dict(body["webclip"])
    if "ssh" in body and isinstance(body["ssh"], dict):
        p.ssh = dict(body["ssh"])


async def api_profiles_list(request):
    """GET /api/profiles — list every scripting profile."""
    import server
    out = [_serialize(p) for p in server.settings.profiles.values()]
    return web.json_response({"success": True, "profiles": out})


async def api_profiles_create(request):
    """POST /api/profiles — create a new profile. Body: {name, label?, ...}.
    Returns 201; 400 if name missing; 409 if name taken."""
    import server
    try:
        body = await request.json()
    except Exception as e:
        return web.json_response({"success": False,
                                  "error": f"Invalid JSON: {e}"}, status=400)
    name = (body.get("name") or "").strip()
    if not name:
        return web.json_response({"success": False,
                                  "error": "name is required"}, status=400)
    if name in server.settings.profiles:
        return web.json_response({"success": False,
                                  "error": f"profile '{name}' already exists"},
                                 status=409)
    p = ScriptingProfile()
    p.name = name
    _apply_fields(p, body)
    p._serverVersion = 1
    server.settings.profiles[name] = p
    saveSettings()
    return web.json_response({"success": True, "profile": _serialize(p)},
                             status=201)


async def api_profiles_update(request):
    """PUT /api/profiles/{name} — update. If-Match required."""
    import server
    name = request.match_info.get("name", "")
    p = server.settings.profiles.get(name)
    if p is None:
        return web.json_response({"success": False,
                                  "error": f"profile '{name}' not found"},
                                 status=404)
    if_match = parse_if_match(request)
    if if_match is None:
        return precondition_required_response("profile")
    if if_match != p._serverVersion:
        return precondition_failed_response("profile", p._serverVersion)
    try:
        body = await request.json()
    except Exception as e:
        return web.json_response({"success": False,
                                  "error": f"Invalid JSON: {e}"}, status=400)
    _apply_fields(p, body)
    bump_version(p)
    saveSettings()
    return web.json_response({"success": True, "profile": _serialize(p)})


async def api_profiles_delete(request):
    """DELETE /api/profiles/{name} — remove. Returns 204; 404 if missing;
    409 + refs list if any Client.profileName references it."""
    import server
    name = request.match_info.get("name", "")
    if name not in server.settings.profiles:
        return web.json_response({"success": False,
                                  "error": f"profile '{name}' not found"},
                                 status=404)
    refs = [k for k, c in server.settings.clients.items()
            if getattr(c, "profileName", None) == name]
    if refs:
        return web.json_response({
            "success": False,
            "error": f"profile '{name}' is assigned to {len(refs)} client(s)",
            "refs": refs,
        }, status=409)
    del server.settings.profiles[name]
    saveSettings()
    return web.Response(status=204)


async def api_clients_assign_profile(request):
    """POST /api/clients/{clientKey}/profile — set Client.profileName.
    Body: {profileName: '...' | null}. null clears the override.
    Returns 200; 404 if client or profile missing."""
    import server
    ckey = request.match_info.get("clientKey", "")
    client = server.settings.clients.get(ckey)
    if client is None:
        return web.json_response({"success": False,
                                  "error": f"client '{ckey}' not found"},
                                 status=404)
    try:
        body = await request.json()
    except Exception as e:
        return web.json_response({"success": False,
                                  "error": f"Invalid JSON: {e}"}, status=400)
    pname = body.get("profileName")
    if pname is not None and pname not in server.settings.profiles:
        return web.json_response({"success": False,
                                  "error": f"profile '{pname}' not found"},
                                 status=404)
    client.profileName = pname   # may be None to clear
    saveSettings()
    return web.json_response({
        "success": True,
        "clientKey": ckey,
        "profileName": pname,
    })
```

**Note:** `Client.profileName` is referenced here but isn't a defined attribute on `Client` (it gets added in PR-3). Until PR-3, `setattr(client, 'profileName', value)` just adds an attribute at runtime. That works — the test below will confirm.

- [ ] **Step 4: Run GET tests — should pass**

```bash
python -m pytest tests/unit/test_api_profiles.py::TestProfilesList -c tests/pytest.ini -v
```

Expected: 2 tests PASS.

- [ ] **Step 5: Add POST + PUT + DELETE tests + run**

Append to `tests/unit/test_api_profiles.py`:

```python
class TestProfilesCreate:
    def _post(self, body):
        req = make_mocked_request('POST', '/api/profiles')
        async def _json(): return body
        req.json = _json
        return req

    @pytest.mark.asyncio
    async def test_create_minimal(self, fresh_settings):
        resp = await api_profiles_create(self._post({"name": "p1"}))
        assert resp.status == 201
        assert "p1" in fresh_settings.profiles
        assert fresh_settings.profiles["p1"]._serverVersion == 1

    @pytest.mark.asyncio
    async def test_create_with_full_shape(self, fresh_settings):
        body = {
            "name": "ipad1-ios5",
            "label": "iPad 1 — iOS 5.1.1",
            "matchDeviceType": "Tablet",
            "scripts": {"login": "echo LOGIN_OK", "start": "echo START_OK"},
            "launch": {"method": "ssh-then-vnc", "vncPassword": "x",
                       "taps": [{"fbX": 945, "fbY": 671}]},
            "webclip": {"bundleId": "com.apple.webapp-X", "title": "MM"},
            "ssh": {"legacyCrypto": True, "user": "root", "keyPath": "~/.ssh/k"},
        }
        resp = await api_profiles_create(self._post(body))
        assert resp.status == 201
        data = json.loads(resp.text)
        assert data['profile']['scripts']['login'] == "echo LOGIN_OK"
        assert data['profile']['launch']['method'] == "ssh-then-vnc"

    @pytest.mark.asyncio
    async def test_create_requires_name(self, fresh_settings):
        resp = await api_profiles_create(self._post({"label": "x"}))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_create_rejects_duplicate(self, fresh_settings):
        fresh_settings.profiles["dup"] = ScriptingProfile()
        resp = await api_profiles_create(self._post({"name": "dup"}))
        assert resp.status == 409


class TestProfilesUpdateDelete:
    def _put(self, name, body, if_match=None):
        headers = {'If-Match': str(if_match)} if if_match is not None else {}
        req = make_mocked_request('PUT', f'/api/profiles/{name}',
                                  headers=headers,
                                  match_info={"name": name})
        async def _json(): return body
        req.json = _json
        return req

    @pytest.mark.asyncio
    async def test_update_with_if_match(self, fresh_settings):
        p = ScriptingProfile(); p.name = "x"; p._serverVersion = 3
        fresh_settings.profiles["x"] = p
        resp = await api_profiles_update(
            self._put("x", {"label": "renamed"}, if_match=3))
        assert resp.status == 200
        data = json.loads(resp.text)
        assert data['profile']['label'] == "renamed"
        assert data['profile']['_serverVersion'] == 4

    @pytest.mark.asyncio
    async def test_update_stale_412(self, fresh_settings):
        p = ScriptingProfile(); p.name = "x"; p._serverVersion = 5
        fresh_settings.profiles["x"] = p
        resp = await api_profiles_update(
            self._put("x", {"label": "renamed"}, if_match=2))
        assert resp.status == 412

    @pytest.mark.asyncio
    async def test_delete_unreferenced(self, fresh_settings):
        fresh_settings.profiles["x"] = ScriptingProfile()
        req = make_mocked_request('DELETE', '/api/profiles/x',
                                  match_info={"name": "x"})
        resp = await api_profiles_delete(req)
        assert resp.status == 204

    @pytest.mark.asyncio
    async def test_delete_referenced_409(self, fresh_settings):
        fresh_settings.profiles["pr"] = ScriptingProfile()
        c1 = Client(); c1.profileName = "pr"
        c2 = Client(); c2.profileName = "pr"
        c3 = Client(); c3.profileName = "other"
        fresh_settings.clients = {"c1": c1, "c2": c2, "c3": c3}
        req = make_mocked_request('DELETE', '/api/profiles/pr',
                                  match_info={"name": "pr"})
        resp = await api_profiles_delete(req)
        assert resp.status == 409
        assert set(json.loads(resp.text)['refs']) == {"c1", "c2"}


class TestClientsAssignProfile:
    def _post(self, ckey, body):
        req = make_mocked_request('POST',
                                  f'/api/clients/{ckey}/profile',
                                  match_info={"clientKey": ckey})
        async def _json(): return body
        req.json = _json
        return req

    @pytest.mark.asyncio
    async def test_assign(self, fresh_settings):
        fresh_settings.profiles["ipad1"] = ScriptingProfile()
        fresh_settings.clients["c1"] = Client()
        resp = await api_clients_assign_profile(
            self._post("c1", {"profileName": "ipad1"}))
        assert resp.status == 200
        assert fresh_settings.clients["c1"].profileName == "ipad1"

    @pytest.mark.asyncio
    async def test_clear(self, fresh_settings):
        fresh_settings.profiles["ipad1"] = ScriptingProfile()
        c = Client(); c.profileName = "ipad1"
        fresh_settings.clients["c1"] = c
        resp = await api_clients_assign_profile(
            self._post("c1", {"profileName": None}))
        assert resp.status == 200
        assert fresh_settings.clients["c1"].profileName is None

    @pytest.mark.asyncio
    async def test_unknown_client_404(self, fresh_settings):
        resp = await api_clients_assign_profile(
            self._post("ghost", {"profileName": "x"}))
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_unknown_profile_404(self, fresh_settings):
        fresh_settings.clients["c1"] = Client()
        resp = await api_clients_assign_profile(
            self._post("c1", {"profileName": "ghost"}))
        assert resp.status == 404
```

Run:

```bash
python -m pytest tests/unit/test_api_profiles.py -c tests/pytest.ini -v
```

Expected: 12 tests PASS.

- [ ] **Step 6: Register routes in `server.py`**

Add to the import block:

```python
from mosaicmesh.api.profiles import (
    api_profiles_list, api_profiles_create,
    api_profiles_update, api_profiles_delete,
    api_clients_assign_profile,
)
```

In the route table:

```python
app.router.add_get('/api/profiles', api_profiles_list)
app.router.add_post('/api/profiles', api_profiles_create)
app.router.add_put('/api/profiles/{name}', api_profiles_update)
app.router.add_delete('/api/profiles/{name}', api_profiles_delete)
app.router.add_post('/api/clients/{clientKey}/profile', api_clients_assign_profile)
```

- [ ] **Step 7: Run the full suite — confirm no regressions**

```bash
python -m pytest tests/unit/ -c tests/pytest.ini --tb=no -q
```

Expected: 13 failed (pre-existing) / (previous + 12) passed / 2 skipped.

- [ ] **Step 8: Commit**

```bash
git add mosaicmesh/api/profiles.py tests/unit/test_api_profiles.py server.py
git commit -m "feat(api/profiles): add ScriptingProfile CRUD + per-client assignment

12 pytest cases. CRUD on /api/profiles + POST /api/clients/{key}/profile
to set Client.profileName (null clears). DELETE returns 409 + refs list
when assigned to any client.

Profiles are inert in PR-2 — Settings.profiles is empty until PR-3
bootstraps the 'ipad1-ios5' default. Until then, lifecycle scripts
still run from the hardcoded DEFAULT_DEVICE_SCRIPTS in
mosaicmesh.device_scripts.

Part of PR-2 of the admin-timeline-redesign spec."
```

---

## Task 6: `mosaicmesh/api/media.py` — relocate from server.py

`GET /api/media` (relocated from `server.py:1957`) and `POST /api/upload` (relocated from `server.py:1150`). Pure relocation — no behavior change.

**Files:**
- Create: `mosaicmesh/api/media.py`
- Create: `tests/unit/test_api_media.py`
- Modify: `server.py` (delete originals, add imports + route registration)

- [ ] **Step 1: Read the existing handlers + create the new module**

First, read both handlers from server.py so the relocation is verifiable:

```bash
grep -n '^async def api_media\|^async def upload_handler\|^async def get_video_duration' server.py
sed -n '1150,1185p' server.py        # upload_handler body
sed -n '1957,1980p' server.py        # api_media body
sed -n '1930,1960p' server.py        # get_video_duration (likely stays in server.py — it's used elsewhere)
```

Then create `mosaicmesh/api/media.py`:

```python
"""REST endpoints for the shared media library.

GET /api/media        - list /media/server/{images,videos} + video durations
POST /api/upload      - accept multipart media uploads

Pure relocation from server.py per PR-2 of the spec. Behavior is
identical; the only changes are:
  - settings.X access goes through `import server; server.settings.X`
    (lazy, established pattern)
  - get_video_duration (used by api_media) stays in server.py for now
    because other code paths use it; we call it via `server.get_video_duration`.
"""
import json
import os
import logging

from aiohttp import web

__all__ = [
    "api_media",
    "upload_handler",
]


async def api_media(request):
    """List the shared media library under media/server/{images,videos}, plus
    per-video durations (seconds) so the playlist editor can offer 'full length'."""
    import server

    def _list(sub):
        d = os.path.join("media", "server", sub)
        if not os.path.isdir(d):
            return []
        return ["/media/server/" + sub + "/" + f
                for f in sorted(os.listdir(d))
                if os.path.isfile(os.path.join(d, f))]

    videos = _list("videos")
    durations = {}
    for url in videos:
        disk = os.path.join("media", "server", "videos", os.path.basename(url))
        d = await server.get_video_duration(disk)
        if d is not None:
            durations[url] = round(d, 1)
    body = json.dumps({"images": _list("images"), "videos": videos,
                       "videoDurations": durations})
    return web.Response(text=body, content_type="application/json")


async def upload_handler(request):
    """POST /upload/{dest} — accept a single multipart file upload and route
    it to the appropriate processor (calibrate / image / video) based on the
    URL dest segment. All three processors live in server.py; we lazy-import
    server and call them as server.<name>(path, filename)."""
    import server
    logging.debug("UPLOAD_HANDLER")
    uploadDest = request.match_info.get('dest')
    logging.debug(uploadDest)
    reader = await request.multipart()
    # reader.next() will `yield` the fields of your form
    field = await reader.next()
    logging.debug(field.name)
    filename = field.filename
    # You cannot rely on Content-Length if transfer is chunked.
    size = 0
    path = os.path.join('cache')
    if not os.path.exists(path):
        os.mkdir(path)
    with open(os.path.join(path, filename), 'wb') as f:
        while True:
            chunk = await field.read_chunk()  # 8192 bytes by default.
            if not chunk:
                break
            size += len(chunk)
            f.write(chunk)

    response = "none"
    ct = 'application/octet-stream'

    if uploadDest == "calibrate":
        response, ct = server.calibrate(os.path.join(path, filename))
    elif uploadDest == "image":
        response, ct = server.processImage(path, filename)
    elif uploadDest == "video":
        response, ct = server.processVideo(path, filename)
    return web.Response(body=response, content_type=ct)
```

**Implementation note:** The upload_handler body is non-trivial (~30 lines of multipart parsing + image/video routing). The exact text from server.py:1150–1180 should be pasted verbatim with the documented substitution.

- [ ] **Step 2: Create tests**

Create `tests/unit/test_api_media.py`:

```python
"""Unit tests for /api/media + /api/upload (relocated from server.py).

Relocation tests — assert handlers are importable from the new module and
that the existing api_media response shape is preserved. The full upload
path is exercised by the existing test_api_endpoints.py suite (which
calls server.upload_handler that resolves through the re-import).
"""
import json
import os
import tempfile
import pytest
from aiohttp.test_utils import make_mocked_request

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import argparse
_orig = argparse.ArgumentParser.parse_args

class _MockArgs:
    Port = 3000
    Verbose = False

argparse.ArgumentParser.parse_args = lambda self, args=None, namespace=None: _MockArgs()
try:
    import server
finally:
    argparse.ArgumentParser.parse_args = _orig

from mosaicmesh.api.media import api_media, upload_handler


def test_media_handlers_importable():
    """Smoke check: both handlers are callable from mosaicmesh.api.media."""
    assert callable(api_media)
    assert callable(upload_handler)


def test_server_reexports_media_handlers():
    """server.py still exposes api_media + upload_handler so existing
    route bindings + tests calling server.X continue to work."""
    assert server.api_media is api_media
    assert server.upload_handler is upload_handler


class TestApiMediaResponseShape:
    @pytest.mark.asyncio
    async def test_lists_empty_directories(self, tmp_path, monkeypatch):
        """Empty media/server/{images,videos} returns empty lists."""
        # Run from a tmp_path so media/server doesn't exist
        monkeypatch.chdir(tmp_path)
        resp = await api_media(make_mocked_request('GET', '/api/media'))
        data = json.loads(resp.text)
        assert data['images'] == []
        assert data['videos'] == []
        assert data['videoDurations'] == {}

    @pytest.mark.asyncio
    async def test_lists_image_files(self, tmp_path, monkeypatch):
        d = tmp_path / "media" / "server" / "images"
        d.mkdir(parents=True)
        (d / "a.png").write_bytes(b"\x89PNG")
        (d / "b.jpg").write_bytes(b"\xff\xd8\xff")
        monkeypatch.chdir(tmp_path)
        resp = await api_media(make_mocked_request('GET', '/api/media'))
        data = json.loads(resp.text)
        assert "/media/server/images/a.png" in data['images']
        assert "/media/server/images/b.jpg" in data['images']
        assert data['videos'] == []
```

- [ ] **Step 3: Run, see fail**

```bash
python -m pytest tests/unit/test_api_media.py -c tests/pytest.ini -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Run again after creating module — should pass**

```bash
python -m pytest tests/unit/test_api_media.py -c tests/pytest.ini -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Delete originals from `server.py` + add re-import**

In `server.py`:
1. Delete the `async def api_media(...)` block (around line 1957–1976).
2. Delete the `async def upload_handler(...)` block (around line 1150–1180).
3. Add to the import block:

```python
from mosaicmesh.api.media import api_media, upload_handler
```

4. Verify the existing route bindings for `/api/media` and `/upload/*` (probably `app.router.add_post('/upload/...', upload_handler)`) still wire through the re-imported names — no change needed.

- [ ] **Step 6: Run the full suite — confirm no regressions**

```bash
python -m pytest tests/unit/ -c tests/pytest.ini --tb=no -q
```

Expected: 13 failed (pre-existing) / (previous + 4) passed / 2 skipped.

If `test_api_endpoints.py::test_api_media_*` exists and fails because it now patches the wrong target, update those patch decorators to `mosaicmesh.api.media.X` (same pattern as Task 9 + Task 11 patch-target updates in PR-1).

- [ ] **Step 7: Commit**

```bash
git add mosaicmesh/api/media.py tests/unit/test_api_media.py server.py
git commit -m "refactor(api/media): relocate api_media + upload_handler to mosaicmesh/api/media.py

Pure relocation from server.py (api_media at ~line 1957, upload_handler
at ~line 1150). server.py re-imports both names; route bindings continue
to wire via the re-import. get_video_duration stays in server.py and is
accessed via lazy 'import server; server.get_video_duration' inside
api_media (other code paths still use it).

4 new pytest cases (importable smoke + re-export identity + empty/populated
media list response shape). Existing /api/upload behavior tests in
test_api_endpoints.py continue to pass unchanged.

Part of PR-2 of the admin-timeline-redesign spec."
```

---

## Task 7: Update `CLAUDE.md` + final verification

Document the new REST surface in CLAUDE.md so future Claude Code sessions know it exists. Run the full suite one more time to confirm no cross-task regressions.

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Read the current CLAUDE.md Architecture + Conventions sections**

```bash
grep -n -A 5 "REST" CLAUDE.md
grep -n -A 3 "api_discovery" CLAUDE.md
```

- [ ] **Step 2: Update the "Device discovery & auto-config" paragraph**

Find the paragraph beginning `**Device discovery & auto-config.**` (added in PR-1's CLAUDE.md update). Append a new paragraph after it:

```markdown
**REST API surface for the admin UI.** The new admin timeline view (PR-4)
hydrates from REST endpoints, one module per resource under
`mosaicmesh/api/`:

  - `/api/playlists` (GET / POST / PUT / DELETE) — `mosaicmesh/api/playlists.py`
  - `/api/schedules` (GET / POST / PUT / DELETE) — `mosaicmesh/api/schedules.py`
  - `/api/profiles`  (GET / POST / PUT / DELETE) + `POST /api/clients/{key}/profile` — `mosaicmesh/api/profiles.py`
  - `/api/media`     (GET) + `/api/upload` (POST) — `mosaicmesh/api/media.py`

All mutating endpoints use `If-Match: <_serverVersion>` for optimistic
concurrency. The helper module `mosaicmesh/api/_concurrency.py` centralizes
the header parsing + 412/428 response shapes. Successful PUTs bump the
target's `_serverVersion` by 1; the response always echoes the new
version on the returned resource. DELETE on `Playlist` or `ScriptingProfile`
returns 409 + a `refs` list when the resource is referenced by a Schedule
or Client respectively.

Response convention (matches `/api/discovery/configure`):
`{success: true, ...}` on success, `{success: false, error: "..."}` on
error, HTTP status per resource (201 create, 204 delete, 400 validation,
404 missing, 409 conflict, 412 stale, 428 missing If-Match).
```

- [ ] **Step 3: Update the Layout section**

In the `mosaicmesh/` bullet list, replace `api/discovery.py` with:

```markdown
  - `api/discovery.py` — `auto_configure_client` (deviceType → displayID),
                         `get_discovered_devices`, `sync_new_client_to_group`,
                         the cache-push propagation calculators, and the
                         three `/api/discovery/*` aiohttp REST handlers.
  - `api/playlists.py` — REST CRUD for `Playlist` (GET/POST/PUT/DELETE
                         /api/playlists; If-Match concurrency; 409+refs
                         on DELETE when referenced by a Schedule).
  - `api/schedules.py` — REST CRUD for `Schedule` with foreign-key
                         validation (playlistName + displayID must exist),
                         freq + byweekday + HH:MM time format checks;
                         id auto-generated server-side (uuid4-16hex).
  - `api/profiles.py`  — REST CRUD for `ScriptingProfile` + per-client
                         assignment (`POST /api/clients/{key}/profile`).
                         PR-2 only ships the CRUD shell; PR-3 wires
                         dispatcher behavior + bootstrap default.
  - `api/media.py`     — `GET /api/media` (lists media/server/{images,
                         videos}); `POST /api/upload` (multipart upload).
                         Relocated from server.py in PR-2.
  - `api/_concurrency.py` — shared If-Match parsing + 412/428 response
                         helpers used by playlists, schedules, profiles.
```

- [ ] **Step 4: Run a quick markdown sanity check**

```bash
head -20 CLAUDE.md
```

Eyeball heading levels and bullet structure.

- [ ] **Step 5: Run the FULL test suite one more time**

```bash
python -m pytest tests/unit/ -c tests/pytest.ini -q
python -m pytest tests/integration/ -c tests/pytest.ini -q
```

Expected:
- Unit: 13 failed (pre-existing) / 296 + (count of new PR-2 tests, ~45) passed / 2 skipped.
- Integration: 6 passed / 2 skipped (unchanged).

- [ ] **Step 6: Smoke-test the server starts cleanly**

```bash
timeout 5 python server.py 2>&1 | tail -10
```

Expected: server boots, prints listening port, then is killed by timeout. No tracebacks during startup.

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude-md): document PR-2 REST surface (/api/playlists, /api/schedules, /api/profiles, /api/media)

CLAUDE.md now describes the four new REST modules under mosaicmesh/api/
plus the shared If-Match concurrency helper, the response-shape
convention, and the 409+refs DELETE behavior. Layout section's
mosaicmesh/ bullet list now enumerates every api/ sub-module.

Closes PR-2 of the admin-timeline-redesign spec."
```

---

## Self-review checklist (run before opening the PR)

- [ ] `python -m pytest tests/unit/ -c tests/pytest.ini --tb=no -q` shows 13 pre-existing failures + ~340 passes / 2 skipped (296 baseline + ~45 new PR-2 tests).
- [ ] `python -m pytest tests/integration/ -c tests/pytest.ini -q` shows 6/2 unchanged.
- [ ] `python server.py -v` starts cleanly; no startup errors.
- [ ] `curl http://localhost:3000/api/playlists` returns `{"success": true, "playlists": []}` (when settings.dat is empty).
- [ ] `curl -X POST http://localhost:3000/api/playlists -d '{"name":"test"}' -H "Content-Type: application/json"` returns 201 with `_serverVersion: 1`.
- [ ] `git log --oneline feature/pr2-rest-endpoints` shows 7 task commits (state model, _concurrency helper, playlists, schedules, profiles, media, CLAUDE.md).

---

## Notes for the implementing engineer

1. **Pattern consistency with PR-1.** Every PR-2 module follows the established mosaicmesh idioms: module-level docstring orienting the reader, `__all__` listing exports, `import server` only inside function bodies (never at module load time), `from mosaicmesh.persistence import saveSettings` for persistence calls. Don't deviate from this.

2. **The `Client.profileName` attribute** doesn't exist on the `Client` class in PR-2 — it's added in PR-3's migration. Python lets us `setattr(client, 'profileName', value)` even without the attribute defined, so the `/api/clients/{key}/profile` endpoint works. The test for "delete profile with refs" relies on this dynamic-attribute behavior — verify it still works after PR-3 lands.

3. **Test patches that target `server.X` for moved functions** — if `tests/unit/test_api_endpoints.py` patches `server.api_media` or `server.upload_handler` (check via `grep -n 'patch.*api_media\|patch.*upload_handler' tests/`), those patches resolve through the re-import in `server.py` because both `server.api_media` and `mosaicmesh.api.media.api_media` point to the SAME function object. So `patch('server.api_media')` continues to work without target updates. If a test patches `server.get_video_duration` (still in server.py), no change needed either.

4. **No new server.py route handlers besides what's added in tasks 3-6.** Specifically, do NOT add wrapping handlers in server.py that delegate to the new modules — `app.router.add_*(..., api_playlists_list)` etc. directly references the re-imported handler. That's the simplest wiring and matches how `/api/discovery/*` already works.

5. **`saveSettings()` is called after every successful mutation.** This is the same convention as `/api/discovery/configure`. The hash-based incremental save in `mosaicmesh/persistence.py` means a no-op mutation (e.g. PUT with identical fields) doesn't actually write to disk, so the overhead is acceptable.

6. **After Task 5 (profiles), the spec's PR-3 (ScriptingProfile dispatcher) becomes much easier to build** — the data model exists, the CRUD endpoints exist, and the only PR-3 work is wiring `_run_device_script` to look up the profile and execute via the dispatcher (plus the bootstrap migration).
