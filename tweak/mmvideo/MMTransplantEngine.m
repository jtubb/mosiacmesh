// CLASS-FREE C engine (see MMTransplantEngine.h for why no ObjC class). Compiled as
// ObjC (.m) + ARC, but defines NO @interface/@implementation. AVFoundation objects are
// held as retained void* (ARC __bridge_retained/_transfer) and driven with NORMAL
// message syntax on Apple's classes — the compiler emits the correct objc_msgSend /
// objc_msgSend_stret / CMTime-by-value ABI from AVFoundation.h, so we never hand-cast
// objc_msgSend. KVO (which needs an ObjC observer object) is replaced by polling
// AVPlayerItem.status inside the periodic time-observer block.
#import "MMTransplantEngine.h"
#import "mmurl.h"
#import <Foundation/Foundation.h>
#import <AVFoundation/AVFoundation.h>
#import <CoreMedia/CoreMedia.h>
#import <substrate.h>

typedef void (*MMVoidFn)(void *);

struct MMEngine {
    void *mp;          // WebCore MediaPlayer* (callback receiver; not owned)
    void *player;      // AVPlayer*        (retained)
    void *item;        // AVPlayerItem*    (retained)
    void *layer;       // AVPlayerLayer*   (retained)
    void *timeObserver;// id               (retained)
    MMVoidFn netChanged, readyChanged, timeChanged;
    int net, ready;
};

static inline AVPlayer     *PL(MMEngine *e){ return (__bridge AVPlayer     *)e->player; }
static inline AVPlayerItem *IT(MMEngine *e){ return (__bridge AVPlayerItem *)e->item; }

MMEngine *mm_engine_create(void *mp) {
    MMEngine *e = (MMEngine *)calloc(1, sizeof(MMEngine));
    if (!e) return NULL;
    e->mp = mp;
    e->netChanged   = (MMVoidFn)MSFindSymbol(NULL, "__ZN7WebCore11MediaPlayer19networkStateChangedEv");
    e->readyChanged = (MMVoidFn)MSFindSymbol(NULL, "__ZN7WebCore11MediaPlayer17readyStateChangedEv");
    e->timeChanged  = (MMVoidFn)MSFindSymbol(NULL, "__ZN7WebCore11MediaPlayer11timeChangedEv");
    return e;
}

// On the periodic time-observer (main queue): poll status -> net/ready, fire WebCore
// callbacks. Runs on the main thread already, so call the callbacks directly.
static void mm_engine_poll(MMEngine *e) {
    if (!e || !e->item) return;
    int net = e->net, ready = e->ready;
    long st = (long)[IT(e) status];           // AVPlayerItemStatus
    mm_status_to_states((int)st, &net, &ready);
    BOOL changed = (net != e->net) || (ready != e->ready);
    e->net = net; e->ready = ready;
    if (changed) {
        if (e->netChanged)   e->netChanged(e->mp);
        if (e->readyChanged) e->readyChanged(e->mp);
    }
    if (e->timeChanged) e->timeChanged(e->mp);
}

static void mm_engine_teardown(MMEngine *e) {
    if (e->timeObserver && e->player) {
        [PL(e) removeTimeObserver:(__bridge id)e->timeObserver];
    }
    if (e->timeObserver) { id t = (__bridge_transfer id)e->timeObserver; e->timeObserver = NULL; (void)t; }
    if (e->layer)        { id t = (__bridge_transfer id)e->layer;        e->layer = NULL;        (void)t; }
    if (e->item)         { id t = (__bridge_transfer id)e->item;         e->item = NULL;         (void)t; }
    if (e->player)       { id t = (__bridge_transfer id)e->player;       e->player = NULL;       (void)t; }
}

void mm_engine_load(MMEngine *e, const char *url) {
    if (!e || !url) return;
    @autoreleasepool {
        char path[512];
        NSURL *u = nil;
        if (mm_url_to_path(url, path, sizeof path)) u = [NSURL URLWithString:[NSString stringWithUTF8String:path]];
        else u = [NSURL URLWithString:[NSString stringWithUTF8String:url]];
        if (!u) return;
        mm_engine_teardown(e);
        AVPlayerItem  *item   = [AVPlayerItem playerItemWithURL:u];
        AVPlayer      *player = [AVPlayer playerWithPlayerItem:item];
        AVPlayerLayer *layer  = [AVPlayerLayer playerLayerWithPlayer:player];
        e->item   = (__bridge_retained void *)item;
        e->player = (__bridge_retained void *)player;
        e->layer  = (__bridge_retained void *)layer;
        e->net = 2; e->ready = 0;   // Loading / HaveNothing until status advances
        MMEngine *eng = e;          // capture the C pointer (no retain cycle)
        id obs = [player addPeriodicTimeObserverForInterval:CMTimeMake(1, 4)
                     queue:dispatch_get_main_queue()
                     usingBlock:^(CMTime t){ (void)t; mm_engine_poll(eng); }];
        e->timeObserver = (__bridge_retained void *)obs;
    }
}

void mm_engine_play(MMEngine *e)  { if (e && e->player) [PL(e) play]; }
void mm_engine_pause(MMEngine *e) { if (e && e->player) [PL(e) pause]; }
int  mm_engine_paused(MMEngine *e){ return (e && e->player) ? (PL(e).rate == 0.0f) : 1; }

void mm_engine_seek(MMEngine *e, double seconds) {
    if (!e || !e->player) return;
    CMTime t = CMTimeMakeWithSeconds(seconds, (int32_t)NSEC_PER_SEC);
    [PL(e) seekToTime:t toleranceBefore:kCMTimeZero toleranceAfter:kCMTimeZero];   // frame-accurate
}

void mm_engine_set_rate(MMEngine *e, float rate) {
    if (!e || !e->player) return;
    PL(e).rate = mm_clamp_rate(rate, 1, 1);   // iOS 5.1 has no canPlayFast/SlowForward; assume capable
}

double mm_engine_current_time(MMEngine *e) {
    if (!e || !e->player) return 0.0;
    double s = CMTimeGetSeconds([PL(e) currentTime]);
    return (s != s) ? 0.0 : s;                // NaN guard
}
double mm_engine_duration(MMEngine *e) {
    if (!e || !e->item) return 0.0;
    double s = CMTimeGetSeconds([IT(e) duration]);
    return (s != s || s < 0) ? 0.0 : s;
}
int   mm_engine_network_state(MMEngine *e) { return e ? e->net : 0; }
int   mm_engine_ready_state(MMEngine *e)   { return e ? e->ready : 0; }
void *mm_engine_player_layer(MMEngine *e)  { return e ? e->layer : NULL; }   // AVPlayerLayer* (CALayer*)

void mm_engine_free(MMEngine *e) {
    if (!e) return;
    @autoreleasepool { mm_engine_teardown(e); }
    free(e);
}
