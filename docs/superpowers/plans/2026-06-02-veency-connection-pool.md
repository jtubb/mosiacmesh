# Veency Connection-Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Eliminate the ~1–3 s `vncdo` subprocess + RFB-handshake-per-tap cost in `_auto_arm_client` by maintaining one persistent VNC connection per iPad in a server-side pool. First tap to a given iPad still pays the cold-connect cost (~1 s); every subsequent tap reuses the open socket (~5–20 ms — single `PointerEvent` write).

**Architecture:** Replace the `vncdo` CLI subprocess in `_auto_arm_client` with calls into `vncdotool.api.connect()` (its Python library API; same package as the CLI). Cache the returned `ThreadedVNCClientProxy` per-iPad in a module-level dict. On any tap failure, evict from the pool so the next attempt re-handshakes. Hook into the existing offline-cleanup path so dead iPads don't leak connections.

**Tech Stack:** Python, asyncio, vncdotool (Twisted-backed library), aiohttp.

**Pivot context:** This work supersedes the cancelled `2026-06-02-mobilesafari-autoplay-tweak.md` plan (see that file's CANCELLED header for the SDK-acquisition reason). The user-gesture gate is still satisfied via synthetic tap; we just made the tap dramatically cheaper.

---

## File structure

- Modify: `requirements.txt` — add `vncdotool>=1.3.0`
- Modify: `server.py`:
  - Add `from vncdotool import api` to imports
  - Add `_veency_pool` + `_veency_lock` module state near VEENCY_PASSWORD (~line 47)
  - Add `_do_tap`, `_get_pooled_vnc`, `_drop_pooled_vnc` helpers near `_auto_arm_client`
  - Rewrite `_auto_arm_client` body (lines ~1718–1755) to use the pool
  - Hook `_drop_pooled_vnc` into `cleanup_old_clients` (~line 453)

No new files. No tests added in this plan (existing test suite covers `_auto_arm_client`'s callers but not its internals; per the project's `tests/README.md` Test Status notes, expanded tweak-internals tests are out of scope here).

---

## Task 1: Add vncdotool to requirements + import it

**Files:**
- Modify: `requirements.txt`
- Modify: `server.py` (one import line)

- [ ] **Step 1: Verify vncdotool is already pip-installed (it is — confirmed 2026-06-02)**

```bash
cd "C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh"
python -c "from vncdotool import api; print('vncdotool', api.__module__, 'OK')"
```

Expected: `vncdotool vncdotool.api OK`. If ImportError, run `pip install vncdotool>=1.3.0` first.

- [ ] **Step 2: Find the existing requirements.txt and where the import block lives in server.py**

```bash
cd "C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh"
cat requirements.txt
grep -nE "^import |^from " server.py | head -20
```

Note where vncdotool would fit alphabetically in requirements.txt, and the line range of the import block in server.py.

- [ ] **Step 3: Add `vncdotool>=1.3.0` to requirements.txt**

Use the Edit tool to append `vncdotool>=1.3.0` to `requirements.txt` (preserve trailing newline). Maintain alphabetical-ish ordering if the existing file is sorted.

- [ ] **Step 4: Add `from vncdotool import api` to server.py imports**

Place it grouped with other third-party imports (e.g., next to `import jsonpickle`, `import aiohttp`). Do NOT put it inside the `if __name__ == '__main__':` block or any function — must be module-level so the rest of the file can reference `api.connect`.

- [ ] **Step 5: Verify server.py still parses**

```bash
cd "C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh"
python -c "import ast; ast.parse(open('server.py').read()); print('OK')"
```

Expected: `OK`. SyntaxError → revert the import.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt server.py
git commit -m "feat(server): add vncdotool Python API import for connection pooling"
```

---

## Task 2: Implement the connection pool + rewrite _auto_arm_client

**Files:**
- Modify: `server.py` (state, helpers, _auto_arm_client body, cleanup hook)

- [ ] **Step 1: Add module-level pool state near VEENCY_PASSWORD**

Find the line `VEENCY_PASSWORD = os.environ.get("MMVNCPW") or "mosaicmesh"` (around line 47). Immediately after it, insert:

```python

# Persistent VNC connections, keyed by client_key. Created lazily on
# first auto-arm; dropped from the pool when the client goes offline
# or on any per-tap failure. Each entry is a ThreadedVNCClientProxy
# from vncdotool.api -- vncdotool runs Twisted's reactor in a
# background thread, and proxy methods are thread-safe to call from
# the asyncio loop via run_in_executor. The lock guards cache
# read-modify-write; the proxy itself has its own internal queuing.
_veency_pool = {}
_veency_lock = asyncio.Lock()
```

(The blank line before the comment is intentional — separates from the constants above.)

- [ ] **Step 2: Add the helper functions immediately above `async def _auto_arm_client`**

Find `async def _auto_arm_client(client_key):` (line ~1718). Insert these helpers RIGHT BEFORE that line:

```python
def _do_tap(proxy, cx, cy):
    """Synchronous worker: move pointer + click button 1. Runs in
    the default ThreadPoolExecutor (offloaded from the asyncio loop
    by _auto_arm_client) because vncdotool's proxy methods block
    on the Twisted reactor's queue dispatch."""
    proxy.mouseMove(cx, cy)
    proxy.mousePress(1)


async def _get_pooled_vnc(client_key, ip):
    """Return a connected ThreadedVNCClientProxy for the given iPad,
    reusing a pooled connection if one exists. First-call cold path:
    full RFB handshake + auth (~1 s LAN). Subsequent calls: dict
    lookup (<1 ms)."""
    async with _veency_lock:
        proxy = _veency_pool.get(client_key)
        if proxy is not None:
            return proxy
    # Cold connect outside the lock so other clients aren't blocked
    # by this iPad's handshake.
    loop = asyncio.get_event_loop()
    proxy = await loop.run_in_executor(
        None,
        lambda: api.connect(f"{ip}::{VEENCY_PORT}",
                            password=VEENCY_PASSWORD,
                            timeout=5))
    async with _veency_lock:
        # Race: another coroutine may have populated the pool while
        # we were handshaking. Their proxy wins; discard ours.
        existing = _veency_pool.get(client_key)
        if existing is not None:
            try:
                await loop.run_in_executor(None, proxy.disconnect)
            except Exception:
                pass
            return existing
        _veency_pool[client_key] = proxy
    return proxy


async def _drop_pooled_vnc(client_key):
    """Evict and disconnect a pooled VNC client. Safe to call when
    the client_key isn't pooled (no-op). Called on per-tap failure
    (so the next attempt re-handshakes) and on client offline
    cleanup (so dead iPads don't leak file descriptors)."""
    async with _veency_lock:
        proxy = _veency_pool.pop(client_key, None)
    if proxy is None:
        return
    try:
        await asyncio.get_event_loop().run_in_executor(None, proxy.disconnect)
    except Exception as e:
        logging.debug("veency pool disconnect for %s: %s", client_key, e)

```

- [ ] **Step 3: Replace the body of `_auto_arm_client`**

The current implementation is at line ~1718:

```python
async def _auto_arm_client(client_key):
    """Deliver one Veency VNC tap (screen centre) to arm an un-armed iOS
    device. Best-effort: missing vncdo / no IP / failure just logs -- the
    PREPARE timeout covers a device that can't be armed.
    ...
    Captures vncdo stderr and checks the exit code so an auth failure
    ... iPad never received the click."""
    if not AUTO_ARM:
        return
    client = settings.clients.get(client_key)
    if not client or not getattr(client, "ip", ""):
        return
    cx = int((getattr(client, "deviceWidth", 0) or 1024) / 2)
    cy = int((getattr(client, "deviceHeight", 0) or 768) / 2)
    target = f"{client.ip}::{VEENCY_PORT}"
    try:
        proc = await asyncio.create_subprocess_exec(
            "vncdo", "-s", target, "-p", VEENCY_PASSWORD,
            "move", str(cx), str(cy), "click", "1",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            raise
        if proc.returncode == 0:
            logging.info("auto-arm: tapped %s at %d,%d", client_key, cx, cy)
        else:
            tail = (err or b"").decode("utf-8", "replace").strip().splitlines()[-2:]
            logging.warning("auto-arm tap rc=%s for %s (%s) at %d,%d: %s",
                            proc.returncode, client_key, target, cx, cy,
                            " | ".join(tail) or "(no stderr)")
    except Exception as e:  # noqa: BLE001
        logging.warning("auto-arm tap failed for %s: %s", client_key, e)
```

Replace it ENTIRELY with:

```python
async def _auto_arm_client(client_key):
    """Deliver one Veency VNC tap (screen centre) to arm an un-armed
    iOS device. Holds one persistent VNC connection per iPad in
    _veency_pool: first tap to an iPad pays the handshake cost
    (~1 s LAN); subsequent taps reuse the open socket
    (~5-20 ms -- single PointerEvent write). On any error the
    pooled connection is dropped and the next attempt
    re-handshakes.

    Replaces the previous vncdo-subprocess implementation
    (~1-3 s/tap regardless of pooling). The user-gesture gate on
    iOS 5 Safari still requires a tap; we just made the tap
    cheap. See docs/superpowers/plans/2026-06-02-veency-connection-pool.md
    for the design.

    Best-effort: missing IP / handshake failure / runtime tap
    failure all just log -- the PREPARE timeout covers a device
    that can't be armed."""
    if not AUTO_ARM:
        return
    client = settings.clients.get(client_key)
    if not client or not getattr(client, "ip", ""):
        return
    cx = int((getattr(client, "deviceWidth", 0) or 1024) / 2)
    cy = int((getattr(client, "deviceHeight", 0) or 768) / 2)
    loop = asyncio.get_event_loop()
    try:
        proxy = await _get_pooled_vnc(client_key, client.ip)
        await loop.run_in_executor(None, _do_tap, proxy, cx, cy)
        logging.info("auto-arm: tapped %s at %d,%d (pooled)",
                     client_key, cx, cy)
    except Exception as e:  # noqa: BLE001
        # Drop the bad connection so the next attempt re-handshakes.
        await _drop_pooled_vnc(client_key)
        logging.warning("auto-arm tap failed for %s: %s", client_key, e)
```

Use the Edit tool with `old_string` = the entire current function (signature + docstring + body), `new_string` = the block above.

- [ ] **Step 4: Hook the pool eviction into cleanup_old_clients**

Find `def cleanup_old_clients(max_age_seconds=24 * 3600):` (around line 453). Locate where it removes a client from `settings.clients` (something like `del settings.clients[key]` or `.pop(key)`). IMMEDIATELY after that line, add:

```python
                asyncio.ensure_future(_drop_pooled_vnc(key))
```

(Match the existing indentation of the deletion line. The `asyncio.ensure_future` schedules the disconnect in the background — we don't await because cleanup_old_clients is sync.)

If you can't find a single deletion line in cleanup_old_clients (e.g., the function uses a different iteration pattern), report NEEDS_CONTEXT with the function's current body — don't guess.

- [ ] **Step 5: Verify server.py parses**

```bash
cd "C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh"
python -c "import ast; ast.parse(open('server.py').read()); print('OK')"
```

Expected: `OK`.

- [ ] **Step 6: Run the unit tests to confirm nothing imports-time-broke**

```bash
cd "C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh"
python pytest_runner.py --unit 2>&1 | tail -15
```

Expected: same pass/fail counts as before this change. If the count changes, investigate.

- [ ] **Step 7: Commit**

```bash
git add server.py
git commit -m "feat(server): connection-pool Veency VNC for ~100x faster auto-arm taps"
```

---

## Task 3: Empirical verification — measure first-tap vs subsequent-tap latency

**Files:** none modified.

- [ ] **Step 1: Stop the currently-running server**

```bash
cd "C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh"
powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -match 'server\\.py' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }"
sleep 2
```

- [ ] **Step 2: Start a fresh server with logging timestamps**

```bash
cd "C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh"
rm -f server.out server.err
nohup python server.py -p 3000 -v > server.out 2> server.err < /dev/null &
sleep 3
powershell.exe -NoProfile -Command "Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort,OwningProcess | Format-Table -AutoSize"
```

Expected: server listening on both `0.0.0.0:3000` and `[::]:3000` (the dual-stack bind).

- [ ] **Step 3: Trigger a Start All on the Test Group (forces Safari reload, which forces NEEDS_ARM emissions)**

```bash
cd "C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh"
python tools/start_all_probe.py "Test Group" 45
```

After the burst, all online iPads should reload Safari and (once their JS detects the video.play() rejection) emit NEEDS_ARM, which triggers `_auto_arm_client` for each. Wait the full 45 seconds for the burst + arms to settle.

- [ ] **Step 4: Extract timing of `auto-arm: tapped` log lines per iPad**

```bash
cd "C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh"
python -c "
import re
from collections import defaultdict
from datetime import datetime
LINE = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3}) INFO auto-arm: tapped (\S+)')
NEEDS = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3}) .* NEEDS_ARM.*SRC.*[\'\"](\S{16,})[\'\"]')
ts_per_key = defaultdict(list)
needs_per_key = {}
for ln in open('server.err', errors='replace'):
    m = LINE.match(ln)
    if m:
        ts = datetime.strptime(f'{m.group(1)}.{m.group(2)}000', '%Y-%m-%d %H:%M:%S.%f')
        ts_per_key[m.group(3)].append(ts)
    m2 = NEEDS.match(ln)
    if m2:
        ts = datetime.strptime(f'{m2.group(1)}.{m2.group(2)}000', '%Y-%m-%d %H:%M:%S.%f')
        needs_per_key.setdefault(m2.group(3), ts)
print('client_key                       NEEDS_ARM->tap  tap-to-tap (subsequent)')
for k in sorted(ts_per_key):
    taps = sorted(ts_per_key[k])
    needs = needs_per_key.get(k)
    first_dt = (taps[0] - needs).total_seconds()*1000 if needs else None
    subs = [(taps[i] - taps[i-1]).total_seconds()*1000 for i in range(1, len(taps))]
    sub_str = f'{min(subs):.0f}-{max(subs):.0f}ms (n={len(subs)})' if subs else 'no-subsequent'
    print(f'  {k:32s} {first_dt:>6.0f}ms       {sub_str}' if first_dt is not None else f'  {k:32s}  no-NEEDS_ARM  {sub_str}')
"
```

Expected: per iPad, the first `NEEDS_ARM`→`auto-arm tapped` delta is ~500-2000ms (cold handshake + RFB negotiation). If any iPad gets a second auto-arm in the same window, the tap-to-tap delta should be 10-200ms (pooled hot path).

If the FIRST tap shows >5000ms, the cold-handshake path has regressed — investigate the `api.connect` call.

If subsequent taps show similar magnitude to first taps (~1000ms), pooling isn't working — likely the pool isn't being read (cache miss every time) or the proxy is being torn down. Inspect `_get_pooled_vnc` for bugs.

- [ ] **Step 5: Inspect the pool size at end-of-test**

```bash
cd "C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh"
# Query the server's pool via a quick inspection: server.py doesn't
# expose a /debug endpoint for the VNC pool, so just verify via
# OS-level FD counting:
powershell.exe -NoProfile -Command "Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue | Where-Object { \$_.RemotePort -eq 5900 } | Measure-Object | Select-Object -ExpandProperty Count"
```

Expected: a count roughly equal to the number of iPads that received an auto-arm (one persistent VNC connection per tapped iPad). If 0, the pool isn't holding connections. If much higher than the iPad count, the pool is leaking.

- [ ] **Step 6: Commit an observation (optional empty commit)**

```bash
cd "C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh"
git commit --allow-empty -m "verify(server): vncdotool pool -- first tap ~Xms cold, subsequent ~Yms hot"
```

(Replace X and Y with the actual observed values from Step 4. Skip the commit if you'd rather keep history tight.)

---

## Acceptance criteria

1. `requirements.txt` lists `vncdotool>=1.3.0`.
2. `server.py` imports `from vncdotool import api` at module top.
3. `_auto_arm_client` no longer spawns the `vncdo` subprocess (verify with `grep -n vncdo server.py` — only docstring/comment references should remain).
4. `_veency_pool` / `_veency_lock` / `_get_pooled_vnc` / `_drop_pooled_vnc` / `_do_tap` are defined.
5. `cleanup_old_clients` evicts removed iPads from the pool.
6. Unit tests pass at the same pass/fail counts as before the change.
7. Empirical: during a fleet Start-All burst, observed `auto-arm: tapped <key> (pooled)` log lines show cold first-tap latencies in the 500-2000ms range and subsequent-tap latencies under 200ms.
