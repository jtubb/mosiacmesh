# Retire lighttpd from the mmvideo cache path — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the misleading `cacheMode` `"lighttpd-localhost"` → `"mmvideo"` and confirm lighttpd is fully vestigial server-side, so the lighttpd daemon can be removed from the fleet with no behavior change.

**Architecture:** Server-side Python only. lighttpd already carries no traffic (download uses `:3000`; playback is intercepted natively by the mmvideo tweak; the old SSH probe was deleted and capability is client-reported via `apply_cache_capability`). So this is a string rename at ~11 functional sites + a boot migration + input-normalization in the two mode validators + comment rewording + a vestigial grep-sweep. The physical daemon removal is a sequenced operator rollout, done last.

**Tech Stack:** Python 3, aiohttp; tests via `python pytest_runner.py --unit` (pytest with `-c tests/pytest.ini`), `server.settings = mock_settings` pattern.

## Global Constraints

- Server-side Python only: `server.py`, `mosaicmesh/`. NO iPad-1 client change (`index.html`, `js/`, mmvideo/mmcache tweaks untouched), NO tweak rebuild, NO client redeploy.
- New code only ever WRITES `cacheMode = "mmvideo"`. The deprecated `"lighttpd-localhost"` is accepted only as INPUT (client/admin messages, old `settings.dat`) and is NORMALIZED to `"mmvideo"` on entry — so all guards compare against `"mmvideo"` alone.
- The three valid modes after this change are `"none"`, `"mmvideo"`, `"service-worker"`. `"lighttpd-localhost"` is transitional-input-only.
- Preserve the capability invariant: client-reported upgrade from `"none"`; client `ANNOUNCE_CACHE_MODE "none"` (local-play failure) is the authoritative downgrade. This logic is unchanged — only the string differs.
- Run the unit suite with `python pytest_runner.py --unit` (never bare `pytest`). Single file: `python -m pytest tests/unit/<file> -c tests/pytest.ini -v`.

---

### Task 1: Rename `cacheMode` `"lighttpd-localhost"` → `"mmvideo"` (writes + guards) and normalize it on validator input

**Files:**
- Modify: `mosaicmesh/websocket/legacy.py:162` (write), `:254` (ANNOUNCE_CACHE_MODE validator)
- Modify: `mosaicmesh/api/discovery.py:155`, `:287`, `:410` (guards), `:454` (set_cache_mode validator)
- Modify: `server.py:423`, `:2166` (guards)
- Modify: `mosaicmesh/render.py:635`, `:1334`, `:1399` (guards)
- Test: `tests/unit/test_media_cache.py`

**Interfaces:**
- Consumes: `apply_cache_capability(client, payload)` (legacy.py) and the `Client.cacheMode` attribute (default `"none"`).
- Produces: after this task, every server WRITE of the mmvideo mode is `"mmvideo"`; every guard compares `== "mmvideo"` / `in (..., "mmvideo", ...)`; both validators map an incoming `"lighttpd-localhost"` to `"mmvideo"` before accepting.

- [ ] **Step 1: Write the failing tests** — add to `tests/unit/test_media_cache.py` (uses the `mock_settings` fixture that sets `server.settings = Settings()`; `from mosaicmesh.websocket import legacy`):

```python
def test_apply_cache_capability_writes_mmvideo(mock_settings):
    from mosaicmesh.websocket import legacy
    from mosaicmesh.state import Client
    c = Client(); c.cacheMode = "none"
    legacy.apply_cache_capability(c, {"cacheCapable": True})
    assert c.cacheMode == "mmvideo"                      # was "lighttpd-localhost"

def test_apply_cache_capability_modern_unchanged(mock_settings):
    from mosaicmesh.websocket import legacy
    from mosaicmesh.state import Client
    c = Client(); c.cacheMode = "none"
    legacy.apply_cache_capability(c, {"cacheBackend": "modern"})
    assert c.cacheMode == "service-worker"

def test_apply_cache_capability_upgrade_only(mock_settings):
    from mosaicmesh.websocket import legacy
    from mosaicmesh.state import Client
    c = Client(); c.cacheMode = "service-worker"
    legacy.apply_cache_capability(c, {"cacheCapable": True})
    assert c.cacheMode == "service-worker"               # not downgraded/overwritten

def test_set_cache_mode_accepts_and_normalizes_legacy(mock_settings):
    # discovery.set_cache_mode: accepts "mmvideo" and (transitional) "lighttpd-localhost",
    # storing "mmvideo" for both; rejects unknown.
    from mosaicmesh.api import discovery
    from mosaicmesh.state import Client
    server_settings = mock_settings
    server_settings.clients["k"] = Client()
    assert discovery._normalize_cache_mode("lighttpd-localhost") == "mmvideo"
    assert discovery._normalize_cache_mode("mmvideo") == "mmvideo"
    assert discovery._normalize_cache_mode("service-worker") == "service-worker"
    assert discovery._normalize_cache_mode("bogus") is None
```

- [ ] **Step 2: Run the tests, verify they fail**

Run: `python -m pytest tests/unit/test_media_cache.py -c tests/pytest.ini -v -k "mmvideo or normalize or modern or upgrade_only"`
Expected: FAIL — `apply_cache_capability` still writes `"lighttpd-localhost"`; `discovery._normalize_cache_mode` does not exist.

- [ ] **Step 3: Add the shared normalizer + apply the write rename** — in `mosaicmesh/api/discovery.py`, add a module-level helper (near the top, after imports):

```python
# Transitional: the mmvideo cache mode was renamed "lighttpd-localhost" -> "mmvideo"
# (2026-07-18; lighttpd is vestigial). Accept the old string as INPUT and normalize
# it to the new one so all guards compare against "mmvideo" alone. Returns None for
# an unknown mode.
_VALID_CACHE_MODES = ("none", "mmvideo", "service-worker")
def _normalize_cache_mode(mode):
    if mode == "lighttpd-localhost":
        return "mmvideo"
    return mode if mode in _VALID_CACHE_MODES else None
```

In `mosaicmesh/websocket/legacy.py:162`, change the write:

```python
    elif p.get("cacheCapable"):
        client.cacheMode = "mmvideo"          # was "lighttpd-localhost"
```

- [ ] **Step 4: Rewire the two validators to normalize**

`mosaicmesh/api/discovery.py:454` (`set_cache_mode` action) — replace the membership check:

```python
    elif action == "set_cache_mode":
        mode = _normalize_cache_mode(data.get("mode"))
        if mode is None:
            return web.json_response({"success": False,
                                      "error": f"invalid mode {data.get('mode')!r}"},
                                     status=400)
        client.cacheMode = mode
        logging.info("set_cache_mode: %s -> %s", client_key, mode)
```

`mosaicmesh/websocket/legacy.py:254` (`ANNOUNCE_CACHE_MODE`) — normalize before storing (import is at call time via the `discovery` module; use a local import to avoid a top-level cycle):

```python
        client = server.settings.clients.get(msg["SRC"])
        from mosaicmesh.api.discovery import _normalize_cache_mode
        mode = _normalize_cache_mode((msg.get("PAYLOAD") or {}).get("mode"))
        if client and mode is not None:
            client.cacheMode = mode
        response["PAYLOAD"] = {"cacheMode": getattr(client, "cacheMode", "none")}
```

- [ ] **Step 5: Rename the guards (all compare against `"mmvideo"` now)** — exact edits:

`server.py:423`:
```python
    if getattr(client, "cacheMode", "none") != "mmvideo":
```
`server.py:2166`:
```python
        if getattr(_c, "cacheMode", "none") == "mmvideo" and getattr(_c, "isOnline", False):
```
`mosaicmesh/api/discovery.py:155`:
```python
    if getattr(client, "cacheMode", "none") != "mmvideo":
```
`mosaicmesh/api/discovery.py:287`:
```python
            if getattr(c, "cacheMode", "none") != "mmvideo":
```
`mosaicmesh/api/discovery.py:410`:
```python
                if getattr(c, "cacheMode", "none") == "mmvideo":
```
`mosaicmesh/render.py:635`:
```python
                and getattr(c, "cacheMode", "none") in ("mmvideo", "service-worker")
```
`mosaicmesh/render.py:1334`:
```python
    if (getattr(client, "cacheMode", "none") == "mmvideo"
```
`mosaicmesh/render.py:1399`:
```python
    cache_on = (getattr(c, "cacheMode", "none") == "mmvideo")
```

- [ ] **Step 6: Run the new tests + the full cache/render/discovery unit suites**

Run: `python -m pytest tests/unit/test_media_cache.py tests/unit/test_cache_reconcile.py tests/unit/test_encode_for_device.py -c tests/pytest.ini -v`
Expected: PASS (new tests green; existing tests still green — the string is the only change). If an existing test hard-codes `"lighttpd-localhost"` as an EXPECTED written/guarded value, update that expectation to `"mmvideo"` (it is asserting the write/guard this task just renamed).

- [ ] **Step 7: Commit**

```bash
git add mosaicmesh/websocket/legacy.py mosaicmesh/api/discovery.py server.py mosaicmesh/render.py tests/unit/test_media_cache.py
git commit -m "refactor(cache): rename cacheMode lighttpd-localhost -> mmvideo (writes+guards); validators normalize the legacy string"
```

---

### Task 2: Boot migration — rewrite persisted `"lighttpd-localhost"` → `"mmvideo"`

**Files:**
- Modify: `mosaicmesh/state.py` — `migrate_client_objects` (near the existing `cacheMode` backfill at ~line 307)
- Test: `tests/unit/test_client_management.py`

**Interfaces:**
- Consumes: `migrate_client_objects()` iterating `server.settings.clients` (each a `Client`); the `cacheMode` backfill already present there.
- Produces: after boot, no `Client.cacheMode == "lighttpd-localhost"` remains — old persisted values become `"mmvideo"`.

- [ ] **Step 1: Write the failing test** — add to `tests/unit/test_client_management.py`:

```python
def test_migrate_rewrites_legacy_cache_mode(mock_settings):
    import server
    from mosaicmesh.state import Client, migrate_client_objects
    c_old = Client(); c_old.cacheMode = "lighttpd-localhost"
    c_sw  = Client(); c_sw.cacheMode  = "service-worker"
    c_none = Client()  # default "none"
    server.settings.clients = {"old": c_old, "sw": c_sw, "n": c_none}
    migrate_client_objects()
    assert server.settings.clients["old"].cacheMode == "mmvideo"
    assert server.settings.clients["sw"].cacheMode == "service-worker"
    assert server.settings.clients["n"].cacheMode == "none"
```

- [ ] **Step 2: Run the test, verify it fails**

Run: `python -m pytest tests/unit/test_client_management.py -c tests/pytest.ini -v -k migrate_rewrites_legacy_cache_mode`
Expected: FAIL — `cacheMode` stays `"lighttpd-localhost"` (no migration yet).

- [ ] **Step 3: Implement the migration** — in `mosaicmesh/state.py`, in `migrate_client_objects`, adjacent to the existing `cacheMode` backfill (~307-308: `if not hasattr(client, 'cacheMode'): client.cacheMode = "none"`), add:

```python
        # 2026-07-18 rename: the mmvideo cache mode was "lighttpd-localhost"
        # (misnomer — lighttpd is vestigial). Rewrite persisted old values so
        # guards (which now compare == "mmvideo") keep treating the device as
        # cache-capable.
        if getattr(client, 'cacheMode', 'none') == 'lighttpd-localhost':
            client.cacheMode = 'mmvideo'
```

- [ ] **Step 4: Run the test, verify it passes**

Run: `python -m pytest tests/unit/test_client_management.py -c tests/pytest.ini -v -k migrate_rewrites_legacy_cache_mode`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/state.py tests/unit/test_client_management.py
git commit -m "feat(cache): migrate persisted cacheMode lighttpd-localhost -> mmvideo at boot"
```

---

### Task 3: Reword lighttpd comments + confirm lighttpd is vestigial

**Files:**
- Modify (comments/docstrings only): `mosaicmesh/websocket/legacy.py` (145-153), `mosaicmesh/api/discovery.py` (118, 152, 332, 397), `mosaicmesh/render.py` (626, 633, 1298, 1389, 1480), `server.py` (421), `mosaicmesh/state.py` (229, 241)
- Test: none (comment-only + a verification grep)

**Interfaces:**
- Consumes: nothing (documentation).
- Produces: no code/behavior change; comments reflect "mmvideo cache mode" (not "lighttpd-localhost"), and a documented proof that no server code path uses lighttpd.

- [ ] **Step 1: Reword the comments** — in each file above, replace phrasings like "lighttpd-localhost iPad", "set to 'lighttpd-localhost' by onboarding", "lighttpd-localhost-only", "lighttpd installed and a writable … dir" with the mmvideo framing, e.g.:
  - `state.py:229-234`: `# set to "mmvideo" when the client reports its mmvideo cache backend on REGISTER (apply_cache_capability). lighttpd is not involved — the mmvideo tweak serves the local file.`
  - `render.py:633`: `# _resolve_media_url, which stays mmvideo-only. Pure — unit-tested.`
  - Keep every `http://127.0.0.1:8080/…` URL builder EXACTLY as-is (it is the client-facing token the mmvideo tweak intercepts — NOT a lighttpd dependency). Only change prose that calls the *mode* "lighttpd-localhost" or claims lighttpd *serves* the file.

- [ ] **Step 2: Run the vestigial-confirmation sweep**

Run: `grep -rnE "8080|lighttpd" server.py mosaicmesh/ | grep -v ".pyc"`
Expected: every hit is EITHER (a) a `http://127.0.0.1:8080/…` URL string built in `render.py` (the client token, consumed by mmvideo on-device), OR (b) a comment. There must be NO line that opens a socket/connection to `:8080`, runs a `lighttpd` process, or shells out to it. Record the sweep output in the commit message as the proof that daemon removal is safe.

- [ ] **Step 3: Run the full unit suite (no behavior changed)**

Run: `python pytest_runner.py --unit`
Expected: PASS (comment-only task; nothing functional changed since Task 2).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "docs(cache): reword lighttpd comments to mmvideo; confirm lighttpd vestigial server-side (grep sweep in body)"
```

---

### Task 4 (OPERATOR-RUN, sequenced last): on-wall canary + fleet lighttpd removal

**Files:** none (fleet operation). Gated on Tasks 1-3 merged + the `:3000` server restarted to load them.

**Interfaces:**
- Consumes: the merged server change (devices now report/hold `cacheMode = "mmvideo"`).
- Produces: lighttpd stopped/removed on every device; the dependency is gone.

- [ ] **Step 1: Deploy + verify the mode.** Restart the `:3000` server. Confirm an eligible online iPad shows `cacheMode = "mmvideo"` (via `GET /api/discovery/stats` or the Fleet cache chip) and still plays cached video.

- [ ] **Step 2: Canary — prove the bypass.** On ONE screen (Windows OpenSSH, per [[fleet-ssh-no-burst]]), stop lighttpd (e.g. `launchctl unload …/lighttpd.plist` or `killall lighttpd` per the device's setup) and confirm a cached-video playlist STILL plays there (mmvideo serves the local file with lighttpd down). If it fails, STOP — investigate before touching the fleet.

- [ ] **Step 3: Fleet-wide removal.** Sequentially (paced, no burst — [[fleet-ssh-no-burst]] / [[fleet-tweak-deploy-ssh-doze]], Windows OpenSSH) stop + disable lighttpd on every device. Reversible: re-enabling is a per-device start command.

- [ ] **Step 4: Post-removal soak.** Over a normal playback run, confirm the fleet still caches + plays cached video and `cacheMode` stays `"mmvideo"`; watch for any `<video>` errors that would signal an interception miss now that the fallback is gone.

---

## Self-Review

**Spec coverage:**
- "Capability detection unchanged / no probe" → acknowledged in Global Constraints + Task 1 (only the string changes; `apply_cache_capability` logic untouched). ✔
- `cacheMode` rename at all functional sites → Task 1 (11 sites listed with exact edits) + normalize-on-input. ✔
- Boot migration → Task 2. ✔
- Back-compat window → Task 1's `_normalize_cache_mode` (accepts `"lighttpd-localhost"` as input) + Task 2 (persisted). ✔
- Comment reword + vestigial confirmation → Task 3. ✔
- lighttpd removal rollout (canary-first, paced) → Task 4. ✔
- Testing (apply_cache_capability, migration, validators, regression, vestigial sweep, on-wall) → Tasks 1-4 steps. ✔

**Placeholder scan:** every code step shows the exact edit; commands have expected outcomes; no TBD/"handle edge cases". ✔

**Type consistency:** `_normalize_cache_mode(mode) -> str|None` is defined once in `discovery.py` (Task 1 Step 3) and consumed by both validators (Task 1 Step 4) and the test (Task 1 Step 1); the written/guarded literal is `"mmvideo"` everywhere; `apply_cache_capability` / `migrate_client_objects` names match the current code. ✔
