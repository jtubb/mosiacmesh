/* Host-compiled unit test for mmcachefile.h -- the decision logic behind the
 * mmcache fetch fast path (audit 1.15).
 *
 *   make -C tweak/mmcache/tests CC=clang test
 *
 * The header is deliberately SYSCALL-FREE: it takes the filesystem facts as
 * arguments and Tweak.x supplies them via NSFileManager/objc_msgSend. The first
 * cut called stat() and SIGKILLed Web.app at dyld load on iOS 5.1 -- `_stat` was
 * the one symbol the 5.1-era libSystem would not bind. So there is nothing to
 * mock here and nothing platform-specific to compile: the two things worth
 * testing are path composition (where truncation bugs live) and the usable-size
 * rule (where a wrong answer means a black screen).
 */
#include <stdio.h>
#include <string.h>

#include "../mmcachefile.h"

static int fails = 0;
#define CHECK(cond, msg) do { if (!(cond)) { printf("FAIL: %s\n", (msg)); fails++; } } while (0)

/* ---- mm_cache_path ---------------------------------------------------------- */

static void test_path_composed(void) {
    char p[256];
    CHECK(mm_cache_path("/var/cache", "seg_abc_0", p, sizeof p) == 1, "normal path should succeed");
    CHECK(strcmp(p, "/var/cache/seg_abc_0.mp4") == 0, "path should be <dir>/<token>.mp4");
}

/* A path that does not fit must FAIL, not truncate -- a truncated path can name a
 * different file, and we would then ack the wrong bytes as cached. */
static void test_path_overflow_fails_and_clears(void) {
    char tiny[12];
    CHECK(mm_cache_path("/var/cache", "seg_abc_0", tiny, sizeof tiny) == 0,
          "a path that would not fit must fail");
    CHECK(tiny[0] == 0, "the buffer must be cleared, never left holding a truncated path");
}

/* Exactly-fits is a success: cap must account for the NUL and no more. */
static void test_path_exact_fit(void) {
    char buf[13];   /* "/c/t.mp4" is 8 chars + NUL; give a little room and check the edge */
    CHECK(mm_cache_path("/c", "t", buf, sizeof buf) == 1, "a path that exactly fits should succeed");
    CHECK(strcmp(buf, "/c/t.mp4") == 0, "exact-fit path should still be correct");
    char edge[9];   /* "/c/t.mp4" = 8 + NUL = 9 -> fits exactly */
    CHECK(mm_cache_path("/c", "t", edge, sizeof edge) == 1, "9-byte buffer fits '/c/t.mp4'");
    char under[8];  /* one short */
    CHECK(mm_cache_path("/c", "t", under, sizeof under) == 0, "8-byte buffer must fail");
}

static void test_path_null_inputs(void) {
    char p[64];
    CHECK(mm_cache_path(NULL, "t", p, sizeof p) == 0, "null dir -> fail");
    CHECK(mm_cache_path("/c", NULL, p, sizeof p) == 0, "null token -> fail");
    CHECK(mm_cache_path("/c", "t", NULL, 64) == 0, "null out -> fail");
    CHECK(mm_cache_path("/c", "t", p, 0) == 0, "zero cap -> fail");
}

/* ---- mm_cache_usable_size --------------------------------------------------- */

static void test_present_regular_nonempty_is_a_hit(void) {
    CHECK(mm_cache_usable_size(1, 1, 19406085LL) == 19406085L,
          "a present, regular, non-empty file is a hit reporting its size");
}

static void test_absent_is_a_miss(void) {
    CHECK(mm_cache_usable_size(0, 0, 0) == 0, "absent -> miss");
    CHECK(mm_cache_usable_size(0, 1, 4096) == 0, "absent wins even if other facts look fine");
}

/* THE IMPORTANT ONE: a zero-byte file must never ack as cached. It is the only
 * shape that can exist without having gone through mm_download's verified write,
 * and acking it hands the player an unplayable src. */
static void test_zero_byte_is_a_miss(void) {
    CHECK(mm_cache_usable_size(1, 1, 0) == 0, "a zero-byte file must be a MISS");
    CHECK(mm_cache_usable_size(1, 1, -1) == 0, "a negative size must be a MISS");
}

static void test_directory_is_a_miss(void) {
    CHECK(mm_cache_usable_size(1, 0, 4096) == 0, "a directory must not count as a cached segment");
}

/* Guard the long narrowing: report a miss rather than a truncated size. */
static void test_oversize_is_a_miss(void) {
    CHECK(mm_cache_usable_size(1, 1, 0x80000000LL) == 0, "a size beyond long range -> miss");
    CHECK(mm_cache_usable_size(1, 1, 0x7FFFFFFFLL) == 0x7FFFFFFFL, "the largest in-range size is a hit");
}

int main(void) {
    test_path_composed();
    test_path_overflow_fails_and_clears();
    test_path_exact_fit();
    test_path_null_inputs();
    test_present_regular_nonempty_is_a_hit();
    test_absent_is_a_miss();
    test_zero_byte_is_a_miss();
    test_directory_is_a_miss();
    test_oversize_is_a_miss();
    if (fails) { printf("%d FAILED\n", fails); return 1; }
    printf("mmcachefile: all tests passed\n");
    return 0;
}
