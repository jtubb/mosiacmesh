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

static void mmlog(const char *m){ FILE*f=fopen("/tmp/mmvideo.log","a"); if(f){fprintf(f,"%s\n",m);fclose(f);} }

// side-table: backend MediaPlayerPrivateiPhone* -> MMEngine* (a C struct, wrapped in
// NSValue so the dict can hold it; the engine is heap-owned by us, freed on drop).
static NSMutableDictionary *gEngines = nil;
static inline id keyFor(void *self){ return [NSValue valueWithPointer:self]; }
static MMEngine *engineFor(void *self){
    id v = gEngines ? [gEngines objectForKey:keyFor(self)] : nil;
    return v ? (MMEngine *)[v pointerValue] : NULL;
}
static void dropEngine(void *self){
    id k = keyFor(self), v = [gEngines objectForKey:k];
    if (v){ mm_engine_free((MMEngine *)[v pointerValue]); [gEngines removeObjectForKey:k]; }
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
    mmlog("[mmvideo] h_load: ENTER");
    o_load(self, strRef);                       // let WebCore run its load state machine
    mmlog("[mmvideo] h_load: o_load done");
    NSString *url = nil;
    if (gCreateCF){ CFStringRef cf = gCreateCF(strRef); if (cf) url = (__bridge_transfer NSString *)cf; }
    mmlog([[NSString stringWithFormat:@"[mmvideo] h_load: url=%@", url ? url : @"(nil)"] UTF8String]);
    if (!url || url.length == 0) return;
    void *mp = *(void **)((char *)self + 4);    // m_player (REFINDINGS)
    dropEngine(self);                            // free+remove any prior engine for this backend
    MMEngine *eng = mm_engine_create(mp);
    mmlog(eng ? "[mmvideo] h_load: engine created" : "[mmvideo] h_load: engine create FAILED");
    if (!eng) return;
    [gEngines setObject:[NSValue valueWithPointer:eng] forKey:keyFor(self)];
    mm_engine_load(eng, [url UTF8String]);
    mmlog("[mmvideo] h_load: mm_engine_load returned");
}
static void h_seek(void *self, float t){ MMEngine *e=engineFor(self); mmlog(e?"[mmvideo] h_seek -> engine":"[mmvideo] h_seek -> orig"); if(e) mm_engine_seek(e,(double)t); else o_seek(self,t); }
static void h_setRate(void *self, float r){ MMEngine *e=engineFor(self); mmlog(e?"[mmvideo] h_setRate -> engine":"[mmvideo] h_setRate -> orig"); if(e) mm_engine_set_rate(e,r); else o_setRate(self,r); }
static void h_play(void *self){ MMEngine *e=engineFor(self); mmlog(e?"[mmvideo] h_play -> engine":"[mmvideo] h_play -> orig"); if(e){ mm_engine_play(e); mm_slot_in(self); } else o_play(self); }
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
        // GETTERS TEMPORARILY DISABLED (bring-up): hooking the const getters
        // (currentTime/duration/maxTimeSeekable/paused/networkState/readyState)
        // destabilizes WebCore's pre-load media-state polling -> SIGSEGV before
        // h_load even fires. Leave them ORIGINAL for now (the original engine loads via
        // o_load and reports state); re-add carefully once playback works. The h_* getter
        // fns + o_* slots stay defined (unused) so nothing else changes.
        // {"__ZNK..11currentTimeEv",(void*)h_currentTime,(void**)&o_currentTime},
        // {"__ZNK..8durationEv",(void*)h_duration,(void**)&o_duration},
        // {"__ZNK..15maxTimeSeekableEv",(void*)h_maxTimeSeekable,(void**)&o_maxTimeSeekable},
        // {"__ZNK..6pausedEv",(void*)h_paused,(void**)&o_paused},
        // {"__ZNK..12networkStateEv",(void*)h_networkState,(void**)&o_networkState},
        // {"__ZNK..10readyStateEv",(void*)h_readyState,(void**)&o_readyState},
    };
    for (unsigned i=0;i<sizeof(H)/sizeof(H[0]);i++){
        void *s = MSFindSymbol(NULL, H[i].sym);
        char b[160]; snprintf(b,sizeof b,"[mmvideo] hook %s = %p", H[i].sym, s); mmlog(b);
        if (s) MSHookFunction(s, H[i].hook, H[i].orig);
    }
    mmlog("[mmvideo] hooks installed (main-queue)");
}

%ctor {
    // Deferred install (off the launch path), but on the MAIN queue — NOT a background
    // queue. The previous background-queue install raced WebCore: MSHookFunction was
    // overwriting a getter's prologue (e.g. networkState) on a bg thread WHILE the main
    // thread concurrently executed it -> corrupted call -> SIGSEGV (self=NULL). On the
    // main queue the overwrite serializes with WebCore's (main-thread) media calls, so
    // no method is ever mid-rewrite when called.
    mmlog("[mmvideo] phase3.2 ctor (plain ObjC; main-queue hook install)");
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(2 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{ mm_install(); });
}
