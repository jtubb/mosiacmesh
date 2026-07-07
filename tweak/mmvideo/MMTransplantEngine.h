// CLASS-FREE C engine API. The engine MUST NOT be an ObjC class: a static ObjC
// @interface/@implementation crashes the dylib load on iOS 5.1 (the 9.3-SDK emits
// newer ObjC2 class metadata the 5.1 runtime mis-parses at map_images — REFINDINGS
// §11), and the runtime class-creation APIs (objc_allocateClassPair…) don't flat-bind
// (§12). So we drive AVFoundation purely via objc_msgSend + @selector() literals over
// objects held as retained void*, with NO class of our own. Phase-3 hooks create one
// engine per <video> backend and route MediaPlayerPrivateiPhone calls into it.
#ifndef MMTRANSPLANTENGINE_H
#define MMTRANSPLANTENGINE_H

typedef struct MMEngine MMEngine;

MMEngine *mm_engine_create(void *backend, void *webCoreMediaPlayer);  // backend = MediaPlayerPrivateiPhone*
                                                       // (self; for ivar mirroring — Option A);
                                                       // webCoreMediaPlayer = MediaPlayer* (callbacks)
void   mm_engine_load(MMEngine *e, const char *url);   // localhost cache URL -> file:// (mm_url_to_path)
void   mm_engine_play(MMEngine *e);
void   mm_engine_pause(MMEngine *e);
void mm_engine_mark_dead(MMEngine *e);
void mm_hook_controller(void *backend);
int    mm_engine_paused(MMEngine *e);
void   mm_engine_seek(MMEngine *e, double seconds);    // frame-accurate (zero tolerance)
void   mm_engine_set_rate(MMEngine *e, float rate);    // mm_clamp_rate -> AVPlayer.rate
double mm_engine_current_time(MMEngine *e);
double mm_engine_duration(MMEngine *e);
int    mm_engine_network_state(MMEngine *e);           // WebCore NetworkState int
int    mm_engine_ready_state(MMEngine *e);             // WebCore ReadyState int
void  *mm_engine_player_layer(MMEngine *e);            // AVPlayerLayer* (CALayer*) for Phase-3 slot-in
void   mm_engine_attach_layer(MMEngine *e, void *figPluginView);  // 3.2b: add our AVPlayerLayer
                                                       // as a sublayer of FigPluginView.layer (done
                                                       // in the engine .m where QuartzCore is already
                                                       // cleanly in play; Tweak.x just passes the ptr)
void   mm_engine_free(MMEngine *e);

#endif
