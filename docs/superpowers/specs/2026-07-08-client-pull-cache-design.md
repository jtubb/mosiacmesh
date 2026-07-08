# Client-Pull Local Video Caching — Design

**Status:** Approved 2026-07-08
**Goal:** Replace the server→device SSH segment-push + on-device lighttpd cache model with a **client-pull** model — each display client downloads its own rendered video segment to local storage and plays it from there — behind one client-agnostic coordinator that works across the legacy iPad-1 (iOS-5) fleet and future modern browsers.

## Why

The current cache path is: server renders → `_push_segment_to_cached_clients` SSHes each segment onto the device → the device's **lighttpd** serves it at `http://127.0.0.1:8080` → mmvideo/AVPlayer plays that local-HTTP URL. Three problems:

1. **It doesn't reach dozing screens.** SSH-push to a PSM-asleep iPad fails; observed state left 20/24 devices with 0 cached segments (propagation 0%), so most screens stream from central over WiFi (the WiFi-bound stall).
2. **It's the thread-leak source.** Server→device SSH/VNC to unreachable devices strands Twisted connectors/threads (the 79-thread / ~40 GB thrash). A client *pull* is the device's own **outbound** fetch, which works under PSM exactly like its SockJS traffic.
3. **lighttpd is a whole service to install, configure, keep-alive, and SSH-probe** per device — pure overhead if the client can hold its own local file.

A client-pull download that plays from a local file eliminates the SSH-push, lighttpd, and the cache-capability SSH probe, and kills the leak at its source.

## Global constraints

- **iOS-5 client code is ES5-only** (no `let`/arrow/`class`/`Promise`/`fetch`); jQuery 1.x + SockJS. `mmCache.js` and any client-JS on the display path must honor this. (admin pages may use modern JS.)
- **iOS-5 JS cannot persist a downloaded video to a playable source** — no Service Workers/Cache API/IndexedDB, no JS file writes, and AVPlayer can't play a `blob:` URL. So the iOS-5 *download+store* step is unavoidably **native (mmvideo)**; JS coordinates it.
- **WiFi/AP is saturation-sensitive** — 24 screens pulling a ~10-20 MB segment at once bursts the AP. Concurrency must be bounded server-side.
- No build step; the server serves hand-written HTML/JS. mmvideo is a MobileSubstrate tweak built via Theos/WSL.

## Key decisions (settled during brainstorming)

| # | Decision |
|---|---|
| Scope | Build **both** backends this pass: mmvideo (iOS-5) **and** Cache-API/Service-Worker (modern). |
| Trigger | **Eager** — pre-cache when a group's render reaches READY, **server-throttled** via a bounded rolling window. |
| Straggler at PLAY | **Start the wall without it (option C)**; it joins on `CACHED` by seeking to `GoTime.now() - startEpoch`. |
| Eviction | **Evict-on-supersede** (new token for a group deletes the prior token's file) + a **total-size-cap** backstop. |
| Integration | **Approach 1** — `mmCache.js` is the coordinator; mmvideo and the Cache API are thin "download-to-here / done" primitives. |

## Architecture

```
                         ┌─────────────── server ───────────────┐
   render READY (token T)│  throttle: bounded rolling window (N) │
                         │  PRECACHE{url,token} ──▶  per client   │
                         │  ◀── CACHED{token} / CACHE_FAILED      │
                         │  cache-state per client (readyToDisplay)│
                         └───────────────────────────────────────┘
                                        │  (SockJS, existing)
                         ┌──────────────▼────────────── client ──┐
                         │  js/mmCache.js  (coordinator, ES5)     │
                         │   - PRECACHE handler, ack, retry       │
                         │   - token→state, eviction, localSrc()  │
                         │   - feature-detect backend at boot     │
                         │        ├─ iOS-5  → mmvideo bridge       │
                         │        └─ modern → Cache API + SW       │
                         └────────────────────────────────────────┘
```

### Backend interface (both implement)
- `fetchToCache(url, token) → Promise-like` — download `url`, store it keyed by `token`, resolve on complete / reject on failure. (ES5: use a callback/Deferred shim, not native `Promise`, on the iOS-5 path.)
- `localSrc(token) → string | null` — the playable local source for a cached token, else `null`.
- `evict(token)` — drop the stored copy.
- `has(token) → bool`.

### iOS-5 / mmvideo backend
- **New native primitive** `fetchToCache(url, token)`: an `NSURLSession` (or `NSData`) download → save to `/var/mobile/Media/mmcache/<token>.mp4` → fire a JS callback over the **existing mmvideo.js bridge** (`__mmCacheDone(token)` / `__mmCacheFail(token, reason)`). One download at a time per device (the server throttle bounds fleet concurrency).
- `localSrc(token)` = `file:///var/mobile/Media/mmcache/<token>.mp4`. mmvideo's existing `playerItemWithURL:` plays a `file://` URL unchanged.
- `evict(token)` = unlink the file.

### Modern backend (JS only, zero native)
- Register `js/sw.js` (Service Worker) at boot; it intercepts fetches for cached segment URLs and serves them from the Cache API.
- `fetchToCache(url, token)` = `caches.open('mm-seg').then(c => c.add(url))` (keep a token→URL map for eviction).
- `localSrc(token)` = the original URL (the SW serves it from cache — no `file://` needed).
- `evict(token)` = `caches.open('mm-seg').then(c => c.delete(url))`.

## Protocol / data flow

1. Render for group **G** reaches **READY** with token **T** and per-client segment URLs (unchanged from today's render pipeline).
2. Server feeds G's cache-capable clients through a **bounded rolling window** of **N** (configurable, e.g. 3-4). Each granted client gets `PRECACHE {url, token}`.
3. Client `mmCache.js` calls `backend.fetchToCache(url, token)` → download begins.
4. On success → client sends `CACHED {token}`. Server marks that client cached-for-T, **advances the window** to the next waiting client.
5. On failure → client sends `CACHE_FAILED {token, reason}`. Server retries bounded (e.g. ≤2), else marks it stream-only and advances.
6. At **PREPARE/PLAY**: the client resolves the `<video>` src via `mmCache.localSrc(token)` when cached, else the **central URL** (stream fallback). The coordinated start (see `_release_group`) proceeds with the ready screens; a screen that finishes caching *after* GO joins by computing position from `GoTime.now() - startEpoch` and seeking (behavior **C**).

Server-side cache-state (`readyToDisplay`, `propagationPercent`, `cachedSegments`) is now driven by the `CACHED` acks rather than push-progress polling.

## Eviction, errors, fallback

- **Evict-on-supersede:** a `PRECACHE` for G with a new token T′ → the client evicts G's prior token file first.
- **Size-cap backstop:** total cache dir bounded by a configurable cap (e.g. N MB); evict oldest beyond it. (iPad-1 has ~13 GB free, so the cap is a safety net, not a routine constraint.)
- **Download failure:** bounded server retry; persistent failure → the client streams from central at play (today's auto-downgrade). Never blocks the wall.
- **Uncached at play:** stream fallback (C) — never blocks the coordinated start.

## lighttpd / SSH-push retirement (phased)

- **Phase 1 — coexist.** Build the pull path. A device with the new mmvideo download uses it; a device without it (or a `CACHE_FAILED`) falls back to the existing lighttpd/central path. Prove the pull path on one device (download→`file://`→autoplay spike), then throttled fleet rollout.
- **Phase 2 — retire.** Once the pull path is proven fleet-wide: delete `_push_segment_to_cached_clients` + `_poll_push_progress`, the cache-capability SSH probe (`_probe_cache_capability` / `_maybe_fire_cache_probe`), and the lighttpd onboarding step + config. `cacheMode` semantics collapse to "has an mmCache backend" (feature-detected client-side), removing the SSH-probe entirely.

## Testing

- **Node unit tests** (`tests/unit/js/`) for `mmCache.js` pure logic: token→state transitions, eviction-on-supersede + size-cap, `localSrc` resolution per backend, PRECACHE/CACHED/retry state machine (backend mocked).
- **mmvideo primitive:** a one-device on-wall spike proving `fetchToCache` → `file://` → mmvideo autoplay before any fleet rollout.
- **Fleet rollout:** throttled, with `MEMWATCH threads:` confirming the SSH/VNC leak path no longer grows (Phase 2 removes it entirely).
- **Server:** unit tests for the throttle rolling-window + the `CACHED`/`CACHE_FAILED`/retry accounting and play-gating on ack.

## Out of scope

- Changing the render pipeline / token computation (reused as-is).
- The coordinated-release improvement (separate follow-up; this design only ensures a late-cached straggler can join via the shared clock).
- Audio-fade / effect rendering (unchanged).
