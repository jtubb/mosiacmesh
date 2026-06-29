#import <Foundation/Foundation.h>
#import <QuartzCore/QuartzCore.h>

// AVPlayer-backed video engine. Phase-3 hooks create one per <video> backend and
// route MediaPlayerPrivateiPhone calls into it; its getters feed WebCore back.
@interface MMTransplantEngine : NSObject
- (instancetype)initWithMediaPlayer:(void *)webCoreMediaPlayer;  // the WebCore MediaPlayer* (callback receiver)
- (void)loadURL:(NSString *)url;     // localhost cache URL -> file:// (mm_url_to_path)
- (void)play;
- (void)pause;
- (BOOL)paused;
- (void)seekTo:(double)seconds;      // frame-accurate (zero tolerance)
- (void)setRate:(float)rate;         // mm_clamp_rate -> AVPlayer.rate
- (double)currentTime;
- (double)duration;
- (int)networkState;                 // WebCore NetworkState int
- (int)readyState;                   // WebCore ReadyState int
- (CALayer *)playerLayer;            // AVPlayerLayer (for Phase-3 slot-in)
@end
