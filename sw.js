// MosaicMesh Service Worker. Caches per-iPad MP4 segments on first
// fetch (population is driven by a non-Range fetch() the page issues
// at PRELOAD time -- AppleCoreMedia issues only Range requests for
// <video>, so the SW would never see a full-file 200 OK to cache
// without the page's help). Range requests on subsequent plays hit
// the cache and get sliced from the cached ArrayBuffer.
//
// Spec: docs/superpowers/specs/2026-06-03-media-cache-design.md

const CACHE_NAME = "mosaicmesh-media-v1";
const MEDIA_PATH_RE = /\/media\/[^/]+\/seg_[a-f0-9]+_\d+\.mp4$/;

self.addEventListener("install", e => {
    self.skipWaiting();
});

self.addEventListener("activate", e => {
    e.waitUntil(caches.keys().then(keys =>
        Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ));
    return self.clients.claim();
});

async function fetchAndCache(cache, cacheKey){
    try {
        const full = await fetch(cacheKey);  // no Range header on this request
        if (full.ok) await cache.put(cacheKey, full);
    } catch (_) { /* best-effort */ }
}

async function serveRangeFromCached(cachedResp, req){
    const range = req.headers.get("Range");
    if (!range) return cachedResp;
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

self.addEventListener("fetch", e => {
    const url = new URL(e.request.url);
    if (!MEDIA_PATH_RE.test(url.pathname)) return;
    const cacheKey = new Request(url.toString());
    e.respondWith(
        caches.open(CACHE_NAME).then(cache =>
            cache.match(cacheKey).then(hit => {
                if (hit) return serveRangeFromCached(hit, e.request);
                fetchAndCache(cache, cacheKey);  // fire-and-forget
                return fetch(e.request);
            })
        )
    );
});
