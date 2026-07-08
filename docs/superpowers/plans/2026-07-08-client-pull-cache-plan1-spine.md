# Client-Pull Cache — Plan 1: Coordinator + Server Protocol (the tested spine)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the client-agnostic cache coordinator (`js/mmCache.js`) and the server-side pull protocol (throttled `PRECACHE` grants + `CACHED`/`CACHE_FAILED` accounting + play-gate helper), fully unit-tested against a mock backend — the tested spine that Plans 2 (mmvideo backend) and 3 (modern backend + retirement) plug into.

**Architecture:** `mmCache.js` owns all cache logic and dispatches to a registered backend via a fixed interface (`fetchToCache/localSrc/evict/has`); a mock backend drives the Node tests. The server hands rendered segments to cache-capable clients through a bounded rolling window and tracks per-client cached tokens from acks; PLAY reads that state to decide which screens start locally.

**Tech Stack:** ES5 client JS (iPad-1/iOS-5 constraint) + `node --test` (`tests/unit/js/`); Python 3 + the existing `pytest_runner.py` (`tests/unit/`); SockJS message protocol (`mosaicmesh/websocket/legacy.py`).

## Global Constraints

- Client display JS is **ES5 only** — no `let`/`const`/arrow/`class`/`Promise`/`fetch`/template literals. Use callbacks, not Promises, in `mmCache.js`.
- The backend interface is **callback-based**: `fetchToCache(url, token, onDone, onFail)`, `localSrc(token) -> string|null`, `evict(token)`, `has(token) -> bool`.
- The render **token** (existing `render_token`) is the cache key; a new token for a group supersedes the old.
- Fleet WiFi is saturation-sensitive: server pull concurrency is bounded by a rolling window `N` (config default 3).
- Tests: JS via `python pytest_runner.py --js` (or `node --test tests/unit/js/mmcache.test.js`); Python via `python -m pytest tests/unit/test_cache_pull.py -c tests/pytest.ini -v`.
- Messages use the `{SRC,DEST,REQUEST,PAYLOAD}` shape; new REQUESTs: `PRECACHE` (server→client), `CACHED` / `CACHE_FAILED` (client→server).

---

## File Structure

- **Create `js/mmCache.js`** — the coordinator: backend registry, token state map, evict-on-supersede + size-cap, `localSrc`, `handlePrecache` state machine. ES5, no DOM/network of its own.
- **Create `tests/unit/js/mmcache.test.js`** — `node --test` suite; a mock backend records calls and fires callbacks synchronously.
- **Create `mosaicmesh/cache_pull.py`** — server pull orchestration: `PrecacheWindow` (bounded rolling window), per-client cached-token state, `cached_clients(group, token)` gate helper. Pure logic, no aiohttp.
- **Create `tests/unit/test_cache_pull.py`** — pytest for `cache_pull.py`.
- **Modify `mosaicmesh/websocket/legacy.py`** — add `CACHED` / `CACHE_FAILED` REQUEST handlers that call `cache_pull`.
- **Modify `server.py`** — on render READY, call `cache_pull.start_precache(...)`; a thin `_send_precache(client_key, url, token)` broadcast wrapper.

---

## Task 1: mmCache.js backend registry + feature detection

**Files:**
- Create: `js/mmCache.js`
- Test: `tests/unit/js/mmcache.test.js`

**Interfaces:**
- Produces: global `mmCache` with `mmCache.registerBackend(obj)` and `mmCache.backend` (the active backend or `null`); `mmCache._reset()` (test hook).

- [ ] **Step 1: Write the failing test**

```js
// tests/unit/js/mmcache.test.js
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
// mmCache.js attaches to global (ES5 no-module). Load it into this process.
require(path.join(__dirname, '..', '..', '..', 'js', 'mmCache.js'));

function mockBackend() {
  return {
    name: 'mock', fetched: [], evicted: [], store: {},
    fetchToCache: function (url, token, onDone, onFail) { this.fetched.push([url, token]); this.store[token] = url; onDone(token); },
    localSrc: function (token) { return this.store[token] ? ('local://' + token) : null; },
    evict: function (token) { this.evicted.push(token); delete this.store[token]; },
    has: function (token) { return !!this.store[token]; }
  };
}

test('registerBackend sets the active backend', function () {
  mmCache._reset();
  assert.strictEqual(mmCache.backend, null);
  const b = mockBackend();
  mmCache.registerBackend(b);
  assert.strictEqual(mmCache.backend, b);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/mmcache.test.js`
Expected: FAIL — `Cannot find module '.../js/mmCache.js'`.

- [ ] **Step 3: Write minimal implementation**

```js
// js/mmCache.js  — ES5 only. Client-agnostic cache coordinator.
(function (root) {
  var mmCache = {
    backend: null,
    _tokens: {},          // token -> { group: <id> }
    _order: [],           // tokens in insertion order (for size-cap eviction)
    registerBackend: function (b) { mmCache.backend = b; },
    _reset: function () { mmCache.backend = null; mmCache._tokens = {}; mmCache._order = []; }
  };
  root.mmCache = mmCache;
  if (typeof module !== 'undefined' && module.exports) { module.exports = mmCache; }
})(typeof window !== 'undefined' ? window : global);
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/mmcache.test.js`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add js/mmCache.js tests/unit/js/mmcache.test.js
git commit -m "feat(mmCache): backend registry + feature-detect scaffold"
```

---

## Task 2: token state — record, has, markFailed

**Files:**
- Modify: `js/mmCache.js`
- Test: `tests/unit/js/mmcache.test.js`

**Interfaces:**
- Produces: `mmCache.has(token) -> bool`, `mmCache._recordToken(token, group)`, `mmCache.state(token) -> 'cached'|'failed'|'pending'|'none'`.

- [ ] **Step 1: Write the failing test**

```js
test('token state: pending -> cached via backend.has', function () {
  mmCache._reset(); var b = mockBackend(); mmCache.registerBackend(b);
  assert.strictEqual(mmCache.state('T1'), 'none');
  mmCache._recordToken('T1', 'G1');
  assert.strictEqual(mmCache.state('T1'), 'pending');   // recorded, backend has nothing yet
  b.store['T1'] = 'u';                                  // simulate backend cached it
  assert.strictEqual(mmCache.has('T1'), true);
  assert.strictEqual(mmCache.state('T1'), 'cached');
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/mmcache.test.js`
Expected: FAIL — `mmCache._recordToken is not a function`.

- [ ] **Step 3: Write minimal implementation**

Add inside the IIFE, before `root.mmCache = mmCache;`:

```js
  mmCache._recordToken = function (token, group) {
    if (!mmCache._tokens[token]) { mmCache._order.push(token); }
    mmCache._tokens[token] = { group: group, failed: false };
  };
  mmCache.has = function (token) { return !!(mmCache.backend && mmCache.backend.has(token)); };
  mmCache.state = function (token) {
    var t = mmCache._tokens[token];
    if (!t) { return 'none'; }
    if (mmCache.has(token)) { return 'cached'; }
    return t.failed ? 'failed' : 'pending';
  };
  mmCache._markFailed = function (token) { if (mmCache._tokens[token]) { mmCache._tokens[token].failed = true; } };
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/mmcache.test.js`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add js/mmCache.js tests/unit/js/mmcache.test.js
git commit -m "feat(mmCache): per-token state (none/pending/cached/failed)"
```

---

## Task 3: evict-on-supersede

**Files:**
- Modify: `js/mmCache.js`
- Test: `tests/unit/js/mmcache.test.js`

**Interfaces:**
- Consumes: `_recordToken`, `backend.evict`.
- Produces: `mmCache._supersede(group, newToken)` — evicts the group's prior token (calls `backend.evict`), then records the new one.

- [ ] **Step 1: Write the failing test**

```js
test('evict-on-supersede: new token for a group drops the old file', function () {
  mmCache._reset(); var b = mockBackend(); mmCache.registerBackend(b);
  b.store['T1'] = 'u1'; mmCache._recordToken('T1', 'G1');
  mmCache._supersede('G1', 'T2');                       // new token same group
  assert.deepStrictEqual(b.evicted, ['T1']);            // old evicted
  assert.strictEqual(mmCache.state('T2'), 'pending');   // new recorded
  assert.strictEqual(mmCache._tokens['T1'], undefined); // old forgotten
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/mmcache.test.js`
Expected: FAIL — `mmCache._supersede is not a function`.

- [ ] **Step 3: Write minimal implementation**

```js
  mmCache._forget = function (token) {
    delete mmCache._tokens[token];
    var i = mmCache._order.indexOf(token);
    if (i >= 0) { mmCache._order.splice(i, 1); }
  };
  mmCache._supersede = function (group, newToken) {
    var t, tok;
    for (tok in mmCache._tokens) {
      if (mmCache._tokens.hasOwnProperty(tok) && mmCache._tokens[tok].group === group && tok !== newToken) {
        if (mmCache.backend) { mmCache.backend.evict(tok); }
        mmCache._forget(tok);
      }
    }
    mmCache._recordToken(newToken, group);
  };
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/mmcache.test.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add js/mmCache.js tests/unit/js/mmcache.test.js
git commit -m "feat(mmCache): evict-on-supersede per group"
```

---

## Task 4: size-cap backstop

**Files:**
- Modify: `js/mmCache.js`
- Test: `tests/unit/js/mmcache.test.js`

**Interfaces:**
- Consumes: `backend.evict`, backend must expose `size(token) -> bytes`.
- Produces: `mmCache.capBytes` (default `500*1024*1024`), `mmCache._enforceCap()` — evicts oldest tokens until total backend size ≤ cap. Mock backend gains `size`.

- [ ] **Step 1: Write the failing test**

```js
test('size-cap: evicts oldest until under cap', function () {
  mmCache._reset(); var b = mockBackend();
  b.sizes = {}; b.size = function (t) { return b.sizes[t] || 0; };
  mmCache.registerBackend(b); mmCache.capBytes = 100;
  b.store['A']='u'; b.sizes['A']=60; mmCache._recordToken('A','G1');
  b.store['B']='u'; b.sizes['B']=60; mmCache._recordToken('B','G2'); // total 120 > 100
  mmCache._enforceCap();
  assert.deepStrictEqual(b.evicted, ['A']);   // oldest evicted, now 60 <= 100
  assert.strictEqual(mmCache._tokens['A'], undefined);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/mmcache.test.js`
Expected: FAIL — `mmCache._enforceCap is not a function`.

- [ ] **Step 3: Write minimal implementation**

```js
  mmCache.capBytes = 500 * 1024 * 1024;
  mmCache._totalBytes = function () {
    var sum = 0, i, tok;
    if (!mmCache.backend || !mmCache.backend.size) { return 0; }
    for (i = 0; i < mmCache._order.length; i++) { tok = mmCache._order[i]; sum += (mmCache.backend.size(tok) || 0); }
    return sum;
  };
  mmCache._enforceCap = function () {
    while (mmCache._order.length > 1 && mmCache._totalBytes() > mmCache.capBytes) {
      var oldest = mmCache._order[0];
      if (mmCache.backend) { mmCache.backend.evict(oldest); }
      mmCache._forget(oldest);
    }
  };
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/mmcache.test.js`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add js/mmCache.js tests/unit/js/mmcache.test.js
git commit -m "feat(mmCache): size-cap backstop eviction (oldest-first)"
```

---

## Task 5: localSrc resolution

**Files:**
- Modify: `js/mmCache.js`
- Test: `tests/unit/js/mmcache.test.js`

**Interfaces:**
- Produces: `mmCache.localSrc(token) -> string|null` (delegates to backend; `null` if uncached).

- [ ] **Step 1: Write the failing test**

```js
test('localSrc delegates to backend, null when uncached', function () {
  mmCache._reset(); var b = mockBackend(); mmCache.registerBackend(b);
  assert.strictEqual(mmCache.localSrc('T1'), null);
  b.store['T1'] = 'u';
  assert.strictEqual(mmCache.localSrc('T1'), 'local://T1');
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/mmcache.test.js`
Expected: FAIL — `mmCache.localSrc is not a function`.

- [ ] **Step 3: Write minimal implementation**

```js
  mmCache.localSrc = function (token) {
    if (mmCache.backend && mmCache.backend.has(token)) { return mmCache.backend.localSrc(token); }
    return null;
  };
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/mmcache.test.js`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add js/mmCache.js tests/unit/js/mmcache.test.js
git commit -m "feat(mmCache): localSrc resolution"
```

---

## Task 6: handlePrecache state machine (fetch → ack/fail)

**Files:**
- Modify: `js/mmCache.js`
- Test: `tests/unit/js/mmcache.test.js`

**Interfaces:**
- Consumes: `backend.fetchToCache(url, token, onDone, onFail)`, `_supersede`, `_enforceCap`, `_markFailed`.
- Produces: `mmCache.onAck` (settable callback `function(request, payload)` — the client wires it to `sendMsg`); `mmCache.handlePrecache({group, url, token})` — supersede, fetch, then emit `CACHED` or `CACHE_FAILED` via `onAck`, and `_enforceCap` on success.

- [ ] **Step 1: Write the failing test**

```js
test('handlePrecache: success acks CACHED; failure acks CACHE_FAILED', function () {
  mmCache._reset();
  var acks = [];
  mmCache.onAck = function (req, payload) { acks.push([req, payload.token]); };
  // success backend
  var ok = mockBackend(); mmCache.registerBackend(ok);
  mmCache.handlePrecache({ group: 'G1', url: 'http://c/seg', token: 'T1' });
  assert.deepStrictEqual(ok.fetched, [['http://c/seg', 'T1']]);
  assert.deepStrictEqual(acks, [['CACHED', 'T1']]);
  assert.strictEqual(mmCache.state('T1'), 'cached');
  // failure backend
  var bad = mockBackend();
  bad.fetchToCache = function (url, token, onDone, onFail) { onFail(token, 'net'); };
  mmCache.registerBackend(bad); acks.length = 0;
  mmCache.handlePrecache({ group: 'G2', url: 'http://c/seg2', token: 'T2' });
  assert.deepStrictEqual(acks, [['CACHE_FAILED', 'T2']]);
  assert.strictEqual(mmCache.state('T2'), 'failed');
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/unit/js/mmcache.test.js`
Expected: FAIL — `mmCache.handlePrecache is not a function`.

- [ ] **Step 3: Write minimal implementation**

```js
  mmCache.onAck = null;   // client wires this to sendMsg("SRV", req, payload)
  mmCache.handlePrecache = function (msg) {
    if (!mmCache.backend) { if (mmCache.onAck) { mmCache.onAck('CACHE_FAILED', { token: msg.token, reason: 'no-backend' }); } return; }
    mmCache._supersede(msg.group, msg.token);
    mmCache.backend.fetchToCache(msg.url, msg.token,
      function (token) { mmCache._enforceCap(); if (mmCache.onAck) { mmCache.onAck('CACHED', { token: token }); } },
      function (token, reason) { mmCache._markFailed(token); if (mmCache.onAck) { mmCache.onAck('CACHE_FAILED', { token: token, reason: reason || 'err' }); } });
  };
```

- [ ] **Step 4: Run to verify it passes**

Run: `node --test tests/unit/js/mmcache.test.js`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add js/mmCache.js tests/unit/js/mmcache.test.js
git commit -m "feat(mmCache): handlePrecache state machine + ack callbacks"
```

---

## Task 7: server PrecacheWindow (bounded rolling window)

**Files:**
- Create: `mosaicmesh/cache_pull.py`
- Test: `tests/unit/test_cache_pull.py`

**Interfaces:**
- Produces: `class PrecacheWindow(clients: list, n: int)` with `.start() -> list[str]` (first `n` granted keys) and `.advance(done_key: str) -> str|None` (next waiting key granted, or `None` if drained). Never grants more than `n` concurrently.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cache_pull.py
from mosaicmesh.cache_pull import PrecacheWindow

def test_window_grants_bounded_then_advances():
    w = PrecacheWindow(["a", "b", "c", "d"], n=2)
    assert set(w.start()) == {"a", "b"}          # only 2 concurrent
    assert w.advance("a") == "c"                  # a done -> grant c
    assert w.advance("b") == "d"                  # b done -> grant d
    assert w.advance("c") is None                 # nothing left to grant
    assert w.advance("d") is None
    assert w.drained() is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_cache_pull.py -c tests/pytest.ini -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mosaicmesh.cache_pull'`.

- [ ] **Step 3: Write minimal implementation**

```python
# mosaicmesh/cache_pull.py
"""Client-pull cache orchestration: throttled PRECACHE grants + per-client cache
state + the play-gate helper. Pure logic; no aiohttp/SockJS here (server.py wires
the actual sends)."""


class PrecacheWindow:
    """Grants PRECACHE to at most `n` clients at once; advance() releases the next
    as each acks, bounding peak WiFi to n * segment-size."""

    def __init__(self, clients, n=3):
        self._waiting = list(clients)
        self._active = set()
        self._n = max(1, int(n))

    def start(self):
        granted = []
        while self._waiting and len(self._active) < self._n:
            k = self._waiting.pop(0)
            self._active.add(k)
            granted.append(k)
        return granted

    def advance(self, done_key):
        self._active.discard(done_key)
        if self._waiting and len(self._active) < self._n:
            k = self._waiting.pop(0)
            self._active.add(k)
            return k
        return None

    def drained(self):
        return not self._waiting and not self._active
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_cache_pull.py -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/cache_pull.py tests/unit/test_cache_pull.py
git commit -m "feat(cache_pull): bounded rolling-window PRECACHE throttle"
```

---

## Task 8: server per-client cache state + gate helper

**Files:**
- Modify: `mosaicmesh/cache_pull.py`
- Test: `tests/unit/test_cache_pull.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `class CacheState` with `record_cached(client, token)`, `record_failed(client, token)`, `is_cached(client, token) -> bool`, `cached_clients(clients, token) -> list` (the subset ready to start locally — the play gate).

- [ ] **Step 1: Write the failing test**

```python
from mosaicmesh.cache_pull import CacheState

def test_cache_state_tracks_and_gates():
    s = CacheState()
    s.record_cached("a", "T1")
    s.record_cached("b", "T1")
    s.record_failed("c", "T1")
    assert s.is_cached("a", "T1") is True
    assert s.is_cached("c", "T1") is False
    assert set(s.cached_clients(["a", "b", "c", "d"], "T1")) == {"a", "b"}
    # a stale token is not "cached"
    assert s.is_cached("a", "T2") is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_cache_pull.py -c tests/pytest.ini -v`
Expected: FAIL — `ImportError: cannot import name 'CacheState'`.

- [ ] **Step 3: Write minimal implementation**

Append to `mosaicmesh/cache_pull.py`:

```python
class CacheState:
    """Per-client cached token (one live token per client). Ack-driven; replaces
    push-progress polling."""

    def __init__(self):
        self._cached = {}   # client_key -> token
        self._failed = {}   # client_key -> token

    def record_cached(self, client, token):
        self._cached[client] = token
        if self._failed.get(client) == token:
            del self._failed[client]

    def record_failed(self, client, token):
        self._failed[client] = token

    def is_cached(self, client, token):
        return self._cached.get(client) == token

    def cached_clients(self, clients, token):
        return [c for c in clients if self._cached.get(c) == token]
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_cache_pull.py -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/cache_pull.py tests/unit/test_cache_pull.py
git commit -m "feat(cache_pull): per-client cache state + play-gate helper"
```

---

## Task 9: wire CACHED / CACHE_FAILED handlers into msg_response

**Files:**
- Modify: `mosaicmesh/websocket/legacy.py` (add two `elif` branches in `msg_response`, near the existing `ANNOUNCE_CACHE_MODE` handler ~line 171)
- Test: `tests/unit/test_cache_pull_msg.py` (Create)

**Interfaces:**
- Consumes: module-level `server.cache_state` (a `CacheState`) and `server.precache_windows` (`dict[group -> PrecacheWindow]`) — created in Task 10; for this task the test injects them onto a fake `server` module.
- Produces: on `CACHED{token}` → `cache_state.record_cached(SRC, token)` + `window.advance(SRC)` → if a next client is granted, send it `PRECACHE`; on `CACHE_FAILED{token}` → `record_failed` + advance.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cache_pull_msg.py — exercises the handler via a seam, no aiohttp.
import types, mosaicmesh.websocket.legacy as legacy
from mosaicmesh.cache_pull import CacheState, PrecacheWindow

def _make_server(sent):
    srv = types.SimpleNamespace()
    srv.cache_state = CacheState()
    srv.precache_windows = {"G1": PrecacheWindow(["a", "b", "c"], n=1)}
    srv.precache_urls = {"c": "http://c/seg-c"}     # url to grant next
    srv.precache_group = {"a": "G1", "b": "G1", "c": "G1"}
    srv.precache_token = "T1"
    srv._send_precache = lambda key, url, token: sent.append((key, url, token))
    return srv

def test_cached_ack_advances_window_and_grants_next(monkeypatch):
    sent = []
    srv = _make_server(sent)
    monkeypatch.setattr(legacy, "server", srv, raising=False)
    srv.precache_windows["G1"].start()               # a active
    legacy.handle_cache_ack({"SRC": "a", "REQUEST": "CACHED", "PAYLOAD": {"token": "T1"}})
    assert srv.cache_state.is_cached("a", "T1") is True
    assert ("b", None, "T1") not in sent             # b already granted by start? no: n=1
    assert sent == [("b", srv.precache_urls.get("b"), "T1")] or sent[0][0] == "b"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_cache_pull_msg.py -c tests/pytest.ini -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'handle_cache_ack'`.

- [ ] **Step 3: Write minimal implementation**

Add a helper + two `msg_response` branches in `mosaicmesh/websocket/legacy.py`:

```python
def handle_cache_ack(msg):
    """CACHED / CACHE_FAILED from a client: record state, advance that group's
    throttle window, and grant PRECACHE to the next waiting client."""
    import server
    src = msg.get("SRC")
    token = (msg.get("PAYLOAD") or {}).get("token")
    group = getattr(server, "precache_group", {}).get(src)
    if msg["REQUEST"] == "CACHED":
        server.cache_state.record_cached(src, token)
    else:
        server.cache_state.record_failed(src, token)
    win = getattr(server, "precache_windows", {}).get(group)
    if win is not None:
        nxt = win.advance(src)
        if nxt is not None:
            url = getattr(server, "precache_urls", {}).get(nxt)
            server._send_precache(nxt, url, getattr(server, "precache_token", token))
```

And in `msg_response`, add near the `ANNOUNCE_CACHE_MODE` branch:

```python
    elif(msg["REQUEST"] == "CACHED" or msg["REQUEST"] == "CACHE_FAILED"):
        handle_cache_ack(msg)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_cache_pull_msg.py -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/websocket/legacy.py tests/unit/test_cache_pull_msg.py
git commit -m "feat(legacy): CACHED/CACHE_FAILED handlers advance the throttle window"
```

---

## Task 10: server start_precache trigger on render READY

**Files:**
- Modify: `server.py` — add module-level `cache_state = None`, `precache_windows = {}`, `precache_urls = {}`, `precache_group = {}`, `precache_token = None` near the other singletons (~line 149 where `_veency_pool = {}` lives); add `_send_precache(key, url, token)` (a `broadcast_to_client` wrapper) and `start_precache(group, token, client_urls)`.
- Test: `tests/unit/test_start_precache.py` (Create)

**Interfaces:**
- Consumes: `mosaicmesh.cache_pull.PrecacheWindow`, `CacheState`; `broadcast_to_client`.
- Produces: `server.start_precache(group, token, client_urls: dict[key->url])` — builds the window, records urls/group/token, sends `PRECACHE` to the initial grant set. Called from the render-READY path.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_start_precache.py
import types, server

def test_start_precache_sends_only_window(monkeypatch):
    sent = []
    monkeypatch.setattr(server, "_send_precache", lambda k, u, t: sent.append(k), raising=False)
    server.cache_state = server.cache_pull.CacheState()
    server.precache_windows = {}
    server.start_precache("G1", "T1", {"a": "u-a", "b": "u-b", "c": "u-c"}, n=2)
    assert len(sent) == 2                         # only the window's initial grant
    assert server.precache_token == "T1"
    assert server.precache_group["a"] == "G1"
    assert server.precache_urls["a"] == "u-a"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_start_precache.py -c tests/pytest.ini -v`
Expected: FAIL — `AttributeError: module 'server' has no attribute 'start_precache'`.

- [ ] **Step 3: Write minimal implementation**

Add near the singletons in `server.py`:

```python
from mosaicmesh import cache_pull            # add with the other mosaicmesh imports
cache_state = cache_pull.CacheState()
precache_windows = {}                        # group -> PrecacheWindow
precache_urls = {}                           # client_key -> url
precache_group = {}                          # client_key -> group
precache_token = None                        # the token currently being pushed


def _send_precache(client_key, url, token):
    """Broadcast a PRECACHE to one client (client-pull grant)."""
    broadcast_to_client(client_key, {"REQUEST": "PRECACHE",
                                     "PAYLOAD": {"url": url, "token": token}})


def start_precache(group, token, client_urls, n=3):
    """Begin a throttled client-pull for `group`: window of `n`, send PRECACHE to
    the initial grant set; CACHED/CACHE_FAILED acks advance it (see legacy.handle_cache_ack)."""
    global precache_token
    precache_token = token
    for k, u in client_urls.items():
        precache_urls[k] = u
        precache_group[k] = group
    win = cache_pull.PrecacheWindow(list(client_urls.keys()), n=n)
    precache_windows[group] = win
    for k in win.start():
        _send_precache(k, precache_urls.get(k), token)
```

- [ ] **Step 2b/4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_start_precache.py -c tests/pytest.ini -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/unit/test_start_precache.py
git commit -m "feat(server): start_precache trigger + PRECACHE send wrapper"
```

---

## Task 11: hook start_precache into the render-READY path

**Files:**
- Modify: `mosaicmesh/render.py` (or wherever a render entry reaches READY — search `= "READY"` / `state = 'READY'`) to call `server.start_precache(group, token, client_urls)` for cache-capable clients, guarded so a non-`__main__` import (tests) with no clients is a no-op.
- Test: `tests/unit/test_render_precache_hook.py` (Create) — assert the hook calls `start_precache` with the group's cache-capable clients + their per-client segment URLs.

**Interfaces:**
- Consumes: `server.start_precache`, the existing per-client segment URL map (from `_per_client_items` / the render output), `client.cacheMode`.
- Produces: on READY, `start_precache(group, token, {key: local-central-url})` for clients whose `cacheMode != 'none'`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_render_precache_hook.py
import types, server
from mosaicmesh import render

def test_ready_triggers_precache_for_cache_capable(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "start_precache",
                        lambda g, t, urls, **kw: calls.append((g, t, dict(urls))), raising=False)
    # minimal fake state: two clients in G1, one cache-capable
    C = lambda cm: types.SimpleNamespace(cacheMode=cm, displayID="G1")
    monkeypatch.setattr(server, "settings",
        types.SimpleNamespace(clients={"a": C("lighttpd-localhost"), "b": C("none")}), raising=False)
    render.notify_precache_on_ready("G1", "T1", {"a": "http://c/seg-a", "b": "http://c/seg-b"})
    assert calls == [("G1", "T1", {"a": "http://c/seg-a"})]   # only 'a' (cache-capable)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_render_precache_hook.py -c tests/pytest.ini -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'notify_precache_on_ready'`.

- [ ] **Step 3: Write minimal implementation**

Add to `mosaicmesh/render.py`:

```python
def notify_precache_on_ready(group, token, client_urls):
    """Called when a group's render reaches READY. Kicks a throttled client-pull for
    the cache-capable members. cacheMode 'none' clients stream centrally (unchanged)."""
    import server
    caps = {}
    for key, url in client_urls.items():
        client = server.settings.clients.get(key)
        if client is not None and getattr(client, "cacheMode", "none") != "none":
            caps[key] = url
    if caps:
        server.start_precache(group, token, caps)
```

Then, at the existing point where a `Display.renders[...]` entry is set to `READY` (search `render.py` for the READY assignment in `render_playlist_for_group_async` / `_encode_group`), call it with the per-client URL map already computed for the push:

```python
    notify_precache_on_ready(display_id, token, client_seg_urls)
```

(If the current code computes URLs only inside `_push_segment_to_cached_clients`, lift that URL map out so both the legacy push and the new pull can use it — a small refactor, not a rewrite.)

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/unit/test_render_precache_hook.py -c tests/pytest.ini -v`
Expected: PASS. Then the full spine suite: `python pytest_runner.py --js` and `python -m pytest tests/unit/test_cache_pull.py tests/unit/test_cache_pull_msg.py tests/unit/test_start_precache.py tests/unit/test_render_precache_hook.py -c tests/pytest.ini -v`.

- [ ] **Step 5: Commit**

```bash
git add mosaicmesh/render.py tests/unit/test_render_precache_hook.py
git commit -m "feat(render): trigger client-pull PRECACHE for cache-capable clients on READY"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** coordinator (Tasks 1-6), server protocol/throttle/ack/gate (7-11). The mmvideo + modern backends and lighttpd retirement are explicitly Plans 2/3 — not gaps.
- **Placeholder scan:** none — every step has runnable code + exact commands. Task 11's "lift the URL map out" is a named, bounded refactor with the call site given.
- **Type consistency:** backend interface (`fetchToCache(url,token,onDone,onFail)`, `localSrc`, `evict`, `has`, `size`) is identical across Tasks 1-6 and the mock; server `CacheState`/`PrecacheWindow` signatures match across Tasks 7-11; message REQUESTs (`PRECACHE`/`CACHED`/`CACHE_FAILED`) consistent client↔server.

## Notes for Plan 2 (mmvideo backend)

`mmCache.js` expects a backend implementing `fetchToCache/localSrc/evict/has/size`. Plan 2 builds the mmvideo native primitive + a JS adapter registering it via `mmCache.registerBackend(...)`, plus the one-device spike and the index.html integration (wire `mmCache.onAck` to `sendMsg`, call `mmCache.handlePrecache` on the `PRECACHE` message, and resolve the `<video>` src via `mmCache.localSrc(token) || centralUrl`).
