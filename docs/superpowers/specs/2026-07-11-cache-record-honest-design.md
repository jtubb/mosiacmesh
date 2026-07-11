# Keep cachedSegments honest — CACHE_FAILED removes the seg — design

**Date:** 2026-07-11
**Status:** Approved (design), pending plan
**Files:** `mosaicmesh/websocket/legacy.py` (`handle_cache_ack`), `tests/unit/test_cache_pull_msg.py`. Server-side; no client change, no deploy-reload needed for the server logic (restart picks it up).

## Problem

`client.cachedSegments` (the server's per-client record of which segments a device holds) drifts
from on-device reality. `handle_cache_ack` **adds** a segkey on a `CACHED` ack but has **no path
that removes** one — the only removals are the render-supersede prune (server.py:486, drops
*old-token* segs) and a manual `clear_cache`. So when a device genuinely loses a local file
(eviction / the historical [[mmcache-supersede-evicts-siblings]] bug / truncation), the record
stays "cached" forever.

On-wall (2026-07-10) this mismatch hid the whole black-wall problem: the server believed all 24
devices had `seg_0` (so it served them the localhost URL), while the devices didn't (404 -> verr
-> couldn't arm). The record was wrong and nothing corrected it.

With the merged arm-recache + watchdog recache, a stale seg self-corrects on the next play when the
re-pull **succeeds** (`CACHED` re-confirms + the device now actually has it). The one uncorrected
gap: when the re-pull **fails** (`CACHE_FAILED`), the record wrongly stays "cached."

## Goal

Make `CACHE_FAILED` remove the seg from `cachedSegments` (symmetric with the `CACHED` add), so a
failed (re)pull keeps the record honest. Combined with the recache re-confirming on success, the
record then tracks reality lazily — corrected whenever a seg is played/attempted.

## Non-goals

- **No proactive client miss-report** (a new "I lost seg_X" message). The recache's `CACHE_FAILED`
  on a failed pull suffices; a successful pull re-confirms via `CACHED`. (YAGNI.)
- **No proactive fleet reconcile-scan** to re-verify/re-push every client. Out of proportion to the
  remaining (mostly observability) harm, and risks the fleet-scale central herd.
- **No client change.** The client already sends `CACHE_FAILED` on a failed pull (`mmCache.onAck`);
  only the server's handling changes.

## Design

### The change (`handle_cache_ack`, `mosaicmesh/websocket/legacy.py`)

Today the mark-cached logic runs only under `if msg["REQUEST"] == "CACHED":` and does `cs.add(segkey)`.
Restructure it to handle BOTH acks — same segkey derivation + set coercion — then add on `CACHED`
and discard on `CACHE_FAILED`:

```python
    req = msg["REQUEST"]
    if req in ("CACHED", "CACHE_FAILED"):
        settings = getattr(_server, "settings", None)
        client = settings.clients.get(src) if settings else None
        if client is not None and token:
            segkey = token[4:] if token.startswith("seg_") else token
            cs = getattr(client, "cachedSegments", None)
            if not isinstance(cs, set):
                cs = set(cs) if cs else set()
                client.cachedSegments = cs
            if req == "CACHED":
                cs.add(segkey)       # pull succeeded -> the device holds it
            else:
                cs.discard(segkey)   # pull failed -> the seg is NOT cached; keep the record honest
```

The existing throttle-window advance (`win.advance` / next `_send_precache`) stays exactly as-is,
below this block, running for both acks (unchanged).

### Data flow

client re-pull fails (arm-recache / watchdog recache / server-granted PRECACHE) -> client `onFail`
-> `CACHE_FAILED` ack -> `handle_cache_ack` discards the segkey -> record honest ("not cached"). A
later successful pull re-adds it via the `CACHED` path. `discard` on a set lacking the key is a
safe no-op.

## Error handling / edge cases

- **Client absent / no token / `cachedSegments` not a set:** the same guards as the `CACHED` path
  (coerce to a set, no-op if `client is None or not token`). No new failure modes.
- **`discard` of an absent key:** no-op (set semantics) — a `CACHE_FAILED` for a seg that was never
  recorded (e.g. a first-ever pull that failed) correctly leaves the record unchanged.
- **`full_<...>` tokens:** the segkey derivation is unchanged (strip `seg_` only; `full_` verbatim),
  so FULL assets discard by their verbatim key, symmetric with how `CACHED` adds them.

## Testing

`tests/unit/test_cache_pull_msg.py` (existing; mocks `server.settings` with a client having
`cachedSegments`):

- **New — `CACHE_FAILED` removes a present seg:** seed `cachedSegments = {"T1_0"}`, call
  `handle_cache_ack({SRC:"a", REQUEST:"CACHE_FAILED", PAYLOAD:{token:"seg_T1_0"}})`, assert
  `"T1_0" not in cachedSegments`.
- **Regression — existing tests still pass:** `test_cached_ack_marks_segment_and_advances`
  (CACHED still adds) and `test_cache_failed_advances_window_without_marking` (CACHE_FAILED from an
  EMPTY set stays empty — `discard` is a no-op) are unaffected.

Run: `python pytest_runner.py --unit` (or the single file via
`python -m pytest tests/unit/test_cache_pull_msg.py -c tests/pytest.ini -v`).

## Deploy

Server-side only. A server restart picks it up (no client reload). No on-wall sign-off required —
the behavior is fully unit-tested; the record self-corrects on subsequent plays as segs are
attempted.
