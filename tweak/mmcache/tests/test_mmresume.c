/* Host-compiled unit test for the resume decision in mmresume.h.
 *
 *   make -C tweak/mmcache/tests CC=clang test
 *
 * WHY RESUME EXISTS (measured on-wall 2026-09-20):
 * mm_download deliberately removed any leftover .part and restarted from zero, so
 * every interruption discarded all progress. With a 93 MB per-screen segment on
 * iPad-1 WiFi the transfer window is long enough that a reload, respring or reboot
 * is near-certain to land inside it -- and each one threw the bytes away. Two
 * .part files sat frozen on exact chunk boundaries (7,340,032 = 7 x 1 MB, and
 * 1,048,576 = 1 x 1 MB) with no CACHED and no CACHE_FAILED ever sent, across two
 * days and 26 re-issued PRECACHE grants. Nothing could ever finish.
 *
 * WHY IT NEEDS A VALIDATOR: resuming into a file that changed on the server would
 * splice two different videos together and the size check would still pass. The
 * server sends a strong ETag on every 206, so the client stores it beside the
 * .part and re-checks it on each chunk. (The server ignores If-Range -- a bogus
 * validator still yields 206 -- so the check has to be client-side.)
 */
#include <stdio.h>
#include <string.h>

#include "../mmresume.h"

static int fails = 0;
#define CHECK(cond, msg) do { if (!(cond)) { printf("FAIL: %s\n", (msg)); fails++; } } while (0)

int main(void) {
    /* ---- no usable partial -> start clean ---- */
    CHECK(mm_resume_decide(0,  "\"tag\"", "\"tag\"", 1000) == MM_DL_CLEAN,
          "empty .part starts clean");
    CHECK(mm_resume_decide(-1, "\"tag\"", "\"tag\"", 1000) == MM_DL_CLEAN,
          "missing .part starts clean");

    /* ---- a partial with no saved validator cannot be trusted ---- */
    CHECK(mm_resume_decide(7340032, NULL, "\"tag\"", 89005350) == MM_DL_CLEAN,
          "no saved tag -> cannot validate -> start clean");
    CHECK(mm_resume_decide(7340032, "", "\"tag\"", 89005350) == MM_DL_CLEAN,
          "blank saved tag -> start clean");

    /* ---- the real case: 7 chunks down, same file, keep going ---- */
    CHECK(mm_resume_decide(7340032, "\"18d6e78b83670c4c-54e1d26\"",
                           "\"18d6e78b83670c4c-54e1d26\"", 89005350) == MM_DL_RESUME,
          "matching validator resumes from the partial");

    /* ---- the file changed under us: discard, do not splice ---- */
    CHECK(mm_resume_decide(7340032, "\"old-etag\"", "\"new-etag\"", 89005350) == MM_DL_RESTART,
          "validator changed mid-transfer -> restart, never splice");

    /* ---- nonsense partials ---- */
    CHECK(mm_resume_decide(89005350, "\"t\"", "\"t\"", 89005350) == MM_DL_RESTART,
          "a .part already at full size was never finalized -> restart");
    CHECK(mm_resume_decide(99999999, "\"t\"", "\"t\"", 89005350) == MM_DL_RESTART,
          "a .part LARGER than the total is corrupt -> restart");

    /* ---- validator not yet known (first request of a resumed pull) ---- */
    CHECK(mm_resume_decide(7340032, "\"t\"", NULL, 0) == MM_DL_RESUME,
          "before the reply arrives, a saved tag + partial means attempt resume");

    /* ---- total not yet known must not be read as 'partial exceeds total' ---- */
    CHECK(mm_resume_decide(7340032, "\"t\"", "\"t\"", 0) == MM_DL_RESUME,
          "total unknown (0) is not a size violation");
    CHECK(mm_resume_decide(7340032, "\"t\"", "\"t\"", -1) == MM_DL_RESUME,
          "total unknown (-1) is not a size violation");

    /* ---- tag equality is exact: no prefix/substring sloppiness ---- */
    CHECK(mm_resume_decide(100, "\"abc\"", "\"abcd\"", 1000) == MM_DL_RESTART,
          "a tag that merely prefixes another is a MISMATCH");

    if (fails == 0) { printf("test_mmresume: all checks passed\n"); return 0; }
    printf("test_mmresume: %d FAILURES\n", fails);
    return 1;
}
