# Cache push progress + propagation UI — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static per-push timeout with stall-based detection driven by iPad-side file-size polling, and add a per-display-group cache propagation bar to admin.html driven by a new SockJS broadcast.

**Architecture:** A new poller coroutine runs alongside each in-flight `_push_segment_to_cached_clients` invocation. Every 2s it SSHs to the destination iPad, reads the segment file size, updates an in-memory `Client.cachePushProgress` field, and broadcasts `CACHE_PROGRESS` via the SockJS bus. The push coroutine awaits `proc.communicate()` *or* a stall signal from the poller (size unchanged for 30s). Admin.html shows the resulting per-group aggregate in a bar updated by the SockJS broadcasts.

**Tech Stack:** Python 3.14 + aiohttp/SockJS server (server.py), jQuery 1.x SockJS client in admin.html. No new dependencies.

**Spec:** docs/superpowers/specs/2026-06-03-cache-progress-and-propagation-ui.md

---

## File structure

| File | Role | Why here |
|---|---|---|
| `server.py` | Module constants, `Client.cachePushProgress` field, refactored `_push_segment_to_cached_clients` with poller+stall, new `get_discovered_devices` fields, new `displayGroupPropagation` in stats handler, `CACHE_PROGRESS` broadcast helper | Single-process async monolith per CLAUDE.md; keeping it in one place matches existing patterns and avoids new module wiring |
| `admin.html` | New propagation bar DOM + JS state + SockJS handler | Per spec: admin.html only (not discovery.html) |
| `tests/unit/test_media_cache.py` | New tests for stall-detection happy + sad paths, progress callbacks, propagation aggregation | Existing 19-test file is the natural home; keeps cache test surface in one place |

The push-progress feature changes the SHAPE of `_push_segment_to_cached_clients`, so existing tests that mock that function need a quick check — but they mock at the `await _push_segment_to_cached_clients(...)` boundary, not its internals, so they remain valid.

---

## Task 1: Module constants + Client field

**Files:**
- Modify: `server.py` (push constants block near line 188, Client class near line 1480, migrate_client_objects)

- [ ] **Step 1: Replace static-timeout constant block**

Replace the existing `_PUSH_CONCURRENCY` + `_PUSH_TIMEOUT_S` + `_push_sem` block with:

```python
# Cap on parallel cache-push scps. The cache is meant to AVOID WiFi
# saturation at PLAY time, but firing 24 parallel scps right after
# a render saturates the same AP and every push times out. With 24
# contending streams the per-iPad rate dropped to ~100 KB/s.
# MMPUSH_CONCURRENCY=2 keeps each push at fair LAN share.
_PUSH_CONCURRENCY = int(os.environ.get("MMPUSH_CONCURRENCY") or 2)

# Stall detection: a push is aborted only if no NEW bytes have
# landed on the iPad's destination file within this window. The
# poller (see _push_segment_to_cached_clients) ssh's stat -c%s
# every _PUSH_POLL_INTERVAL_S seconds and sets stall_event if
# size hasn't increased in _PUSH_STALL_WINDOW_S. Replaces the
# earlier static 600s per-push timeout (a healthy slow transfer
# over contended WiFi can legitimately exceed any static ceiling).
_PUSH_STALL_WINDOW_S = int(os.environ.get("MMPUSH_STALL_S") or 30)
_PUSH_POLL_INTERVAL_S = float(os.environ.get("MMPUSH_POLL_S") or 2.0)

# Lazy module-level semaphore (created on first use, when an event
# loop is guaranteed to exist).
_push_sem = None


def _get_push_sem():
    global _push_sem
    if _push_sem is None:
        _push_sem = asyncio.Semaphore(_PUSH_CONCURRENCY)
    return _push_sem
```

- [ ] **Step 2: Add `cachePushProgress` to Client class**

In `class Client` `__init__` (server.py around line 1480 — find by searching `self.cachedSegments = set()`), add right after `cachedSegments`:

```python
        # In-memory only (does not persist; meaningful only during a push).
        # Set to a dict by _push_segment_to_cached_clients when a push starts;
        # cleared to None when the push ends (success or stall). Shape:
        #   {"token", "n", "bytesSent", "totalBytes",
        #    "startedMs", "lastChangeMs", "status", "mbps"}
        self.cachePushProgress = None
```

- [ ] **Step 3: Backfill in `migrate_client_objects`**

In `migrate_client_objects` (find by grepping `def migrate_client_objects`), alongside the other `if not hasattr(c, 'X'):` lines, add:

```python
        if not hasattr(c, "cachePushProgress"):
            c.cachePushProgress = None
```

- [ ] **Step 4: Verify import works**

```bash
python -c "import server; print(server._PUSH_STALL_WINDOW_S, server._PUSH_POLL_INTERVAL_S)"
```

Expected: `30 2.0`

- [ ] **Step 5: Commit**

```bash
git add server.py
git commit -m "feat(mosaic): add push stall-detection constants + cachePushProgress field"
```

---

## Task 2: Stall-detecting push refactor

**Files:**
- Modify: `server.py` `_push_segment_to_cached_clients` (currently around line 1907 per the prior edit)

- [ ] **Step 1: Write the new push body**

Replace the existing function body (from `src = "media/...` through the `except Exception` end) with:

```python
    src = "media/%s/videos/seg_%s_%d.mp4" % (client_key, segment_hash, segment_n)
    dst = ("%s@%s:/var/mobile/Media/MosaicMeshCache/seg_%s_%d.mp4"
           % (SSH_USER, client.ip, segment_hash, segment_n))
    cmd = ["scp", "-i", SSH_KEY_PATH] + SSH_LEGACY_OPTS + [src, dst]
    try:
        total_bytes = os.path.getsize(src)
    except OSError:
        logging.warning("cache-push %s: source %s missing", client_key, src)
        return

    sem = _get_push_sem()
    seg_key = "%s_%d" % (segment_hash, segment_n)
    async with sem:
        now_ms = int(time.time() * 1000)
        client.cachePushProgress = {
            "token": segment_hash,
            "n": segment_n,
            "bytesSent": 0,
            "totalBytes": total_bytes,
            "startedMs": now_ms,
            "lastChangeMs": now_ms,
            "status": "pushing",
            "mbps": 0.0,
        }
        _broadcast_cache_progress(client_key, client)

        stall_event = asyncio.Event()
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        poller = asyncio.ensure_future(
            _poll_push_progress(client_key, client, stall_event, proc))
        try:
            communicate_task = asyncio.ensure_future(proc.communicate())
            stall_task = asyncio.ensure_future(stall_event.wait())
            done, pending = await asyncio.wait(
                {communicate_task, stall_task},
                return_when=asyncio.FIRST_COMPLETED)
            if stall_task in done and communicate_task not in done:
                # Stalled: kill scp, drain output, exit
                proc.kill()
                try:
                    await asyncio.wait_for(proc.communicate(), timeout=5)
                except asyncio.TimeoutError:
                    pass
                communicate_task.cancel()
                logging.warning(
                    "cache-push %s seg_%s_%d: stalled (no progress for %ds, "
                    "%d/%d bytes)",
                    client_key, segment_hash, segment_n,
                    _PUSH_STALL_WINDOW_S,
                    client.cachePushProgress["bytesSent"], total_bytes)
                client.cachePushProgress["status"] = "stalled"
                _broadcast_cache_progress(client_key, client)
                client.cachePushProgress = None
                return
            # scp finished first (success or non-zero exit)
            stall_task.cancel()
            _out, err = await communicate_task
            if proc.returncode == 0:
                client.cachedSegments.add(seg_key)
                client.cachePushProgress["bytesSent"] = total_bytes
                client.cachePushProgress["status"] = "cached"
                _broadcast_cache_progress(client_key, client)
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
        finally:
            poller.cancel()
            try:
                await poller
            except (asyncio.CancelledError, Exception):
                pass
            client.cachePushProgress = None
```

- [ ] **Step 2: Add the `_poll_push_progress` helper**

Add immediately after `_push_segment_to_cached_clients`:

```python
async def _poll_push_progress(client_key, client, stall_event, proc):
    """Periodically ssh to the iPad and read the destination file's
    size. Updates client.cachePushProgress in place. Sets stall_event
    when no size change is seen for _PUSH_STALL_WINDOW_S. Broadcasts
    CACHE_PROGRESS over SockJS each time bytesSent changes."""
    prog = client.cachePushProgress
    if prog is None:
        return
    seg_path = ("/var/mobile/Media/MosaicMeshCache/seg_%s_%d.mp4"
                % (prog["token"], prog["n"]))
    ssh_cmd = (["ssh", "-i", SSH_KEY_PATH] + SSH_LEGACY_OPTS
               + ["%s@%s" % (SSH_USER, client.ip),
                  "stat -c%s " + seg_path + " 2>/dev/null || echo 0"])
    last_broadcast_bytes = -1
    while not proc.returncode and proc.returncode != 0:
        await asyncio.sleep(_PUSH_POLL_INTERVAL_S)
        if proc.returncode is not None:
            break
        try:
            poll_proc = await asyncio.create_subprocess_exec(
                *ssh_cmd, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(poll_proc.communicate(), timeout=10)
            sz = int((out or b"0").strip() or 0)
        except Exception:
            continue
        now_ms = int(time.time() * 1000)
        if client.cachePushProgress is None:
            return  # push ended underneath us
        if sz > client.cachePushProgress["bytesSent"]:
            elapsed_s = max(0.001,
                            (now_ms - client.cachePushProgress["startedMs"]) / 1000)
            client.cachePushProgress["bytesSent"] = sz
            client.cachePushProgress["lastChangeMs"] = now_ms
            client.cachePushProgress["mbps"] = round(
                sz / 1024 / 1024 / elapsed_s, 2)
            if sz != last_broadcast_bytes:
                _broadcast_cache_progress(client_key, client)
                last_broadcast_bytes = sz
        else:
            # No change since last poll: check stall window
            if now_ms - client.cachePushProgress["lastChangeMs"] >= \
                    _PUSH_STALL_WINDOW_S * 1000:
                stall_event.set()
                return
```

- [ ] **Step 3: Add the `_broadcast_cache_progress` helper**

Add immediately after `_poll_push_progress`:

```python
def _broadcast_cache_progress(client_key, client):
    """Emit a SockJS CACHE_PROGRESS message reflecting client.cache
    PushProgress. Sent DEST=ALL; admin.html listens, iPads ignore."""
    prog = client.cachePushProgress or {}
    payload = {
        "clientKey": client_key,
        "ip": getattr(client, "ip", ""),
        "displayID": getattr(client, "displayID", None),
        "token": prog.get("token"),
        "n": prog.get("n"),
        "bytesSent": prog.get("bytesSent", 0),
        "totalBytes": prog.get("totalBytes", 0),
        "percent": (100.0 * prog.get("bytesSent", 0)
                    / max(1, prog.get("totalBytes", 1))),
        "mbps": prog.get("mbps", 0.0),
        "status": prog.get("status", "cached"),
    }
    socketmanager.broadcast(jsonpickle.encode({
        "DEST": "ALL",
        "REQUEST": "CACHE_PROGRESS",
        "PAYLOAD": payload,
    }))
```

- [ ] **Step 4: Quick syntax check**

```bash
python -c "import ast; ast.parse(open('server.py').read()); print('PARSE_OK')"
```

Expected: `PARSE_OK`

- [ ] **Step 5: Make sure the 19 existing media_cache tests still pass**

```bash
python -m pytest tests/unit/test_media_cache.py -c tests/pytest.ini -v
```

Expected: `19 passed`. The existing tests mock at the `_push_segment_to_cached_clients` boundary (they call it directly, not its internals), so the refactor should be transparent. If anything regressed, fix here before moving on.

- [ ] **Step 6: Commit**

```bash
git add server.py
git commit -m "feat(mosaic): stall-based push timeout via iPad-side size polling"
```

---

## Task 3: Discovery API surface

**Files:**
- Modify: `server.py` `get_discovered_devices` (around line 402 per the existing edit history)
- Modify: `server.py` `api_discovery_stats`

- [ ] **Step 1: Add per-device fields to `get_discovered_devices`**

In `get_discovered_devices`, find the `device_info` dict assembly (look for `"cacheMode": getattr(client, "cacheMode", "none")`). After that line, add:

```python
            "cachePushProgress": getattr(client, "cachePushProgress", None),
```

Then immediately after the existing `"cachedSegments": ...` line, compute and add `propagationPercent`:

```python
            "expectedSegments": _expected_segments_for_client(client),
            "propagationPercent": _propagation_percent_for_client(client),
```

- [ ] **Step 2: Add the helpers**

Add these helpers ABOVE `get_discovered_devices`:

```python
def _expected_seg_keys_for_display(display):
    """Set of seg_KEY strings (token_n) the display CURRENTLY expects
    to be cached on any lighttpd-localhost iPad in its group. Driven
    by the display's renderedToken (so a stale cache for a previous
    render isn't counted)."""
    if not display or not getattr(display, "renderedToken", None):
        return set()
    token = display.renderedToken
    keys = set()
    for i, me in enumerate(getattr(display, "mediaElements", []) or []):
        if _is_renderable(me) and isVideoItem(me.file) \
                and me.playmode == PlayMode.SEGMENT:
            keys.add("%s_%d" % (token, i))
    return keys


def _expected_segments_for_client(client):
    """Number of seg_ items this client SHOULD have cached given the
    current rendered token of its display group."""
    did = getattr(client, "displayID", None)
    if not did:
        return 0
    return len(_expected_seg_keys_for_display(settings.displays.get(did)))


def _propagation_percent_for_client(client):
    """0-100. Fraction of currently-expected segments this client has
    in cachedSegments. Returns 100.0 for clients in displays with no
    renderable segments (vacuously cached)."""
    did = getattr(client, "displayID", None)
    if not did:
        return 100.0
    expected = _expected_seg_keys_for_display(settings.displays.get(did))
    if not expected:
        return 100.0
    cached = getattr(client, "cachedSegments", set()) or set()
    have = sum(1 for k in expected if k in cached)
    return round(100.0 * have / len(expected), 1)
```

- [ ] **Step 3: Add `displayGroupPropagation` to `api_discovery_stats`**

In `api_discovery_stats` (search for `cacheStats` to find it), after the existing dict assembly, before `web.json_response`, add:

```python
    # Per-display-group cache propagation: counts each iPad in the
    # group as one of {fullyCached, pushing, stalled, idle}.
    group_prop = {}
    for did, display in settings.displays.items():
        expected_keys = _expected_seg_keys_for_display(display)
        if not expected_keys:
            continue  # nothing renderable -> bar is meaningless
        total = 0
        full = pushing = stalled = idle = 0
        for k, c in settings.clients.items():
            if getattr(c, "displayID", None) != did:
                continue
            if getattr(c, "cacheMode", "none") != "lighttpd-localhost":
                continue
            total += 1
            cached = getattr(c, "cachedSegments", set()) or set()
            if expected_keys.issubset(cached):
                full += 1
            elif getattr(c, "cachePushProgress", None):
                status = c.cachePushProgress.get("status", "pushing")
                if status == "stalled":
                    stalled += 1
                else:
                    pushing += 1
            else:
                idle += 1
        if total > 0:
            group_prop[did] = {
                "total": total, "fullyCached": full,
                "pushing": pushing, "stalled": stalled, "idle": idle,
                "percent": round(100.0 * full / total, 1),
            }
```

Then add `"displayGroupPropagation": group_prop,` to the response dict.

- [ ] **Step 4: Smoke-test the API**

```bash
python -c "import ast; ast.parse(open('server.py').read()); print('PARSE_OK')"
```

Then with the server running (we'll restart later for this; this is just a static check):

```bash
python -m pytest tests/unit/test_media_cache.py -c tests/pytest.ini -v
```

Still expect 19 passed.

- [ ] **Step 5: Commit**

```bash
git add server.py
git commit -m "feat(mosaic): expose cachePushProgress + propagation% in discovery API"
```

---

## Task 4: Unit tests for stall + propagation

**Files:**
- Create/Modify: `tests/unit/test_media_cache.py`

- [ ] **Step 1: Add stall-detection test**

```python
def test_push_stalls_when_destination_size_does_not_change(monkeypatch, tmp_path):
    """Poller sees zero bytes-sent progress for the stall window;
    push aborts with status='stalled' and cachedSegments unchanged."""
    import server
    # Set a tight stall window so the test runs fast
    monkeypatch.setattr(server, "_PUSH_STALL_WINDOW_S", 0)
    monkeypatch.setattr(server, "_PUSH_POLL_INTERVAL_S", 0.01)

    src_dir = tmp_path / "media" / "ipad1" / "videos"
    src_dir.mkdir(parents=True)
    (src_dir / "seg_f00d_1.mp4").write_bytes(b"\0" * 1024)
    monkeypatch.chdir(tmp_path)

    client = server.Client()
    client.ip = "192.168.1.50"
    client.cacheMode = "lighttpd-localhost"
    client.cachedSegments = set()
    monkeypatch.setattr(server, "settings",
                        type("S", (), {"clients": {"ipad1": client}})())

    # Mock scp to never finish (simulate stuck transfer)
    class FakeProc:
        returncode = None
        def __init__(self): self._done = asyncio.Event()
        async def communicate(self):
            await self._done.wait()
            return (b"", b"")
        def kill(self):
            self.returncode = -9
            self._done.set()
    monkeypatch.setattr(server.asyncio, "create_subprocess_exec",
                        lambda *a, **k: asyncio.sleep(0, result=FakeProc()))
    # Mock ssh poller to always return the same size
    monkeypatch.setattr(server, "_poll_push_progress",
                        lambda *a, **k: asyncio.sleep(0))

    asyncio.run(server._push_segment_to_cached_clients("ipad1", "f00d", 1))
    assert client.cachedSegments == set()
    assert client.cachePushProgress is None
```

(Some shaping of the test will be needed to thread the mocks correctly; adjust as you implement.)

- [ ] **Step 2: Add propagation-percent test**

```python
def test_propagation_percent_for_client_partial_cache():
    """A client whose displayID's rendered token has 2 SEGMENT items
    of which the client has 1 cached returns 50.0."""
    import server
    display = type("D", (), {})()
    display.renderedToken = "abc"
    display.mediaElements = [
        type("ME", (), {"playmode": server.PlayMode.SEGMENT,
                        "file": "/media/server/videos/foo.mp4",
                        "id": "i1"})(),
        type("ME", (), {"playmode": server.PlayMode.SEGMENT,
                        "file": "/media/server/videos/bar.mp4",
                        "id": "i2"})(),
    ]
    server.settings = type("S", (), {})()
    server.settings.displays = {"G": display}
    server.settings.clients = {}
    client = type("C", (), {})()
    client.displayID = "G"
    client.cachedSegments = {"abc_0"}
    assert server._propagation_percent_for_client(client) == 50.0
```

- [ ] **Step 3: Add stats-aggregation test**

```python
async def test_display_group_propagation_aggregates(client_dict_for_test):
    """Two cached, one pushing, one idle -> percent=50, breakdown
    matches the inputs."""
    # ... (writes a tiny settings.clients + settings.displays, calls
    # api_discovery_stats, asserts on the JSON response)
```

(Adapt to the existing test fixture patterns in test_media_cache.py — look at how the other tests bootstrap `server.settings`.)

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/unit/test_media_cache.py -c tests/pytest.ini -v
```

Expect: `22 passed` (19 original + 3 new). If a new test is shaped wrong, fix it before moving on.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_media_cache.py
git commit -m "test(mosaic): cover stall detection + propagation aggregation"
```

---

## Task 5: admin.html propagation bar

**Files:**
- Modify: `admin.html`

- [ ] **Step 1: Locate the play/stop controls**

Open `admin.html` and search for the existing play/stop button row (likely in a div near the top of the body, or near the per-display-group section). Note the surrounding container so we can place the bar above it.

- [ ] **Step 2: Add the bar markup**

In the per-display-group section, before the play/stop controls, add:

```html
<div class="cache-propagation" data-display-id="{{group_id}}">
    <div class="cache-propagation-label">
        <strong class="cache-propagation-title">Cache propagation</strong>
        <span class="cache-propagation-counts">—</span>
    </div>
    <div class="cache-propagation-bar">
        <div class="cache-propagation-fill" style="width:0%"></div>
        <div class="cache-propagation-fill cache-propagation-fill-inflight"
             style="width:0%"></div>
    </div>
</div>
```

If admin.html builds groups dynamically from JS rather than templated HTML, instead create the element in JS at group-render time.

- [ ] **Step 3: Add the CSS**

```css
.cache-propagation { margin: 4px 0 8px; }
.cache-propagation-label { display: flex; justify-content: space-between;
    font-size: 12px; color: #555; margin-bottom: 2px; }
.cache-propagation-bar { position: relative; width: 100%; height: 8px;
    background: #eee; border-radius: 4px; overflow: hidden; }
.cache-propagation-fill { position: absolute; top: 0; left: 0; height: 100%;
    background: #2e7d32; transition: width 250ms ease-out; }
.cache-propagation-fill-inflight { background: #81c784; opacity: 0.6; }
.cache-propagation.stalled .cache-propagation-counts { color: #b00; }
```

- [ ] **Step 4: Wire JS — initial fetch + SockJS handler**

In the existing admin JS (modern JS allowed per CLAUDE.md), add an init block that:

1. On admin page load, fetches `/api/discovery/stats` and renders bars from `displayGroupPropagation`.
2. Subscribes to SockJS `CACHE_PROGRESS` messages and updates the relevant group's bar.

```javascript
const propagationState = new Map();  // displayID -> {total, fullyCached, pushing[], idle, stalled}

function renderPropagationBar(displayID) {
    const el = document.querySelector(`.cache-propagation[data-display-id="${displayID}"]`);
    if (!el) return;
    const s = propagationState.get(displayID);
    if (!s) return;
    const cachedPct = s.total ? (100 * s.fullyCached / s.total) : 0;
    // In-flight contributes a fractional bonus: sum of per-iPad
    // percent for those currently pushing, divided by total.
    const inflightContrib = s.total
        ? Array.from(s.pushing.values()).reduce((a, b) => a + b, 0) / s.total
        : 0;
    el.querySelector(".cache-propagation-fill:not(.cache-propagation-fill-inflight)")
        .style.width = `${cachedPct}%`;
    el.querySelector(".cache-propagation-fill-inflight").style.width =
        `${cachedPct + inflightContrib}%`;
    const stalledNote = s.stalled > 0 ? ` · ${s.stalled} stalled` : "";
    el.querySelector(".cache-propagation-counts").textContent =
        `${s.fullyCached}/${s.total} cached · ${s.pushing.size} pushing${stalledNote}`;
    el.classList.toggle("stalled", s.stalled > 0);
}

function initPropagationBars() {
    fetch("/api/discovery/stats")
        .then(r => r.json())
        .then(data => {
            const groups = data.displayGroupPropagation || {};
            for (const [displayID, agg] of Object.entries(groups)) {
                propagationState.set(displayID, {
                    total: agg.total, fullyCached: agg.fullyCached,
                    pushing: new Map(),  // clientKey -> percent
                    stalled: agg.stalled, idle: agg.idle,
                });
                renderPropagationBar(displayID);
            }
        });
}

function onCacheProgress(payload) {
    const displayID = payload.displayID;
    if (!displayID) return;
    let s = propagationState.get(displayID);
    if (!s) {
        // Group not yet known -- fetch full stats and re-render
        initPropagationBars();
        return;
    }
    if (payload.status === "cached") {
        s.pushing.delete(payload.clientKey);
        s.fullyCached = Math.min(s.total, s.fullyCached + 1);
    } else if (payload.status === "stalled") {
        s.pushing.delete(payload.clientKey);
        s.stalled = (s.stalled || 0) + 1;
    } else {  // "pushing"
        s.pushing.set(payload.clientKey, payload.percent);
    }
    renderPropagationBar(displayID);
}

// Hook into existing SockJS message handler. In admin.html the
// SockJS messages arrive via some onmessage handler -- find that
// and add: case "CACHE_PROGRESS": onCacheProgress(msg.PAYLOAD); break;
```

The implementer will need to find admin.html's existing SockJS message switch and add the `CACHE_PROGRESS` case. Look for `REQUEST ===` or `msg.REQUEST` and follow the pattern.

- [ ] **Step 5: Call `initPropagationBars()` on page load**

In the existing admin.html DOMContentLoaded / window.onload handler, add `initPropagationBars()`.

- [ ] **Step 6: Commit**

```bash
git add admin.html
git commit -m "feat(mosaic): admin.html cache propagation bar (per display group)"
```

---

## Task 6: End-to-end manual verification

**Files:** none — manual.

- [ ] **Step 1: Stop the running server + start with new code**

```powershell
# Find the running server PID, stop it
Get-Process python | Where-Object { (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine -match "server\.py" } | Stop-Process -Force
# Start fresh
python server.py -p 3000 -v
```

(Or use the harness's run_in_background to stay attached.)

- [ ] **Step 2: Open admin.html in a browser, confirm the bar renders**

Navigate to `http://192.168.1.60:3000/admin.html`. Test Group's bar should appear showing the existing fullyCached count (whatever survived the restart).

- [ ] **Step 3: Trigger a `force_push` and watch the bar update live**

In another shell:

```powershell
$body = @{action="force_push"; displayID="Test Group"} | ConvertTo-Json -Compress
Invoke-RestMethod -Uri "http://localhost:3000/api/discovery/configure" -Method POST -ContentType "application/json" -Body $body
```

Expected behaviour in the browser:
- Bar shows existing cached count
- As each push lands, fullyCached increments and the bar grows in 24ths
- During each push, the lighter "in-flight" band visibly extends ahead of the solid bar
- No iPad gets stuck at ~6 MB and timeout-killed (the previous 120s bug)

- [ ] **Step 4: Stall test (optional but recommended)**

In another shell:

```powershell
# Block one iPad's port 22 for 60s, then unblock
$ip = "192.168.1.50"
# Use netsh or a Windows firewall rule to drop outbound TCP/22 to this IP briefly
# (or just unplug its WiFi via the network harness if you have one)
```

Expected: that iPad's bar entry flips to `stalled` within ~30s of the block, and `stalled` count appears in the chip.

- [ ] **Step 5: If everything looks good, tag and announce**

```bash
git log --oneline | head -10
# Confirm the feature commits are landed
```

If issues, file them as next-session work.

---

## Self-review

Spec coverage:
- (1) stall-based push timeout → Task 1 + Task 2 ✓
- (2) per-display-group propagation bar → Task 3 + Task 5 ✓
- Unit tests → Task 4 ✓
- Manual verification → Task 6 ✓

Placeholder check: each task has concrete code blocks for the changes. Two steps (Task 4 Step 3 and Task 5 Step 4 hook-into-existing-handler) reference "find the existing pattern" rather than dictating exact code, because they depend on the existing file's structure that varies by historical contribution. These are explicit signposts, not vague placeholders.

Type consistency: `cachePushProgress` dict keys (`token`, `n`, `bytesSent`, `totalBytes`, `startedMs`, `lastChangeMs`, `status`, `mbps`) used consistently across Task 1 (init), Task 2 (mutation), Task 3 (API exposure), Task 5 (UI consumption).
