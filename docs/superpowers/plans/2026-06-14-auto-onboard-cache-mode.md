# Auto-onboard `lighttpd-localhost` Cache Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Devices that are genuinely cache-capable (lighttpd serving on :8080 + cache dir present) get `cacheMode = "lighttpd-localhost"` set automatically on REGISTER via a server-side SSH probe, so the already-wired post-render push and the existing cache-status UI light up with no operator action.

**Architecture:** A new fire-and-forget `_probe_cache_capability` coroutine SSH-probes the device for the two real push prerequisites and flips `cacheMode` up (capable) or down (lost). It's fired from the REGISTER handler for eligible (iPad/tablet) devices, bounded by a semaphore + in-flight guard. The render push, URL rewrite, propagation math, and Fleet cache chip are unchanged — this only flips the gating flag and surfaces a clean three-state label.

**Tech Stack:** Python 3.14 / aiohttp / asyncio (server), jsonpickle persistence, Node `--test` for the pure JS helper.

---

## Spec

`docs/superpowers/specs/2026-06-14-auto-onboard-cache-mode-design.md`

## Background facts the implementer needs

- Tests **must** use a runner, never bare `pytest`:
  `python -m pytest tests/unit/test_media_cache.py -c tests/pytest.ini -v`
- Python 3.14: drive coroutines in sync tests with `asyncio.run(...)`, never
  `get_event_loop().run_until_complete()`. `tests/unit/test_media_cache.py`
  already defines a `_run(coro)` helper — reuse it.
- SSH constants live in `mosaicmesh/device_scripts.py` and are re-exported from
  `server.py`: `SSH_KEY_PATH`, `SSH_USER` (`"root"`), `SSH_LEGACY_OPTS` (list).
  The push destination dir is `/var/mobile/Media/MosaicMeshCache/`; lighttpd
  serves on `127.0.0.1:8080` (see `_per_client_items` / `_push_segment_to_cached_clients`).
- `_push_segment_to_cached_clients` early-returns unless `cacheMode ==
  "lighttpd-localhost"`. It is already called from `_encode_group`
  (`mosaicmesh/render.py:507`), which both render paths invoke. No render change.
- `asyncio` is already imported in `mosaicmesh/websocket/legacy.py` (it uses
  `asyncio.ensure_future` for `_auto_arm_client`).
- The manual setter `set_cache_mode` lives in `mosaicmesh/api/discovery.py`
  (`client.cacheMode = mode`); persistence is `server.saveSettings()`.

## File Structure

- `mosaicmesh/state.py` — add `cacheProbedMs` field + migration backfill.
- `server.py` — `_PROBE_*` constants, `_get_probe_sem`, `_probe_inflight` set,
  `_is_probe_eligible(client)`, `_probe_cache_capability(client_key)`.
- `mosaicmesh/websocket/legacy.py` — fire the probe in the REGISTER branch.
- `mosaicmesh/api/discovery.py` — add `cacheProbedMs` to the device payload.
- `js/timeline/fleet/fleet-status.js` — three-state `deviceCacheStatus` (add a
  `state` field: `network` / `caching` / `local`).
- Tests: `tests/unit/test_media_cache.py`, `tests/unit/js/fleet-status.test.js`.

---

### Task 1: `cacheProbedMs` state field + migration

**Files:**
- Modify: `mosaicmesh/state.py` (Client `__init__` near `self.cacheMode`; `migrate_client_objects` near the `cacheMode`/`cachedSegments` backfill)
- Test: `tests/unit/test_media_cache.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_media_cache.py`:

```python
def test_client_has_cacheProbedMs_default():
    from mosaicmesh.state import Client
    c = Client()
    assert c.cacheProbedMs is None

def test_migrate_backfills_cacheProbedMs():
    from mosaicmesh.state import Client, migrate_client_objects
    import server
    c = Client()
    del c.cacheProbedMs            # simulate an older pickled client
    server.settings.clients = {"old": c}
    migrate_client_objects()
    assert server.settings.clients["old"].cacheProbedMs is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_media_cache.py::test_client_has_cacheProbedMs_default tests/unit/test_media_cache.py::test_migrate_backfills_cacheProbedMs -c tests/pytest.ini -v`
Expected: FAIL (`AttributeError: 'Client' object has no attribute 'cacheProbedMs'`)

- [ ] **Step 3: Add the field + migration**

In `mosaicmesh/state.py`, in `Client.__init__`, immediately after `self.cachedSegments = set()` (the line that follows `self.cacheMode = "none"`):

```python
        # Wall-clock ms of the last server-side cache-capability SSH probe
        # (None = never probed). Observability only; nothing gates on it.
        self.cacheProbedMs = None
```

In `migrate_client_objects`, next to the existing `cachedSegments` backfill:

```python
        if not hasattr(client, 'cacheProbedMs'):
            client.cacheProbedMs = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_media_cache.py -c tests/pytest.ini -k cacheProbedMs -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/state.py tests/unit/test_media_cache.py
git commit -m "feat(cache): add Client.cacheProbedMs field + migration"
```

---

### Task 2: `_is_probe_eligible` helper

**Files:**
- Modify: `server.py` (near the other client helpers, e.g. just below `_push_segment_to_cached_clients`)
- Test: `tests/unit/test_media_cache.py`

- [ ] **Step 1: Write the failing test**

```python
def test_is_probe_eligible():
    import server
    from mosaicmesh.state import Client
    def mk(dt, ip, osn="iOS"):
        c = Client(); c.deviceType = dt; c.ip = ip; c.osName = osn; return c
    assert server._is_probe_eligible(mk("tablet", "192.168.1.50")) is True
    assert server._is_probe_eligible(mk("smartphone", "192.168.1.51", "iOS")) is True
    assert server._is_probe_eligible(mk("desktop", "192.168.1.52", "Windows")) is False
    assert server._is_probe_eligible(mk("tablet", "")) is False          # no ip
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_media_cache.py::test_is_probe_eligible -c tests/pytest.ini -v`
Expected: FAIL (`AttributeError: module 'server' has no attribute '_is_probe_eligible'`)

- [ ] **Step 3: Implement**

In `server.py`, add:

```python
def _is_probe_eligible(client):
    """True for the provisioned display devices we SSH-probe for cache
    capability: Apple touch devices (iPad-1 reclassifies to deviceType
    'tablet'; iOS phones report 'smartphone') that have an IP. Everything
    else never gets cacheMode and always serves centrally."""
    if not getattr(client, "ip", ""):
        return False
    dt = (getattr(client, "deviceType", "") or "").lower()
    osn = (getattr(client, "osName", "") or "").lower()
    return dt in ("tablet", "smartphone") or osn == "ios"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_media_cache.py::test_is_probe_eligible -c tests/pytest.ini -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_media_cache.py
git commit -m "feat(cache): _is_probe_eligible gate for cache-capability probe"
```

---

### Task 3: `_probe_cache_capability` (upgrade path)

**Files:**
- Modify: `server.py` (constants + `_get_probe_sem` + `_probe_inflight` + the coroutine, beside `_push_segment_to_cached_clients`)
- Test: `tests/unit/test_media_cache.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_media_cache.py` already mocks `asyncio.create_subprocess_exec`
for push tests. Add a small fake-proc helper if one isn't already present, then:

```python
class _FakeProc:
    def __init__(self, rc, out=b"", err=b""):
        self.returncode = rc; self._out = out; self._err = err
    async def communicate(self):
        return (self._out, self._err)
    def kill(self):
        self.returncode = -9

def test_probe_sets_lighttpd_localhost_on_ok(monkeypatch):
    import server
    from mosaicmesh.state import Client
    c = Client(); c.ip = "192.168.1.50"; c.cacheMode = "none"
    server.settings.clients = {"ipad1": c}
    async def fake_exec(*a, **k):
        return _FakeProc(0, out=b"MM_CACHE_OK\n")
    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(server, "saveSettings", lambda *a, **k: None)
    _run(server._probe_cache_capability("ipad1"))
    assert c.cacheMode == "lighttpd-localhost"
    assert c.cacheProbedMs is not None

def test_probe_no_ip_skips(monkeypatch):
    import server
    from mosaicmesh.state import Client
    c = Client(); c.ip = ""; c.cacheMode = "none"
    server.settings.clients = {"x": c}
    called = {"n": 0}
    async def fake_exec(*a, **k):
        called["n"] += 1; return _FakeProc(0, b"MM_CACHE_OK")
    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", fake_exec)
    _run(server._probe_cache_capability("x"))
    assert called["n"] == 0
    assert c.cacheMode == "none"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_media_cache.py -c tests/pytest.ini -k probe -v`
Expected: FAIL (`AttributeError: ... '_probe_cache_capability'`)

- [ ] **Step 3: Implement the constants, semaphore, in-flight set, and coroutine**

In `server.py`, near the other `_PUSH_*` constants:

```python
_PROBE_CONCURRENCY = 4          # max concurrent cache-capability SSH probes
_PROBE_TIMEOUT_S = 20           # overall ceiling per probe
_PROBE_CONNECT_TIMEOUT_S = 10   # ssh ConnectTimeout
_probe_sem = None
_probe_inflight = set()         # client_keys with a probe currently running

def _get_probe_sem():
    global _probe_sem
    if _probe_sem is None:
        _probe_sem = asyncio.Semaphore(_PROBE_CONCURRENCY)
    return _probe_sem
```

Then the coroutine:

```python
async def _probe_cache_capability(client_key):
    """SSH-probe a device for the two real push prerequisites (cache dir +
    lighttpd serving on :8080) and flip cacheMode accordingly. Fire-and-forget;
    never blocks the caller, never raises. Upgrade: none -> lighttpd-localhost
    when 'MM_CACHE_OK' comes back. Downgrade handled in a later task."""
    client = settings.clients.get(client_key)
    if not client or not getattr(client, "ip", ""):
        return
    if client_key in _probe_inflight:
        return                                  # no duplicate concurrent probe
    _probe_inflight.add(client_key)
    try:
        remote = ("test -d /var/mobile/Media/MosaicMeshCache && "
                  "curl -sf -m 3 http://localhost:8080/ >/dev/null && "
                  "echo MM_CACHE_OK")
        cmd = (["ssh", "-i", SSH_KEY_PATH] + SSH_LEGACY_OPTS
               + ["-T", "-o", "ConnectTimeout=%d" % _PROBE_CONNECT_TIMEOUT_S,
                  "%s@%s" % (SSH_USER, client.ip), remote])
        ok = False
        async with _get_probe_sem():
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE)
                out, _err = await asyncio.wait_for(
                    proc.communicate(), timeout=_PROBE_TIMEOUT_S)
                ok = (proc.returncode == 0 and b"MM_CACHE_OK" in (out or b""))
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                ok = False
            except Exception as e:            # noqa: BLE001
                logging.warning("cache-probe %s: %s", client_key, e)
                ok = False
        client.cacheProbedMs = int(time.time() * 1000)
        if ok and client.cacheMode != "lighttpd-localhost":
            client.cacheMode = "lighttpd-localhost"
            logging.info("cache-probe: %s is cache-capable -> lighttpd-localhost",
                         client_key)
            saveSettings()
        # (downgrade path added in Task 4)
    finally:
        _probe_inflight.discard(client_key)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_media_cache.py -c tests/pytest.ini -k probe -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_media_cache.py
git commit -m "feat(cache): _probe_cache_capability SSH probe (upgrade path)"
```

---

### Task 4: probe downgrade path (clear cachedSegments)

**Files:**
- Modify: `server.py` (`_probe_cache_capability`)
- Test: `tests/unit/test_media_cache.py`

- [ ] **Step 1: Write the failing test**

```python
def test_probe_downgrades_and_clears_on_failure(monkeypatch):
    import server
    from mosaicmesh.state import Client
    c = Client(); c.ip = "192.168.1.50"
    c.cacheMode = "lighttpd-localhost"
    c.cachedSegments = {"abc123_0", "abc123_1"}
    server.settings.clients = {"ipad1": c}
    async def fake_exec(*a, **k):
        return _FakeProc(1, out=b"", err=b"curl: connection refused")
    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(server, "saveSettings", lambda *a, **k: None)
    _run(server._probe_cache_capability("ipad1"))
    assert c.cacheMode == "none"
    assert c.cachedSegments == set()

def test_probe_failure_leaves_none_untouched(monkeypatch):
    import server
    from mosaicmesh.state import Client
    c = Client(); c.ip = "192.168.1.50"; c.cacheMode = "none"
    server.settings.clients = {"ipad1": c}
    async def fake_exec(*a, **k):
        return _FakeProc(1)
    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(server, "saveSettings", lambda *a, **k: None)
    _run(server._probe_cache_capability("ipad1"))
    assert c.cacheMode == "none"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_media_cache.py::test_probe_downgrades_and_clears_on_failure -c tests/pytest.ini -v`
Expected: FAIL (cacheMode stays `lighttpd-localhost`)

- [ ] **Step 3: Implement the downgrade branch**

In `_probe_cache_capability`, replace the `# (downgrade path added in Task 4)`
comment with:

```python
        elif not ok and client.cacheMode == "lighttpd-localhost":
            client.cacheMode = "none"
            client.cachedSegments = set()   # unreachable now; stop emitting dead localhost URLs
            logging.info("cache-probe: %s no longer cache-capable -> none (cleared cache)",
                         client_key)
            saveSettings()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_media_cache.py -c tests/pytest.ini -k probe -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_media_cache.py
git commit -m "feat(cache): probe downgrade clears cachedSegments when lighttpd gone"
```

---

### Task 5: fire the probe from REGISTER

**Files:**
- Modify: `mosaicmesh/websocket/legacy.py` (REGISTER branch, after device detection at ~line 238, OUTSIDE the `if is_new_client:` block so it fires on every register)
- Test: `tests/unit/test_media_cache.py`

- [ ] **Step 1: Write the failing test**

```python
def test_register_fires_probe_for_eligible(monkeypatch):
    import server
    from mosaicmesh.state import Client
    fired = []
    async def fake_probe(key):
        fired.append(key)
    monkeypatch.setattr(server, "_probe_cache_capability", fake_probe)
    c = Client(); c.ip = "192.168.1.50"; c.deviceType = "tablet"
    # _maybe_fire_cache_probe is the thin testable wrapper the REGISTER
    # branch calls; it gates on _is_probe_eligible then ensure_future.
    server._maybe_fire_cache_probe("ipad1", c)
    import asyncio as _a
    # let the scheduled task run
    _run(_a.sleep(0))
    assert fired == ["ipad1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_media_cache.py::test_register_fires_probe_for_eligible -c tests/pytest.ini -v`
Expected: FAIL (`AttributeError: ... '_maybe_fire_cache_probe'`)

- [ ] **Step 3: Implement the wrapper + wire it into REGISTER**

In `server.py`, add (keeps the REGISTER branch a one-liner and is unit-testable):

```python
def _maybe_fire_cache_probe(client_key, client):
    """Fire-and-forget the cache-capability probe for an eligible device.
    Safe to call when no event loop is running (tests) -- silently skips."""
    if not _is_probe_eligible(client):
        return
    try:
        asyncio.ensure_future(_probe_cache_capability(client_key))
    except RuntimeError:
        pass   # no running loop (sync test context)
```

In `mosaicmesh/websocket/legacy.py`, in the REGISTER branch, immediately AFTER
the legacy-iPad reclassification block (after line ~238, before `if
is_new_client:`):

```python
        # Auto-onboard local cache: probe eligible (iPad/tablet) devices for
        # lighttpd + cache dir and flip cacheMode so the post-render push engages.
        server._maybe_fire_cache_probe(msg["SRC"], client)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_media_cache.py::test_register_fires_probe_for_eligible -c tests/pytest.ini -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server.py mosaicmesh/websocket/legacy.py tests/unit/test_media_cache.py
git commit -m "feat(cache): fire cache-capability probe on every eligible REGISTER"
```

---

### Task 6: expose `cacheProbedMs` in the device payload

**Files:**
- Modify: `mosaicmesh/api/discovery.py` (`get_discovered_devices`, the per-device dict near `propagationPercent`)
- Test: `tests/unit/test_media_cache.py`

- [ ] **Step 1: Write the failing test**

```python
def test_devices_payload_includes_cacheProbedMs():
    import server
    from mosaicmesh.state import Client
    from mosaicmesh.api.discovery import get_discovered_devices
    c = Client(); c.cacheProbedMs = 1234567
    server.settings.clients = {"ipad1": c}
    devs = get_discovered_devices()
    d = next(x for x in devs if x["clientKey"] == "ipad1")
    assert d["cacheProbedMs"] == 1234567
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_media_cache.py::test_devices_payload_includes_cacheProbedMs -c tests/pytest.ini -v`
Expected: FAIL (`KeyError: 'cacheProbedMs'`)

- [ ] **Step 3: Implement**

In `mosaicmesh/api/discovery.py`, in `get_discovered_devices`, in the per-device
dict next to the existing `"propagationPercent": _propagation_percent_for_client(client),`
line, add:

```python
            "cacheProbedMs": getattr(client, "cacheProbedMs", None),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_media_cache.py::test_devices_payload_includes_cacheProbedMs -c tests/pytest.ini -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/api/discovery.py tests/unit/test_media_cache.py
git commit -m "feat(cache): expose cacheProbedMs in /api/discovery/devices"
```

---

### Task 7: three-state `deviceCacheStatus` (Fleet UI)

**Files:**
- Modify: `js/timeline/fleet/fleet-status.js` (`deviceCacheStatus`)
- Test: `tests/unit/js/fleet-status.test.js`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/js/fleet-status.test.js`:

```javascript
import { deviceCacheStatus } from '../../../js/timeline/fleet/fleet-status.js';

test('deviceCacheStatus: none -> network state', () => {
  const s = deviceCacheStatus({ cacheMode: 'none' });
  assert.equal(s.state, 'network');
  assert.equal(s.applicable, false);
});

test('deviceCacheStatus: capable + partial -> caching state', () => {
  const s = deviceCacheStatus({ cacheMode: 'lighttpd-localhost',
    cachedSegments: ['t_0'], expectedSegments: 4 });
  assert.equal(s.state, 'caching');
  assert.equal(s.percent, 25);
});

test('deviceCacheStatus: capable + full -> local state', () => {
  const s = deviceCacheStatus({ cacheMode: 'lighttpd-localhost',
    cachedSegments: ['t_0','t_1','t_2','t_3'], expectedSegments: 4 });
  assert.equal(s.state, 'local');
  assert.equal(s.percent, 100);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/unit/js/fleet-status.test.js`
Expected: FAIL (`s.state` is undefined)

- [ ] **Step 3: Add the `state` field**

In `js/timeline/fleet/fleet-status.js`, in `deviceCacheStatus`, add a `state` to
each return. For the `mode === 'none'` early return and the `!expected` early
return, add `state: 'network'`. For the final return, compute:

```javascript
  const state = percent >= 100 ? 'local' : 'caching';
```

and include `state` in that return object. (Leave `applicable`, `percent`,
`label`, etc. unchanged — the chip keeps working; `state` is the new clean
three-way discriminator.)

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test tests/unit/js/fleet-status.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add js/timeline/fleet/fleet-status.js tests/unit/js/fleet-status.test.js
git commit -m "feat(cache): three-state deviceCacheStatus (network/caching/local)"
```

---

### Task 8: full suite, docs, final review

**Files:**
- Modify: `CLAUDE.md` (cache-mode note), `docs/superpowers/specs/2026-06-03-media-cache-design.md` (cross-ref the auto-onboard)

- [ ] **Step 1: Run the Python unit suite**

Run: `python pytest_runner.py --unit`
Expected: PASS (no regressions; new `test_media_cache.py` probe tests green)

- [ ] **Step 2: Run the JS suite**

Run: `python pytest_runner.py --js`
Expected: PASS (180+ tests, including new `deviceCacheStatus` state tests)

- [ ] **Step 3: Update docs**

In `CLAUDE.md`, under the media-cache / cache-mode notes, add one line:
`cacheMode is auto-onboarded: a server-side SSH probe (_probe_cache_capability)
on every eligible REGISTER flips a lighttpd-equipped device to lighttpd-localhost
(and downgrades + clears cache when lighttpd is gone), so the post-render push
auto-engages. Gated by _is_probe_eligible; bounded by _PROBE_CONCURRENCY.`

- [ ] **Step 4: Manual fleet verification (record results)**

1. Restart/register one provisioned sign; confirm the server log shows
   `cache-probe: <key> is cache-capable -> lighttpd-localhost`.
2. Save/render a playlist for its group; confirm `cache-push: <key> seg_...` log
   lines and the device's `cachedSegments` fill (via `/api/discovery/devices`).
3. Run a play test; confirm `127.0.0.1:8080` fetches appear in the log and the
   `GET /media/<key>/seg_...` LAN saturation is gone.
4. Confirm a non-provisioned device stays `cacheMode: none` and serves centrally.

- [ ] **Step 5: Commit + final review**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-06-03-media-cache-design.md
git commit -m "docs(cache): document auto-onboard cache-mode probe"
```

Then dispatch a final code review over the whole change set (the
subagent-driven-development skill does this automatically at the end).

---

## Self-review

- **Spec coverage:** §1 probe → Tasks 2–4; §2 REGISTER wiring → Task 5; §3 payoff
  (no new code) → confirmed in Task 8 manual step; §4 UI three-state → Task 7;
  §5 persistence → `saveSettings()` in Tasks 3–4; `cacheProbedMs` → Tasks 1, 6.
  Error handling (timeout/exception → not-capable) → Task 3. All covered.
- **Placeholders:** none — every code step shows the code.
- **Type/name consistency:** `_probe_cache_capability`, `_is_probe_eligible`,
  `_maybe_fire_cache_probe`, `_get_probe_sem`, `_probe_inflight`, `cacheProbedMs`,
  and the `state` field name are used identically across tasks.
