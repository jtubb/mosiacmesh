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
#import <objc/message.h>      // objc_msgSend for the dynamic FigPluginView.layer call (3.2b)
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
    double curTime;    // cached from the observer block (avoids stret [player currentTime])
    int playing;       // tracked via play/pause/setRate (avoids reading .rate)
    int slotted;       // 3.2b: AVPlayerLayer attached to FigPluginView (once)
    float desiredRate; // last NONZERO rate requested — what play() resumes at (see mm_engine_play)
};

static inline AVPlayer     *PL(MMEngine *e){ return (__bridge AVPlayer     *)e->player; }
static inline AVPlayerItem *IT(MMEngine *e){ return (__bridge AVPlayerItem *)e->item; }

// seconds from a CMTime by hand — value/timescale. Avoids CMTimeGetSeconds and the
// struct-return ([currentTime]/[duration]) messaging, both of which crash the iOS-5.1
// load (REFINDINGS §13). int64->double goes through the provided mmbuiltins __floatdidf.
static inline double mm_secs(CMTime t){ return t.timescale ? (double)t.value / (double)t.timescale : 0.0; }

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
                     usingBlock:^(CMTime t){
                         eng->curTime = mm_secs(t);   // cache currentTime (avoids stret read)
                         mm_engine_poll(eng);
                     }];
        e->timeObserver = (__bridge_retained void *)obs;
    }
}

void mm_engine_play(MMEngine *e)  {
    if (e && e->player){
        // RESUME AT DESIRED RATE — do NOT call [AVPlayer play], which is defined as
        // setRate:1.0 and would clobber a 2x rate. WebCore re-asserts setRate+play on a
        // tight loop (~thousands/sec); with [play] last in each pair the rate snapped back
        // to 1.0 every frame. Setting rate>0 directly both starts playback AND honors 2x.
        float r = e->desiredRate > 0.0f ? e->desiredRate : 1.0f;
        PL(e).rate = r;  e->playing = 1;
    }
}
void mm_engine_pause(MMEngine *e) { if (e && e->player){ [PL(e) pause]; e->playing = 0; } }
int  mm_engine_paused(MMEngine *e){ return e ? !e->playing : 1; }   // tracked flag, no .rate read

void mm_engine_seek(MMEngine *e, double seconds) {
    if (!e || !e->player) return;
    CMTime t = CMTimeMakeWithSeconds(seconds, (int32_t)NSEC_PER_SEC);
    [PL(e) seekToTime:t toleranceBefore:kCMTimeZero toleranceAfter:kCMTimeZero];   // frame-accurate
}

void mm_engine_set_rate(MMEngine *e, float rate) {
    if (!e || !e->player) return;
    float r = mm_clamp_rate(rate, 1, 1);   // iOS 5.1 has no canPlayFast/SlowForward; assume capable
    if (r > 0.0f) e->desiredRate = r;      // remember nonzero rate so play() resumes at it
    PL(e).rate = r;
    e->playing = (r != 0.0f);
}

// currentTime is the value cached by the observer block (avoids the stret
// [player currentTime]); duration is left to the ORIGINAL engine via the Tweak.x
// fallback (avoids stret [item duration] + CMTimeGetSeconds — REFINDINGS §13).
double mm_engine_current_time(MMEngine *e) { return e ? e->curTime : 0.0; }
double mm_engine_duration(MMEngine *e) {
    (void)e;
    return 0.0;   // sentinel: Tweak.x routes duration/maxTimeSeekable to the original engine
}
int   mm_engine_network_state(MMEngine *e) { return e ? e->net : 0; }
int   mm_engine_ready_state(MMEngine *e)   { return e ? e->ready : 0; }
void *mm_engine_player_layer(MMEngine *e)  { return e ? e->layer : NULL; }   // AVPlayerLayer* (CALayer*)

// 3.2b + polish: add our AVPlayerLayer as the visible sublayer of FigPluginView.layer,
// SCREEN-sized, with the original's render sublayers hidden (neuter). Done HERE (engine .m
// already imports AVFoundation/QuartzCore + loads fine) so Tweak.x stays minimal (§19).
// Once-only via `slotted`.
//
// FRAME: our layer must be SCREEN-sized, adapting to the device orientation (this iPad is
// PORTRAIT 768x1024, not landscape — a hardcode is wrong for a mixed-orientation fleet). The
// FigPluginView's own bounds is content-sized (too small). The right source is the ROOT layer
// (walk superlayer up to the window's layer) = the real screen in the app's orientation. Read
// it no-stret: CALayer boxes `bounds` as an NSValue (CA KVC, valueForKey: -> id), and
// -[NSValue getValue:&rect] is void/pointer (no stret — §13). CALayer sublayers aren't clipped
// to the host (masksToBounds defaults NO), so even if our frame overflows the FigPluginView it
// fills the display. Fallback = iPad-1 portrait if the read fails.
//
// NEUTER: the original engine still o_loads (required — it advances WebCore networkState past
// NETWORK_EMPTY so play() can fire) but it stalls on the http URL and never decodes. Hide the
// host's existing render sublayers and put our opaque layer on top, so only our video shows.
void mm_engine_attach_layer(MMEngine *e, void *fpvp) {
    if (!e || !e->layer || e->slotted || !fpvp) return;
    AVPlayerLayer *vlayer = (__bridge AVPlayerLayer *)e->layer;
    id fpv = (__bridge id)fpvp;                       // FigPluginView (UIView subclass)
    CALayer *host = ((id (*)(id, SEL))objc_msgSend)(fpv, @selector(layer));
    if (!host) return;
    CALayer *root = host, *sup;                       // walk to the window's layer (screen-sized)
    while ((sup = [root superlayer])) root = sup;
    CGRect rb = (CGRect){ {0,0}, {0,0} };
    id bv = [root valueForKey:@"bounds"];             // NSValue(CGRect) — no stret
    if (bv) [bv getValue:&rb];                          // void/pointer — no stret
    double ww = rb.size.width, hh = rb.size.height;
    if (ww < 1.0 || hh < 1.0 || ww > 4096.0 || hh > 4096.0) { ww = 768.0; hh = 1024.0; }  // fallback (iPad-1 portrait)
    vlayer.frame = (CGRect){ {0, 0}, {ww, hh} };               // screen-sized, this device's orientation
    vlayer.videoGravity = AVLayerVideoGravityResizeAspectFill; // fill the layer, no letterbox
    for (CALayer *sub in [host sublayers]) sub.hidden = YES;   // NEUTER the original's surface
    [host addSublayer:vlayer];                                 // ours: visible, on top
    e->slotted = 1;
}

void mm_engine_free(MMEngine *e) {
    if (!e) return;
    @autoreleasepool { mm_engine_teardown(e); }
    free(e);
}
