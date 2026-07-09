// js/sw.js — MosaicMesh Service Worker: cache-first for segments pre-cached by
// mmCacheBackendModern (Cache API 'mm-seg'); everything else falls through to network.
var MM_CACHE = 'mm-seg';
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', function (event) {
  event.respondWith(
    caches.open(MM_CACHE).then(function (c) {
      return c.match(event.request).then(function (hit) {
        return hit || fetch(event.request);
      });
    })
  );
});
