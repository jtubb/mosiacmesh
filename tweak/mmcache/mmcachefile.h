#ifndef MMCACHEFILE_H
#define MMCACHEFILE_H
/* mmcachefile.h — "is this segment already on disk?" for the mmcache fetch fast
 * path. Header-only, pure C99 + stat(), no ObjC, so it host-compiles for
 * tests/test_mmcachefile.c the way mmurl.h does.
 *
 * WHY THIS EXISTS — AUDIT-2026-09-15.md 1.13/1.15:
 *   The JS cache map `_present` (js/mmCacheBackendMmvideo.js) starts EMPTY on every
 *   page load and is filled only by __mmCacheDone for fetches completed in that page
 *   session; nothing enumerates the cache dir at startup. And the native fetch op had
 *   no existing-file check — it always re-downloaded. So every reload re-downloaded
 *   every segment. Invisible at 3.5-6 MB segments; at 19 MB it is ~440 MB across the
 *   fleet, throttled 3-at-a-time, 12+ minutes, during which screens stream centrally
 *   (which an iPad-1 cannot sustain at that size) or hang until the poll watchdog
 *   gives up with wd-dead — a permanently dark screen.
 *
 * WHY PRESENCE IS SUFFICIENT (do not weaken this without re-reading mm_download):
 *   The writer rejects any transfer whose byte count != expectedContentLength BEFORE
 *   writing, and then writes with writeToFile:atomically: (flag 1). A partial file can
 *   therefore never appear in the cache directory, so a file being there means it was
 *   verified complete when written. Presence implies validity.
 *
 *   The one shape that could exist WITHOUT having gone through that verified write is
 *   a zero-byte file (a stray touch, a truncated restore), so that is treated as a
 *   MISS. Acking an empty file would hand the player an unplayable src and produce a
 *   decode error no retry clears.
 */

#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>

/* MSVC's CRT (the dev host) spells the regular-file test differently to POSIX. */
#ifndef S_ISREG
#define S_ISREG(m) (((m) & _S_IFMT) == _S_IFREG)
#endif

/* Size in bytes of the already-cached segment for `token`, or 0 for "not usable,
 * go download it". Always writes the composed path to `pathout` when it fits, so
 * the caller can log exactly what was probed.
 *
 * Returns 0 (miss) for: null args, a path that would not fit `cap` (never act on a
 * TRUNCATED path — it can name a different file), a missing entry, a non-regular
 * file, or a zero-byte file. */
static long mm_cached_size(const char *dir, const char *token, char *pathout, size_t cap) {
    struct stat st;
    int n;
    if (!dir || !token || !pathout || cap == 0) {
        return 0;
    }
    n = snprintf(pathout, cap, "%s/%s.mp4", dir, token);
    if (n < 0 || (size_t)n >= cap) {
        if (cap) { pathout[0] = 0; }
        return 0;
    }
    if (stat(pathout, &st) != 0) {
        return 0;
    }
    if (!S_ISREG(st.st_mode)) {
        return 0;
    }
    if (st.st_size <= 0) {
        return 0;
    }
    return (long)st.st_size;
}

#endif /* MMCACHEFILE_H */
