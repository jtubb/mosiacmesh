/* Host-compiled unit test for the in-flight download registry in mminflight.h.
 *
 *   make -C tweak/mmcache/tests CC=clang test
 *
 * WHY THIS EXISTS (measured on-wall 2026-09-26):
 * `mmcache://fetch` was not idempotent. The handler checked only for a FINISHED
 * <token>.mp4 on disk and then unconditionally dispatch_async_f'd another
 * mm_download onto the GLOBAL CONCURRENT queue. Nothing tracked work already in
 * progress, so every re-issued PRECACHE grant for a token that was still
 * downloading started a second, third and sixth downloader on the same .part.
 *
 * That is fatal to resume specifically, not merely wasteful: each instance that
 * decides MM_DL_CLEAN removes the shared .part and recreates it empty, WHILE its
 * siblings are appending to it. Bytes therefore cannot accumulate and
 * mm_resume_decide is never reached with a usable partial. Canary 192.168.1.82's
 * log for seg_4298b787efa0_0 showed the shape exactly: 11 `fetch` lines against 7
 * terminations, six `fetch` in a row with nothing between them, adjacent failures
 * at 13,631,488 and at 0 (two live writers), and not one `resume ... from=` line
 * ever. The transfer only completed when the storm thinned out enough to leave a
 * single survivor running the whole 62 chunks from zero.
 *
 * The duplicate grants are legitimate and must keep arriving: the JS _pending map
 * is per-page-load, so a webclip reload mid-transfer loses it while the native
 * download keeps running, and the server re-grants. The tweak is the only layer
 * that survives that reload, so the exclusivity invariant has to live here.
 *
 * THREADING CONTRACT: the table is touched ONLY from the main thread -- claimed in
 * the UIWebView shouldStartLoadWithRequest: handler, released inside the
 * main-queue block that already carries done/fail back to JS. So it needs no lock,
 * and adds no new linker symbol (a static class or OSSpinLock would; see
 * mmcachefile.h for what the 5.1 loader rejects).
 */
#include <stdio.h>
#include <string.h>

#include "../mminflight.h"

static int fails = 0;
#define CHECK(cond, msg) do { if (!(cond)) { printf("FAIL: %s\n", (msg)); fails++; } } while (0)

int main(void) {
    mm_inflight t;

    /* ---- a fresh table holds nothing ---- */
    mm_inflight_init(&t);
    CHECK(!mm_inflight_has(&t, "seg_abc_0"), "fresh table must be empty");
    CHECK(mm_inflight_count(&t) == 0, "fresh table count must be 0");

    /* ---- first claim wins ---- */
    CHECK(mm_inflight_claim(&t, "seg_abc_0") == 1, "first claim must succeed");
    CHECK(mm_inflight_has(&t, "seg_abc_0"), "claimed token must be present");
    CHECK(mm_inflight_count(&t) == 1, "count must be 1 after one claim");

    /* ---- THE BUG: a second claim of the same token must be REFUSED ----
     * This is the whole point. Two downloaders on one .part is what made resume
     * impossible, so the duplicate grant has to be dropped here. */
    CHECK(mm_inflight_claim(&t, "seg_abc_0") == 0, "duplicate claim must be refused");
    CHECK(mm_inflight_count(&t) == 1, "a refused claim must not add a slot");

    /* ---- different tokens coexist: distinct segments download in parallel ---- */
    CHECK(mm_inflight_claim(&t, "seg_abc_1") == 1, "a different token must claim");
    CHECK(mm_inflight_count(&t) == 2, "two distinct tokens occupy two slots");
    CHECK(mm_inflight_has(&t, "seg_abc_0") && mm_inflight_has(&t, "seg_abc_1"),
          "both distinct tokens present");

    /* ---- release frees exactly one slot and re-enables the token ---- */
    CHECK(mm_inflight_release(&t, "seg_abc_0") == 1, "release of a held token reports 1");
    CHECK(!mm_inflight_has(&t, "seg_abc_0"), "released token must be gone");
    CHECK(mm_inflight_has(&t, "seg_abc_1"), "release must not disturb a sibling");
    CHECK(mm_inflight_count(&t) == 1, "count drops to 1 after one release");
    CHECK(mm_inflight_claim(&t, "seg_abc_0") == 1, "a released token may be claimed again");

    /* ---- releasing something never claimed is a benign no-op ----
     * Required, not merely tolerated: the HIT fast path answers dispatch_done
     * WITHOUT claiming (nothing downloads), and the release lives inside
     * dispatch_done so it cannot be forgotten on any exit path. */
    mm_inflight_init(&t);
    CHECK(mm_inflight_release(&t, "never_claimed") == 0,
          "release of an unheld token reports 0");
    CHECK(mm_inflight_count(&t) == 0, "a no-op release must not corrupt the count");
    CHECK(mm_inflight_release(&t, "") == 0, "release of an empty token reports 0");

    /* ---- prefix collisions are distinct tokens ----
     * Segment tokens share long prefixes (seg_<rt>_0 / seg_<rt>_10), so a compare
     * that stopped at the shorter length would alias two real segments and refuse
     * a download that must happen. */
    mm_inflight_init(&t);
    CHECK(mm_inflight_claim(&t, "seg_4298b787efa0_1") == 1, "claim _1");
    CHECK(mm_inflight_claim(&t, "seg_4298b787efa0_10") == 1,
          "_10 is not _1 and must claim its own slot");
    CHECK(mm_inflight_count(&t) == 2, "prefix-sharing tokens take two slots");
    CHECK(mm_inflight_release(&t, "seg_4298b787efa0_1") == 1, "release _1");
    CHECK(mm_inflight_has(&t, "seg_4298b787efa0_10"), "_10 must survive releasing _1");

    /* ---- an empty token is not claimable ----
     * token_is_safe() already rejects it upstream; refusing here too keeps the
     * table from filling with slots nothing will ever release. */
    mm_inflight_init(&t);
    CHECK(mm_inflight_claim(&t, "") == 0, "empty token must not claim");
    CHECK(mm_inflight_claim(&t, (const char *)0) == 0, "NULL token must not claim");
    CHECK(mm_inflight_count(&t) == 0, "rejected claims leave the table empty");

    /* ---- an over-long token is refused rather than silently truncated ----
     * Truncation would alias two different segments into one slot, which is the
     * same aliasing bug as a prefix compare. dlctx.token is 128 bytes, and
     * MM_INFLIGHT_TOKMAX matches it, so a token that would not fit was never a
     * real token. */
    mm_inflight_init(&t);
    {
        char big[MM_INFLIGHT_TOKMAX + 8];
        memset(big, 'a', sizeof big - 1);
        big[sizeof big - 1] = 0;
        CHECK(mm_inflight_claim(&t, big) == 0, "over-long token must be refused");
        CHECK(mm_inflight_count(&t) == 0, "an over-long token occupies no slot");
    }

    /* ---- a full table refuses rather than evicting ----
     * Evicting a live entry would re-permit a second downloader on a .part that is
     * still being written -- the exact bug. Refusing is safe because the server
     * re-grants: the download is deferred, never corrupted. Reaching this at all
     * means something upstream is wrong, so the tweak logs it. */
    mm_inflight_init(&t);
    {
        int i, claimed = 0;
        char tok[32];
        for (i = 0; i < MM_INFLIGHT_MAX; i++) {
            snprintf(tok, sizeof tok, "seg_t_%d", i);
            if (mm_inflight_claim(&t, tok)) { claimed++; }
        }
        CHECK(claimed == MM_INFLIGHT_MAX, "table must hold MM_INFLIGHT_MAX tokens");
        CHECK(mm_inflight_count(&t) == MM_INFLIGHT_MAX, "count equals capacity when full");
        CHECK(mm_inflight_claim(&t, "seg_overflow_0") == 0, "a full table refuses a new token");
        CHECK(!mm_inflight_has(&t, "seg_overflow_0"), "the refused token is not stored");
        /* ...and an ALREADY-HELD token still reports refused when full, which is
         * the same answer a duplicate gets when there is room. */
        CHECK(mm_inflight_claim(&t, "seg_t_0") == 0, "duplicate refused even when full");
        CHECK(mm_inflight_release(&t, "seg_t_0") == 1, "release frees a slot when full");
        CHECK(mm_inflight_claim(&t, "seg_overflow_0") == 1, "the freed slot is reusable");
    }

    /* ---- slot reuse does not resurrect a stale token ----
     * A released slot must not still compare equal to its old token, or a
     * duplicate grant arriving after completion would be wrongly refused and the
     * segment would never re-download. */
    mm_inflight_init(&t);
    CHECK(mm_inflight_claim(&t, "seg_stale_0") == 1, "claim before release");
    CHECK(mm_inflight_release(&t, "seg_stale_0") == 1, "release it");
    CHECK(!mm_inflight_has(&t, "seg_stale_0"), "the slot must not remember the token");
    CHECK(mm_inflight_claim(&t, "seg_other_0") == 1, "the slot takes a new token");
    CHECK(!mm_inflight_has(&t, "seg_stale_0"), "old token still absent after reuse");

    /* ---- double release is idempotent ----
     * dispatch_fail can be reached after dispatch_progress has already run for the
     * token; only the terminal call releases, but a second must not underflow the
     * count or free a slot a NEW claim has since taken. */
    mm_inflight_init(&t);
    CHECK(mm_inflight_claim(&t, "seg_dbl_0") == 1, "claim for double-release");
    CHECK(mm_inflight_release(&t, "seg_dbl_0") == 1, "first release reports 1");
    CHECK(mm_inflight_release(&t, "seg_dbl_0") == 0, "second release reports 0");
    CHECK(mm_inflight_count(&t) == 0, "count must not go negative");

    if (fails) { printf("%d CHECK(s) failed\n", fails); return 1; }
    printf("test_mminflight: all checks passed\n");
    return 0;
}
