#ifndef MMINFLIGHT_H
#define MMINFLIGHT_H
/* mminflight.h — one downloader per token, ever.
 * Header-only C99: no syscalls, no ObjC, no locks, so it host-compiles for
 * tests/test_mminflight.c the way mmrange.h, mmcachefile.h and mmresume.h do.
 *
 * WHY THIS EXISTS (measured on-wall 2026-09-26):
 *   `mmcache://fetch` was not idempotent. The handler checked only for a FINISHED
 *   <token>.mp4 on disk and then unconditionally dispatch_async_f'd another
 *   mm_download onto the GLOBAL CONCURRENT queue. Nothing tracked work already in
 *   progress, so every re-issued PRECACHE grant for a token that was still
 *   downloading started another downloader on the same .part.
 *
 *   That is not merely wasteful -- it is what made resume unreachable. Every
 *   instance that decides MM_DL_CLEAN removes the shared .part and recreates it
 *   empty WHILE its siblings append to it, so bytes cannot accumulate and
 *   mm_resume_decide never sees a usable partial. Canary 192.168.1.82's log for
 *   seg_4298b787efa0_0 shows the shape exactly: 11 `fetch` lines against 7
 *   terminations, six `fetch` in a row with nothing between them, adjacent
 *   failures at 13,631,488 and at 0 (two live writers racing one file), and not
 *   one `resume ... from=` line, ever. The 64 MB segment finally landed only when
 *   the storm thinned enough to leave a single survivor, which then ran all 62
 *   chunks from zero -- the very thing resume was written to avoid.
 *
 * WHY THE GUARD BELONGS HERE AND NOT IN THE JS:
 *   The duplicate grants are legitimate and must keep arriving. mmCache's
 *   _pending map is per-page-load, so a webclip reload mid-transfer loses it while
 *   the native download keeps running in the same process, and the server re-grants
 *   on schedule. A JS-side guard cannot see across that reload -- and worse, it
 *   would wedge a token permanently if a native->JS eval were ever lost, because
 *   only the eval clears _pending. The tweak survives the reload and clears its own
 *   slot deterministically at the end of mm_download with no eval involved, so the
 *   exclusivity invariant lives here and only here.
 *
 * REFUSE, NEVER EVICT: a full table returns 0 rather than reclaiming a slot.
 *   Evicting a live entry would re-permit a second downloader on a .part that is
 *   still being written -- precisely the bug. Refusing is safe because the server
 *   re-grants, so the download is deferred rather than corrupted.
 *
 * THREADING CONTRACT: touched from the MAIN THREAD ONLY -- claimed in the
 *   UIWebView shouldStartLoadWithRequest: handler, released inside the main-queue
 *   block that already carries done/fail back to JS. Hence no lock, and no new
 *   linker symbol (a static ObjC class or an OSSpinLock would add one, and the
 *   5.1 loader SIGKILLs the dylib over that -- see mmcachefile.h).
 */

#define MM_INFLIGHT_MAX     8     /* concurrent distinct tokens; the fleet uses 1  */
#define MM_INFLIGHT_TOKMAX  128   /* must match dlctx.token so nothing truncates   */

typedef struct {
    char tok[MM_INFLIGHT_MAX][MM_INFLIGHT_TOKMAX];
} mm_inflight;

/* A free slot is an empty string, so zeroing the table is what empties it. */
static void mm_inflight_init(mm_inflight *t) {
    int i, j;
    if (!t) { return; }
    for (i = 0; i < MM_INFLIGHT_MAX; i++) {
        for (j = 0; j < MM_INFLIGHT_TOKMAX; j++) { t->tok[i][j] = 0; }
    }
}

/* Compare two tokens EXACTLY. A prefix is not a match: segment tokens share long
 * prefixes (seg_<rt>_1 vs seg_<rt>_10), and aliasing two real segments into one
 * slot would refuse a download that has to happen. */
static int mm_tok_equal(const char *a, const char *b) {
    if (!a || !b) { return 0; }
    if (!*a || !*b) { return 0; }        /* an empty token names nothing */
    while (*a && *b) { if (*a != *b) { return 0; } a++; b++; }
    return *a == '\0' && *b == '\0';
}

/* Is a download for `tok` already running? */
static int mm_inflight_has(const mm_inflight *t, const char *tok) {
    int i;
    if (!t || !tok || !*tok) { return 0; }
    for (i = 0; i < MM_INFLIGHT_MAX; i++) {
        if (mm_tok_equal(t->tok[i], tok)) { return 1; }
    }
    return 0;
}

/* How many downloads are running. Scanned rather than counted in a field, so the
 * two can never disagree after an unbalanced release. */
static int mm_inflight_count(const mm_inflight *t) {
    int i, n = 0;
    if (!t) { return 0; }
    for (i = 0; i < MM_INFLIGHT_MAX; i++) { if (t->tok[i][0]) { n++; } }
    return n;
}

/* Take the token's slot. Returns 1 when the caller now owns it and must start the
 * download, 0 when it must NOT -- already in flight, unusable token, or no slot.
 * Every 0 has the same meaning to the caller: drop this grant on the floor. */
static int mm_inflight_claim(mm_inflight *t, const char *tok) {
    int i, n;
    if (!t || !tok || !*tok) { return 0; }
    /* Reject rather than truncate: a truncated token would alias two segments into
     * one slot, which is the same aliasing bug as a prefix compare. */
    for (n = 0; tok[n]; n++) { if (n >= MM_INFLIGHT_TOKMAX - 1) { return 0; } }
    if (mm_inflight_has(t, tok)) { return 0; }     /* the duplicate this exists to stop */
    for (i = 0; i < MM_INFLIGHT_MAX; i++) {
        if (!t->tok[i][0]) {
            int j;
            for (j = 0; j < n; j++) { t->tok[i][j] = tok[j]; }
            t->tok[i][n] = 0;
            return 1;
        }
    }
    return 0;                                      /* full: defer, never evict */
}

/* Give the slot back. Returns 1 if the token was held, 0 if it was not -- which is
 * a required no-op, not an error: the HIT fast path answers dispatch_done WITHOUT
 * having claimed anything, and the release lives inside dispatch_done/dispatch_fail
 * so that it cannot be forgotten on any exit path of mm_download. A second release
 * of the same token must also report 0 and free nothing, or it could hand away a
 * slot a NEW claim has since taken. */
static int mm_inflight_release(mm_inflight *t, const char *tok) {
    int i;
    if (!t || !tok || !*tok) { return 0; }
    for (i = 0; i < MM_INFLIGHT_MAX; i++) {
        if (mm_tok_equal(t->tok[i], tok)) {
            t->tok[i][0] = 0;                      /* empty string == free slot */
            return 1;
        }
    }
    return 0;
}

#endif /* MMINFLIGHT_H */
