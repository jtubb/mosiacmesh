# Self-healing cache reconciliation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A periodic reconcile in `process()` re-PRECACHEs any online cache-capable client missing its group's current-render segments, so a client that missed the render-time PRECACHE self-heals to local playback instead of streaming centrally.

**Architecture:** New fill-reconcile helpers in `server.py` (near `start_precache`/`precache_windows`/`process()`), reusing `_expected_seg_keys_for_display` (what a client should have), `_client_is_push_eligible` (online+cache-capable+IP), and `start_precache` (throttled send via the per-group `PrecacheWindow`). One shared URL helper (`pull_url_for_seg_key`) in `render.py` keeps render-time and reconcile URLs identical.

**Tech Stack:** Python 3.14, aiohttp, pytest (`python -m pytest -c tests/pytest.ini`).

## Global Constraints

- **DO NOT stage or commit `index.html`.** It has a temporary uncommitted `MMFORCE_TDBG_TEMP` edit for live fleet testing. Every `git add` MUST name exact files — NEVER `git add -A` or `git add .`.
- Server-side Python only; no client JS. `import server` stays side-effect-free.
- Reuse the existing throttle: `start_precache(group, token, client_urls, n=3)` + the per-group `precache_windows[group]` (`PrecacheWindow`). A group's window is "active" iff `group in precache_windows` (drained windows are popped by the process() sweep).
- One URL per client per `start_precache` call (`client_urls: {key -> url}`). The reconcile sends ONE missing seg per client per cycle; multiple missing segs resolve over successive cycles.
- Do NOT change: `notify_precache_on_ready` (render-time trigger), `PrecacheWindow`, the process() window sweep, `_reconcile_ipad_cache` (prune), `_per_client_items`.
- `RENDER_READY == "READY"` (from `mosaicmesh.render`).
- Seg-key format (from `_expected_seg_keys_for_display`, same format as `client.cachedSegments`): SEGMENT `"<token>_<i>"`, FULL `"full_<token>_<i>"`.

---

### Task 1: `pull_url_for_seg_key` URL helper (DRY)

**Files:**
- Modify: `mosaicmesh/render.py` — add helper near the `_encode_group` pull block (~line 825); refactor that block to use it.
- Modify: `server.py:59` — add `pull_url_for_seg_key` to the `from mosaicmesh.render import (...)` block.
- Test: `tests/unit/test_cache_reconcile.py` (new file).

**Interfaces:**
- Produces: `pull_url_for_seg_key(client_key, seg_key) -> str`. FULL keys (`"full_..."`) → shared server asset; SEGMENT keys → per-client warp path.

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_cache_reconcile.py`:

```python
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
from mosaicmesh import render as R


def test_pull_url_for_seg_key_segment():
    assert R.pull_url_for_seg_key("ck1", "abc123_0") == "/media/ck1/videos/seg_abc123_0.mp4"

def test_pull_url_for_seg_key_full():
    assert R.pull_url_for_seg_key("ck1", "full_abc123_2") == "/media/server/videos/full_abc123_2.mp4"

def test_pull_url_for_seg_key_reexported_on_server():
    assert server.pull_url_for_seg_key("ck1", "abc123_0") == "/media/ck1/videos/seg_abc123_0.mp4"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/unit/test_cache_reconcile.py -c tests/pytest.ini -q -k pull_url`
Expected: FAIL — `AttributeError: module 'mosaicmesh.render' has no attribute 'pull_url_for_seg_key'`

- [ ] **Step 3: Implement** — in `mosaicmesh/render.py`, add just above the `_encode_group` pull block (near line 824, before `_pull_urls = {}`):

```python
def pull_url_for_seg_key(client_key, seg_key):
    """Client-pull URL for a cached-segment key. FULL keys ('full_<token>_<i>')
    map to the shared server asset; SEGMENT keys ('<token>_<i>') to the client's
    own per-screen warp. DRY: used by BOTH the render-time PRECACHE trigger and
    the periodic cache reconcile so they build identical URLs."""
    if seg_key.startswith("full_"):
        return "/media/server/videos/%s.mp4" % seg_key
    return "/media/%s/videos/seg_%s.mp4" % (client_key, seg_key)
```

Then refactor the render pull block (currently ~lines 828-830) from:

```python
            _pull_urls = {}
            for _push_key, _push_n in seg_push_targets:
                _pull_urls[_push_key] = "/media/%s/videos/seg_%s_%d.mp4" % (_push_key, token, _push_n)
            for _push_key, _push_n in full_push_targets:
                _pull_urls.setdefault(_push_key, "/media/server/videos/full_%s_%d.mp4" % (token, _push_n))
```

to:

```python
            _pull_urls = {}
            for _push_key, _push_n in seg_push_targets:
                _pull_urls[_push_key] = pull_url_for_seg_key(_push_key, "%s_%d" % (token, _push_n))
            for _push_key, _push_n in full_push_targets:
                _pull_urls.setdefault(_push_key, pull_url_for_seg_key(_push_key, "full_%s_%d" % (token, _push_n)))
```

In `server.py`, add `pull_url_for_seg_key,` to the `from mosaicmesh.render import (...)` block (starts at line 59).

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/unit/test_cache_reconcile.py -c tests/pytest.ini -q -k pull_url`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit** (exact files — NOT index.html)

```bash
git add mosaicmesh/render.py server.py tests/unit/test_cache_reconcile.py
git commit -m "feat(cache): pull_url_for_seg_key helper (DRY render-time + reconcile URLs)"
```

---

### Task 2: `clients_needing_precache` selector

**Files:**
- Modify: `server.py` — add the selector near `start_precache` (~line 183); add `_client_is_push_eligible` to the `from mosaicmesh.render import (...)` block (line 59).
- Test: `tests/unit/test_cache_reconcile.py`

**Interfaces:**
- Consumes: `_expected_seg_keys_for_display` (already in server namespace), `_client_is_push_eligible` (add to import).
- Produces: `clients_needing_precache(display_id) -> dict[str, list[str]]` — `{client_key: sorted(missing seg keys)}` for online, cache-capable, has-IP clients in the group missing ≥1 expected seg. `{}` if the group has no expected segs.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_cache_reconcile.py`:

```python
from mosaicmesh.state import Settings, Display, Client

@pytest.fixture
def fresh_settings():
    prev = getattr(server, "settings", None)
    server.settings = Settings()
    yield server.settings
    server.settings = prev

def _mk_client(did, mode="lighttpd-localhost", online=True, ip="1.2.3.4", cached=None):
    c = Client(); c.displayID = did; c.cacheMode = mode; c.isOnline = online
    c.ip = ip; c.cachedSegments = set(cached or [])
    return c

def test_needing_selects_only_missing_eligible(fresh_settings, monkeypatch):
    monkeypatch.setattr(server, "_expected_seg_keys_for_display",
                        lambda d: {"tok_0", "tok_1"})
    fresh_settings.displays["G1"] = Display()
    fresh_settings.clients["miss"] = _mk_client("G1", cached=["tok_0"])          # missing tok_1
    fresh_settings.clients["full"] = _mk_client("G1", cached=["tok_0", "tok_1"]) # up to date
    fresh_settings.clients["off"]  = _mk_client("G1", online=False, cached=[])   # offline
    fresh_settings.clients["none"] = _mk_client("G1", mode="none", cached=[])    # not cache-capable
    fresh_settings.clients["other"]= _mk_client("G2", cached=[])                 # other group
    out = server.clients_needing_precache("G1")
    assert out == {"miss": ["tok_1"]}

def test_needing_empty_when_no_expected(fresh_settings, monkeypatch):
    monkeypatch.setattr(server, "_expected_seg_keys_for_display", lambda d: set())
    fresh_settings.displays["G1"] = Display()
    fresh_settings.clients["c"] = _mk_client("G1", cached=[])
    assert server.clients_needing_precache("G1") == {}

def test_needing_sorts_multiple_missing(fresh_settings, monkeypatch):
    monkeypatch.setattr(server, "_expected_seg_keys_for_display",
                        lambda d: {"tok_1", "tok_0"})
    fresh_settings.displays["G1"] = Display()
    fresh_settings.clients["c"] = _mk_client("G1", cached=[])
    assert server.clients_needing_precache("G1") == {"c": ["tok_0", "tok_1"]}
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/unit/test_cache_reconcile.py -c tests/pytest.ini -q -k needing`
Expected: FAIL — `AttributeError: module 'server' has no attribute 'clients_needing_precache'`

- [ ] **Step 3: Implement** — in `server.py`, add `_client_is_push_eligible,` to the `from mosaicmesh.render import (...)` block (line 59). Then add near `start_precache` (after it, ~line 198):

```python
def clients_needing_precache(display_id):
    """{client_key: sorted(missing seg keys)} for ONLINE, cache-capable, has-IP
    clients in `display_id` whose cachedSegments lack >=1 of the group's expected
    seg keys (the current renderedToken's segments). Empty if the group expects
    no segs. Used by the periodic cache reconcile."""
    display = settings.displays.get(display_id)
    expected = _expected_seg_keys_for_display(display)
    if not expected:
        return {}
    out = {}
    for key, c in settings.clients.items():
        if getattr(c, "displayID", None) != display_id:
            continue
        if not _client_is_push_eligible(c):
            continue
        cached = set(getattr(c, "cachedSegments", None) or [])
        missing = set(expected) - cached
        if missing:
            out[key] = sorted(missing)
    return out
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/unit/test_cache_reconcile.py -c tests/pytest.ini -q -k needing`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_cache_reconcile.py
git commit -m "feat(cache): clients_needing_precache selector (online+capable+missing segs)"
```

---

### Task 3: `reconcile_group_cache` orchestrator

**Files:**
- Modify: `server.py` — add after `clients_needing_precache`.
- Test: `tests/unit/test_cache_reconcile.py`

**Interfaces:**
- Consumes: `clients_needing_precache` (Task 2), `pull_url_for_seg_key` (Task 1), `precache_windows`, `start_precache`, `RENDER_READY` (add to import if not present — it is via render import).
- Produces: `reconcile_group_cache(display_id) -> None`.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_cache_reconcile.py`:

```python
def test_reconcile_skips_when_window_active(fresh_settings, monkeypatch):
    monkeypatch.setattr(server, "precache_windows", {"G1": object()})
    called = []
    monkeypatch.setattr(server, "start_precache", lambda *a, **k: called.append(a))
    server.reconcile_group_cache("G1")
    assert called == []

def test_reconcile_skips_when_not_ready(fresh_settings, monkeypatch):
    monkeypatch.setattr(server, "precache_windows", {})
    d = Display(); d.renderedToken = "tok"; d.currentPlaylistName = "P"
    d.renders = {"P": {"state": "RENDERING"}}
    fresh_settings.displays["G1"] = d
    called = []
    monkeypatch.setattr(server, "start_precache", lambda *a, **k: called.append(a))
    server.reconcile_group_cache("G1")
    assert called == []

def test_reconcile_starts_precache_one_seg_per_client(fresh_settings, monkeypatch):
    monkeypatch.setattr(server, "precache_windows", {})
    d = Display(); d.renderedToken = "tok"; d.currentPlaylistName = "P"
    d.renders = {"P": {"state": "READY"}}
    fresh_settings.displays["G1"] = d
    monkeypatch.setattr(server, "clients_needing_precache",
                        lambda did: {"ck1": ["tok_0", "tok_1"], "ck2": ["tok_0"]})
    calls = []
    monkeypatch.setattr(server, "start_precache",
                        lambda group, token, urls, **k: calls.append((group, token, urls)))
    server.reconcile_group_cache("G1")
    assert len(calls) == 1
    group, token, urls = calls[0]
    assert group == "G1" and token == "tok"
    # one seg per client (the first, sorted) -> URL via pull_url_for_seg_key
    assert urls == {"ck1": "/media/ck1/videos/seg_tok_0.mp4",
                    "ck2": "/media/ck2/videos/seg_tok_0.mp4"}

def test_reconcile_noop_when_nothing_missing(fresh_settings, monkeypatch):
    monkeypatch.setattr(server, "precache_windows", {})
    d = Display(); d.renderedToken = "tok"; d.currentPlaylistName = "P"
    d.renders = {"P": {"state": "READY"}}
    fresh_settings.displays["G1"] = d
    monkeypatch.setattr(server, "clients_needing_precache", lambda did: {})
    called = []
    monkeypatch.setattr(server, "start_precache", lambda *a, **k: called.append(a))
    server.reconcile_group_cache("G1")
    assert called == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/unit/test_cache_reconcile.py -c tests/pytest.ini -q -k reconcile_`
Expected: FAIL — `AttributeError: module 'server' has no attribute 'reconcile_group_cache'`

- [ ] **Step 3: Implement** — in `server.py`, add after `clients_needing_precache`:

```python
def reconcile_group_cache(display_id):
    """Self-heal: if the group's current render is READY and no PrecacheWindow is
    active, re-PRECACHE ONE missing seg per online cache-capable client that lacks
    it. Skipping while a window is in flight throttles a mass reconnect (3-at-a-
    time via the window); multiple missing segs per client resolve over successive
    process() cycles. Only re-sends EXISTING assets -- never encodes."""
    if display_id in precache_windows:
        return   # a window is draining -- don't clobber
    display = settings.displays.get(display_id)
    if display is None:
        return
    token = getattr(display, "renderedToken", None)
    if not token:
        return
    cur = getattr(display, "currentPlaylistName", None)
    entry = (getattr(display, "renders", None) or {}).get(cur) if cur else None
    if not entry or entry.get("state") != RENDER_READY:
        return
    needing = clients_needing_precache(display_id)
    if not needing:
        return
    client_urls = {}
    for key, missing in needing.items():
        client_urls[key] = pull_url_for_seg_key(key, missing[0])   # one/cycle, sorted
    start_precache(display_id, token, client_urls)
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/unit/test_cache_reconcile.py -c tests/pytest.ini -q -k reconcile_`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_cache_reconcile.py
git commit -m "feat(cache): reconcile_group_cache (READY-gated, window-throttled re-precache)"
```

---

### Task 4: wire the reconcile into `process()`

**Files:**
- Modify: `server.py` — add `_reconcile_all_group_caches()` + call it in `process()` right after the precache-window sweep (~line 2058).
- Test: `tests/unit/test_cache_reconcile.py`

**Interfaces:**
- Consumes: `reconcile_group_cache` (Task 3).
- Produces: `_reconcile_all_group_caches() -> None`.

- [ ] **Step 1: Write the failing test** — append to `tests/unit/test_cache_reconcile.py`:

```python
def test_reconcile_all_calls_each_group_and_isolates_errors(fresh_settings, monkeypatch):
    fresh_settings.displays["G1"] = Display()
    fresh_settings.displays["G2"] = Display()
    seen = []
    def _fake(did):
        seen.append(did)
        if did == "G1":
            raise RuntimeError("boom")   # one group failing must not stop the others
    monkeypatch.setattr(server, "reconcile_group_cache", _fake)
    server._reconcile_all_group_caches()   # must not raise
    assert set(seen) == {"G1", "G2"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_cache_reconcile.py -c tests/pytest.ini -q -k reconcile_all`
Expected: FAIL — `AttributeError: module 'server' has no attribute '_reconcile_all_group_caches'`

- [ ] **Step 3: Implement** — in `server.py`, add near `reconcile_group_cache`:

```python
def _reconcile_all_group_caches():
    """Run the per-group cache fill-reconcile for every display group. Called
    once per process() cycle. Per-group errors are isolated so one bad group
    never stalls the loop."""
    for _did in list(settings.displays.keys()):
        try:
            reconcile_group_cache(_did)
        except Exception as _e:
            logging.error("cache reconcile failed for %s: %s", _did, _e)
```

Then in `process()`, immediately AFTER the precache-window sweep `for` loop (the block that ends with `logging.error("precache window sweep failed ...")`, ~line 2058) and before the "Release any PREPARING groups" comment, add:

```python
    # Fill-reconcile: re-PRECACHE cache-capable clients missing the current
    # render's segments (e.g. offline/reconnecting when the render's PRECACHE
    # fired). Throttled: reconcile_group_cache skips a group whose PrecacheWindow
    # is still draining, so a mass reconnect drains 3-at-a-time.
    _reconcile_all_group_caches()
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_cache_reconcile.py -c tests/pytest.ini -q -k reconcile_all`
Expected: PASS (1 passed)

- [ ] **Step 5: Verify wiring + commit**

Run: `grep -n "_reconcile_all_group_caches()" server.py` → expect two hits (the def + the call in process()).
Run: `python -c "import server; print('import OK')"`

```bash
git add server.py tests/unit/test_cache_reconcile.py
git commit -m "feat(cache): run cache fill-reconcile each process() cycle"
```

---

## Final verification (after all tasks)

- [ ] `python -c "import server; print('OK')"`
- [ ] `python -m pytest tests/unit/test_cache_reconcile.py -c tests/pytest.ini -q` — all pass.
- [ ] `python -m pytest tests/unit/ -c tests/pytest.ini -q` — no regressions.
- [ ] `git status --porcelain index.html` → still ` M` (the tdbg force was NEVER committed).

## Self-Review (plan author)

- **Spec coverage:** URL helper (Task 1) = spec "URL construction"; selector (Task 2) = spec step 3; orchestrator (Task 3) = spec steps 1/2/4 + guards + one-seg-per-client; wiring (Task 4) = spec "fill-reconcile step in process()". Throttle (skip-if-window-active) is in Task 3. READY gate in Task 3.
- **Placeholder scan:** none — every code step carries complete code; tests carry real assertions.
- **Type consistency:** `pull_url_for_seg_key(client_key, seg_key)` used identically in Tasks 1/3; `clients_needing_precache -> {key: sorted-list}` consumed by Task 3's `missing[0]`; `reconcile_group_cache(display_id)` consumed by Task 4.
- **index.html guard:** every `git add` names exact files; final check asserts index.html stayed uncommitted.
