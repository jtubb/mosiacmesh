#import "MMTransplantEngine.h"
#import "mmurl.h"
#import <AVFoundation/AVFoundation.h>
#import <CoreMedia/CoreMedia.h>
#import <substrate.h>

typedef void (*MMVoidFn)(void *);

@implementation MMTransplantEngine {
    void *_mp;                 // WebCore MediaPlayer*
    AVPlayer *_player;
    AVPlayerItem *_item;
    AVPlayerLayer *_layer;
    id _timeObserver;
    BOOL _observing;
    MMVoidFn _netChanged, _readyChanged, _timeChanged;
    int _net, _ready;
}

- (instancetype)initWithMediaPlayer:(void *)mp {
    if ((self = [super init])) {
        _mp = mp; _net = 0; _ready = 0; _observing = NO;
        _netChanged   = (MMVoidFn)MSFindSymbol(NULL, "__ZN7WebCore11MediaPlayer19networkStateChangedEv");
        _readyChanged = (MMVoidFn)MSFindSymbol(NULL, "__ZN7WebCore11MediaPlayer17readyStateChangedEv");
        _timeChanged  = (MMVoidFn)MSFindSymbol(NULL, "__ZN7WebCore11MediaPlayer11timeChangedEv");
    }
    return self;
}

// callbacks must reach WebCore on the main thread
- (void)fire:(MMVoidFn)fn {
    if (!fn || !_mp) return;
    void *mp = _mp;
    dispatch_async(dispatch_get_main_queue(), ^{ fn(mp); });
}

- (void)teardown {
    if (_observing && _item) {
        [_item removeObserver:self forKeyPath:@"status"];
        [_item removeObserver:self forKeyPath:@"duration"];
        _observing = NO;
    }
    if (_timeObserver && _player) { [_player removeTimeObserver:_timeObserver]; _timeObserver = nil; }
    _item = nil; _player = nil; _layer = nil;
}

- (void)loadURL:(NSString *)url {
    char path[512];
    NSURL *u = nil;
    if (mm_url_to_path([url UTF8String], path, sizeof path)) u = [NSURL URLWithString:[NSString stringWithUTF8String:path]];
    else u = [NSURL URLWithString:url];
    if (!u) return;
    [self teardown];
    _item = [AVPlayerItem playerItemWithURL:u];
    _player = [AVPlayer playerWithPlayerItem:_item];
    _layer = [AVPlayerLayer playerLayerWithPlayer:_player];
    [_item addObserver:self forKeyPath:@"status" options:0 context:(void *)1];
    [_item addObserver:self forKeyPath:@"duration" options:0 context:(void *)2];
    _observing = YES;
    __weak MMTransplantEngine *weak = self;
    _timeObserver = [_player addPeriodicTimeObserverForInterval:CMTimeMake(1, 4)
                        queue:dispatch_get_main_queue()
                        usingBlock:^(CMTime t){ MMTransplantEngine *s = weak; if (s && s->_timeChanged && s->_mp) s->_timeChanged(s->_mp); }];
}

- (void)observeValueForKeyPath:(NSString *)kp ofObject:(id)obj change:(NSDictionary *)c context:(void *)ctx {
    if (ctx == (void *)1) {                 // status
        int net = _net, ready = _ready;
        mm_status_to_states((int)_item.status, &net, &ready);
        _net = net; _ready = ready;
        [self fire:_netChanged];
        [self fire:_readyChanged];
    } else if (ctx == (void *)2) {          // duration
        [self fire:_readyChanged];
    }
}

- (void)play  { [_player play]; }
- (void)pause { [_player pause]; }
- (BOOL)paused { return _player ? (_player.rate == 0.0f) : YES; }

- (void)seekTo:(double)seconds {
    if (!_player) return;
    CMTime t = CMTimeMakeWithSeconds(seconds, NSEC_PER_SEC);
    [_player seekToTime:t toleranceBefore:kCMTimeZero toleranceAfter:kCMTimeZero];
}

- (void)setRate:(float)rate {
    if (!_player) return;
    // iOS 5.1 AVPlayerItem has no canPlayFastForward/SlowForward (iOS 6+); assume capable.
    _player.rate = mm_clamp_rate(rate, 1, 1);
}

- (double)currentTime {
    if (!_player) return 0.0;
    double s = CMTimeGetSeconds(_player.currentTime);
    return (s != s) ? 0.0 : s;   // NaN guard
}
- (double)duration {
    if (!_item) return 0.0;
    double s = CMTimeGetSeconds(_item.duration);
    return (s != s || s < 0) ? 0.0 : s;
}
- (int)networkState { return _net; }
- (int)readyState { return _ready; }
- (CALayer *)playerLayer { return _layer; }

- (void)dealloc { [self teardown]; }
@end
