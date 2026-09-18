/* Host-compiled unit test for mmrange.h -- the range arithmetic behind mmcache's
 * chunked, constant-memory segment download (audit 1.13).
 *
 *   make -C tweak/mmcache/tests CC=clang test
 *
 * WHY CHUNKED RANGES rather than an async NSURLConnection delegate: a delegate is
 * an object, and this tweak cannot define an ObjC class -- a static class SIGKILLs
 * the dylib load on iOS 5.1 and the runtime class-creation APIs do not flat-bind
 * (REFINDINGS 11/12). Issuing sequential ranged sendSynchronousRequest: calls keeps
 * peak RAM at one chunk with no delegate and no new linker symbols.
 *
 * These tests cover the two places real bugs live: parsing the server's
 * Content-Range reply, and computing the next window without ever overrunning the
 * file or looping forever.
 */
#include <stdio.h>
#include <string.h>

#include "../mmrange.h"

static int fails = 0;
#define CHECK(cond, msg) do { if (!(cond)) { printf("FAIL: %s\n", (msg)); fails++; } } while (0)

/* ---- mm_parse_content_range ------------------------------------------------- */

static void test_parse_typical(void) {
    long long a = -1, b = -1, t = -1;
    CHECK(mm_parse_content_range("bytes 0-1048575/19406085", &a, &b, &t) == 1, "typical header parses");
    CHECK(a == 0 && b == 1048575 && t == 19406085LL, "typical header yields first/last/total");
}

static void test_parse_mid_file_window(void) {
    long long a = -1, b = -1, t = -1;
    CHECK(mm_parse_content_range("bytes 1048576-2097151/19406085", &a, &b, &t) == 1, "mid-file window parses");
    CHECK(a == 1048576 && b == 2097151 && t == 19406085LL, "mid-file values correct");
}

/* Servers vary in spacing/case; be liberal in what we accept. */
static void test_parse_tolerates_formatting(void) {
    long long a, b, t;
    CHECK(mm_parse_content_range("bytes   5-9/10", &a, &b, &t) == 1 && a == 5 && b == 9 && t == 10,
          "extra spaces tolerated");
    CHECK(mm_parse_content_range("BYTES 5-9/10", &a, &b, &t) == 1, "case-insensitive unit");
}

/* An unknown total ("*") is useless to us -- we cannot verify completeness, so
 * refuse rather than download something we cannot check. */
static void test_parse_rejects_unknown_total(void) {
    long long a, b, t;
    CHECK(mm_parse_content_range("bytes 0-99/*", &a, &b, &t) == 0, "unknown total must be rejected");
}

static void test_parse_rejects_garbage(void) {
    long long a, b, t;
    CHECK(mm_parse_content_range("", &a, &b, &t) == 0, "empty -> reject");
    CHECK(mm_parse_content_range(NULL, &a, &b, &t) == 0, "null -> reject");
    CHECK(mm_parse_content_range("items 0-9/10", &a, &b, &t) == 0, "wrong unit -> reject");
    CHECK(mm_parse_content_range("bytes 0-9", &a, &b, &t) == 0, "missing total -> reject");
    CHECK(mm_parse_content_range("bytes 9-0/10", &a, &b, &t) == 0, "last < first -> reject");
    CHECK(mm_parse_content_range("bytes 0-99/10", &a, &b, &t) == 0, "last beyond total -> reject");
}


/* An absurdly long digit run is rejected. The cap is what makes overflow
 * impossible WITHOUT a 64-bit division, which would pull in the compiler-rt
 * builtin ___divdi3 that this toolchain cannot bind (the dylib would SIGKILL at
 * load). Guarding the behaviour keeps anyone from "simplifying" it back. */
static void test_parse_rejects_absurd_digits(void) {
    long long a, b, t;
    CHECK(mm_parse_content_range("bytes 0-9/123456789012345678901234567890", &a, &b, &t) == 0,
          "a 30-digit total must be rejected, not overflowed");
}

/* ---- mm_range_header -------------------------------------------------------- */

static void test_range_header(void) {
    char h[64];
    CHECK(mm_range_header(h, sizeof h, 0, 1048575) == 1, "header composes");
    CHECK(strcmp(h, "bytes=0-1048575") == 0, "header text is 'bytes=<start>-<end>'");
    CHECK(mm_range_header(h, 8, 0, 1048575) == 0, "a header that will not fit must fail, not truncate");
}

/* ---- mm_next_range ---------------------------------------------------------- */

static void test_next_range_first_window(void) {
    long long s, e;
    CHECK(mm_next_range(19406085LL, 0, 1048576, &s, &e) == 1, "first window exists");
    CHECK(s == 0 && e == 1048575, "first window is [0, chunk-1]");
}

static void test_next_range_resumes_from_got(void) {
    long long s, e;
    CHECK(mm_next_range(19406085LL, 1048576, 1048576, &s, &e) == 1, "second window exists");
    CHECK(s == 1048576 && e == 2097151, "second window starts where the first ended");
}

/* The last window must clamp to the final byte -- asking past EOF makes a server
 * answer 416 and the download stalls forever. */
static void test_next_range_clamps_tail(void) {
    long long s, e;
    CHECK(mm_next_range(100, 64, 1048576, &s, &e) == 1, "tail window exists");
    CHECK(s == 64 && e == 99, "tail window clamps to total-1");
}

static void test_next_range_exact_multiple(void) {
    long long s, e;
    CHECK(mm_next_range(2097152LL, 1048576, 1048576, &s, &e) == 1, "penultimate window exists");
    CHECK(s == 1048576 && e == 2097151, "exact-multiple tail is a full chunk");
    CHECK(mm_next_range(2097152LL, 2097152LL, 1048576, &s, &e) == 0, "no window once got == total");
}

static void test_next_range_done_and_overrun(void) {
    long long s, e;
    CHECK(mm_next_range(100, 100, 1048576, &s, &e) == 0, "got == total -> done");
    CHECK(mm_next_range(100, 150, 1048576, &s, &e) == 0, "got > total -> done, never negative window");
}

/* Defensive: a zero/negative chunk would loop forever asking for nothing. */
static void test_next_range_rejects_bad_chunk(void) {
    long long s, e;
    CHECK(mm_next_range(100, 0, 0, &s, &e) == 0, "zero chunk -> refuse");
    CHECK(mm_next_range(100, 0, -5, &s, &e) == 0, "negative chunk -> refuse");
    CHECK(mm_next_range(0, 0, 1048576, &s, &e) == 0, "zero total -> refuse");
    CHECK(mm_next_range(100, 0, 1048576, NULL, &e) == 0, "null out -> refuse");
}

/* A whole download walks to completion in the expected number of chunks and
 * covers every byte exactly once. */
static void test_full_walk_covers_every_byte(void) {
    long long total = 19406085LL, got = 0, s, e;
    int n = 0;
    while (mm_next_range(total, got, 1048576, &s, &e)) {
        CHECK(s == got, "each window starts exactly where the last ended");
        got = e + 1;
        if (++n > 100) { break; }
    }
    CHECK(got == total, "the walk covers the file exactly");
    CHECK(n == 19, "19406085 / 1048576 -> 19 chunks");
}

int main(void) {
    test_parse_typical();
    test_parse_mid_file_window();
    test_parse_tolerates_formatting();
    test_parse_rejects_unknown_total();
    test_parse_rejects_garbage();
    test_parse_rejects_absurd_digits();
    test_range_header();
    test_next_range_first_window();
    test_next_range_resumes_from_got();
    test_next_range_clamps_tail();
    test_next_range_exact_multiple();
    test_next_range_done_and_overrun();
    test_next_range_rejects_bad_chunk();
    test_full_walk_covers_every_byte();
    if (fails) { printf("%d FAILED\n", fails); return 1; }
    printf("mmrange: all tests passed\n");
    return 0;
}
