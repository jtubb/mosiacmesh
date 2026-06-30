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

// side-table: backend MediaPlayerPrivateiPhone* -> MMTransplantEngine* (dict retains the engine)
static NSMutableDictionary *gEngines = nil;
static inline id keyFor(void *self){ return [NSValue valueWithPointer:self]; }
static MMTransplantEngine *engineFor(void *self){ return gEngines ? [gEngines objectForKey:keyFor(self)] : nil; }

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

static void h_load(void *self, void *strRef){
    o_load(self, strRef);                       // let WebCore run its load state machine
    NSString *url = nil;
    if (gCreateCF){ CFStringRef cf = gCreateCF(strRef); if (cf) url = (__bridge_transfer NSString *)cf; }
    if (!url || url.length == 0) return;
    void *mp = *(void **)((char *)self + 4);    // m_player (REFINDINGS)
    MMTransplantEngine *eng = [[MMTransplantEngine alloc] initWithMediaPlayer:mp];
    [gEngines setObject:eng forKey:keyFor(self)];   // replaces+releases any prior engine for this backend
    [eng loadURL:url];
}
static void h_seek(void *self, float t){ MMTransplantEngine *e=engineFor(self); if(e)[e seekTo:(double)t]; else o_seek(self,t); }
static void h_setRate(void *self, float r){ MMTransplantEngine *e=engineFor(self); if(e)[e setRate:r]; else o_setRate(self,r); }
static void h_play(void *self){ MMTransplantEngine *e=engineFor(self); if(e)[e play]; else o_play(self); }
static void h_pause(void *self){ MMTransplantEngine *e=engineFor(self); if(e)[e pause]; else o_pause(self); }
static void h_cancelLoad(void *self){ if(engineFor(self)) [gEngines removeObjectForKey:keyFor(self)]; o_cancelLoad(self); }
static float h_currentTime(void *self){ MMTransplantEngine *e=engineFor(self); return e?(float)[e currentTime]:o_currentTime(self); }
static float h_duration(void *self){ MMTransplantEngine *e=engineFor(self); return e?(float)[e duration]:o_duration(self); }
static float h_maxTimeSeekable(void *self){ MMTransplantEngine *e=engineFor(self); return e?(float)[e duration]:o_maxTimeSeekable(self); }
static bool  h_paused(void *self){ MMTransplantEngine *e=engineFor(self); return e?[e paused]:o_paused(self); }
static int   h_networkState(void *self){ MMTransplantEngine *e=engineFor(self); return e?[e networkState]:o_networkState(self); }
static int   h_readyState(void *self){ MMTransplantEngine *e=engineFor(self); return e?[e readyState]:o_readyState(self); }

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
        {"__ZNK7WebCore24MediaPlayerPrivateiPhone11currentTimeEv",(void*)h_currentTime,(void**)&o_currentTime},
        {"__ZNK7WebCore24MediaPlayerPrivateiPhone8durationEv",(void*)h_duration,(void**)&o_duration},
        {"__ZNK7WebCore24MediaPlayerPrivateiPhone15maxTimeSeekableEv",(void*)h_maxTimeSeekable,(void**)&o_maxTimeSeekable},
        {"__ZNK7WebCore24MediaPlayerPrivateiPhone6pausedEv",(void*)h_paused,(void**)&o_paused},
        {"__ZNK7WebCore24MediaPlayerPrivateiPhone12networkStateEv",(void*)h_networkState,(void**)&o_networkState},
        {"__ZNK7WebCore24MediaPlayerPrivateiPhone10readyStateEv",(void*)h_readyState,(void**)&o_readyState},
    };
    for (unsigned i=0;i<sizeof(H)/sizeof(H[0]);i++){
        void *s = MSFindSymbol(NULL, H[i].sym);
        char b[160]; snprintf(b,sizeof b,"[mmvideo] hook %s = %p", H[i].sym, s); mmlog(b);
        if (s) MSHookFunction(s, H[i].hook, H[i].orig);
    }
    mmlog("[mmvideo] hooks installed (deferred)");
}

%ctor {
    mmlog("[mmvideo] phase3.2 ctor (plain ObjC; deferring hook install)");
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(2 * NSEC_PER_SEC)),
                   dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_DEFAULT, 0), ^{ mm_install(); });
}
