/* Host-compiled unit test for the per-chunk response contract in mmrange.h.
 *
 *   make -C tweak/mmcache/tests CC=clang test
 *
 * WHY THIS EXISTS (measured on-wall 2026-09-29):
 * mm_download parsed Content-Range on the FIRST chunk only -- it sat inside
 * `if (total < 0)`, whose whole purpose was to learn the entity size. Every
 * subsequent chunk's window went unchecked. A reply that did not correspond to
 * the range just requested was therefore appended at the wrong offset, silently.
 *
 * It shows up on the wall as an over-read. sign1screen20:
 *     TRUNCATED token=seg_4298b787efa0_0 got=64476672 expect=64227456   (x3 identical)
 *     TRUNCATED ... got=64467968 ... / got=64229760 ...
 *     OK token=seg_4298b787efa0_0 bytes=64227456 chunks=237
 * Six complete ~64MB transfers discarded, ~385MB of AP traffic wasted, and 237
 * chunk requests for a 62-chunk file. sign1screen1, still on the older
 * eae0e7fb build, shows the same signature (got=89128960 expect=89005350), so
 * this predates the in-flight guard and is fleet-wide.
 *
 * The server was exonerated by direct measurement: 62 sequential 1MB windows and
 * 36 concurrent windows against the live media_handler all returned the exact
 * requested length AND byte-identical content. So the mismatch arises between
 * NSURLConnection and this loop -- sendSynchronousRequest: consults the shared
 * NSURLCache on iOS 5, and the server sends no Cache-Control, which makes a
 * cached partial served against a different Range the leading suspect.
 *
 * THE OVER-READ IS THE LUCKY CASE. It only got caught because the byte count
 * overshot and the completeness check rejected the file. Had a mis-delivered
 * chunk made the bytes sum to exactly `total`, the file would have PASSED that
 * check holding wrong bytes at a wrong offset -- an unplayable cached segment
 * that every later HIT would serve. That is a silent corruption path, and it is
 * what this contract closes; the wasted bandwidth is the lesser half.
 *
 * Checking `n == last - first + 1` is deliberately not enough on its own: a
 * correctly-sized body for the WRONG window is exactly the corruption case, and
 * only the first/last comparison catches it.
 */
#include <stdio.h>
#include <string.h>

#include "../mmrange.h"

static int fails = 0;
#define CHECK(cond, msg) do { if (!(cond)) { printf("FAIL: %s\n", (msg)); fails++; } } while (0)

int main(void) {
    long long total = 64227456;

    /* ---- the happy path: the reply is exactly the window we asked for ---- */
    CHECK(mm_chunk_verify("bytes 0-1048575/64227456", 0, 1048575, 1048576, total)
          == MM_CHUNK_OK, "exact match must pass");
    CHECK(mm_chunk_verify("bytes 63963136-64227455/64227456",
                          63963136, 64227455, 264320, total) == MM_CHUNK_OK,
          "the short tail window must pass");

    /* ---- before the total is known, the first chunk cannot compare it ----
     * total<=0 means "not learned yet"; the window still has to match. */
    CHECK(mm_chunk_verify("bytes 0-1048575/64227456", 0, 1048575, 1048576, -1)
          == MM_CHUNK_OK, "first chunk with unknown total must pass");
    CHECK(mm_chunk_verify("bytes 0-1048575/64227456", 0, 1048575, 1048576, 0)
          == MM_CHUNK_OK, "total 0 also means not-yet-known");

    /* ---- THE BUG: a reply for a DIFFERENT window ----
     * This is the case that produced the over-read and could have produced a
     * silently corrupt file. Note the body length is perfectly plausible. */
    CHECK(mm_chunk_verify("bytes 0-1048575/64227456", 2097152, 3145727, 1048576, total)
          == MM_CHUNK_WRONG_WINDOW, "a stale cached window must be rejected");
    CHECK(mm_chunk_verify("bytes 1048576-2097151/64227456", 0, 1048575, 1048576, total)
          == MM_CHUNK_WRONG_WINDOW, "wrong start must be rejected");
    CHECK(mm_chunk_verify("bytes 0-1048574/64227456", 0, 1048575, 1048575, total)
          == MM_CHUNK_WRONG_WINDOW, "off-by-one end must be rejected");

    /* ---- the entity changed under us ----
     * Distinct from a wrong window: continuing would splice two different videos,
     * and the byte count could still reach the advertised total. */
    CHECK(mm_chunk_verify("bytes 0-1048575/99999999", 0, 1048575, 1048576, total)
          == MM_CHUNK_ENTITY_CHANGED, "a changed total must be rejected");

    /* ---- body length disagreeing with the window it claims ----
     * A truthful Content-Range with the wrong number of bytes behind it. Writing
     * it would desynchronise every following offset. */
    CHECK(mm_chunk_verify("bytes 0-1048575/64227456", 0, 1048575, 1048575, total)
          == MM_CHUNK_BAD_LENGTH, "short body for its window must be rejected");
    CHECK(mm_chunk_verify("bytes 0-1048575/64227456", 0, 1048575, 1048577, total)
          == MM_CHUNK_BAD_LENGTH, "over-long body must be rejected");
    /* The exact shape seen on the wall: bytes beyond the end of the entity. */
    CHECK(mm_chunk_verify("bytes 63963136-64227455/64227456",
                          63963136, 64227455, 1048576, total) == MM_CHUNK_BAD_LENGTH,
          "a full chunk returned for the short tail is the over-read");

    /* ---- unusable or missing header ----
     * Rejecting is mandatory: without a parseable Content-Range there is nothing
     * to verify the bytes against, and appending unverifiable bytes is the bug. */
    CHECK(mm_chunk_verify((const char *)0, 0, 1048575, 1048576, total)
          == MM_CHUNK_BAD_HEADER, "NULL header must be rejected");
    CHECK(mm_chunk_verify("", 0, 1048575, 1048576, total)
          == MM_CHUNK_BAD_HEADER, "empty header must be rejected");
    CHECK(mm_chunk_verify("bytes 0-1048575/*", 0, 1048575, 1048576, total)
          == MM_CHUNK_BAD_HEADER, "a '*' total cannot verify completeness");
    CHECK(mm_chunk_verify("garbage", 0, 1048575, 1048576, total)
          == MM_CHUNK_BAD_HEADER, "unparseable header must be rejected");

    /* ---- a zero-length body is never acceptable ----
     * The loop would make no progress and spin to its guard limit. */
    CHECK(mm_chunk_verify("bytes 0-1048575/64227456", 0, 1048575, 0, total)
          == MM_CHUNK_BAD_LENGTH, "empty body must be rejected");

    /* ---- nonsense requested windows are caller errors, still rejected ---- */
    CHECK(mm_chunk_verify("bytes 0-1048575/64227456", -1, 1048575, 1048576, total)
          != MM_CHUNK_OK, "negative start must not pass");
    CHECK(mm_chunk_verify("bytes 100-50/64227456", 100, 50, 1, total)
          != MM_CHUNK_OK, "inverted window must not pass");

    /* ---- header case and whitespace tolerance ----
     * The unit token is compared case-insensitively by mm_parse_content_range;
     * proxies vary. This must not become a spurious rejection that stops a pull. */
    CHECK(mm_chunk_verify("Bytes 0-1048575/64227456", 0, 1048575, 1048576, total)
          == MM_CHUNK_OK, "capitalised unit must pass");
    CHECK(mm_chunk_verify("  bytes   0-1048575/64227456", 0, 1048575, 1048576, total)
          == MM_CHUNK_OK, "leading/interior whitespace must pass");

    /* ---- every rejection has a DISTINCT code ----
     * The tweak logs it, and which check failed is the difference between
     * suspecting the cache, the server, or the network. A single boolean would
     * have left the 2026-09-29 diagnosis exactly where it started. */
    CHECK(MM_CHUNK_OK == 0, "OK must be 0 so callers can test truthiness of failure");
    CHECK(MM_CHUNK_BAD_HEADER != MM_CHUNK_WRONG_WINDOW, "codes distinct");
    CHECK(MM_CHUNK_WRONG_WINDOW != MM_CHUNK_ENTITY_CHANGED, "codes distinct");
    CHECK(MM_CHUNK_ENTITY_CHANGED != MM_CHUNK_BAD_LENGTH, "codes distinct");
    CHECK(MM_CHUNK_BAD_LENGTH != MM_CHUNK_BAD_HEADER, "codes distinct");

    if (fails) { printf("%d CHECK(s) failed\n", fails); return 1; }
    printf("test_mmchunk: all checks passed\n");
    return 0;
}
