#ifndef MMRESUME_H
#define MMRESUME_H
/* mmresume.h — decide whether a partially-downloaded segment can be continued.
 * Header-only C99, no syscalls, no ObjC, so it host-compiles for
 * tests/test_mmresume.c the way mmrange.h and mmcachefile.h do.
 *
 * WHY RESUME EXISTS (measured on-wall 2026-09-20):
 *   mm_download deliberately removed any leftover .part and restarted from zero:
 *   "a leftover .part from an interrupted pull must never be mistaken for
 *   progress". Correct about the danger, wrong about the remedy — it made
 *   progress impossible to accumulate. With a 93 MB per-screen segment on iPad-1
 *   WiFi the transfer takes long enough that a reload, respring or reboot is
 *   near-certain to land inside it, and every one of them threw away everything.
 *
 *   The fleet showed exactly that: two .part files frozen on exact chunk
 *   boundaries (7,340,032 = 7 x 1 MB and 1,048,576 = 1 x 1 MB), zero CACHED and
 *   zero CACHE_FAILED acks for that token across two days, and 26 re-issued
 *   PRECACHE grants that each restarted from nothing. 11 of 24 screens never
 *   held a single byte. Raising the server's ack deadline stopped the thrashing
 *   but could not make the transfer finish — only resume can.
 *
 * WHY A VALIDATOR IS MANDATORY:
 *   Continuing into a file that CHANGED on the server splices two different
 *   videos, and the completeness check cannot catch it — the byte count would
 *   still reach the advertised total. The server sends a strong ETag on every
 *   206, so the client stores it next to the .part and re-checks it on each
 *   chunk. HTTP's If-Range is the textbook mechanism, but this server ignores it
 *   (a bogus validator still yields 206), so the comparison must happen here.
 *
 * COMPLETENESS IS STILL THE INVARIANT (mmrange.h): bytes accumulate in the temp
 * path and are renamed into the cache dir only once the count equals the
 * advertised total. Resume changes where the count STARTS, never that rule.
 */

#define MM_DL_CLEAN   0   /* ignore any partial; download from byte 0          */
#define MM_DL_RESUME  1   /* continue from the partial's current size          */
#define MM_DL_RESTART 2   /* a partial exists but is unusable; discard and redo */

/* Compare two ETag strings EXACTLY. A prefix is not a match: "abc" and "abcd"
 * are different entities, and treating them as equal would splice files. */
static int mm_tag_equal(const char *a, const char *b) {
    if (!a || !b) { return 0; }
    if (!*a || !*b) { return 0; }          /* an empty tag validates nothing */
    while (*a && *b) { if (*a != *b) { return 0; } a++; b++; }
    return *a == '\0' && *b == '\0';
}

/* How should a download start, given what is on disk and what the server says?
 *
 *   part_size  bytes already in the .part (<=0 when absent/empty)
 *   saved_tag  validator stored when those bytes were written (NULL/"" if none)
 *   cur_tag    validator the server just reported; NULL when not yet known
 *              (the first request of a resumed pull has not had a reply yet)
 *   total      full entity size from Content-Range; <=0 when not yet known
 *
 * Deliberately total-tolerant: a not-yet-known total (0 or -1) must never be
 * read as "the partial exceeds the file", or the first resumed request of every
 * pull would discard the very bytes it is trying to continue from. */
static int mm_resume_decide(long long part_size, const char *saved_tag,
                            const char *cur_tag, long long total) {
    if (part_size <= 0) { return MM_DL_CLEAN; }          /* nothing to continue */
    if (!saved_tag || !*saved_tag) { return MM_DL_CLEAN; } /* unverifiable bytes */

    /* Validator known and different => the entity changed under us. */
    if (cur_tag && *cur_tag && !mm_tag_equal(saved_tag, cur_tag)) {
        return MM_DL_RESTART;
    }
    /* A partial at or beyond the full size was never finalized, or is corrupt.
     * Equality counts: a complete file should have been renamed into place, so
     * finding it still as .part means the finalize step did not happen. */
    if (total > 0 && part_size >= total) { return MM_DL_RESTART; }

    return MM_DL_RESUME;
}

#endif /* MMRESUME_H */
