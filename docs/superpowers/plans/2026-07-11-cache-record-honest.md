# Keep cachedSegments Honest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a `CACHE_FAILED` ack remove the seg from `client.cachedSegments` (symmetric with the `CACHED` add), so a failed (re)pull keeps the server's cache record honest.

**Architecture:** One server-side change in `handle_cache_ack` (`mosaicmesh/websocket/legacy.py`): restructure the mark-cached block to run for both `CACHED` and `CACHE_FAILED`, adding on the former and discarding on the latter. Server-only; unit-tested via the existing `test_cache_pull_msg.py` seam.

**Tech Stack:** Python 3, pytest (via `pytest_runner.py`).

## Global Constraints

- **Server-only.** No client change. The client already sends `CACHE_FAILED` on a failed pull.
- **Preserve existing behavior:** `CACHED` still adds the segkey; the throttle-window advance (`win.advance` / next `_send_precache`) still runs for BOTH acks, unchanged.
- **segkey derivation unchanged:** `token[4:]` if it starts with `seg_`, else the token verbatim (so `full_<...>` keys stay verbatim).
- **Run tests via a runner** (never bare `pytest`): `python pytest_runner.py --unit`, or the single file with `python -m pytest tests/unit/test_cache_pull_msg.py -c tests/pytest.ini -v`.

---

### Task 1: `CACHE_FAILED` removes the seg from `cachedSegments`

**Files:**
- Modify: `mosaicmesh/websocket/legacy.py` (the mark-cached block inside `handle_cache_ack`)
- Modify: `tests/unit/test_cache_pull_msg.py` (add one test)

**Interfaces:**
- Consumes: `legacy.handle_cache_ack(msg)` (existing); the test seam `_make_server(sent)` (existing, sets `srv.settings.clients["a"].cachedSegments`).
- Produces: no new symbols — only the behavior of `handle_cache_ack` on a `CACHE_FAILED` ack changes (now discards the segkey).

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_cache_pull_msg.py`, after `test_cache_failed_advances_window_without_marking` (the last test), append:

```python
def test_cache_failed_removes_present_segment(monkeypatch):
    sent = []
    srv = _make_server(sent)
    # Seed the STALE state: the record claims "a" holds T1_0, but the device lost it.
    srv.settings.clients["a"].cachedSegments = {"T1_0"}
    monkeypatch.setattr(legacy, "server", srv, raising=False)
    srv.precache_windows["G1"].start()               # a active
    legacy.handle_cache_ack({"SRC": "a", "REQUEST": "CACHE_FAILED", "PAYLOAD": {"token": "seg_T1_0"}})
    assert "T1_0" not in srv.settings.clients["a"].cachedSegments   # failed pull -> record corrected
    assert sent[0][0] == "b"                                        # still advances the window
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/test_cache_pull_msg.py::test_cache_failed_removes_present_segment -c tests/pytest.ini -v`
Expected: FAIL — `"T1_0"` is still in `cachedSegments` (the current `handle_cache_ack` never removes on `CACHE_FAILED`).

- [ ] **Step 3: Restructure the mark-cached block to add-or-discard**

In `mosaicmesh/websocket/legacy.py`, inside `handle_cache_ack`, replace this block:

```python
    if msg["REQUEST"] == "CACHED":
        # Mark the segment cached on the Client so _per_client_items rewrites its item to
        # the local http://127.0.0.1:8080/<segname>.mp4 URL (the play-from-local path).
        # cachedSegments holds seg keys: 'seg_<rt>_<i>' -> '<rt>_<i>' (strip 'seg_');
        # 'full_<rt>_<i>' stays verbatim (that's the key _per_client_items checks).
        settings = getattr(_server, "settings", None)
        client = settings.clients.get(src) if settings else None
        if client is not None and token:
            segkey = token[4:] if token.startswith("seg_") else token
            cs = getattr(client, "cachedSegments", None)
            if not isinstance(cs, set):
                cs = set(cs) if cs else set()
                client.cachedSegments = cs
            cs.add(segkey)
```

with:

```python
    req = msg["REQUEST"]
    if req in ("CACHED", "CACHE_FAILED"):
        # Keep cachedSegments honest. CACHED -> the device holds the seg, so
        # _per_client_items can rewrite its item to the local
        # http://127.0.0.1:8080/<segname>.mp4 URL. CACHE_FAILED -> the (re)pull failed,
        # so the seg is NOT cached: remove it (a device that lost a local file was
        # previously stuck 'cached' forever, which mis-routed serves to a 404).
        # cachedSegments holds seg keys: 'seg_<rt>_<i>' -> '<rt>_<i>' (strip 'seg_');
        # 'full_<rt>_<i>' stays verbatim (that's the key _per_client_items checks).
        settings = getattr(_server, "settings", None)
        client = settings.clients.get(src) if settings else None
        if client is not None and token:
            segkey = token[4:] if token.startswith("seg_") else token
            cs = getattr(client, "cachedSegments", None)
            if not isinstance(cs, set):
                cs = set(cs) if cs else set()
                client.cachedSegments = cs
            if req == "CACHED":
                cs.add(segkey)
            else:
                cs.discard(segkey)   # discard of an absent key is a safe no-op
```

Leave everything below this block (the `win = getattr(_server, "precache_windows", ...)` advance) exactly as-is — it already runs for both acks.

- [ ] **Step 4: Run the new test + the existing two to verify they pass**

Run: `python -m pytest tests/unit/test_cache_pull_msg.py -c tests/pytest.ini -v`
Expected: PASS — all three:
- `test_cached_ack_marks_segment_and_advances` (CACHED still adds `T1_0` + advances to `b`),
- `test_cache_failed_advances_window_without_marking` (CACHE_FAILED from an empty set stays empty — `discard` no-op — + still advances),
- `test_cache_failed_removes_present_segment` (CACHE_FAILED now removes the present `T1_0`).

- [ ] **Step 5: Run the full unit suite (no regression)**

Run: `python pytest_runner.py --unit`
Expected: PASS (the reliably-passing unit set; nothing else touches `handle_cache_ack`).

- [ ] **Step 6: Commit**

```bash
git add mosaicmesh/websocket/legacy.py tests/unit/test_cache_pull_msg.py
git commit -m "fix(cache): CACHE_FAILED removes the seg from cachedSegments

handle_cache_ack added on CACHED but never removed, so a device that
lost a local file stayed 'cached' forever (mis-routing serves to a
localhost 404). Make CACHE_FAILED discard the segkey (symmetric); with
arm-recache re-confirming on a successful re-pull, the record now
self-corrects lazily on play. Server-only; unit-tested."
```

---

## Deploy note (not a task step)

Server-side only — a server restart picks it up (no client reload, no on-wall sign-off). The
currently-stale `cachedSegments` entries self-correct on subsequent plays: a re-pull that succeeds
re-confirms via `CACHED`, one that fails removes via this new `CACHE_FAILED` path.
