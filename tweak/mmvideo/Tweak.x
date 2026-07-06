// Plain ObjC + Logos (.x). Converted from Tweak.xm — see MMTransplantEngine.m header
// for why ObjC++ (.xm) crashes at load on iOS 5.1. Behavior is the Phase-3.1
// interception layer unchanged, except the MSHookFunction install is DEFERRED off the
// launch critical path onto a background queue (proven-safe load pattern; the side
// table is initialized there too, before any hook can fire).
#import <substrate.h>
#import <Foundation/Foundation.h>
#import <CoreFoundation/CoreFoundation.h>
#import "MMTransplantEngine.h"
#import <dispatch/dispatch.h>
#import <stdio.h>
#import <unistd.h>
#import <mach/mach.h>         // vm_protect for the vtable-slot overwrite (currentTime/duration)

static void mmlog(const char *m){ FILE*f=fopen("/tmp/mmvideo.log","a"); if(f){fprintf(f,"%s\n",m);fclose(f);} }

// side-table: backend MediaPlayerPrivateiPhone* -> MMEngine* (a C struct, wrapped in
// NSValue so the dict can hold it; the engine is heap-owned by us, freed on drop).
static NSMutableDictionary *gEngines = nil;
// gCurrentEngine: a single atomic pointer to the active engine, for the VTABLE getters
// (currentTime/duration) which can be called from the QuickTime-plugin thread — so they must
// NOT touch the non-thread-safe gEngines dict. The wall shows one video, so one engine.
static MMEngine *gCurrentEngine = NULL;
volatile float g_curTime = 0.0f; volatile float g_curDuration = 0.0f;   // I3: 4-byte atomic on armv7; getter reads these, never the freeable engine
static inline id keyFor(void *self){ return [NSValue valueWithPointer:self]; }
static MMEngine *engineFor(void *self){
    id v = gEngines ? [gEngines objectForKey:keyFor(self)] : nil;
    return v ? (MMEngine *)[v pointerValue] : NULL;
}
static void dropEngine(void *self){
    id k = keyFor(self), v = [gEngines objectForKey:k];
    if (v){ MMEngine *e=(MMEngine *)[v pointerValue]; if(e==gCurrentEngine) gCurrentEngine=NULL;
            [gEngines removeObjectForKey:k]; mm_engine_mark_dead(e); mm_engine_pause(e);
            dispatch_async(dispatch_get_main_queue(), ^{ mm_engine_free(e); });   // C1: free on main = serialized w/ observer block
    }
}

typedef CFStringRef (*CreateCFFn)(void *);   // WTF::String::createCFString() const
static CreateCFFn gCreateCF = NULL;

static void  (*o_load)(void*,void*);
static void  (*o_seek)(void*,float);
static void  (*o_setRate)(void*,float);
static void  (*o_play)(void*);
static void  (*o_pause)(void*);
static void  (*o_cancelLoad)(void*);
static float (*o_currentTime)(void*);
static float (*o_duration)(void*);
static float (*o_maxTimeSeekable)(void*);
static bool  (*o_paused)(void*);
static int   (*o_networkState)(void*);
static int   (*o_readyState)(void*);
static void  (*o_HTMLplay)(void*, bool);      // WebCore::HTMLMediaElement::play(bool isUserGesture)

static void mm_install_vtable_getters(void *self);   // defined below; called from h_load (once)

// 3.2b: locate FigPluginView (controller this+8, ivar +0x4 — REFINDINGS §9) and hand the
// raw pointer to the engine, which does the CALayer work (it already imports QuartzCore +
// loads fine). Tweak.x stays minimal — NO QuartzCore/CALayer/objc_msgSend/@selector here
// (adding those to Tweak.x crashed the load — §19). Guard reads with the pointer sanity check.
static void mm_slot_in(void *self){
    MMEngine *e = engineFor(self);
    if (!e) return;
    void *ctrlp = *(void **)((char *)self + 8);                 // FPVMediaPlayerHelper
    if (!ctrlp || (uintptr_t)ctrlp < 0x1000 || ((uintptr_t)ctrlp & 3)) return;
    void *fpvp  = *(void **)((char *)ctrlp + 4);                // FigPluginView (+0x4)
    if (!fpvp  || (uintptr_t)fpvp  < 0x1000 || ((uintptr_t)fpvp  & 3)) return;
    mm_engine_attach_layer(e, fpvp);
}

static void h_load(void *self, void *strRef){
    o_load(self, strRef);                       // let WebCore run its load state machine
    NSString *url = nil;
    if (gCreateCF){ CFStringRef cf = gCreateCF(strRef); if (cf) url = (__bridge_transfer NSString *)cf; }
    if (!url || url.length == 0) return;
    void *mp = *(void **)((char *)self + 4);    // m_player (REFINDINGS)
    dropEngine(self);                            // free+remove any prior engine for this backend
    MMEngine *eng = mm_engine_create(self, mp);  // self = backend, for ivar mirroring (Option A)
    if (!eng) return;
    [gEngines setObject:[NSValue valueWithPointer:eng] forKey:keyFor(self)];
    gCurrentEngine = eng;                        // for the vtable getters (atomic, cross-thread)
    mm_engine_load(eng, [url UTF8String]);
    mm_install_vtable_getters(self);             // redirect currentTime/duration to our engine (once)
}
static void h_seek(void *self, float t){ MMEngine *e=engineFor(self); { char _b[64]; snprintf(_b,sizeof _b,"[dbg] SEEK t=%.2f gct=%.2f",(double)t,(double)g_curTime); mmlog(_b); } if(e) mm_engine_seek(e,(double)t); else o_seek(self,t); }
static void h_setRate(void *self, float r){ MMEngine *e=engineFor(self); if(e) mm_engine_set_rate(e,r); else o_setRate(self,r); }
static void h_play(void *self){ MMEngine *e=engineFor(self); if(e){ mm_engine_play(e); mm_slot_in(self); } else o_play(self); }
static void h_pause(void *self){ MMEngine *e=engineFor(self); if(e) mm_engine_pause(e); else o_pause(self); }
static void h_cancelLoad(void *self){ if(engineFor(self)) dropEngine(self); o_cancelLoad(self); }
static float h_currentTime(void *self){ MMEngine *e=engineFor(self); return e?(float)mm_engine_current_time(e):o_currentTime(self); }
// duration/maxTimeSeekable come from the ORIGINAL engine (which also loaded the URL via
// o_load, so it knows the duration) — avoids the engine doing a stret [item duration] +
// CMTimeGetSeconds, both of which crash the iOS-5.1 load (REFINDINGS §13).
static float h_duration(void *self){ return o_duration(self); }
static float h_maxTimeSeekable(void *self){ return o_maxTimeSeekable(self); }
static bool  h_paused(void *self){ MMEngine *e=engineFor(self); return e? (mm_engine_paused(e)?true:false) : o_paused(self); }
static int   h_networkState(void *self){ MMEngine *e=engineFor(self); return e?mm_engine_network_state(e):o_networkState(self); }
static int   h_readyState(void *self){ MMEngine *e=engineFor(self); return e?mm_engine_ready_state(e):o_readyState(self); }

// AUTOPLAY: WebCore's HTMLMediaElement gates load() + play()/setRate() on m_restrictions,
// which it derives from Settings::mediaPlaybackRequiresUserGesture() (true on iOS-5). That
// gate is UPSTREAM of MediaPlayerPrivateiPhone — so our action hooks can't defeat it; the
// backend never even sees load()/play() without a gesture. Forcing this getter false makes
// WebCore skip the RequireUserGestureFor{Load,RateChange}Restriction flags, so the NORMAL
// pipeline (incl. our backend hooks) runs unattended — no tap. Verified call sites (534.48.3):
//   load(): if (m_restrictions & RequireUserGestureForLoadRestriction && !isUserGesture) ec=INVALID_STATE_ERR;
//   play(): if (m_restrictions & RequireUserGestureForRateChangeRestriction && !isUserGesture) return;
// AUTOPLAY: HTMLMediaElement::play(bool isUserGesture) gates with (verified 534.48.3):
//   if (m_restrictions & RequireUserGestureForRateChangeRestriction && !isUserGesture) return;
// load() is NOT gated (h_load fires unattended) but play() IS — so a no-tap <video>.play()
// returns early and our backend play() never fires (no video). Force isUserGesture=true so the
// gate passes; everything downstream is the PROVEN path (h_play -> mm_engine_play + slot-in).
// This is a main-thread DOM call (not a hot cross-thread getter), so safe to patch — unlike §21.
static void h_HTMLplay(void *self, bool isUserGesture){ (void)isUserGesture; o_HTMLplay(self, true); }

// VTABLE getters for currentTime/duration (computed from the controller — not ivars, so the
// ivar-mirror can't reach them). They're C++ virtuals; we overwrite the vtable SLOT (one
// aligned pointer write = atomic), NOT the function prologue (the §21 torn-patch crash). Use
// gCurrentEngine (atomic), never gEngines — these may run on the QuickTime-plugin thread.
static float h_vt_currentTime(void *self){ return gCurrentEngine ? (float)g_curTime : o_currentTime(self); }
static float h_vt_duration(void *self){ return gCurrentEngine ? (float)g_curDuration : o_duration(self); }

// Make the vtable slot writable (shared-cache page -> COW private copy via VM_PROT_COPY, the
// same way code patching works) and overwrite it. Aligned word write is atomic for readers.
static void mm_vt_set(void **slot, void *fn){
    vm_protect(mach_task_self(), (vm_address_t)((uintptr_t)slot & ~((uintptr_t)4095)), 8192,
               FALSE, VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY);
    *slot = fn;
}
// Find the currentTime/duration slots by scanning the object's vtable for their runtime
// addresses (MSFindSymbol — robust against ASLR slide), then overwrite. Once-only.
static void mm_install_vtable_getters(void *self){
    static int done = 0;
    if (done || !self) return;
    void **vt = *(void ***)self;            // object vptr -> virtual function array
    if (!vt) return;
    void *ct = MSFindSymbol(NULL, "__ZNK7WebCore24MediaPlayerPrivateiPhone11currentTimeEv");
    void *du = MSFindSymbol(NULL, "__ZNK7WebCore24MediaPlayerPrivateiPhone8durationEv");
    int hits = 0;
    for (int i = 0; i < 64 && hits < 2; i++){
        void *s = vt[i];
        if (ct && s == ct){ o_currentTime = (float(*)(void*))ct; mm_vt_set(&vt[i], (void*)h_vt_currentTime); hits++; }
        else if (du && s == du){ o_duration = (float(*)(void*))du; mm_vt_set(&vt[i], (void*)h_vt_duration); hits++; }
    }
    done = (hits > 0);
    char b[64]; snprintf(b,sizeof b,"[mmvideo] vtable getters hooked: %d/2", hits); mmlog(b);
}

static void mm_install(void){
    gEngines = [[NSMutableDictionary alloc] init];
    gCreateCF = (CreateCFFn)MSFindSymbol(NULL, "__ZNK3WTF6String14createCFStringEv");
    struct { const char *sym; void *hook; void **orig; } H[] = {
        {"__ZN7WebCore24MediaPlayerPrivateiPhone4loadERKN3WTF6StringE",(void*)h_load,(void**)&o_load},
        {"__ZN7WebCore24MediaPlayerPrivateiPhone4seekEf",(void*)h_seek,(void**)&o_seek},
        {"__ZN7WebCore24MediaPlayerPrivateiPhone7setRateEf",(void*)h_setRate,(void**)&o_setRate},
        {"__ZN7WebCore24MediaPlayerPrivateiPhone4playEv",(void*)h_play,(void**)&o_play},
        {"__ZN7WebCore24MediaPlayerPrivateiPhone5pauseEv",(void*)h_pause,(void**)&o_pause},
        {"__ZN7WebCore24MediaPlayerPrivateiPhone10cancelLoadEv",(void*)h_cancelLoad,(void**)&o_cancelLoad},
        // The const getters (currentTime/duration/maxTimeSeekable/paused/networkState/
        // readyState) are NOT hooked: they're polled at high frequency from the QuickTime
        // media plugin's OWN thread, so MSHookFunction's multi-instruction prologue rewrite
        // races that thread -> torn code -> SIGBUS in WebCore (crash 2026-06-30, §21 — the
        // backtrace faulted in WebCore on the QuickTime-plugin thread, not in our hook). No
        // main-thread install timing avoids it (the racing caller isn't the main thread).
        // The player works fine without getter feedback; WebCore's <video> model just stays
        // cosmetically out of sync. The h_* getter fns + o_* slots stay defined (unused) for
        // a future NON-patching state-sync approach.
    };
    for (unsigned i=0;i<sizeof(H)/sizeof(H[0]);i++){
        void *s = MSFindSymbol(NULL, H[i].sym);
        if (s) MSHookFunction(s, H[i].hook, H[i].orig);
    }
    // AUTOPLAY: defeat the play/rate-change gesture gate by forcing HTMLMediaElement::play's
    // isUserGesture arg true. (The earlier Settings::mediaPlaybackRequiresUserGesture approach
    // was wrong — that symbol doesn't exist in 534.48.3, and load isn't gated anyway; only
    // play() is.) NULL-guarded: absent symbol -> hook simply doesn't install.
    void *hp = MSFindSymbol(NULL, "__ZN7WebCore16HTMLMediaElement4playEb");
    if (hp) { MSHookFunction(hp, (void*)h_HTMLplay, (void**)&o_HTMLplay);
              mmlog("[mmvideo] autoplay: HTMLMediaElement::play(bool) hooked -> force gesture=true"); }
    else      mmlog("[mmvideo] autoplay: HTMLMediaElement::play symbol NOT FOUND");
    mmlog("[mmvideo] hooks installed");   // single load heartbeat (deploy verification)
}

// NOTE: a WebSocket-enable experiment lived here (force RuntimeEnabledFeatures::webSocketEnabled
// / WebSocket::isAvailable true). REMOVED — iOS-5.1's built-in WebSocket is the disabled/broken
// Hixie-76-era impl that does NOT interoperate with the RFC-6455 aiohttp server, so forcing it on
// just makes SockJS commit to websocket and fail ("cannot connect") in BOTH Safari and the
// webclip. The whole iOS-5.1 fleet correctly runs SockJS over XHR; enabling ws is a dead end
// (only a native RFC-6455 transplant behind window.WebSocket would work — not worth it).

%ctor {
    // Deferred install (off the launch path), but on the MAIN queue — NOT a background
    // queue. The previous background-queue install raced WebCore: MSHookFunction was
    // overwriting a getter's prologue (e.g. networkState) on a bg thread WHILE the main
    // thread concurrently executed it -> corrupted call -> SIGSEGV (self=NULL). On the
    // main queue the overwrite serializes with WebCore's (main-thread) media calls, so
    // no method is ever mid-rewrite when called.
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(2 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{ mm_install(); });
}
