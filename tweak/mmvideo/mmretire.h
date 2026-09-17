#ifndef MMRETIRE_H
#define MMRETIRE_H
/* mmretire.h — deferred reclaim for a heap object that an ALREADY-DISPATCHED
 * callback may still hold by raw pointer. Header-only, pure C99, no ObjC and no
 * platform calls (the caller supplies `now_s`), so it host-compiles for
 * tests/test_mmretire.c exactly like mmurl.h does.
 *
 * WHY THIS EXISTS — AUDIT-races-leaks.md C1, the mmvideo SIGSEGV:
 *   The AVPlayer periodic-time-observer block captures the raw `MMEngine *`. When a
 *   <video> reloads or cancels, dropEngine() tore the engine down and free()d it.
 *   -removeTimeObserver: does NOT recall a block AVFoundation has already
 *   dispatch_async'd to the main queue, so that block could then run against freed
 *   memory: read eng->dead (garbage -> may read 0 and pass the guard), read
 *   eng->mp, and call WebCore::MediaPlayer::timeChanged(garbage) -> EXC_BAD_ACCESS
 *   in WebCore, dispatched from AVFoundation on the main thread. That is the exact
 *   Web.app crash signature.
 *
 *   The `dead` flag alone cannot fix this: the flag lives IN the freed struct, and
 *   the next mm_engine_create() calloc()s the same size class, so a recycled block
 *   reads back dead==0 and the guard opens.
 *
 * THE INVARIANT: an object handed to this list is never freed while a callback
 * could still be holding it. Reclaim is gated on a quiescence window many multiples
 * of the 250ms observer interval, so by the time a pointer is freed every block
 * that could have captured it has long since run (or been discarded).
 *
 * Every entry point mutates the list, so ALL of them must be called from ONE queue
 * — in mmvideo that is the main queue, the same queue the observer block runs on.
 */

#define MM_RETIRE_CAP      32     /* held entries; overflow leaks rather than evicts */
#define MM_RETIRE_QUIESCE  10.0   /* seconds; 40x the 250ms observer interval */

typedef void (*mm_retire_free_fn)(void *);

typedef struct {
    void  *p[MM_RETIRE_CAP];
    double at[MM_RETIRE_CAP];   /* monotonic seconds when the entry was parked */
    int    n;                   /* live entries, oldest at index 0 */
} mm_retire_list;

static void mm_retire_init(mm_retire_list *L) {
    if (L) { L->n = 0; }
}

/* Reclaim every entry parked STRICTLY LONGER than `quiesce_s` ago. Returns the
 * number freed. Entries are kept in park order, so this only ever pops from the
 * front — at the boundary we KEEP, because rounding must never favour freeing. */
static int mm_retire_sweep(mm_retire_list *L, double now_s, double quiesce_s,
                           mm_retire_free_fn fr) {
    int keep = 0, i, freed = 0;
    if (!L) { return 0; }
    for (i = 0; i < L->n; i++) {
        if ((now_s - L->at[i]) > quiesce_s) {
            if (fr) { fr(L->p[i]); }
            freed++;
        } else {
            L->p[keep] = L->p[i];
            L->at[keep] = L->at[i];
            keep++;
        }
    }
    L->n = keep;
    return freed;
}

/* Park `p` for deferred reclaim. Sweeps first so steady-state churn stays bounded.
 * Returns 1 if parked, 0 if the list is full — in which case the caller LEAKS `p`,
 * which is the correct outcome: ~96 bytes of leak is harmless, freeing an object a
 * live callback still holds is the crash this whole file exists to prevent. */
static int mm_retire_park(mm_retire_list *L, void *p, double now_s, double quiesce_s,
                          mm_retire_free_fn fr) {
    if (!L || !p) { return 0; }
    mm_retire_sweep(L, now_s, quiesce_s, fr);
    if (L->n >= MM_RETIRE_CAP) { return 0; }
    L->p[L->n] = p;
    L->at[L->n] = now_s;
    L->n++;
    return 1;
}

#endif /* MMRETIRE_H */
