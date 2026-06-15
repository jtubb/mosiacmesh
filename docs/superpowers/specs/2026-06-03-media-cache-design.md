# Per-Device Media Cache — Design

> **Follow-up (2026-06-14):** `cacheMode` is now auto-onboarded via a server-side SSH probe on REGISTER — see `docs/superpowers/specs/2026-06-14-auto-onboard-cache-mode-design.md`.

## Goal

Eliminate the LAN-bandwidth bottleneck when a display group plays SEGMENT video items. Today, every iPad in the group fetches its 100 MB+ per-iPad rendered MP4 from the central server in parallel at PLAY time; measured peak throughput hit 5.3 Gbps for ~90 seconds during a 24-iPad cycle. The shared 802.11 WiFi medium can deliver only ~10 MB/s per iPad under contention, so AppleCoreMedia stalls within a few frames, video doesn't actually play, and drift measurement (the original motivating use case) is impossible at scale.

The fix is to put each iPad's per-iPad MP4 on local storage *before* PLAY, so the iPad reads it without competing for shared bandwidth. Two paths under a unified server contract:

- **iPad-1 / iOS 5.1.1**: a tiny `lighttpd` daemon installed via onboarding serves the cache directory over `http://127.0.0.1:8080/...`. Server `scp`s the per-iPad MP4 in after render.
- **Modern devices** (iOS 11+ / Android / Fire / desktop): a Service Worker registered by the page intercepts `/media/*.mp4` fetches and serves from the W3C Cache API populated at first-fetch time.

Both paths share the same server-side per-device URL routing: when emitting a PLAY payload, the server picks `localhost` URLs for devices that have the file cached, central-server URLs for everyone else.

## Why this design — what we ruled out

Empirical validation in the 2026-06-02 → 2026-06-03 session:

| Approach | Result |
|---|---|
| Pure-WebKit caching on iOS 5 (AppCache, WebSQL, localStorage, HTTP cache) | All capped at ~5–50 MB; 100 MB MP4s don't fit even with user-tap quota expansion |
| `WebKitAllowFileAccessFromFileURLs` + related prefs via plist | No effect — empirically tested, `<video src="file://...">` still rejected as `MEDIA_ERR_SRC_NOT_SUPPORTED` |
| `com.bigboss.patchsafari2` (Saurik's "file:// for MobileSafari") | Hooks `TabDocument`'s navigation policy, not `HTMLMediaElement`'s sub-resource URL gate. Empirically tested side-by-side with HTTP control — same code-4 rejection [[media-probe-needs-known-good-control]] |
| `kr.iolate.simulatetouch` / `kr.iolate.beeappcontrol` etc. | All target iOS 6+ APIs (WKWebView), incompatible with iOS 5.1.1 |
| Cydia Store paid packages (WebOffline, Safari Download Manager, etc.) | All hosted at `cydiastore_*.deb` paths that returned 404 since Cydia Store went defunct ~2018 |
| Custom MobileSubstrate tweak hooking `HTMLMediaElement` | Would work but faces iPhoneOS5.1.sdk acquisition wall (same problem that killed the cancelled autoplay-tweak plan) |
| **`apt-get install lighttpd` from Saurik's repo** | **✅ Works.** `lighttpd 1.4.18-7` is on `apt.saurik.com`, already-configured on every iPad. Standard apt-get install. Side-by-side probe confirmed iOS 5 Safari plays `<video src="http://127.0.0.1:8080/...">` identically to remote HTTP. |
| **Service Worker + Cache API** | Standard W3C, works on iOS 11.3+, Android, Fire OS, desktop. Future-proof for non-iOS-5 hardware. |

The combination of `lighttpd` for iPad-1 and Service Worker for modern devices is the smallest-surface-area solution that empirically works on the current fleet AND scales to future hardware migrations without a rewrite.

## Architecture

```
┌────────────────────────────────────────────────────────────────────────────┐
│                       MosaicMesh central server                            │
│                                                                            │
│  Render pipeline (unchanged)                                               │
│     ↓ writes media/<client>/videos/seg_<encode_ver_hash>_<n>.mp4           │
│                                                                            │
│  Per-device cache state (new)                                              │
│     Client.cacheMode      = "lighttpd-localhost" | "service-worker" | "none" │
│     Client.cachedSegments = set of seg_HASH already on this device         │
│                                                                            │
│  Render-complete hook (new)                                                │
│     For each iPad in cacheMode=lighttpd-localhost:                         │
│       scp /media/<client>/videos/seg_HASH.mp4 → iPad's cache dir           │
│       record seg_HASH in cachedSegments on success                         │
│                                                                            │
│  PLAY payload URL routing (new)                                            │
│     For each media element going in the playlist:                          │
│       if cacheMode == "lighttpd-localhost" and seg_HASH in cachedSegments  │
│         → "http://127.0.0.1:8080/seg_HASH.mp4"                             │
│       else                                                                 │
│         → "http://192.168.1.60:3000/media/<client>/seg_HASH.mp4"           │
│       (modern devices always get the central URL; their SW intercepts)     │
└────────────────────────────────────────────────────────────────────────────┘
                    │ scp push                       │ HTTP (PLAY payload)
                    ▼                                ▼
┌──────────────────────────────┐    ┌─────────────────────────────────────┐
│ iPad-1 / iOS 5.1.1           │    │  Modern device (iOS 11+, Android,   │
│                              │    │   Fire, desktop)                    │
│ /var/mobile/Media/           │    │                                     │
│   MosaicMeshCache/           │    │  Page registers /sw.js              │
│     seg_HASH1.mp4            │    │  ↓                                  │
│     seg_HASH2.mp4            │    │  Service Worker intercepts          │
│     ...                      │    │   /media/*.mp4                      │
│                              │    │  ↓                                  │
│ /usr/sbin/lighttpd           │    │  Cache hit? serve from Cache API    │
│   bound 127.0.0.1:8080       │    │  Cache miss? passthrough + populate │
│   document-root = ↑          │    │                                     │
│   MIME .mp4 = video/mp4      │    │  Cache key = full URL (URL contains │
│   started by LaunchDaemon    │    │   encode_ver in seg_HASH for        │
│   /Library/LaunchDaemons/    │    │   automatic versioning)             │
│     com.mosaicmesh           │    │                                     │
│     .lighttpd.plist          │    │  Cache eviction: explicit on        │
│                              │    │   encode_ver bump; LRU by Cache API │
│ Safari plays from localhost  │    │   on quota pressure                 │
│  -- zero LAN bandwidth       │    │                                     │
└──────────────────────────────┘    └─────────────────────────────────────┘
```

The unifying contract: **the server decides per-device which URL to emit in PLAY payloads.** Devices don't have to do anything special at request time — they just fetch whatever URL the server hands them. The Service Worker on modern devices is transparent (URLs look like normal central-server URLs and the SW interception is invisible to the page); lighttpd on iPad-1 is explicit (URLs point at `127.0.0.1:8080`).

## Components

### 1. Server-side cache-state model (`server.py`)

Add fields to the `Client` class:

```python
class Client:
    # ... existing fields ...
    cacheMode = "none"            # one of: "lighttpd-localhost", "service-worker", "none"
    cachedSegments = set()        # set of seg_HASH currently cached on the device
```

`cacheMode` is set during onboarding (for iPad-1, when lighttpd is installed) or auto-detected during REGISTER (modern devices announce Service Worker support via the SW registration handshake — see Component 5).

`cachedSegments` is updated by:
- The scp-push completion handler (Component 3): adds the hash on successful transfer
- A startup-cleanup pass: removes hashes the device's cache no longer holds (handles iPad reboots that may clear the dir)

Both fields persist to `settings.dat` via the existing jsonpickle path. `Client.__init__` and `migrate_client_objects()` backfill them with defaults.

### 2. Onboarding installs lighttpd + config + LaunchDaemon (`tools/onboard_devices.ps1`)

Three new steps after the existing tweak install (5.4*) block, before the respring (5.5):

**Step 5.4d** — add `lighttpd` to `$DEFAULT_TWEAKS` so it's installed via the existing apt-get path. lighttpd pulls in its standard deps (`bzip2`, `libxml2`, `libxml2-lib`, `pcre`, `sqlite3`, `sqlite3-lib`) automatically from Saurik's repo, already configured on every iPad.

**Step 5.4e** — write the lighttpd config via SSH heredoc (same pattern as the existing Veency / Insomnia plist writes):

Target path: `/etc/lighttpd/lighttpd.conf`

Content:
```
server.modules = ( "mod_indexfile", "mod_dirlisting", "mod_staticfile" )
server.document-root = "/var/mobile/Media/MosaicMeshCache/"
server.bind = "127.0.0.1"
server.port = 8080
server.errorlog = "/var/log/lighttpd-error.log"
server.pid-file = "/var/run/lighttpd.pid"
dir-listing.activate = "disable"
mimetype.assign = (
    ".mp4"  => "video/mp4",
    ".m4v"  => "video/x-m4v",
    ".mov"  => "video/quicktime",
    ".jpg"  => "image/jpeg",
    ".png"  => "image/png",
    ".html" => "text/html",
    ".js"   => "application/javascript",
    ".css"  => "text/css",
    ""      => "application/octet-stream"
)
index-file.names = ( "index.html" )
```

Server bound to `127.0.0.1` only — never accessible from the LAN. The cache dir is created with `mkdir -p /var/mobile/Media/MosaicMeshCache; chown mobile:mobile`.

**Step 5.4f** — write a LaunchDaemon plist at `/Library/LaunchDaemons/com.mosaicmesh.lighttpd.plist` so lighttpd starts at boot and re-launches if killed:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.mosaicmesh.lighttpd</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/sbin/lighttpd</string>
        <string>-D</string>
        <string>-f</string>
        <string>/etc/lighttpd/lighttpd.conf</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardErrorPath</key><string>/var/log/lighttpd-launchd.log</string>
</dict>
</plist>
```

`-D` is lighttpd's "don't daemonize" flag — launchd needs the process to stay in foreground so it can track + restart it. `KeepAlive=true` means launchd auto-restarts if lighttpd ever exits.

After writing both files, `launchctl load /Library/LaunchDaemons/com.mosaicmesh.lighttpd.plist` starts it immediately (in addition to the boot start). On re-roots or re-onboardings, the plist gets overwritten and re-loaded.

After all this, the onboarding step sets `client.cacheMode = "lighttpd-localhost"` on the server side via an admin API call. This is the operator-visible signal that this device is now in the cache fleet.

### 3. Render-complete push hook (`server.py`)

In the existing render pipeline (the async render job that produces `media/<client>/videos/seg_HASH.mp4`), add a post-success step that fans out per-iPad scp pushes for `cacheMode == "lighttpd-localhost"` clients:

```python
async def _push_segment_to_cached_clients(client_key, segment_hash, segment_n):
    """After a render completes, scp the per-iPad mp4 to each iPad that's
    in lighttpd-localhost cache mode. Updates Client.cachedSegments on
    success. Best-effort -- a failed scp just means the PLAY for that
    iPad falls back to the central-server URL until the next render or
    a manual repair."""
    client = settings.clients.get(client_key)
    if not client or client.cacheMode != "lighttpd-localhost":
        return
    src = f"media/{client_key}/videos/seg_{segment_hash}_{segment_n}.mp4"
    dst = f"{SSH_USER}@{client.ip}:/var/mobile/Media/MosaicMeshCache/seg_{segment_hash}_{segment_n}.mp4"
    cmd = (["scp", "-i", SSH_KEY_PATH] + SSH_LEGACY_OPTS + [src, dst])
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode == 0:
            client.cachedSegments.add(f"{segment_hash}_{segment_n}")
            logging.info("cache-push: %s seg_%s -> %s",
                         client_key, segment_hash, client.ip)
        else:
            logging.warning("cache-push failed for %s seg_%s: %s",
                            client_key, segment_hash,
                            (err or b"").decode("utf-8", "replace")[:80])
    except asyncio.TimeoutError:
        proc.kill()
        logging.warning("cache-push timeout for %s seg_%s", client_key, segment_hash)
```

Concurrency: the render pipeline already has `_RENDER_CONCURRENCY` capped at some N; we cap the cache-push concurrency separately at e.g. 4 — too many parallel scp pushes saturate the central server's outbound and re-create the WiFi-contention problem we're trying to solve. (The push is small relative to the play burst because it's serialized over time, and only one iPad's worth at a time.)

This push is called by the render job's success path. If it fails, the iPad's `cachedSegments` doesn't include the hash → next PLAY for that iPad uses the central URL → it works (just at the old slow path) → operator sees `cache-push failed` in logs and can re-run.

### 4. PLAY payload URL routing (`server.py`)

In `_build_media_elements` (which constructs the playlist items broadcast as PRELOAD/PLAY payloads), replace the static URL construction with per-recipient routing. The function currently produces a single list of `mediaElements`; we need to produce per-iPad lists.

The existing `broadcast_to_display_group` already iterates clients to broadcast. Augment the inner loop to compute per-iPad URLs:

```python
def _resolve_media_url(client, item):
    """Pick the right URL for this client given its cacheMode."""
    if item.playmode != "SEGMENT":
        return item.file   # only SEGMENT-rendered files are cached
    seg_key = f"{item.seg_hash}_{item.seg_n}"
    if (client.cacheMode == "lighttpd-localhost"
            and seg_key in client.cachedSegments):
        return f"http://127.0.0.1:8080/seg_{seg_key}.mp4"
    # Fallback: central server URL
    return f"http://{SERVER_HOST}:{SERVER_PORT}/media/{client.clientKey}/seg_{seg_key}.mp4"
```

The PRELOAD/PLAY broadcast becomes per-iPad rather than identical-payload-broadcast. This is a structural change — the server already iterates clients when broadcasting (via `socketmanager`); the new logic substitutes a per-iPad URL into the playlist payload before each send. JSON encoding cost goes up linearly with iPad count, but each payload is small (<5 KB) so it's not a hot path.

### 5. Service Worker for modern devices (new file `sw.js`, modification of `index.html`)

New file: `sw.js` at the repo root, served by `index_handler` like other JS.

```javascript
// MosaicMesh Service Worker -- caches per-iPad MP4 segments on first
// fetch. URLs from the server still look like normal central-server
// URLs (http://192.168.1.60:3000/media/<key>/seg_HASH.mp4); the SW
// just intercepts and serves from Cache API on second-and-later fetches.

const CACHE_NAME = "mosaicmesh-media-v1";
const MEDIA_PATH_RE = /\/media\/[^/]+\/seg_[a-f0-9]+_\d+\.mp4$/;

self.addEventListener("install", e => {
    self.skipWaiting();
});

self.addEventListener("activate", e => {
    // Best-effort eviction of any old cache names (encode_ver bumps create
    // new URLs naturally; this just sweeps no-longer-referenced versions).
    e.waitUntil(caches.keys().then(keys =>
        Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ));
    return self.clients.claim();
});

self.addEventListener("fetch", e => {
    const url = new URL(e.request.url);
    if (!MEDIA_PATH_RE.test(url.pathname)) return;
    // Strip Range header to look up the FULL cached file by canonical URL
    const cacheKey = new Request(url.toString());
    e.respondWith(
        caches.open(CACHE_NAME).then(cache =>
            cache.match(cacheKey).then(hit => {
                if (hit) {
                    // Cache hit: serve the requested range slice from the
                    // full cached body. The Range header (if present) tells
                    // us what slice to return.
                    return serveRangeFromCached(hit, e.request);
                }
                // Cache miss: fetch FULL file (no Range header) in
                // background and populate cache. For this current
                // request, fall through to network so the user isn't
                // blocked on the full download.
                fetchAndCache(cache, cacheKey);
                return fetch(e.request);
            })
        )
    );
});

// Helpers (see PRELOAD prefetch below for the main population path)
async function fetchAndCache(cache, cacheKey){
    try {
        const full = await fetch(cacheKey);  // no Range header
        if (full.ok) await cache.put(cacheKey, full);
    } catch (_) { /* best-effort */ }
}

async function serveRangeFromCached(cachedResp, req){
    const range = req.headers.get("Range");
    if (!range) return cachedResp;  // non-range request: return whole file
    const m = /bytes=(\d+)-(\d*)/.exec(range);
    if (!m) return cachedResp;
    const buf = await cachedResp.arrayBuffer();
    const start = parseInt(m[1], 10);
    const end = m[2] ? Math.min(parseInt(m[2], 10), buf.byteLength - 1) : buf.byteLength - 1;
    return new Response(buf.slice(start, end + 1), {
        status: 206,
        headers: {
            "Content-Type": cachedResp.headers.get("Content-Type") || "video/mp4",
            "Content-Range": `bytes ${start}-${end}/${buf.byteLength}`,
            "Content-Length": String(end - start + 1)
        }
    });
}
```

**Critical detail about the Service Worker path: iOS AppleCoreMedia issues Range requests from the very first byte of a `<video>` source load.** It never makes a non-range `GET` for the file. That means the SW's `fetch` handler would always see `206 Partial Content` responses, never a `200 OK` to cache — and the cache would never populate via the natural traffic path.

The fix is **explicit preload-time prefetch**: the page's JS, on receipt of the PRELOAD message (which arrives before PLAY and lists all media URLs the playlist will reference), issues a non-range `fetch()` for each new URL. That `200 OK` response gets cached. The `<video>` element's later range requests then hit the SW's cache-with-range-slice path above:

```javascript
// In the page's PRELOAD handler (modern-device branch)
function prefetchPlaylistMedia(items) {
    if (!('caches' in window)) return;  // SW not supported -> central URL fetch is fine
    items.forEach(function(it) {
        if (it.playmode !== "SEGMENT") return;
        // Trigger non-range fetch; SW intercepts and caches the 200 OK.
        // We DON'T await; this is fire-and-forget background warming.
        fetch(it.mediaUrl, { mode: 'cors', cache: 'no-store' }).catch(function(){});
    });
}
```

The `cache: 'no-store'` here disables the browser's built-in HTTP cache (which is too small for our files); the SW's Cache API is what we want, and the SW's own put() path is the only thing that touches it.

With this pattern: PRELOAD fires the prefetch, prefetch populates Cache API via SW, then PLAY's `<video src>` load uses Range requests that hit the now-warm cache via the SW's range-slice helper. **The end-to-end path works around the AppleCoreMedia Range-request quirk.**

In `index.html` (after the existing legacy JS — which iOS 5 won't execute past ES5 anyway, so we can use modern syntax in a guarded block):

```html
<script>
// Modern-device Service Worker registration. iOS 5 Safari treats this
// script tag as a no-op because navigator.serviceWorker is undefined
// AND any modern syntax throws SyntaxError before execution. The
// try/catch swallows that so iOS 5 doesn't see a JS error in the page.
try {
    if (navigator && navigator.serviceWorker) {
        navigator.serviceWorker.register('/sw.js').then(function(reg) {
            // Notify the server that this device is in service-worker
            // cache mode. The server then knows to use central-server
            // URLs in PLAY payloads (the SW intercepts transparently).
            sock.send(generateMessage("SRV", "ANNOUNCE_CACHE_MODE",
                                       {"mode": "service-worker"}));
        }).catch(function(){});
    }
} catch (e) { /* iOS 5 -- ignore */ }
</script>
```

Server-side: new SockJS message handler `ANNOUNCE_CACHE_MODE` sets `client.cacheMode = "service-worker"`. iPad-1 never sends this message (its Safari doesn't have `navigator.serviceWorker`), so its `cacheMode` stays at whatever onboarding set it to (`lighttpd-localhost`) or defaults to `"none"`.

### 6. Cache invalidation

The render pipeline already bakes the `encode_ver` constant into each segment's filename (`seg_<encode_ver_hash>_<n>.mp4`). When `encode_ver` bumps (because a render setting changed), new filenames are generated; old files become orphans.

Two cleanups handle this:

**Server-side `cachedSegments` reconciliation**: every render pass, the new push step adds new hashes to `cachedSegments`. A periodic janitor in `process()` (every 5s loop) removes hash entries that no longer correspond to any current playlist's media references, AND sends a "delete this old file" ssh command to the iPad:

```python
async def _reconcile_ipad_cache(client):
    """Remove iPad-local segment files that no longer correspond to any
    current playlist. Best-effort; failures just leave orphans."""
    in_use = set()
    for d in settings.displays.values():
        if client.displayID != d.displayID:
            continue
        for item in (d.mediaElements or []):
            if item.playmode == "SEGMENT":
                in_use.add(f"{item.seg_hash}_{item.seg_n}")
    stale = client.cachedSegments - in_use
    for s in stale:
        cmd = (["ssh", "-i", SSH_KEY_PATH] + SSH_LEGACY_OPTS +
               [f"{SSH_USER}@{client.ip}",
                f"rm -f /var/mobile/Media/MosaicMeshCache/seg_{s}.mp4"])
        # ... spawn + log ...
        client.cachedSegments.discard(s)
```

**Service Worker side**: bump `CACHE_NAME` from `"mosaicmesh-media-v1"` to `"mosaicmesh-media-v2"` when `encode_ver` bumps. The `activate` handler evicts old caches. Done at deploy time, not runtime.

### 7. First-PLAY-after-render fallback

There's a window between "render completes" and "scp finishes" where `client.cachedSegments` doesn't yet include the new hash. If a PLAY fires during that window, `_resolve_media_url` returns the central URL → iPad fetches via WiFi for that one play → next PLAY uses localhost. This is acceptable: only the first PLAY after a fresh render is slow, and only for whichever iPads' scp hasn't completed yet (others are already cached). No special handling needed beyond what's described in Component 4.

## Risk areas + mitigations

1. **lighttpd config on iOS 5 quirks.** The lighttpd 1.4.18 package from Saurik's repo is very old and may have config-syntax differences from the upstream docs. Mitigation: the config we use is empirically validated 2026-06-03 on a real iPad-1. Future maintenance: keep the config minimal so syntax changes don't catch us.

2. **scp throughput vs render frequency.** A 24-iPad fleet × 100 MB = 2.4 GB per render. At ~30 KB/s observed during the lighttpd-install download (a single iPad's WiFi share), 24 parallel scps would each take ~55 minutes to complete the 100 MB. That's not practical for "re-render takes effect quickly." Mitigation: cap push concurrency at 4, accept the staggered ~14-minute rollout, and treat post-render cache state as eventually-consistent. Render frequency is operator-driven and not hot-path.

3. **iPad disk fill.** Per-iPad MP4s are ~100 MB; with multiple playlists worth of segments, disk could fill. iPad-1 has 16-32 GB; comfortably holds ~100 playlists. Mitigation: the `_reconcile_ipad_cache` janitor sweeps unreferenced files; operator monitors via the discovery API showing `cachedSegments` count per iPad.

4. **lighttpd dies / crashes / fails to start.** Mitigation: `KeepAlive=true` in the LaunchDaemon plist makes launchd auto-respawn. If the binary itself is broken (corrupted install), the fallback URL routing means PLAY still works via central server — just slowly.

5. **Service Worker registration race.** On modern devices, the first page load registers the SW; the SW only intercepts subsequent fetches. So the very first PLAY after a hard refresh might not be intercepted. Mitigation: SW's `clients.claim()` activates it immediately on install (skipping the usual wait-for-next-load); plus the page can defer the `ANNOUNCE_CACHE_MODE` send until SW is confirmed active.

6. **Cross-origin on Service Worker side.** The SW is registered for the central server's origin (where the page is loaded from). It intercepts only same-origin fetches. The PLAY payload URLs need to be same-origin (which they are — all central-server URLs). No cross-origin gymnastics.

7. **Per-iPad PLAY payload generation cost.** The change from "one payload broadcast to all" to "per-iPad payload computed before send" adds CPU cost. 24 iPads × ~5 KB JSON encode = ~120 KB total work per PLAY — trivial.

## What this design does NOT cover

- **Sub-100MB media (IMAGES, audio).** Per-iPad image files are tiny (~50–500 KB); the WiFi-saturation cost is negligible. They keep using the central-server URL. The same is true for shared assets (`js/`, `*.html`, `*.css`).
- **First-touch warming.** No "pre-populate the cache during onboarding" step. The cache fills incrementally as render-and-push cycles run. Future enhancement if it becomes painful.
- **Cache statistics endpoint.** A `/api/cache/stats` admin endpoint could show per-iPad cache hit/miss counts and disk usage. Worth adding later but out of scope now.
- **Encryption at rest.** Cache files on iPad are world-readable to mobile UID. Not a concern for our use case (LAN-private, content isn't sensitive). If it ever becomes one, lighttpd config can require basic auth.
- **CDN-style fan-out.** Could imagine a peer-to-peer cache where iPads serve each other. Vastly out of scope.

## Acceptance criteria

1. After onboarding completes against a clean-rooted iPad, `dpkg -l lighttpd` returns the installed package and `launchctl list | grep com.mosaicmesh.lighttpd` shows it running.
2. `ssh root@<ipad-ip> "curl -sI http://127.0.0.1:8080/"` returns an HTTP response (any status — proves the daemon is listening and binding correctly).
3. After a SEGMENT render completes, the server log shows `cache-push: <client-key> seg_<hash> -> <iPad-ip>` for each `cacheMode=lighttpd-localhost` iPad, and `client.cachedSegments` for each contains the new hash.
4. Triggering a PLAY on a display group results in `server.err` showing `media_handler` GETs *only* from non-cached iPads (modern devices and `cacheMode=none`); cached iPads instead show no central-server `/media/` GETs in the relevant window — they fetched from `127.0.0.1:8080` instead.
5. Aggregate central-server outbound during a 24-iPad PLAY drops from ~27 GB / 93 s (current) to ≪ 1 GB (only the un-cached fraction, expected to be near-zero in steady state).
6. Drift measurement via `tools/run_and_collect.py` produces drift samples from ALL 24 iPads (vs. the current 0–2 with the WiFi-saturation bottleneck), with median drift < 100 ms and p90 < 200 ms.
7. On a modern device (Safari iOS 11+ / Chrome desktop / Chrome Android) loading the page, `navigator.serviceWorker.controller` becomes non-null within a few seconds, and the server's `client.cacheMode` for that device flips to `"service-worker"`.
8. Bumping `encode_ver` triggers a render → new filenames → push → old filenames eventually swept; iPads never run out of disk during normal use.
