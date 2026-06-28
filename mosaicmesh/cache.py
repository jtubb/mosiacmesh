"""File-content cache + file-handle pool used by static and media handlers.

Module-level state (the cache dict, file-handle pool dict, eviction limits)
is co-located with the functions that manage it. These functions operate on
filesystem paths — they don't reference server.settings, so no lazy
'import server' is required.
"""
import os
import logging
# File cache with modification time tracking
file_cache = {}
cache_stats = {'hits': 0, 'misses': 0}
# FIFO entry cap (T3.4). Holds the ~60 prewarmed static files plus a media
# working set without evicting the static. Env-overridable for big fleets.
_CACHE_MAX_ENTRIES = int(os.environ.get("MM_FILECACHE_MAX_ENTRIES") or 200)

# File handle pool for range requests
file_handle_pool = {}
pool_max_size = 50


def get_pooled_file_handle(file_path, mode='rb'):
    """Get cached file handle from pool"""
    key = f"{file_path}:{mode}"
    if key not in file_handle_pool:
        if len(file_handle_pool) >= pool_max_size:
            # Close oldest handle
            oldest_key = next(iter(file_handle_pool))
            file_handle_pool[oldest_key].close()
            del file_handle_pool[oldest_key]
        file_handle_pool[key] = open(file_path, mode)
    return file_handle_pool[key]


def release_file(file_path):
    """Close any pooled handle for file_path and drop its cached content, so the
    file can be deleted/replaced. Windows refuses to unlink a file that still has
    an open handle (WinError 32), so a caller about to os.remove a served media
    file MUST release it first. Matches by NORMALIZED path: the serving handler
    pools under a forward-slash relative path ('media/server/videos/x.mp4'),
    while a deleter may pass an os.path.join backslash path on Windows. The pool
    key is '<path>:<mode>' — split the mode off before comparing. Best-effort."""
    target = os.path.normcase(os.path.normpath(file_path))
    for key in list(file_handle_pool.keys()):
        if os.path.normcase(os.path.normpath(key.rsplit(':', 1)[0])) == target:
            try:
                file_handle_pool[key].close()
            except OSError:
                pass
            del file_handle_pool[key]
    for ckey in list(file_cache.keys()):
        if os.path.normcase(os.path.normpath(ckey)) == target:
            del file_cache[ckey]


def close_file_pool():
    """Close all pooled file handles and clear the file cache"""
    for handle in file_handle_pool.values():
        handle.close()
    file_handle_pool.clear()
    file_cache.clear()
    cache_stats['hits'] = 0
    cache_stats['misses'] = 0


def prewarm_static_cache():
    """Pre-populate file_cache with the static assets every iPad fetches
    on page load (index.html + js/*). Avoids blocking the asyncio event
    loop on synchronous open()/read() during a fleet-wide Start burst:
    24 iPads loading the page simultaneously is ~24*5 = ~120 small file
    fetches. Without pre-warming, the first fetch of each file blocks
    the loop while disk I/O happens, serializing the entire burst.
    After this call, get_cached_file() returns pure-dict hits at request
    time.

    Logged with hit count so a misconfigured deploy (missing files) is
    obvious in the startup log."""
    static_files = []
    for name in ('index.html', 'admin.html', 'discovery.html'):
        if os.path.isfile(name):
            static_files.append(name)
    # Walk js/ RECURSIVELY (T3.4): the admin timeline lives under js/timeline/**
    # (ES modules), which a flat os.listdir('js') missed — so the admin's many
    # module fetches all hit cold disk reads on first load. os.walk caches the
    # whole tree (mosiacmesh.js, GoTime.js, and every js/timeline module).
    if os.path.isdir('js'):
        for root, _dirs, files in os.walk('js'):
            for f in files:
                static_files.append(os.path.join(root, f))
    loaded = 0
    for f in static_files:
        if get_cached_file(f) is not None:
            loaded += 1
    logging.info("prewarm_static_cache: %d files cached (%.0f KiB total)",
                 loaded,
                 sum(len(v.get('content', b'')) for v in file_cache.values()) / 1024)


def get_cached_file(file_path):
    """Get file content with caching based on modification time.

    Cache entries are stored as {'content': bytes, 'mtime': float}. This
    function is the only reader/writer of that value format.
    """
    if not os.path.exists(file_path):
        return None
    try:
        mod_time = os.path.getmtime(file_path)

        # Check if file is in cache and not modified
        cached = file_cache.get(file_path)
        if cached is not None and cached['mtime'] == mod_time:
            cache_stats['hits'] += 1
            return cached['content']

        # File not cached or modified - read from disk
        with open(file_path, 'rb') as f:
            data = f.read()
        cache_stats['misses'] += 1
        file_cache[file_path] = {'content': data, 'mtime': mod_time}

        # Limit cache size to prevent memory issues (simple FIFO). T3.4: raised
        # 100 -> 200 because the recursive prewarm now seeds ~60 static files
        # (js/timeline/**); the old cap let served media evict the prewarmed
        # static, reintroducing the cold-read blocking prewarm exists to avoid.
        if len(file_cache) > _CACHE_MAX_ENTRIES:
            oldest_key = next(iter(file_cache))
            del file_cache[oldest_key]

        return data
    except (OSError, IOError):
        return None
