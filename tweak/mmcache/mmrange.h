#ifndef MMRANGE_H
#define MMRANGE_H
/* mmrange.h — range arithmetic for mmcache's chunked, constant-memory segment
 * download. Header-only C99, no syscalls, no ObjC, so it host-compiles for
 * tests/test_mmrange.c the way mmurl.h does.
 *
 * WHY CHUNKED RANGES (audit 1.13):
 *   mm_download used sendSynchronousRequest:, which materializes the whole body as
 *   one in-memory NSData. On a 247 MB iPad-1 with Web.app idling ~22 MB, that caps a
 *   segment at ~15-20 MB and therefore caps CLIP LENGTH at roughly 2 minutes — a
 *   10-minute render produced 150 MB/screen, which would jetsam the webclip, and
 *   nothing relaunches a dead webclip.
 *
 *   The textbook fix — an async NSURLConnection delegate appending each
 *   didReceiveData: chunk — is NOT available here: a delegate is an object, and this
 *   tweak must define NO ObjC class (a static class SIGKILLs the dylib load on iOS
 *   5.1, and objc_allocateClassPair does not flat-bind — REFINDINGS 11/12).
 *
 *   So instead: keep the synchronous call (already proven to bind) and ask for one
 *   ~1 MB window at a time, appending each to an NSFileHandle. Peak RAM is one
 *   chunk regardless of file size, with no delegate and no new linker symbols. The
 *   server already serves 206 range responses (media_handler does this for iPad-1
 *   video seeking), so the transport is production-exercised.
 *
 * COMPLETENESS IS STILL THE INVARIANT: the caller must accumulate into a TEMP path
 * and rename into the cache dir only once the byte count equals the advertised
 * total. A partial file must never appear there — mmcachefile.h's fast path treats
 * presence as proof of validity.
 */

#include <stdio.h>
#include <stddef.h>

#define MM_RANGE_CHUNK (1024L * 1024L)   /* peak RAM per request */

/* Digits are parsed by hand rather than with sscanf/strtoll ON PURPOSE.
 * Every libSystem function this dylib calls must already appear in the -U
 * flat-bind list in the Makefile; anything new is a symbol the 5.1-era libSystem
 * may refuse to bind, which SIGKILLs the whole tweak at load. That is exactly how
 * an earlier cut of the fast path (which called stat()) took a screen down.
 * Inline digit arithmetic emits no call at all.
 *
 * NOTE the digit CAP rather than an arithmetic overflow check. The obvious guard,
 * `v > (LLONG_MAX - d) / 10`, divides a long long by a runtime-dependent value, and
 * clang lowers 64-bit division to the compiler-rt builtin ___divdi3 — for which this
 * toolchain ships no libclang_rt, so the dylib would SIGKILL at load (the same class
 * of failure mmvideo's mmbuiltins.c exists to work around). Capping at 18 digits
 * makes overflow impossible by construction (10^18 < LLONG_MAX) and needs no
 * division at all. The symbol gate in build.sh caught this; keep it that way. */
#define MM_SCAN_MAX_DIGITS 18

static int mm_scan_ll(const char **pp, long long *out) {
    const char *p = *pp;
    long long v = 0;
    int n = 0;
    while (*p >= '0' && *p <= '9') {
        if (++n > MM_SCAN_MAX_DIGITS) { return 0; }   /* absurd length -> reject */
        v = v * 10 + (*p - '0');
        p++;
    }
    if (!n) { return 0; }
    *pp = p; *out = v;
    return 1;
}

/* Parse a Content-Range reply: "bytes <first>-<last>/<total>".
 * Returns 1 and fills the outs on success, 0 on anything we cannot trust.
 * Rejects a "*" total: without it we cannot verify completeness, and downloading
 * something we cannot check is exactly the bug the length guard exists to stop. */
static int mm_parse_content_range(const char *h, long long *first,
                                  long long *last, long long *total) {
    const char *p = h;
    long long a = 0, b = 0, t = 0;
    if (!h || !first || !last || !total) { return 0; }
    while (*p == ' ' || *p == '\t') { p++; }
    /* unit, case-insensitive */
    { const char *u = "bytes"; while (*u) { char c = *p; if (c >= 'A' && c <= 'Z') { c = (char)(c - 'A' + 'a'); }
        if (c != *u) { return 0; } p++; u++; } }
    if (*p != ' ' && *p != '\t') { return 0; }
    while (*p == ' ' || *p == '\t') { p++; }
    if (!mm_scan_ll(&p, &a)) { return 0; }
    if (*p++ != '-') { return 0; }
    if (!mm_scan_ll(&p, &b)) { return 0; }
    if (*p++ != '/') { return 0; }
    if (!mm_scan_ll(&p, &t)) { return 0; }   /* a "*" total fails here, as intended */
    if (a < 0 || b < a || t <= 0) { return 0; }
    if (b > t - 1) { return 0; }          /* window cannot extend past EOF */
    *first = a; *last = b; *total = t;
    return 1;
}

/* Compose "bytes=<start>-<end>" into `out`. Returns 1, or 0 if it would not fit —
 * never emit a truncated header, which would request the wrong window. */
static int mm_range_header(char *out, size_t cap, long long start, long long end) {
    int n;
    if (!out || cap == 0 || start < 0 || end < start) {
        if (out && cap) { out[0] = 0; }
        return 0;
    }
    n = snprintf(out, cap, "bytes=%lld-%lld", start, end);
    if (n < 0 || (size_t)n >= cap) { out[0] = 0; return 0; }
    return 1;
}

/* Next window to request given how many bytes are already on disk.
 * Returns 1 and sets [start,end] inclusive, or 0 when the download is complete
 * (or the inputs are unusable). The tail is clamped to total-1: asking past EOF
 * makes a server answer 416 and the loop would never finish. */
static int mm_next_range(long long total, long long got, long chunk,
                         long long *start, long long *end) {
    long long e;
    if (!start || !end) { return 0; }
    if (total <= 0 || chunk <= 0 || got < 0) { return 0; }
    if (got >= total) { return 0; }                 /* done, or overrun */
    e = got + (long long)chunk - 1;
    if (e > total - 1) { e = total - 1; }
    *start = got;
    *end = e;
    return 1;
}

#endif /* MMRANGE_H */
