# Per-Device Media Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the 27 GB / 93 s LAN-bandwidth saturation observed during fleet PLAY by caching per-iPad MP4 segments on each device's local storage. Two paths under a unified server contract — `lighttpd` daemon on iPad-1 / iOS 5 fleet, Service Worker + Cache API on modern devices — chosen by a per-`Client.cacheMode` flag and exposed via per-iPad URL routing in PLAY payloads.

**Architecture:** Server-side `Client.cacheMode` + `Client.cachedSegments` drive a `_resolve_media_url(client, item)` decision that returns `http://127.0.0.1:8080/...` for cached iPad-1 devices or central-server URLs otherwise. Onboarding installs lighttpd + config + LaunchDaemon. A render-complete hook scp-pushes segments to iPad-1 cache mode devices. Cache invalidation rides on the existing `encode_ver` hash embedded in segment filenames.

**Tech Stack:** Python (aiohttp + asyncio + jsonpickle on the server), PowerShell (onboarding), JavaScript (sw.js + index.html integration), lighttpd 1.4.18 (from Saurik's apt repo), launchd.

**Spec:** `docs/superpowers/specs/2026-06-03-media-cache-design.md`

**Phasing:** Tasks 1–11 deliver the iPad-1 lighttpd cache path end-to-end. Tasks 12–14 add Service Worker support for modern devices (orthogonal; can be deferred until modern hardware joins the fleet). Tasks 15–16 are validation. The plan is internally consistent if you stop after Task 11.

---

## File structure

Files this plan creates or modifies:

- Modify: `server.py`
  - `Client` class: add `cacheMode`, `cachedSegments` fields (~lines 1430–1470, the Client class block)
  - `migrate_client_objects()` (search for the function name): backfill the new fields on old persisted objects
  - `msg_response()` (~line 2099): add `ANNOUNCE_CACHE_MODE` handler
  - Near render pipeline: add `_push_segment_to_cached_clients()` async helper
  - Near `_build_media_elements`: add `_resolve_media_url()` + per-iPad payload generation
  - `process()` loop: invoke a periodic `_reconcile_ipad_cache()` janitor
  - `api_discovery_configure` (~line 1850 area): add `set_cache_mode` action
- Modify: `tools/onboard_devices.ps1`
  - `$DEFAULT_TWEAKS`: append `lighttpd`
  - New step 5.4d: write `/etc/lighttpd/lighttpd.conf` via SSH heredoc
  - New step 5.4e: write `/Library/LaunchDaemons/com.mosaicmesh.lighttpd.plist` + `launchctl load`
  - New step 5.4f: POST to server's `api_discovery_configure` to set `cacheMode=lighttpd-localhost`
- Create: `sw.js` (project root)
- Modify: `index.html`
  - Add Service Worker registration block (legacy-iOS-5-safe try/catch)
  - Add `prefetchPlaylistMedia()` called from the PRELOAD handler
- Modify: `tests/unit/test_client_management.py` (existing test file for Client class)
  - Add tests for `cacheMode` + `cachedSegments` defaults + migration
- Create: `tests/unit/test_media_cache.py`
  - Tests for `_resolve_media_url`, `_push_segment_to_cached_clients` mock-based tests, `_reconcile_ipad_cache`
- Modify: `requirements.txt` — no change (vncdotool already there from earlier work; no new deps)

---

## Task 1: Add `cacheMode` and `cachedSegments` fields to `Client`

**Files:**
- Modify: `server.py` (Client class, `migrate_client_objects`)
- Modify: `tests/unit/test_client_management.py`

- [ ] **Step 1: Find the existing `Client` class definition and tests**

```bash
cd "C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh"
grep -nE "^class Client" server.py
grep -nE "def migrate_client_objects" server.py
ls tests/unit/test_client_management.py
```

Expected: one line for `class Client:` and one line for `def migrate_client_objects(`. Note the line numbers.

- [ ] **Step 2: Write failing tests in `tests/unit/test_client_management.py`**

Add these tests to the existing file (don't replace it):

```python
def test_client_cachemode_default_is_none():
    c = server.Client()
    assert c.cacheMode == "none"


def test_client_cachedsegments_default_is_empty_set():
    c = server.Client()
    assert c.cachedSegments == set()
    # mutating one client's set must not affect another's
    c.cachedSegments.add("abc_1")
    c2 = server.Client()
    assert c2.cachedSegments == set()


def test_migrate_backfills_cache_fields_on_old_client():
    """A Client pickled before the cache fields were added should get
    backfilled to defaults by migrate_client_objects()."""
    server.settings = server.Settings()
    c = server.Client()
    # Simulate a pre-cache-fields Client by deleting the attributes
    del c.cacheMode
    del c.cachedSegments
    server.settings.clients["legacy"] = c
    server.migrate_client_objects()
    after = server.settings.clients["legacy"]
    assert after.cacheMode == "none"
    assert after.cachedSegments == set()
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
python -m pytest tests/unit/test_client_management.py::test_client_cachemode_default_is_none \
                tests/unit/test_client_management.py::test_client_cachedsegments_default_is_empty_set \
                tests/unit/test_client_management.py::test_migrate_backfills_cache_fields_on_old_client \
                -c tests/pytest.ini -v
```

Expected: all three FAIL with AttributeError (Client has no attribute `cacheMode` / `cachedSegments`) or the migrate test fails because the field stays missing.

- [ ] **Step 4: Add the fields to `Client.__init__`**

Find `class Client:` and its `__init__`. Add these two assignments alongside the existing field initialisations (placement order doesn't matter for functionality; group them with other "discovery" fields for readability):

```python
        # Cache-state model (2026-06-03). cacheMode = "none" by default;
        # set to "lighttpd-localhost" by onboarding when the iPad has
        # lighttpd installed and a writable /var/mobile/Media/
        # MosaicMeshCache/ dir. Set to "service-worker" by the client's
        # ANNOUNCE_CACHE_MODE message when SW registration succeeds.
        # See docs/superpowers/specs/2026-06-03-media-cache-design.md.
        self.cacheMode = "none"
        # Hashes of segments currently cached on this device, in the
        # form "<encode_ver_hash>_<segment_index>" (matches the
        # seg_<HASH>_<N>.mp4 filename convention from the render
        # pipeline). Populated by _push_segment_to_cached_clients on
        # successful scp; pruned by _reconcile_ipad_cache.
        self.cachedSegments = set()
```

- [ ] **Step 5: Update `migrate_client_objects()` to backfill old persisted clients**

Find the function. It already backfills several newer fields onto older Client objects loaded from `settings.dat`. Append these defensive guards at the end (style matching the existing `if not hasattr(c, "..."): c.... = ...` pattern):

```python
        if not hasattr(c, "cacheMode"):
            c.cacheMode = "none"
        if not hasattr(c, "cachedSegments"):
            c.cachedSegments = set()
```

- [ ] **Step 6: Run the tests, verify they pass**

```bash
python -m pytest tests/unit/test_client_management.py::test_client_cachemode_default_is_none \
                tests/unit/test_client_management.py::test_client_cachedsegments_default_is_empty_set \
                tests/unit/test_client_management.py::test_migrate_backfills_cache_fields_on_old_client \
                -c tests/pytest.ini -v
```

Expected: all three PASS.

- [ ] **Step 7: Run the broader unit suite to check no regression**

```bash
python pytest_runner.py --unit 2>&1 | tail -5
```

Expected: pre-existing pass/fail counts unchanged except the three new tests now pass.

- [ ] **Step 8: Commit**

```bash
git add server.py tests/unit/test_client_management.py
git commit -m "feat(server): Client gets cacheMode + cachedSegments fields

Cache-state model from the media-cache spec. Default cacheMode=\"none\"
means no caching (current behavior). Backfill in migrate_client_objects
covers Clients persisted to settings.dat before these fields existed.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: Add `ANNOUNCE_CACHE_MODE` SockJS message handler

**Files:**
- Modify: `server.py` (msg_response in the if/elif chain)
- Modify: `tests/unit/test_client_management.py` OR new file `tests/unit/test_cache_announce.py`

- [ ] **Step 1: Find an existing simple `msg_response` handler to mirror**

```bash
grep -nE 'elif\(msg\["REQUEST"\] == "SYNACK"\)' server.py
sed -n '2153,2160p' server.py
```

Expected: the SYNACK handler is a short, atomic pattern we'll mirror.

- [ ] **Step 2: Write a failing test**

Append to `tests/unit/test_client_management.py`:

```python
def test_announce_cache_mode_sets_service_worker():
    """A client emitting ANNOUNCE_CACHE_MODE with mode=service-worker
    should result in client.cacheMode being set to that value."""
    from unittest.mock import MagicMock
    server.settings = server.Settings()
    c = server.Client()
    c.clientID = "sess123"
    server.settings.clients["modern_device"] = c

    session = MagicMock()
    session.id = "sess123"
    session.request.headers = {"User-Agent": "Mozilla/5.0 (...) Chrome/120"}
    session.request.remote = "192.168.1.100"

    msg = {"SRC": "modern_device", "DEST": "SRV",
           "REQUEST": "ANNOUNCE_CACHE_MODE",
           "PAYLOAD": {"mode": "service-worker"}}
    server.msg_response(msg, session)
    assert server.settings.clients["modern_device"].cacheMode == "service-worker"


def test_announce_cache_mode_rejects_unknown_mode():
    """An unknown mode value must NOT clobber an existing cacheMode."""
    from unittest.mock import MagicMock
    server.settings = server.Settings()
    c = server.Client()
    c.clientID = "sess123"
    c.cacheMode = "lighttpd-localhost"
    server.settings.clients["k"] = c
    session = MagicMock(); session.id = "sess123"
    session.request.headers = {"User-Agent": "x"}; session.request.remote = "1.1.1.1"
    server.msg_response({"SRC":"k","DEST":"SRV","REQUEST":"ANNOUNCE_CACHE_MODE",
                          "PAYLOAD":{"mode":"hack"}}, session)
    assert server.settings.clients["k"].cacheMode == "lighttpd-localhost"
```

- [ ] **Step 3: Run tests; verify they fail**

```bash
python -m pytest tests/unit/test_client_management.py::test_announce_cache_mode_sets_service_worker \
                tests/unit/test_client_management.py::test_announce_cache_mode_rejects_unknown_mode \
                -c tests/pytest.ini -v
```

Expected: both FAIL — the request type isn't handled yet, so cacheMode stays at its default.

- [ ] **Step 4: Add the handler**

Find the SYNACK handler in `msg_response`. Right after its block, insert:

```python
    elif(msg["REQUEST"] == "ANNOUNCE_CACHE_MODE"):
        # Client-announced cache capability. The client side knows whether
        # it has a working Service Worker, lighttpd, or neither; it tells
        # us so we can route PLAY payload URLs correctly. The whitelist
        # below prevents a malicious or bug-induced client from setting
        # an arbitrary string (which would break _resolve_media_url's
        # logic in subtle ways).
        client = settings.clients.get(msg["SRC"])
        mode = (msg.get("PAYLOAD") or {}).get("mode")
        if client and mode in ("none", "lighttpd-localhost", "service-worker"):
            client.cacheMode = mode
        response["PAYLOAD"] = {"cacheMode": getattr(client, "cacheMode", "none")}
```

- [ ] **Step 5: Run tests, verify pass**

```bash
python -m pytest tests/unit/test_client_management.py::test_announce_cache_mode_sets_service_worker \
                tests/unit/test_client_management.py::test_announce_cache_mode_rejects_unknown_mode \
                -c tests/pytest.ini -v
```

Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add server.py tests/unit/test_client_management.py
git commit -m "feat(server): ANNOUNCE_CACHE_MODE handler accepts client-announced cache capability

Whitelisted to none|lighttpd-localhost|service-worker so a bad client
cannot poke arbitrary strings into Client.cacheMode.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: Add `set_cache_mode` action to `api_discovery_configure`

**Files:**
- Modify: `server.py` (api_discovery_configure)

This is the path the onboarding script will use to mark an iPad as `cacheMode=lighttpd-localhost` after installing lighttpd. The existing discovery REST API at `/api/discovery/configure` already accepts an `action` field (per CLAUDE.md: "configure accepts both field-update and action payloads").

- [ ] **Step 1: Find the existing handler + its action branches**

```bash
grep -nE 'def api_discovery_configure|action.*reconfigure|action.*bulk' server.py
```

Note the line range of the function so the new branch fits the existing style.

- [ ] **Step 2: Add the action branch**

Inside `api_discovery_configure`'s `if action == ...:` chain, after the existing `reconfigure` / `bulk_reconfigure` branches, add:

```python
        elif action == "set_cache_mode":
            client_key = payload.get("clientKey")
            mode = payload.get("mode")
            client = settings.clients.get(client_key)
            if client is None:
                return web.json_response({"status": "ERROR",
                                          "message": f"unknown client {client_key}"},
                                         status=404)
            if mode not in ("none", "lighttpd-localhost", "service-worker"):
                return web.json_response({"status": "ERROR",
                                          "message": f"invalid mode {mode!r}"},
                                         status=400)
            client.cacheMode = mode
            save_settings_incremental()
            return web.json_response({"status": "SUCCESS",
                                      "clientKey": client_key,
                                      "cacheMode": client.cacheMode})
```

- [ ] **Step 3: Verify the module parses + import has no side effects**

```bash
python -c "import ast; ast.parse(open('server.py').read()); print('parse OK')"
python -c "import server; print('import OK')"
```

Both must succeed.

- [ ] **Step 4: Quick manual smoke (only if the server is currently running and you have an iPad to target — otherwise skip; Task 15 validates end-to-end)**

```bash
# Pick any existing online client_key from the discovery API
KEY=$(curl -s http://localhost:3000/api/discovery/devices \
      | python -c "import json,sys; print([d['clientKey'] for d in (json.load(sys.stdin).get('devices', json.load.__self__) if isinstance(json.load.__self__, dict) else [])][0])")
# Actually simpler — get one fresh:
KEY=$(curl -s http://localhost:3000/api/discovery/devices | python -c "import json,sys; d=json.load(sys.stdin); print((d.get('devices',d) if isinstance(d,dict) else d)[0]['clientKey'])")
curl -s -X POST http://localhost:3000/api/discovery/configure \
     -H "Content-Type: application/json" \
     -d "{\"action\":\"set_cache_mode\",\"clientKey\":\"$KEY\",\"mode\":\"none\"}"
# Expected: {"status":"SUCCESS","clientKey":"...","cacheMode":"none"}
```

- [ ] **Step 5: Commit**

```bash
git add server.py
git commit -m "feat(server): api_discovery_configure set_cache_mode action

Onboarding will POST {action:set_cache_mode, clientKey, mode:lighttpd-localhost}
after installing lighttpd, so the server knows to route this client's PLAY
payloads to localhost URLs.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: Implement `_resolve_media_url` pure-function URL routing

**Files:**
- Modify: `server.py` (new helper)
- Create or modify: `tests/unit/test_media_cache.py`

This function is the core decision point. Pure function = easy to TDD.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_media_cache.py`:

```python
"""Tests for the media-cache URL routing logic. See
docs/superpowers/plans/2026-06-03-media-cache.md Task 4."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import server


class _Item:
    """Minimal MediaElement stand-in for unit tests."""
    def __init__(self, file, playmode="SEGMENT", seg_hash=None, seg_n=None):
        self.file = file
        self.playmode = playmode
        self.seg_hash = seg_hash
        self.seg_n = seg_n


def test_resolve_media_url_returns_localhost_for_cached_ipad1():
    client = server.Client()
    client.clientKey = "abc"
    client.cacheMode = "lighttpd-localhost"
    client.cachedSegments = {"f00d_1"}
    item = _Item(file="ignored", seg_hash="f00d", seg_n=1)
    url = server._resolve_media_url(client, item)
    assert url == "http://127.0.0.1:8080/seg_f00d_1.mp4"


def test_resolve_media_url_falls_back_to_central_for_uncached_ipad1():
    client = server.Client()
    client.clientKey = "abc"
    client.cacheMode = "lighttpd-localhost"
    client.cachedSegments = set()  # not yet pushed
    item = _Item(file="ignored", seg_hash="f00d", seg_n=1)
    url = server._resolve_media_url(client, item)
    # Central URL pattern matches /media/<key>/seg_<hash>_<n>.mp4
    assert "/media/abc/seg_f00d_1.mp4" in url
    assert "127.0.0.1" not in url


def test_resolve_media_url_central_for_service_worker_mode():
    """Modern devices: server emits central URL; SW intercepts transparently."""
    client = server.Client()
    client.clientKey = "modern"
    client.cacheMode = "service-worker"
    client.cachedSegments = {"f00d_1"}  # doesn't matter for SW mode
    item = _Item(file="ignored", seg_hash="f00d", seg_n=1)
    url = server._resolve_media_url(client, item)
    assert "/media/modern/seg_f00d_1.mp4" in url
    assert "127.0.0.1" not in url


def test_resolve_media_url_passthrough_for_non_segment_items():
    """SCRIPT, IMAGE, etc. items pass their .file through unchanged."""
    client = server.Client()
    client.clientKey = "abc"
    client.cacheMode = "lighttpd-localhost"
    item = _Item(file="bouncingBalls", playmode="SCRIPT")
    assert server._resolve_media_url(client, item) == "bouncingBalls"


def test_resolve_media_url_central_for_cachemode_none():
    """Devices that haven't announced cache support get central URLs."""
    client = server.Client()
    client.clientKey = "abc"
    client.cacheMode = "none"
    item = _Item(file="ignored", seg_hash="f00d", seg_n=1)
    url = server._resolve_media_url(client, item)
    assert "/media/abc/seg_f00d_1.mp4" in url
```

- [ ] **Step 2: Run tests; verify they fail with AttributeError on `_resolve_media_url`**

```bash
python -m pytest tests/unit/test_media_cache.py -c tests/pytest.ini -v
```

Expected: all 5 tests FAIL with `AttributeError: module 'server' has no attribute '_resolve_media_url'`.

- [ ] **Step 3: Find the right spot in server.py for the helper**

```bash
grep -nE "def _build_media_elements|def broadcast_to_display_group" server.py
```

Place the new `_resolve_media_url` ABOVE `_build_media_elements` (or wherever the media URL strings are currently constructed) so it's available to all callers.

- [ ] **Step 4: Add `_resolve_media_url`**

Insert this function at the located position:

```python
# Per-client URL routing for media-cache-aware clients. See spec
# 2026-06-03-media-cache-design.md. For SEGMENT items on an iPad in
# lighttpd-localhost cache mode that has the segment cached, returns
# the localhost URL so Safari fetches from local lighttpd (zero LAN
# bandwidth). For every other case -- non-SEGMENT items, cache miss,
# different cache mode -- returns the central-server URL.
def _resolve_media_url(client, item):
    # Non-SEGMENT items (SCRIPT, IMAGE, etc.) are tiny + uncacheable
    # by this design; pass through their .file as-is.
    if getattr(item, "playmode", None) != "SEGMENT":
        return getattr(item, "file", "")
    seg_hash = getattr(item, "seg_hash", None)
    seg_n = getattr(item, "seg_n", None)
    if seg_hash is None or seg_n is None:
        # Defensive: a SEGMENT item without hash+n metadata can't be
        # cached. Return the original file path (existing behavior).
        return getattr(item, "file", "")
    seg_key = f"{seg_hash}_{seg_n}"
    if (getattr(client, "cacheMode", "none") == "lighttpd-localhost"
            and seg_key in getattr(client, "cachedSegments", set())):
        return f"http://127.0.0.1:8080/seg_{seg_key}.mp4"
    # Central-server URL. clientKey is set on the Client by REGISTER;
    # tests pass it explicitly. Modern (service-worker) clients also
    # get this central URL -- their SW intercepts transparently.
    ckey = getattr(client, "clientKey", None) or "unknown"
    return f"http://192.168.1.60:3000/media/{ckey}/seg_{seg_key}.mp4"
```

- [ ] **Step 5: Run the tests; verify all pass**

```bash
python -m pytest tests/unit/test_media_cache.py -c tests/pytest.ini -v
```

Expected: all 5 PASS.

- [ ] **Step 6: Run the broader unit suite — make sure nothing regressed**

```bash
python pytest_runner.py --unit 2>&1 | tail -5
```

Expected: same pre-existing pass/fail counts plus 5 new passes.

- [ ] **Step 7: Commit**

```bash
git add server.py tests/unit/test_media_cache.py
git commit -m "feat(server): _resolve_media_url per-client URL routing for media cache

Pure function that returns http://127.0.0.1:8080/... for SEGMENT items
on iPads where cacheMode=lighttpd-localhost AND the segment hash is in
cachedSegments. Falls back to central-server URL for all other cases
(uncached, modern devices with SW, non-SEGMENT items, missing metadata).

Five unit tests cover the matrix.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: Wire `_resolve_media_url` into the PRELOAD/PLAY broadcast path

**Files:**
- Modify: `server.py` (broadcast_to_display_group OR wherever the playlist payload is encoded; possibly `_build_media_elements`)

This is a real refactor: today the server builds the mediaElements list once and broadcasts the same encoded JSON to every client in the group. After this task, each client gets a per-client mediaElements list with URLs adjusted by `_resolve_media_url`.

**TDD is hard for this one** (broadcast uses socketmanager, real-time, etc.), so we rely on the post-deploy validation in Task 15. The change itself is mechanical.

- [ ] **Step 1: Find the broadcast site for PRELOAD**

```bash
grep -nE "PRELOAD|broadcast_to_display_group|jsonpickle.encode.*mediaElements|jsonpickle.encode.*items|sync_new_client_to_group" server.py | head -30
```

Identify where the PRELOAD payload's `mediaElements` is constructed, encoded, and sent. Note: there may be multiple sites — the main PLAY trigger, the `sync_new_client_to_group` mid-PLAY-reconnect resend, possibly an admin-initiated re-broadcast.

- [ ] **Step 2: Read the existing logic carefully**

Read 60 lines centered on each `PRELOAD` site identified above:

```bash
# (substitute each line number)
sed -n '<line>,<line+60>p' server.py
```

Note for each site:
- How the message dict is constructed
- Whether it's broadcast (same encoded blob to N clients) or per-client (custom payload per recipient)
- The client iteration pattern (manager.broadcast vs socketmanager.get + session.send)

- [ ] **Step 3: For each PRELOAD construction site, change broadcast → per-client**

Pattern: instead of

```python
payload = {"REQUEST": "PRELOAD", "PAYLOAD": {"items": [...]}}
encoded = jsonpickle.encode(payload)
socketmanager.broadcast(encoded)
```

it becomes

```python
for client_key, client in settings.clients.items():
    if client.displayID != display_id or not client.isOnline:
        continue
    per_client_items = []
    for item in display.mediaElements:
        # Shallow-copy item-as-dict and substitute the URL
        d = item.__dict__.copy() if hasattr(item, "__dict__") else dict(item)
        d["file"] = _resolve_media_url(client, item)
        per_client_items.append(d)
    payload = {"DEST": client_key, "REQUEST": "PRELOAD",
               "PAYLOAD": {"items": per_client_items, ...other-fields...}}
    encoded = jsonpickle.encode(payload)
    # Send to this client only -- use the targeted-send pattern already
    # established by _deliver(); falls back to broadcast on miss
    _deliver(client_key, encoded, client)
```

(Adjust to match the exact existing code style — preserve every other field that's currently in the PRELOAD payload; only `mediaElements[*].file` should differ per-client.)

If `_deliver` doesn't exist or doesn't fit, replicate its targeted-send pattern: `socketmanager.get(client.clientID)` then `session.send(encoded)`, with a fallback to `socketmanager.broadcast(encoded)` so a brand-new client that hasn't been added to the session manager yet still receives the message.

- [ ] **Step 4: Verify module still parses + imports clean**

```bash
python -c "import ast; ast.parse(open('server.py').read()); print('parse OK')"
python -c "import server; print('import OK')"
```

- [ ] **Step 5: Run all unit tests**

```bash
python pytest_runner.py --unit 2>&1 | tail -8
```

Expected: pre-existing pass/fail counts unchanged. The PRELOAD-broadcast change isn't unit-tested directly; we rely on Task 15's empirical validation.

- [ ] **Step 6: Commit**

```bash
git add server.py
git commit -m "refactor(server): PRELOAD broadcast becomes per-client URL routing

Previously the same mediaElements payload was broadcast to every client
in the display group. Now each client receives a payload with its
mediaElements[*].file rewritten by _resolve_media_url -- so iPad-1
devices with lighttpd-localhost cache mode and a cached segment hash
get http://127.0.0.1:8080/seg_X.mp4 instead of the central-server URL.

The legacy broadcast behavior is preserved for uncached devices and
service-worker clients: their per-client _resolve_media_url result is
the same central-server URL it would have been before.

Per-client JSON encode cost is trivial (~5 KB per client x 24 iPads).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: Implement `_push_segment_to_cached_clients` render hook

**Files:**
- Modify: `server.py` (new async helper)
- Modify: `tests/unit/test_media_cache.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_media_cache.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_push_segment_adds_hash_to_cachedSegments_on_success():
    server.settings = server.Settings()
    c = server.Client()
    c.clientKey = "ipad1"
    c.ip = "192.168.1.50"
    c.cacheMode = "lighttpd-localhost"
    server.settings.clients["ipad1"] = c

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
        _run(server._push_segment_to_cached_clients("ipad1", "f00d", 1))

    assert "f00d_1" in server.settings.clients["ipad1"].cachedSegments


def test_push_segment_does_not_update_on_scp_failure():
    server.settings = server.Settings()
    c = server.Client()
    c.clientKey = "ipad1"; c.ip = "192.168.1.50"; c.cacheMode = "lighttpd-localhost"
    server.settings.clients["ipad1"] = c

    fake_proc = MagicMock()
    fake_proc.returncode = 1
    fake_proc.communicate = AsyncMock(return_value=(b"", b"scp: connection refused\n"))

    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
        _run(server._push_segment_to_cached_clients("ipad1", "f00d", 1))

    assert "f00d_1" not in server.settings.clients["ipad1"].cachedSegments


def test_push_segment_skips_clients_not_in_lighttpd_mode():
    """A client whose cacheMode is service-worker or none must NOT
    have an scp attempted (we'd waste bandwidth and time)."""
    server.settings = server.Settings()
    c = server.Client(); c.clientKey="modern"; c.ip="192.168.1.100"; c.cacheMode="service-worker"
    server.settings.clients["modern"] = c

    fake_create = AsyncMock()
    with patch("asyncio.create_subprocess_exec", fake_create):
        _run(server._push_segment_to_cached_clients("modern", "f00d", 1))

    fake_create.assert_not_called()
```

- [ ] **Step 2: Run tests; verify they fail**

```bash
python -m pytest tests/unit/test_media_cache.py -c tests/pytest.ini -v -k push_segment
```

Expected: all 3 push_segment tests FAIL (no `_push_segment_to_cached_clients`).

- [ ] **Step 3: Add the function**

Find `_run_device_script` in `server.py` (a similar async-spawn-ssh pattern we mirror). Insert `_push_segment_to_cached_clients` near it:

```python
async def _push_segment_to_cached_clients(client_key, segment_hash, segment_n):
    """Scp a freshly-rendered per-iPad mp4 to the iPad's lighttpd cache
    directory. Called from the render pipeline's success path for each
    Client with cacheMode == "lighttpd-localhost".

    Best-effort: a failed scp leaves the segment hash absent from
    Client.cachedSegments, which means _resolve_media_url will hand
    out the central-server URL for the next PLAY of this segment on
    this iPad. Operator sees the failure in server.err and can re-run.

    Spec: docs/superpowers/specs/2026-06-03-media-cache-design.md
    section 'Render-complete push hook'."""
    client = settings.clients.get(client_key)
    if not client:
        return
    if getattr(client, "cacheMode", "none") != "lighttpd-localhost":
        return
    if not getattr(client, "ip", ""):
        logging.warning("cache-push %s: no IP, skipping", client_key)
        return
    src = f"media/{client_key}/videos/seg_{segment_hash}_{segment_n}.mp4"
    dst = (f"{SSH_USER}@{client.ip}:"
           f"/var/mobile/Media/MosaicMeshCache/seg_{segment_hash}_{segment_n}.mp4")
    cmd = (["scp", "-i", SSH_KEY_PATH] + SSH_LEGACY_OPTS + [src, dst])
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logging.warning("cache-push %s seg_%s_%d: timeout",
                            client_key, segment_hash, segment_n)
            return
        if proc.returncode == 0:
            client.cachedSegments.add(f"{segment_hash}_{segment_n}")
            logging.info("cache-push: %s seg_%s_%d -> %s",
                         client_key, segment_hash, segment_n, client.ip)
        else:
            tail = (err or b"").decode("utf-8", "replace").strip().splitlines()[-2:]
            logging.warning("cache-push rc=%s for %s seg_%s_%d: %s",
                            proc.returncode, client_key,
                            segment_hash, segment_n, " | ".join(tail))
    except Exception as e:  # noqa: BLE001
        logging.warning("cache-push exception for %s seg_%s_%d: %s",
                        client_key, segment_hash, segment_n, e)
```

- [ ] **Step 4: Run the push_segment tests; verify pass**

```bash
python -m pytest tests/unit/test_media_cache.py -c tests/pytest.ini -v -k push_segment
```

Expected: all 3 PASS.

- [ ] **Step 5: Hook the call into the render-complete path**

Find where the render pipeline marks a segment as done (search for where the rendered `.mp4` file is finalised + the per-client display state is updated). Add a fan-out:

```python
# After the per-iPad render completes:
for ckey in <iterable_of_clients_for_this_render>:
    asyncio.ensure_future(_push_segment_to_cached_clients(ckey, segment_hash, segment_n))
```

Match the existing concurrency-control pattern in the render pipeline. If concurrency limiting is needed (per-client 2-3 in-flight scps max to avoid AP saturation), wrap with a semaphore similar to `_RENDER_CONCURRENCY`.

- [ ] **Step 6: Run full unit suite**

```bash
python pytest_runner.py --unit 2>&1 | tail -5
```

- [ ] **Step 7: Commit**

```bash
git add server.py tests/unit/test_media_cache.py
git commit -m "feat(server): _push_segment_to_cached_clients scp's renders to iPad cache

Async helper called from the render pipeline's success path. Skips
clients not in lighttpd-localhost cacheMode. On scp success, adds
the seg_HASH_N key to client.cachedSegments (which then drives
_resolve_media_url to emit localhost URLs).

Three unit tests cover: success path adds to cachedSegments, failure
path does NOT add, and non-lighttpd-localhost clients are skipped
without an scp attempt.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: Implement `_reconcile_ipad_cache` periodic janitor

**Files:**
- Modify: `server.py` (new helper + `process()` integration)
- Modify: `tests/unit/test_media_cache.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
def test_reconcile_removes_orphan_hashes_from_cachedSegments():
    """A hash in cachedSegments that isn't referenced by any current
    playlist media element on this iPad's display group should be
    swept out (and a delete-ssh fires)."""
    server.settings = server.Settings()
    c = server.Client(); c.clientKey="ipad1"; c.ip="192.168.1.50"
    c.cacheMode = "lighttpd-localhost"; c.displayID="G1"
    c.cachedSegments = {"keep_1", "orphan_3"}
    server.settings.clients["ipad1"] = c
    d = server.Display(); d.displayID="G1"
    # Build a fake media element list referencing only "keep_1"
    class _It:
        def __init__(self, h, n):
            self.playmode="SEGMENT"; self.seg_hash=h; self.seg_n=n
    d.mediaElements = [_It("keep", 1)]
    server.settings.displays["G1"] = d

    fake_proc = MagicMock(); fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))
    async def fake_subproc(*a, **k): return fake_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_subproc):
        _run(server._reconcile_ipad_cache(c))

    assert c.cachedSegments == {"keep_1"}


def test_reconcile_noop_for_non_lighttpd_clients():
    """Service-worker / none clients have no on-device cache to clean;
    janitor should skip them entirely (no ssh attempts)."""
    server.settings = server.Settings()
    c = server.Client(); c.clientKey="m"; c.ip="1.1.1.1"
    c.cacheMode = "service-worker"; c.cachedSegments = {"x_1"}
    server.settings.clients["m"] = c

    fake = AsyncMock()
    with patch("asyncio.create_subprocess_exec", fake):
        _run(server._reconcile_ipad_cache(c))
    fake.assert_not_called()
    assert c.cachedSegments == {"x_1"}  # unchanged
```

- [ ] **Step 2: Run; verify they fail**

```bash
python -m pytest tests/unit/test_media_cache.py -c tests/pytest.ini -v -k reconcile
```

Expected: FAIL on missing `_reconcile_ipad_cache`.

- [ ] **Step 3: Add the function**

Near `_push_segment_to_cached_clients`:

```python
async def _reconcile_ipad_cache(client):
    """Remove cached segment files on this iPad that no longer
    correspond to any current playlist media element on this iPad's
    display group. Best-effort -- ssh failures just leave orphans
    on disk (cosmetic concern, recovered next reconciliation).

    Skips non-lighttpd-localhost clients (they have no on-device
    cache for us to clean)."""
    if getattr(client, "cacheMode", "none") != "lighttpd-localhost":
        return
    if not getattr(client, "ip", ""):
        return
    # Build set of seg_HASH_N keys currently referenced by this
    # client's display group's playlist.
    in_use = set()
    did = getattr(client, "displayID", None)
    display = settings.displays.get(did) if did else None
    if display:
        for item in (getattr(display, "mediaElements", []) or []):
            if getattr(item, "playmode", None) != "SEGMENT":
                continue
            h = getattr(item, "seg_hash", None)
            n = getattr(item, "seg_n", None)
            if h is not None and n is not None:
                in_use.add(f"{h}_{n}")
    stale = set(client.cachedSegments) - in_use
    if not stale:
        return
    # Remove from server-side state immediately (the file deletes happen
    # async). Worst case a stale file lingers on disk after we forget
    # about it -- next reconciliation will retry the delete.
    for s in stale:
        client.cachedSegments.discard(s)
        cmd = (["ssh", "-i", SSH_KEY_PATH] + SSH_LEGACY_OPTS +
               [f"{SSH_USER}@{client.ip}",
                f"rm -f /var/mobile/Media/MosaicMeshCache/seg_{s}.mp4"])
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            await asyncio.wait_for(proc.communicate(), timeout=15)
        except Exception as e:  # noqa: BLE001
            logging.debug("cache-reconcile rm failed for %s seg_%s: %s",
                          client.clientKey, s, e)
```

- [ ] **Step 4: Hook into `process()` loop**

Find the `process()` function (the every-5-seconds background loop). Add the reconciliation:

```python
    # Cache reconciliation: sweep orphans from each cached iPad.
    # Done every process() tick (every ~5s) but it's a no-op for
    # iPads where cachedSegments matches the current playlist.
    for c in list(settings.clients.values()):
        if c.cacheMode == "lighttpd-localhost" and c.isOnline:
            asyncio.ensure_future(_reconcile_ipad_cache(c))
```

- [ ] **Step 5: Run tests, pass**

```bash
python -m pytest tests/unit/test_media_cache.py -c tests/pytest.ini -v -k reconcile
python pytest_runner.py --unit 2>&1 | tail -5
```

- [ ] **Step 6: Commit**

```bash
git add server.py tests/unit/test_media_cache.py
git commit -m "feat(server): _reconcile_ipad_cache periodic janitor sweeps orphan segment files

Called from process() every ~5s for each lighttpd-localhost client.
Identifies seg_HASH_N entries in cachedSegments that no longer match
any current playlist media element on this iPad's display group, and
fires an rm -f via SSH while removing the entry server-side. Two unit
tests cover: orphan removal + skip non-lighttpd clients.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 8: Add `lighttpd` to `$DEFAULT_TWEAKS` in onboarding

**Files:**
- Modify: `tools/onboard_devices.ps1`

- [ ] **Step 1: Locate the `$DEFAULT_TWEAKS` array**

```bash
grep -nE "\$DEFAULT_TWEAKS" tools/onboard_devices.ps1
```

- [ ] **Step 2: Add `lighttpd` to the array with an explanatory comment**

After the existing entries (the Insomnia line is a good neighbour), append:

```powershell
    # --- per-device media cache (2026-06-03) ---
    # lighttpd serves /var/mobile/Media/MosaicMeshCache/ at
    # http://127.0.0.1:8080/ -- per-iPad pre-rendered video segments
    # play from local disk instead of competing for shared WiFi
    # bandwidth. Deps (pcre, libxml2, sqlite3, bzip2) all resolve
    # from Saurik's repo which is already configured on these iPads.
    # Onboarding steps 5.4d/5.4e/5.4f below write the config plist,
    # the LaunchDaemon plist, and the server-side cacheMode flag.
    # See docs/superpowers/specs/2026-06-03-media-cache-design.md.
    'lighttpd',
```

- [ ] **Step 3: Verify the script still parses**

```bash
powershell.exe -NoProfile -Command "try { \$null = [System.Management.Automation.Language.Parser]::ParseFile('C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh\tools\onboard_devices.ps1', [ref]\$null, [ref]\$null); 'parse OK' } catch { 'PARSE ERROR: ' + \$_.Exception.Message }"
```

Expected: `parse OK`.

- [ ] **Step 4: Commit**

```bash
git add tools/onboard_devices.ps1
git commit -m "feat(onboard): add lighttpd to DEFAULT_TWEAKS for per-iPad media cache

Standard apt-get install from Saurik's already-configured repo. Deps
(pcre, libxml2, sqlite3, bzip2) auto-resolve. Subsequent steps 5.4d,
5.4e, 5.4f write config + LaunchDaemon plist + server-side cacheMode
flag. See docs/superpowers/specs/2026-06-03-media-cache-design.md.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 9: Onboarding step 5.4d — write `lighttpd.conf` via SSH

**Files:**
- Modify: `tools/onboard_devices.ps1`

- [ ] **Step 1: Find the insertion point**

Step 5.4c (Insomnia plist) ends; step 5.5 (respring) begins. The new 5.4d goes between them.

```bash
grep -nE "5\.4c|5\.5\) respring" tools/onboard_devices.ps1
```

- [ ] **Step 2: Insert step 5.4d**

After the closing `}` of the 5.4c Insomnia block, before the `# 5.5) respring` comment, paste:

```powershell
    # 5.4d) write /etc/lighttpd/lighttpd.conf. Binds to 127.0.0.1 only
    #       (never LAN-accessible), document-root = the cache dir,
    #       correct MIME types (video/mp4 for .mp4 etc.). Same heredoc
    #       pattern as 5.4b (Veency) / 5.4c (Insomnia).
    if ($status -eq "OK" -and $pkgsToInstall) {
        $lighttpdConfig = (
            "mkdir -p /etc/lighttpd /var/log /var/run /var/mobile/Media/MosaicMeshCache;`n" +
            "chown mobile:mobile /var/mobile/Media/MosaicMeshCache;`n" +
            "cat > /etc/lighttpd/lighttpd.conf << 'CONF'`n" +
            "server.modules = ( `"mod_indexfile`", `"mod_dirlisting`", `"mod_staticfile`" )`n" +
            "server.document-root = `"/var/mobile/Media/MosaicMeshCache/`"`n" +
            "server.bind = `"127.0.0.1`"`n" +
            "server.port = 8080`n" +
            "server.errorlog = `"/var/log/lighttpd-error.log`"`n" +
            "server.pid-file = `"/var/run/lighttpd.pid`"`n" +
            "dir-listing.activate = `"disable`"`n" +
            "mimetype.assign = (`n" +
            "    `".mp4`"  => `"video/mp4`",`n" +
            "    `".m4v`"  => `"video/x-m4v`",`n" +
            "    `".mov`"  => `"video/quicktime`",`n" +
            "    `".jpg`"  => `"image/jpeg`",`n" +
            "    `".png`"  => `"image/png`",`n" +
            "    `".html`" => `"text/html`",`n" +
            "    `".js`"   => `"application/javascript`",`n" +
            "    `".css`"  => `"text/css`",`n" +
            "    `"`"      => `"application/octet-stream`"`n" +
            ")`n" +
            "index-file.names = ( `"index.html`" )`n" +
            "CONF`n" +
            "echo LIGHTTPD_CONF_OK"
        )
        try {
            $lOut = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" $lighttpdConfig 2>&1) | Out-String
            if ($lOut -match 'LIGHTTPD_CONF_OK') {
                Write-Host "  lighttpd config: written" -ForegroundColor Green
            } else {
                Write-Host "  lighttpd config unexpected: $($lOut.Trim() -replace '\s+',' ')" -ForegroundColor Yellow
            }
        } catch {
            Write-Host "  lighttpd config failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

```

- [ ] **Step 3: Parse-check + commit**

```bash
powershell.exe -NoProfile -Command "try { \$null = [System.Management.Automation.Language.Parser]::ParseFile('C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh\tools\onboard_devices.ps1', [ref]\$null, [ref]\$null); 'parse OK' } catch { 'PARSE ERROR: ' + \$_.Exception.Message }"
git add tools/onboard_devices.ps1
git commit -m "feat(onboard): step 5.4d writes /etc/lighttpd/lighttpd.conf

Binds 127.0.0.1:8080 only (never LAN-accessible), serves the cache
dir, correct MIME types. Heredoc pattern matches existing 5.4b/c
plist writes.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 10: Onboarding step 5.4e — write LaunchDaemon + load it

**Files:**
- Modify: `tools/onboard_devices.ps1`

- [ ] **Step 1: Insert step 5.4e after 5.4d**

```powershell
    # 5.4e) write the LaunchDaemon plist so lighttpd starts at every
    #       boot AND auto-respawns if killed. -D flag keeps lighttpd
    #       in foreground so launchd's KeepAlive can track it. launchctl
    #       load fires it immediately (in addition to the boot start).
    if ($status -eq "OK" -and $pkgsToInstall) {
        $lighttpdLaunchd = (
            "mkdir -p /Library/LaunchDaemons;`n" +
            "cat > /Library/LaunchDaemons/com.mosaicmesh.lighttpd.plist << 'PLIST'`n" +
            "<?xml version=`"1.0`" encoding=`"UTF-8`"?>`n" +
            "<!DOCTYPE plist PUBLIC `"-//Apple//DTD PLIST 1.0//EN`" `"http://www.apple.com/DTDs/PropertyList-1.0.dtd`">`n" +
            "<plist version=`"1.0`">`n" +
            "<dict>`n" +
            "    <key>Label</key><string>com.mosaicmesh.lighttpd</string>`n" +
            "    <key>ProgramArguments</key>`n" +
            "    <array>`n" +
            "        <string>/usr/sbin/lighttpd</string>`n" +
            "        <string>-D</string>`n" +
            "        <string>-f</string>`n" +
            "        <string>/etc/lighttpd/lighttpd.conf</string>`n" +
            "    </array>`n" +
            "    <key>RunAtLoad</key><true/>`n" +
            "    <key>KeepAlive</key><true/>`n" +
            "    <key>StandardErrorPath</key><string>/var/log/lighttpd-launchd.log</string>`n" +
            "</dict>`n" +
            "</plist>`n" +
            "PLIST`n" +
            "chmod 644 /Library/LaunchDaemons/com.mosaicmesh.lighttpd.plist;`n" +
            "launchctl unload /Library/LaunchDaemons/com.mosaicmesh.lighttpd.plist 2>/dev/null;`n" +
            "launchctl load /Library/LaunchDaemons/com.mosaicmesh.lighttpd.plist;`n" +
            "sleep 2;`n" +
            "if [ -f /var/run/lighttpd.pid ]; then`n" +
            "  echo LIGHTTPD_LAUNCHD_OK pid=`$(cat /var/run/lighttpd.pid);`n" +
            "else`n" +
            "  echo LIGHTTPD_LAUNCHD_NO_PID;`n" +
            "fi"
        )
        try {
            $lOut = (& $ssh -i $KeyPath -p $p @sshLegacy "$User@$hostName" $lighttpdLaunchd 2>&1) | Out-String
            if ($lOut -match 'LIGHTTPD_LAUNCHD_OK pid=(\d+)') {
                Write-Host "  lighttpd LaunchDaemon: running pid=$($Matches[1])" -ForegroundColor Green
            } else {
                Write-Host "  lighttpd LaunchDaemon: $($lOut.Trim() -replace '\s+',' ')" -ForegroundColor Yellow
            }
        } catch {
            Write-Host "  lighttpd LaunchDaemon failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

```

- [ ] **Step 2: Parse-check + commit**

```bash
powershell.exe -NoProfile -Command "try { \$null = [System.Management.Automation.Language.Parser]::ParseFile('C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh\tools\onboard_devices.ps1', [ref]\$null, [ref]\$null); 'parse OK' } catch { 'PARSE ERROR: ' + \$_.Exception.Message }"
git add tools/onboard_devices.ps1
git commit -m "feat(onboard): step 5.4e LaunchDaemon plist auto-starts lighttpd at boot

Same LaunchDaemon pattern as the existing autolock-off daemon. -D flag
keeps lighttpd in foreground so launchd's KeepAlive=true can track + 
respawn it. Verifies success by reading the pid file after launchctl
load.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 11: Onboarding step 5.4f — POST cacheMode to server

**Files:**
- Modify: `tools/onboard_devices.ps1`

- [ ] **Step 1: Insert step 5.4f**

After 5.4e, before 5.5 respring. This uses PowerShell's `Invoke-RestMethod` to call the server's `api_discovery_configure` set_cache_mode action we added in Task 3:

```powershell
    # 5.4f) mark this client as lighttpd-localhost cacheMode on the
    #       server side so future PLAY payloads route to the iPad's
    #       localhost lighttpd. Requires that the iPad has REGISTERed
    #       with the server at least once (so settings.clients has
    #       an entry for it). Onboarding usually triggers a REGISTER
    #       via step 7 (open MosaicMesh page); for fresh-imaged iPads
    #       you may need a second onboarding pass.
    if ($status -eq "OK" -and $pkgsToInstall) {
        try {
            # Look up the iPad's clientKey via /api/discovery/devices.
            $devs = Invoke-RestMethod -Uri "http://192.168.1.60:3000/api/discovery/devices" -TimeoutSec 5
            $devList = if ($devs.devices) { $devs.devices } else { $devs }
            $thisDev = $devList | Where-Object { $_.ip -eq $hostName } | Select-Object -First 1
            if ($thisDev -and $thisDev.clientKey) {
                $body = @{
                    action = "set_cache_mode"
                    clientKey = $thisDev.clientKey
                    mode = "lighttpd-localhost"
                } | ConvertTo-Json -Compress
                $resp = Invoke-RestMethod -Uri "http://192.168.1.60:3000/api/discovery/configure" `
                    -Method POST -ContentType "application/json" -Body $body -TimeoutSec 5
                if ($resp.status -eq "SUCCESS") {
                    Write-Host "  cacheMode: server marked $($thisDev.clientKey) as lighttpd-localhost" -ForegroundColor Green
                } else {
                    Write-Host "  cacheMode response unexpected: $($resp | ConvertTo-Json -Compress)" -ForegroundColor Yellow
                }
            } else {
                Write-Host "  cacheMode: no clientKey for ip=$hostName in discovery API yet" -ForegroundColor DarkYellow
                Write-Host "                (run onboarding again after iPad first REGISTERs)" -ForegroundColor DarkYellow
            }
        } catch {
            Write-Host "  cacheMode set failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

```

- [ ] **Step 2: Parse-check + commit**

```bash
powershell.exe -NoProfile -Command "try { \$null = [System.Management.Automation.Language.Parser]::ParseFile('C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh\tools\onboard_devices.ps1', [ref]\$null, [ref]\$null); 'parse OK' } catch { 'PARSE ERROR: ' + \$_.Exception.Message }"
git add tools/onboard_devices.ps1
git commit -m "feat(onboard): step 5.4f marks cacheMode=lighttpd-localhost via /api/discovery/configure

POSTs to the set_cache_mode action added in Task 3. Looks up the
iPad's clientKey by IP. If the iPad hasn't REGISTERed yet (so no
clientKey exists in the discovery API), prints a warning and tells
the operator to re-run onboarding after the first REGISTER.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 12: Create `sw.js` Service Worker (modern devices)

**Files:**
- Create: `sw.js`

Tasks 12–14 add the modern-device path. Skippable if you only need iPad-1 today.

- [ ] **Step 1: Write `sw.js`**

Create `sw.js` at the project root with this content (copy from spec Component 5, then append the helpers):

```javascript
// MosaicMesh Service Worker. Caches per-iPad MP4 segments on first
// fetch (population is driven by a non-Range fetch() the page issues
// at PRELOAD time -- AppleCoreMedia issues only Range requests for
// <video>, so the SW would never see a full-file 200 OK to cache
// without the page's help). Range requests on subsequent plays hit
// the cache and get sliced from the cached ArrayBuffer.
//
// Spec: docs/superpowers/specs/2026-06-03-media-cache-design.md

const CACHE_NAME = "mosaicmesh-media-v1";
const MEDIA_PATH_RE = /\/media\/[^/]+\/seg_[a-f0-9]+_\d+\.mp4$/;

self.addEventListener("install", e => {
    self.skipWaiting();
});

self.addEventListener("activate", e => {
    e.waitUntil(caches.keys().then(keys =>
        Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ));
    return self.clients.claim();
});

async function fetchAndCache(cache, cacheKey){
    try {
        const full = await fetch(cacheKey);  // no Range header on this request
        if (full.ok) await cache.put(cacheKey, full);
    } catch (_) { /* best-effort */ }
}

async function serveRangeFromCached(cachedResp, req){
    const range = req.headers.get("Range");
    if (!range) return cachedResp;
    const m = /bytes=(\d+)-(\d*)/.exec(range);
    if (!m) return cachedResp;
    const buf = await cachedResp.arrayBuffer();
    const start = parseInt(m[1], 10);
    const end = m[2] ? Math.min(parseInt(m[2], 10), buf.byteLength - 1) : buf.byteLength - 1;
    return new Response(buf.slice(start, end + 1), {
        status: 206,
        headers: {
            "Content-Type": cachedResp.headers.get("Content-Type") || "video/mp4",
            "Content-Range": `bytes ${start}-${end}/${buf.byteLength}`,
            "Content-Length": String(end - start + 1)
        }
    });
}

self.addEventListener("fetch", e => {
    const url = new URL(e.request.url);
    if (!MEDIA_PATH_RE.test(url.pathname)) return;
    const cacheKey = new Request(url.toString());
    e.respondWith(
        caches.open(CACHE_NAME).then(cache =>
            cache.match(cacheKey).then(hit => {
                if (hit) return serveRangeFromCached(hit, e.request);
                fetchAndCache(cache, cacheKey);  // fire-and-forget
                return fetch(e.request);
            })
        )
    );
});
```

- [ ] **Step 2: Verify the server serves it correctly**

`index_handler` in server.py already routes `/sw.js` because it matches the catch-all root pattern. Test:

```bash
# (server must be running)
curl -sI http://localhost:3000/sw.js | head -5
```

Expected: `HTTP/1.1 200 OK` and `Content-Type: application/javascript`.

If the Content-Type comes back as something else, `index_handler`'s extension switch needs the .js branch verified — but per memory it's already there.

- [ ] **Step 3: Commit**

```bash
git add sw.js
git commit -m "feat(client): sw.js Service Worker caches /media/seg_*.mp4

Caches full-file responses (populated by the page's non-Range fetch()
at PRELOAD time -- see Task 13). Serves Range slices from the cached
ArrayBuffer on subsequent <video> Range requests. Eviction by
CACHE_NAME version on activate.

iOS 5 Safari doesn't have Service Worker support, so this file is
loaded but never registered on those devices (see Task 13's
try/catch guard in index.html).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 13: SW registration + ANNOUNCE_CACHE_MODE + prefetch in index.html

**Files:**
- Modify: `index.html`

- [ ] **Step 1: Find a good insertion point in `index.html`**

```bash
grep -nE "navigator|generateMessage|sock\.send|recv-PRELOAD" index.html | head
```

The Service Worker registration goes near the top of the page-level JS, AFTER `generateMessage` and `sock` are defined. The prefetch call goes inside the PRELOAD message handler.

- [ ] **Step 2: Add the SW registration block**

In the `<script>` tag where the SockJS connection is set up, after the connection's onopen handler defines `sock`, add this block (the try/catch + guards prevent iOS 5 Safari from blowing up on the modern syntax):

```html
<script>
// Service Worker registration -- modern devices only. iOS 5 Safari
// has navigator.serviceWorker == undefined and any modern syntax
// inside the try block throws SyntaxError. The try/catch swallows
// that so iOS 5 sees this script as a no-op. See spec
// docs/superpowers/specs/2026-06-03-media-cache-design.md Task 13.
(function(){
    try {
        if (navigator && navigator.serviceWorker) {
            navigator.serviceWorker.register('/sw.js').then(function(reg){
                // Wait for the worker to actually take control before
                // announcing -- otherwise the next PRELOAD's prefetch
                // would bypass an inactive SW.
                if (navigator.serviceWorker.controller) {
                    announceCacheMode();
                } else {
                    navigator.serviceWorker.addEventListener('controllerchange',
                        announceCacheMode);
                }
            }).catch(function(){
                // SW registration failed (e.g., file 404). Don't announce
                // -- server keeps this client at cacheMode="none".
            });
        }
    } catch (e) { /* iOS 5 with modern syntax errors -- ignore */ }
})();

function announceCacheMode(){
    try {
        if (typeof sock === "undefined" || sock === null) return;
        sock.send(generateMessage("SRV", "ANNOUNCE_CACHE_MODE",
                                   {"mode": "service-worker"}));
    } catch (e) { /* best-effort */ }
}

// Called by the existing PRELOAD message handler when running on a
// modern device. Issues a non-Range fetch() for each new SEGMENT URL,
// which populates the Service Worker's Cache API. Subsequent <video>
// Range requests get sliced from the cached body.
function prefetchPlaylistMedia(items){
    if (!('caches' in window)) return;  // SW not supported -> no-op
    if (!items || !items.length) return;
    for (var i = 0; i < items.length; i++){
        var it = items[i];
        if (it.playmode !== "SEGMENT") continue;
        if (!it.file) continue;
        try {
            // fire-and-forget; SW intercepts and populates cache.
            fetch(it.file, { mode: 'cors', cache: 'no-store' }).catch(function(){});
        } catch (e) { /* ES5-incompatible browser -- ignore */ }
    }
}
</script>
```

- [ ] **Step 3: Wire `prefetchPlaylistMedia` into the PRELOAD handler**

Find the PRELOAD message handler in `index.html` (search for `"recv-PRELOAD"` log tag or `PAYLOAD.items`). Inside the handler, after the existing logic that processes the items, add:

```html
<script>
// (inside the existing PRELOAD branch, after the existing item processing)
prefetchPlaylistMedia(data_obj.PAYLOAD.items);
</script>
```

The exact spot depends on the existing PRELOAD logic's structure — match its style.

- [ ] **Step 4: Verify the page still parses on iOS 5**

Push the modified `index.html` to one iPad (any responsive one) and load it; check the page didn't error out (look for the existing dbg tags in CLIENTLOG):

```bash
# Quick reload on .69 (or whichever is responsive)
ssh -i ~/.ssh/mosaic_ipad -o HostKeyAlgorithms=+ssh-rsa -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=accept-new -o BatchMode=yes root@192.168.1.69 \
    "killall MobileSafari 2>/dev/null; sleep 1; uiopen 'http://192.168.1.60:3000/?tdbg'"
# Wait ~10s then check server.err for CLIENTLOG entries with errors
grep -E "recv-PRELOAD|js-error|exception" server.err | tail -10
```

Expected: PRELOAD and play-cycle logs continue to appear normally for the iPad; no `js-error` events. If iOS 5 Safari errored on the modern syntax, it'd show in the page's behavior (page doesn't load, no register, no SYNACK).

- [ ] **Step 5: Run unit tests (touched only client code, no Python tests affected)**

```bash
python pytest_runner.py --unit 2>&1 | tail -5
```

- [ ] **Step 6: Commit**

```bash
git add index.html
git commit -m "feat(client): Service Worker registration + PRELOAD-time prefetch

Modern devices register /sw.js, announce ANNOUNCE_CACHE_MODE so server
knows to keep emitting central URLs (SW intercepts transparently), and
fire a non-Range fetch() for each PRELOADed SEGMENT to populate the
Cache API before <video> issues its Range requests.

Whole block guarded by try/catch + navigator.serviceWorker check so
iOS 5 Safari (no SW support, no modern syntax) treats the additions
as no-ops.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 14: Pilot deploy on one iPad

**Files:** none modified.

Full end-to-end validation before the fleet-wide rollout. Use iPad .69 (sign1screen12) — confirmed reachable from this session's work.

- [ ] **Step 1: Run onboarding against just .69 with the new steps**

```bash
cd "C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh"
# Adjust Hosts arg to whatever your script's CLI takes for a single iPad
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/onboard_devices.ps1 -Hosts 192.168.1.69 -InstallTweaks
```

Watch for green lines: `lighttpd config: written`, `lighttpd LaunchDaemon: running pid=N`, `cacheMode: server marked ... as lighttpd-localhost`. Any yellow/red lines indicate an issue worth investigating before proceeding.

- [ ] **Step 2: Verify on the iPad**

```bash
ssh -i ~/.ssh/mosaic_ipad -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa \
    -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o BatchMode=yes \
    root@192.168.1.69 '
echo "--- lighttpd installed? ---"
dpkg -l lighttpd
echo "--- launchd loaded + running? ---"
launchctl list | grep mosaicmesh.lighttpd
echo "--- pid file + port bound? ---"
cat /var/run/lighttpd.pid
echo "--- can we talk to it locally? ---"
echo -e "GET / HTTP/1.0\r\n\r\n" | /usr/bin/nc 127.0.0.1 8080 2>/dev/null || echo "(no nc -- try later in tdbg page)"
'
```

Expected: `ii lighttpd 1.4.18-7`, a launchctl entry, a numeric pid, and (if nc exists) a `HTTP/1.0 4xx` response (404 because no index file — that's still proof of listening).

- [ ] **Step 3: Verify server.cacheMode flag was set**

```bash
curl -s http://localhost:3000/api/discovery/devices \
    | python -c "
import json, sys
d = json.load(sys.stdin)
for dev in (d.get('devices', d) if isinstance(d, dict) else d):
    if dev.get('ip') == '192.168.1.69':
        print('cacheMode:', dev.get('cacheMode'))
        print('cachedSegments:', dev.get('cachedSegments'))
"
```

Expected: `cacheMode: lighttpd-localhost`, `cachedSegments: []` (still empty — render hasn't pushed yet).

- [ ] **Step 4: Trigger a fresh render of a SEGMENT-containing playlist**

(Mechanism for triggering a render varies — admin UI button, API call, or just re-saving the playlist.) After the render completes, verify the push happened:

```bash
grep "cache-push:" server.err | tail -20
```

Expected: at least one `cache-push: jecpgri3ygzgds4i seg_X_N -> 192.168.1.69` log line.

Then re-check the iPad's cache dir:

```bash
ssh -i ~/.ssh/mosaic_ipad ... root@192.168.1.69 "ls -la /var/mobile/Media/MosaicMeshCache/"
```

Expected: at least one `seg_<HASH>_<N>.mp4` file present, owned by `mobile`.

- [ ] **Step 5: Trigger a PLAY; verify the iPad uses the localhost URL**

```bash
# Mark log offset
LOG_MARK=$(stat -c '%s' server.err)
# Trigger PLAY via existing tools (or admin UI)
python tools/run_and_collect.py "Test Group" "Test" 60 2>&1 | tail -10
# Inspect server.err for /media/ GETs from .69 since the mark
python -c "
import re
PATTERN = re.compile(r'192\.168\.1\.69.*GET /media/')
with open('server.err','rb') as f:
    f.seek($LOG_MARK)
    text = f.read().decode('utf-8','replace')
hits = [ln for ln in text.splitlines() if PATTERN.search(ln)]
print(f'central-server /media/ GETs from .69: {len(hits)}')
for h in hits[:5]: print(' ', h)
"
```

Expected: **zero or near-zero** central-server `/media/` GETs from .69. If lighttpd-served localhost URLs were emitted to .69, AppleCoreMedia fetched from `127.0.0.1:8080` instead — no requests reach the central server.

(Other iPads in the group, still in `cacheMode=none`, will continue to hit `/media/` — that's expected until the fleet-wide rollout in Task 15.)

- [ ] **Step 6: Watch the video play visually on .69**

Walk to sign1screen12 if accessible. The video should play. If it doesn't (black box, abort, etc.) — capture the page state via the existing `?tdbg` CLIENTLOG mechanism and investigate before the fleet rollout.

- [ ] **Step 7: Commit a verification record**

```bash
git commit --allow-empty -m "verify(cache): pilot on .69 — lighttpd installed + cacheMode set + PLAY routes to localhost"
```

(Or skip the empty commit.)

---

## Task 15: Fleet-wide rollout

**Files:** none modified.

- [ ] **Step 1: Snapshot pre-rollout state**

```bash
cd "C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh"
curl -s http://localhost:3000/api/discovery/devices | python -c "
import json, sys
d = json.load(sys.stdin)
devs = d.get('devices', d) if isinstance(d, dict) else d
tg = [x for x in devs if x.get('displayID') == 'Test Group']
print(f'Test Group: {len(tg)} iPads')
print(f'  isOnline: {sum(1 for x in tg if x.get(\"isOnline\"))}')
print(f'  cacheMode breakdown: {dict((k, sum(1 for x in tg if x.get(\"cacheMode\") == k)) for k in (\"none\",\"lighttpd-localhost\",\"service-worker\"))}'
)
"
```

Note the counts so we can compare post-rollout.

- [ ] **Step 2: Re-onboard the full fleet with the new steps**

```powershell
.\tools\onboard_devices.ps1 -InstallTweaks
```

Expected per iPad:
- `insomnia: enabled` (existing — should still be green from prior onboarding state)
- `lighttpd config: written` (new — Task 9)
- `lighttpd LaunchDaemon: running pid=...` (new — Task 10)
- `cacheMode: server marked ... as lighttpd-localhost` (new — Task 11)

Yellow lines for un-reachable iPads: handle them after the bulk pass (per the existing onboarding's failure-tolerant behavior).

- [ ] **Step 3: Re-check cacheMode distribution**

```bash
curl -s http://localhost:3000/api/discovery/devices | python -c "
import json, sys
d = json.load(sys.stdin)
devs = d.get('devices', d) if isinstance(d, dict) else d
tg = [x for x in devs if x.get('displayID') == 'Test Group']
print(f'cacheMode breakdown: {dict((k, sum(1 for x in tg if x.get(\"cacheMode\") == k)) for k in (\"none\",\"lighttpd-localhost\",\"service-worker\"))}'
)
"
```

Expected: as many iPads as reached cleanly should now be `lighttpd-localhost`. Any stragglers in `none` are likely the same SSH-degraded iPads we saw earlier this session.

- [ ] **Step 4: Trigger a render for the Test playlist**

(Same render trigger as Task 14 Step 4.) Wait for completion — push fan-out happens in the background.

```bash
# Watch the cache-push log lines flow
tail -F server.err | grep cache-push
```

Wait until you see a `cache-push: ...` for each `lighttpd-localhost` client. Then Ctrl-C the tail.

- [ ] **Step 5: Trigger a fleet PLAY + measure**

```bash
LOG_MARK=$(stat -c '%s' server.err)
python tools/run_and_collect.py "Test Group" "Test" 120 2>&1 | tail -40
```

Watch the drift summary at the bottom — this is the success metric.

- [ ] **Step 6: Verify central-server `/media/` request volume dropped**

```bash
python -c "
import re
PATTERN = re.compile(r'GET /media/.*\.mp4')
n = 0; bytes_ = 0
with open('server.err','rb') as f:
    f.seek($LOG_MARK)
    text = f.read().decode('utf-8','replace')
for ln in text.splitlines():
    m = PATTERN.search(ln)
    if m:
        n += 1
        bm = re.search(r' \d+ (\d+) ', ln)
        if bm: bytes_ += int(bm.group(1))
print(f'central-server /media/ GETs during PLAY: {n}')
print(f'central-server /media/ bytes served: {bytes_/1e9:.2f} GB')
"
```

Expected: GB count drops from the previous ~27 GB / 93 s baseline to ≪ 1 GB (only the few iPads still in `cacheMode=none` plus any first-PLAY-after-render fallback hits).

- [ ] **Step 7: Verify drift across the fleet**

The same `tools/run_and_collect.py` output from Step 5 should show drift samples from N iPads with N close to the cached-iPads count. Median drift should be sub-100ms once playback is steady (compare to the pre-cache reference of 0–2 iPads producing data).

- [ ] **Step 8: Commit a verification record**

```bash
git commit --allow-empty -m "verify(cache): fleet-wide rollout — N/24 iPads on lighttpd-localhost; drift measurable across the fleet"
```

(Replace N with actual.)

---

## Task 16: Acceptance criteria + final cleanup

**Files:** none modified.

- [ ] **Step 1: Run through each of the spec's 8 acceptance criteria**

For each criterion in `docs/superpowers/specs/2026-06-03-media-cache-design.md` section "Acceptance criteria" (1 through 8), produce a one-line note on whether it passes or fails on the current fleet. Capture the output of the relevant verification command alongside.

- [ ] **Step 2: If anything failed, file follow-up issues**

For any criterion not met (e.g., a specific iPad couldn't be onboarded, the SW path needs a future modern device to validate), open a short note in the repo at `docs/superpowers/specs/` or in an issue tracker so future sessions know where the gap is.

- [ ] **Step 3: Done**

The cache subsystem is live. The drift measurement that was the original motivating goal becomes available at full fleet scale. Pick a next priority from the operator's backlog.
