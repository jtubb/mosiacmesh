# MosaicMesh Server Scaling Audit — toward 200+ screens

**Date:** 2026-06-28
**Target:** scale the fleet from ~24 to **200+ browser display clients** (1st-gen iPads over SockJS).
**Method:** read-only audit of the three server subsystems — (A) real-time message/control-loop paths, (B) render + cache-push pipeline, (C) persistence / state / static-file caching / startup.

## The governing constraint

The server is a **single-process `asyncio` loop** (aiohttp + SockJS). At 200 screens the dominant failure mode is **synchronous work blocking the event loop** — any CPU- or IO-bound call run *inline* in a handler stalls *all* clients simultaneously. The fix pattern recurs throughout: move blocking work off the loop (`run_in_executor` / `aiohttp.web.FileResponse` / subprocess), and stop doing O(N) work per event (batch, debounce, index, memoize).

Two anchor numbers:
- `settings.dat` is **~1.75 KB/client** → 42 KB at 24, **~350 KB at 200** — and it is jsonpickle-encoded *whole* on every save.
- A playlist render fires **one ffmpeg per screen** → 24 today, **200** at target (~500 s wall at concurrency 6).

At 24 clients these costs are invisible; at 200 each one freezes the wall.

---

## Tier 1 — Will break at 200 (fix before scaling)

| # | Problem | Location | Behavior at 200 | Fix |
|---|---|---|---|---|
| T1.1 | Whole-state `jsonpickle.encode` synchronous on the loop. **Corrected:** REGISTER does *not* save per-event (relies on the 50 s periodic save), so the reconnect storm isn't the save bottleneck. The genuine N-scaling save storms are three CHURN paths that each fire ~once per device/render in a boot/calibrate-all burst: cache-probe (`server.py:347–357`), `REPORT_CANVAS` (`legacy.py:383`), render-complete (`render.py:860`). | as noted | each does a full ~100 ms encode (at 200) back-to-back → multi-second cumulative loop freeze during boot/calibration | **DONE** (`a899c8c`): dirty-flag coalescer — `request_save()` (O(1) flag) + `flush_pending_save()` once per `process()` cycle → one encode per window. Operator REST mutations stay synchronous (immediate durability). `run_in_executor` on the encode was rejected: it races a live-mutated `Settings` graph. |
| T1.2 | Per-screen ffmpeg fan-out — O(N×V) processes per render | `render.py:618–735` (`_encode_group`) | 200 ffmpeg/render, ~500 s wall; calibrate-all over P playlists ≈ tens of minutes | One `filter_complex` decode → N perspective outputs in **one** ffmpeg (split → N `perspective`/`scale` chains). Golden/visual-gate the output. |
| T1.3 | Reconnect-storm broadcasts: OPEN+REGISTER fire 3–4 all-sessions broadcasts each | `legacy.py`; `dispatch.py` | 200 iPads reconnect → ~120k socket writes in seconds; loop saturated | **DONE** (`f4c144a`): no admin-session concept needed. `JOIN`/`DEVICE_DISCOVERED` (OPEN) + `DISC` (CLOSE) had **zero consumers** — removed. `CLIENTS_CAME_ONLINE` now batched via `websocket/online_batch.py` (dedupe by clientKey, one `{devices:[…]}` broadcast ~0.5 s later; consumer already iterates the array). |
| T1.4 | Unbounded parallel mDNS on boot | `server.py:1008` (`asyncio.gather` over all IPs; `_mdns_reverse` sleeps 1.5 s in executor) | `process()` blocked ~38 s on first boot (executor pool ~8, 200 tasks each sleeping) | `asyncio.Semaphore(20)` around the per-IP resolve. |
| T1.5 | SSH pile-up — cache probe + `_reconcile_ipad_cache` with no cooldown/in-flight guard | `server.py:361` (probe), `server.py:2354,703–713` (reconcile, every 5 s) | hundreds of `ssh` subprocs stacking each tick; iPad sshd conn-limit → orphans | Per-client **in-flight guard** + 60 s reconcile backoff; **5-min cooldown** on `cacheProbedMs` before re-probing. |
| T1.6 | Synchronous video range/large-file reads on the loop | `server.py` media_handler | 200 concurrent reads serialized → multi-second stalls during PLAY + whole 80MB segments buffered in RAM | **DONE pending on-wall sign-off** (`c513651`): videos / range requests / ≥10MB files now serve via `web.FileResponse` (non-blocking sendfile, no RAM buffer). Empirically header-for-header equal 206s to the old response; open-ended ranges read to EOF (iPad-1 quirk preserved). Tests rewritten to real HTTP round-trips. Residual: iPad-1 UIWebView reaction to the added ETag/Last-Modified needs a real video play before full trust. |

## Tier 2 — High (throughput drains)

| # | Problem | Location | Fix |
|---|---|---|---|
| T2.1 | Cache-push fan-out: O(N×V) coroutines, serial SCP (~200 min at 200 screens) | `render.py:743`; `server.py:374–497` | Pre-filter `seg_push_targets` to **cached + online** clients before `ensure_future`; cap queued push tasks; skip offline. |
| T2.2 | `DeviceDetector.parse()` inline per REGISTER | `legacy.py:226` | `run_in_executor` + cache result on the Client; skip re-detect when UA unchanged. |
| T2.3 | `CACHE_PROGRESS` broadcast to all sessions at poll rate | `server.py:619` | Target admin sessions only. |
| T2.4 | Boot revalidation `os.path.exists` storm — O(P×G×N) syscalls | `render.py:875–928` | One `os.listdir()` per client → set-membership check instead of per-file stat. |

## Tier 3 — Medium (O(N)-per-event cleanups)

| # | Problem | Location | Fix |
|---|---|---|---|
| T3.1 | `broadcast_to_display_group` re-encodes the dict N× | `broadcast.py:86–90` | Encode the payload once; substitute the `DEST` value (UUID string). |
| T3.2 | `handle_client_disconnect` O(N) scan per disconnect | `dispatch.py:40–47` | `session_id → client_key` reverse index (extend `session_store`). |
| T3.3 | Image warps inline (OpenCV) block the loop ~1–4 s/render | `render.py:692–714` | `run_in_executor` for the warp+`imwrite` loop. |
| T3.4 | Static cache: FIFO cap 100 too small; prewarm misses `js/timeline/`; `getmtime` per hit | `cache.py` | Raise cap (size-based); recursive prewarm; cache mtime on an interval. |
| T3.5 | `/api/discovery/stats` double client-scan + redundant seg-key calc | `discovery.py:253–311` | Memoize `_expected_seg_keys_for_display` per request; merge the two scans. |
| T3.6 | `render_token` O(N) numpy alloc per gate check (called O(G×P) on save) | `render.py:360–392` | Cache token per `(display_id, item_sig)`; invalidate on calibration/client change. |
| T3.7 | Startup blocking (`revalidate_renders_on_boot`, sweep) before serving; prewarm runs *after* `site.start()` | `server.py:2384–2421,2468` | Move `prewarm_static_cache` before `site.start()`; run boot revalidation/sweep in an executor. |
| T3.8 | `/api/displays` `_serialize` does 3 redundant passes over clients | `displays.py:44–76` | Single-pass count of clients/online/calibrated. |

## Already well-bounded (do not touch)

- Render-queue outer concurrency cap (`_QUEUE_CONCURRENCY`); the `Semaphore(_RENDER_CONCURRENCY)` inside a render.
- **FULL** mode encodes exactly one shared asset regardless of N.
- Push semaphore (`_PUSH_CONCURRENCY`) — AP-saturation guard tuned on real 24-iPad data.
- Debounce + idempotent enqueue (one save → one job per group, not per screen).
- `save_settings_incremental` hash-skip (no write when unchanged); the empty-overwrite guard.
- `RENDERS_CHANGED` 1/s throttle; `_token_is_live` shared-token delete guard.

---

## Recommended sequencing

1. **Tier 1 safe wins first.** **Done + deployed:** T1.4 (mDNS `Semaphore(20)`) + T1.5 (SSH probe cooldown + reconcile guard) in `dec068b`; T1.1 (save coalescer) in `a899c8c`; T1.3 (drop dead broadcasts + batch `CLIENTS_CAME_ONLINE`) in `f4c144a`. **Implemented, pending on-wall sign-off:** T1.6 (FileResponse streaming) in `c513651`. **Remaining:** T1.2.
2. **T1.2 (render `filter_complex` fan-in)** — the biggest CPU win but the riskiest: the per-screen warped output must match the current per-process output. Build it behind a golden/visual gate (compare a frame hash of old vs new per screen) before switching the default.
3. **Tier 2 / Tier 3** as follow-ups once Tier 1 is deployed and the fleet is larger.

**Structural note (beyond 200):** if the fleet grows past a few hundred, the single-process model itself becomes the ceiling — at that point consider sharding broadcast/serve across worker processes, moving render to a dedicated worker/queue, and per-entity (not whole-state) persistence. Tier 1–3 buy headroom to ~200–300 without that restructure.
