/* Host-compiled unit test for mmretire.h -- the deferred-reclaim list that closes
 * AUDIT-races-leaks.md C1 (observer-block use-after-free of the freed MMEngine).
 *
 * Pure C, no ObjC/AVFoundation, so it builds and runs on the dev host exactly like
 * tests/test_mmurl.c:   make -C tweak/mmvideo/tests retire
 *
 * The invariant under test: an object handed to the list is NEVER freed while an
 * already-dispatched callback could still hold its raw pointer. The list only
 * reclaims entries that have been parked longer than a quiescence window that is
 * many multiples of the 250ms periodic-time-observer interval.
 */
#include <stdio.h>
#include <string.h>
#include "../mmretire.h"

static int   g_freed_n = 0;          /* entries recorded (bounded by the array below) */
static int   g_freed_total = 0;      /* every free, uncapped -- the churn test needs this */
static void *g_freed[64];
static void  rec_free(void *p) { g_freed_total++; if (g_freed_n < 64) g_freed[g_freed_n++] = p; }
static int   was_freed(void *p) { int i; for (i = 0; i < g_freed_n; i++) if (g_freed[i] == p) return 1; return 0; }
static void  reset(void) { g_freed_n = 0; g_freed_total = 0; memset(g_freed, 0, sizeof g_freed); }

static int fails = 0;
#define CHECK(cond, msg) do { if (!(cond)) { printf("FAIL: %s\n", (msg)); fails++; } } while (0)

#define QUIESCE 10.0

/* 1. The whole point: parking must not free. A block already on the main queue
 *    still holds this pointer and will read ->dead through it. */
static void test_park_does_not_free(void) {
    mm_retire_list L; int a = 1;
    reset(); mm_retire_init(&L);
    CHECK(mm_retire_park(&L, &a, 100.0, QUIESCE, rec_free) == 1, "park should accept the entry");
    CHECK(!was_freed(&a), "park must NOT free immediately (that IS the C1 UAF)");
    CHECK(L.n == 1, "parked entry should be held");
}

/* 2. Reclaim is time-gated: still-young entries stay, aged-out entries go. */
static void test_sweep_is_time_gated(void) {
    mm_retire_list L; int young = 1, old = 2;
    reset(); mm_retire_init(&L);
    mm_retire_park(&L, &old,   100.0, QUIESCE, rec_free);
    mm_retire_park(&L, &young, 108.0, QUIESCE, rec_free);
    mm_retire_sweep(&L, 111.0, QUIESCE, rec_free);      /* old=11s, young=3s */
    CHECK(was_freed(&old),   "an entry parked longer than the window must be reclaimed");
    CHECK(!was_freed(&young), "an entry inside the window must NOT be reclaimed");
    CHECK(L.n == 1, "only the aged-out entry should leave the list");
}

/* 3. Exactly-at-the-boundary is NOT old enough. The window is a safety margin;
 *    round toward keeping the memory, never toward freeing it. */
static void test_boundary_keeps(void) {
    mm_retire_list L; int a = 1;
    reset(); mm_retire_init(&L);
    mm_retire_park(&L, &a, 100.0, QUIESCE, rec_free);
    mm_retire_sweep(&L, 110.0, QUIESCE, rec_free);      /* exactly QUIESCE */
    CHECK(!was_freed(&a), "at exactly the window boundary the entry must be kept");
}

/* 4. Steady-state churn stays bounded -- park sweeps first, so a long run of
 *    drops does not grow the list without limit. */
static void test_churn_is_bounded(void) {
    mm_retire_list L; static int objs[200]; int i;
    reset(); mm_retire_init(&L);
    for (i = 0; i < 200; i++) { mm_retire_park(&L, &objs[i], 100.0 + i * 1.0, QUIESCE, rec_free); }
    CHECK(L.n <= MM_RETIRE_CAP, "list must never exceed its capacity");
    CHECK(L.n <= 11, "1 drop/sec with a 10s window should settle at ~11 held entries");
    CHECK(g_freed_total == 189, "aged-out entries should be reclaimed as churn continues");
}

/* 5. A burst that overruns the capacity must LEAK, never free something young.
 *    Leaking ~96 bytes is harmless; freeing a live engine is the crash. */
static void test_overflow_leaks_never_frees_young(void) {
    mm_retire_list L; static int objs[MM_RETIRE_CAP + 8]; int i, rejected = 0;
    reset(); mm_retire_init(&L);
    for (i = 0; i < MM_RETIRE_CAP + 8; i++) {
        if (!mm_retire_park(&L, &objs[i], 100.0, QUIESCE, rec_free)) { rejected++; }
    }
    CHECK(rejected == 8, "a full list must reject the excess rather than evict");
    CHECK(g_freed_n == 0, "nothing parked within the window may be freed, even under overflow");
}

/* 6. Reclaim order is oldest-first, so the list never strands an ancient entry
 *    behind younger ones. */
static void test_reclaims_oldest_first(void) {
    mm_retire_list L; int a = 1, b = 2, c = 3;
    reset(); mm_retire_init(&L);
    mm_retire_park(&L, &a, 100.0, QUIESCE, rec_free);
    mm_retire_park(&L, &b, 101.0, QUIESCE, rec_free);
    mm_retire_park(&L, &c, 120.0, QUIESCE, rec_free);
    mm_retire_sweep(&L, 122.0, QUIESCE, rec_free);
    CHECK(g_freed_n == 2 && g_freed[0] == &a && g_freed[1] == &b, "oldest entries reclaim first, in order");
}

int main(void) {
    test_park_does_not_free();
    test_sweep_is_time_gated();
    test_boundary_keeps();
    test_churn_is_bounded();
    test_overflow_leaks_never_frees_young();
    test_reclaims_oldest_first();
    if (fails) { printf("%d FAILED\n", fails); return 1; }
    printf("mmretire: all tests passed\n");
    return 0;
}
