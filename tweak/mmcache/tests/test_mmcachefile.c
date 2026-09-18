/* Host-compiled unit test for mmcachefile.h -- the "is this segment already on
 * disk?" check behind the fetch fast path (audit 1.15).
 *
 *   make -C tweak/mmcache/tests CC=clang test
 *
 * Why the fast path is safe to build on mere PRESENCE: mm_download rejects any
 * transfer whose byte count != expectedContentLength BEFORE writing, and writes
 * with writeToFile:atomically: (flag 1). A partial file therefore can never
 * appear in the cache dir, so a file being there means it was verified complete.
 * These tests pin the rules that keep that argument true -- above all that a
 * ZERO-BYTE file is never reported as a hit, since that is the one shape that
 * could exist without having gone through the verified write.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

/* The dev host is Windows (clang/MSVC); the tweak itself builds for iOS. Only the
 * test needs mkdir, so shim it rather than drag a portability layer into the
 * header that ships. */
#ifdef _WIN32
#include <direct.h>
#define MKDIR(p) _mkdir(p)
#else
#define MKDIR(p) mkdir((p), 0777)
#endif

#include "../mmcachefile.h"

static int fails = 0;
#define CHECK(cond, msg) do { if (!(cond)) { printf("FAIL: %s\n", (msg)); fails++; } } while (0)

/* Scratch dir under whatever temp location this host actually has. Hard-coding
 * /tmp fails on the Windows dev box (it maps to a C:\tmp that need not exist). */
static char DIRBUF[512];
static const char *DIR = DIRBUF;

static void pick_tmpdir(void) {
    const char *base = getenv("TMPDIR");
    if (!base || !*base) base = getenv("TMP");
    if (!base || !*base) base = getenv("TEMP");
    if (!base || !*base) base = ".";
    snprintf(DIRBUF, sizeof DIRBUF, "%s/mmcachefile_test", base);
}

static void write_file(const char *name, size_t bytes) {
    char p[512];
    snprintf(p, sizeof p, "%s/%s", DIR, name);
    FILE *f = fopen(p, "wb");
    if (!f) { printf("FAIL: could not create %s\n", p); fails++; return; }
    for (size_t i = 0; i < bytes; i++) { fputc('x', f); }
    fclose(f);
}

/* 1. A present, non-empty segment reports its real size -> fast-path hit. */
static void test_present_returns_size(void) {
    char path[512];
    write_file("tokA.mp4", 4096);
    long n = mm_cached_size(DIR, "tokA", path, sizeof path);
    CHECK(n == 4096, "a present non-empty segment should report its byte count");
    CHECK(strstr(path, "/tokA.mp4") != NULL, "path should be <dir>/<token>.mp4");
}

/* 2. Absent -> miss, so we fall through to the real download. */
static void test_absent_returns_zero(void) {
    char path[512];
    long n = mm_cached_size(DIR, "nosuchtoken", path, sizeof path);
    CHECK(n == 0, "an absent segment must be a miss");
}

/* 3. THE IMPORTANT ONE: a zero-byte file must never ack as cached. Acking it
 *    would hand the player an empty file and produce a decode error that no
 *    retry clears. */
static void test_zero_byte_is_a_miss(void) {
    char path[512];
    write_file("empty.mp4", 0);
    long n = mm_cached_size(DIR, "empty", path, sizeof path);
    CHECK(n == 0, "a zero-byte file must be treated as a MISS, not a hit");
}

/* 4. A directory sitting where the file should be is not a cached segment. */
static void test_directory_is_a_miss(void) {
    char p[512], path[512];
    snprintf(p, sizeof p, "%s/adir.mp4", DIR);
    MKDIR(p);
    long n = mm_cached_size(DIR, "adir", path, sizeof path);
    CHECK(n == 0, "a directory must not count as a cached segment");
}

/* 5. If the path does not fit the buffer, report a miss rather than acting on a
 *    TRUNCATED path -- a truncated path can name a different file. */
static void test_overflow_is_a_miss(void) {
    char tiny[8];
    long n = mm_cached_size(DIR, "tokA", tiny, sizeof tiny);
    CHECK(n == 0, "a path that would not fit must be a miss, never truncated");
}

/* 6. Defensive: null inputs are a miss, not a crash. */
static void test_null_inputs(void) {
    char path[512];
    CHECK(mm_cached_size(NULL, "tokA", path, sizeof path) == 0, "null dir -> miss");
    CHECK(mm_cached_size(DIR, NULL, path, sizeof path) == 0, "null token -> miss");
    CHECK(mm_cached_size(DIR, "tokA", NULL, 512) == 0, "null pathout -> miss");
}

int main(void) {
    pick_tmpdir();
    MKDIR(DIR);
    test_present_returns_size();
    test_absent_returns_zero();
    test_zero_byte_is_a_miss();
    test_directory_is_a_miss();
    test_overflow_is_a_miss();
    test_null_inputs();
    if (fails) { printf("%d FAILED\n", fails); return 1; }
    printf("mmcachefile: all tests passed\n");
    return 0;
}
