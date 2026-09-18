#ifndef MMCACHEFILE_H
#define MMCACHEFILE_H
/* mmcachefile.h — pure decision logic for the mmcache fetch fast path (audit 1.15).
 * Header-only C99. NO SYSCALLS, deliberately: see the symbol note below. Host-compiles
 * for tests/test_mmcachefile.c the way mmurl.h does.
 *
 * WHY THIS EXISTS:
 *   The JS cache map `_present` (js/mmCacheBackendMmvideo.js) starts EMPTY on every page
 *   load and is filled only by __mmCacheDone for fetches completed in that page session;
 *   nothing enumerates the cache dir at startup. The native fetch op had no existing-file
 *   check either, so EVERY RELOAD RE-DOWNLOADED EVERY SEGMENT — ~440 MB across the fleet
 *   at 19 MB segments, throttled 3-at-a-time, 12+ minutes of screens streaming centrally
 *   or hanging into wd-dead (a permanently dark screen).
 *
 * WHY PRESENCE IS SUFFICIENT (do not weaken without re-reading mm_download):
 *   The writer rejects any transfer whose byte count != expectedContentLength BEFORE
 *   writing, then writes with writeToFile:atomically: (flag 1). A partial file can never
 *   appear in the cache dir, so presence implies the file was verified complete.
 *   The one shape that could exist WITHOUT that verified write is a zero-byte file (stray
 *   touch, truncated restore) — hence the non-empty rule. Acking an empty file would hand
 *   the player an unplayable src and produce a decode error no retry clears.
 *
 * WHY NO stat() HERE — learned the hard way 2026-09-18:
 *   The first cut of this called stat(). It built clean and passed every check build.sh
 *   runs (no ObjC classes, no C++ unwind, no unresolved builtins) — and then SIGKILLed
 *   Web.app at dyld load on the device, 8 launch attempts in a row, because `_stat` is
 *   the ONE symbol the 5.1-era libSystem would not bind (we compile against the 9.3 SDK).
 *   Restoring the previous dylib launched first try, and an `llvm-nm -u` diff showed
 *   `_stat` as the only added undefined symbol.
 *   RULE FOR THIS FLEET: every undefined symbol in a new build must already appear in the
 *   last known-good dylib. Diff BEFORE deploying. Consequently this header takes the
 *   filesystem facts as ARGUMENTS; Tweak.x obtains them through NSFileManager via
 *   objc_msgSend, which adds no symbol that is not already bound.
 */

#include <stdio.h>
#include <stddef.h>

/* Compose "<dir>/<token>.mp4" into `out`. Returns 1 on success, 0 if the arguments are
 * unusable or the path would not fit — never emit a TRUNCATED path, which can name a
 * different file than intended. `out` is cleared on failure. */
static int mm_cache_path(const char *dir, const char *token, char *out, size_t cap) {
    int n;
    if (!dir || !token || !out || cap == 0) {
        if (out && cap) { out[0] = 0; }
        return 0;
    }
    n = snprintf(out, cap, "%s/%s.mp4", dir, token);
    if (n < 0 || (size_t)n >= cap) {
        out[0] = 0;
        return 0;
    }
    return 1;
}

/* The fast-path rule. Returns the byte count to ack, or 0 meaning "not usable, go
 * download it". A cached segment counts only if it exists, is a regular file, and is
 * non-empty. Sizes beyond LONG range are reported as a miss rather than truncated. */
static long mm_cache_usable_size(int exists, int is_regular, long long size) {
    if (!exists || !is_regular) { return 0; }
    if (size <= 0) { return 0; }
    if (size > 0x7FFFFFFFLL) { return 0; }
    return (long)size;
}

#endif /* MMCACHEFILE_H */
