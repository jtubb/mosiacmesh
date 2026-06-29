# REFINDINGS.md — Phase-1 static RE of `MediaPlayerPrivateiPhone` + WebCore callbacks

Host-side reverse-engineering of the pulled iOS-5.1 `dyld_shared_cache_armv7`
(199 MB, `dyld_v1   armv7`, dyldBase `0x2fe00000`). No device touched.

**Cache mappings** (vm → file):
- map0: `0x30000000..0x37c05000` → file `0x0`        (__TEXT, all images)
- map1: `0x3e000000..0x3f7e1000` → file `0x7c05000`   (__DATA)
- map2: `0x37c05000..0x3a4fd000` → file `0x93e6000`   (shared __LINKEDIT)

**WebCore image base** vmaddr `0x37119000` (confirmed). Backend class
`WebCore::MediaPlayerPrivateiPhone`. All addresses below are static cache
vmaddrs; subtract the on-device WebCore slide before use (Phase-2 resolves the
live base via `MSGetImageByName`/`_dyld_get_image_header`).

Disassembly: Capstone Thumb-2 (`disasm2.py`); `objc_msgSend` thunk at
`0x37859260`, `objc_msgSend_stret` at `0x37859290`. ObjC selector strings are
reached through `__objc_selrefs` slots that this iOS-5 v1 cache does **not**
pre-bind to clean selector vmaddrs (the slot pointer lands in the methtype
pool), so individual selector *names* are recorded from prior RE / call-shape,
not re-derived byte-exact — see Section 1 note. The control-flow, field offsets,
enum constants, and callback targets below **are** byte-exact from the disasm.

---

## Section 1 — Full `MediaPlayerPrivateiPhone` method map  ✅ RESOLVED

50 symbols matched `MediaPlayerPrivateiPhone` in WebCore's symtab. vtable:
`__ZTVN7WebCore24MediaPlayerPrivateiPhoneE` @ `0x3f560c30`.

### Instance field layout (derived from the disassembly, byte-exact)

| offset | meaning | evidence |
|--------|---------|----------|
| `this+0`    | C++ vtable ptr            | `str.w r2,[ip]` in ctor; seek does `ldr r1,[this]; ldr r1,[r1,#0x3c]; blx r1` (clamp vfunc) |
| `this+4`    | **`MediaPlayer*` back-ptr (m_player)** | ctor `str r1,[r0,#4]`; all `*Changed` calls load `[this+4]` as the arg (Section 2/3) |
| `this+8`    | **MPAVController** (the ObjC controller) | every forwarding method does `ldr rX,[this,#8]` then `objc_msgSend` |
| `this+0xc`  | item/data object (MPAVItem-side) | ctor `str r4,[r0,#0xc]`; `cancelLoad` msgSends `[this+0xc]`; `deliverNotification` msgSends `[this+0xc]` when reentrancy>0 |
| `this+0x10` | **WebMediaPlayerProxy*** | `load` msgSends `[this+0x10]`; `cancelLoad` msgSends `[this+0x10]`; `setMediaPlayerProxy` retains/releases into `[this+8]`... see note |
| `this+0x14` | **m_networkState (int)** | `networkState()` is `ldr r0,[r0,#0x14]; bx lr`; `deliverNotification`/`load` write enum ints here |
| `this+0x18` | **m_readyState (int)** | `readyState()` is `ldr r0,[r0,#0x18]; bx lr`; `deliverNotification` writes enum ints here |
| `this+0x1c` | reentrancy / notification-guard counter | `play`/`pause`/`load` do `++[this+0x1c]` around the msgSend, `--` after |
| `this+0x20` | volume-change guard counter | volume-changed path `++/--[this+0x20]` |
| `this+0x24` | **m_rate (float)** | `setRate` `vstr s0,[r0,#0x24]` |
| `this+0x28` | secondary ready/seek sub-state (int) | `deliverNotification` reads/writes `[this+0x28]` to compute readyState transitions |
| `this+0x2c` | **flag byte** (bit5 `0x20`=paused, bit1 `0x02`, bit2 `0x04`, bit4 `0x10`, bits in `0xcf`/`0xed`/`0xdf` masks) | `play` `&=0xdf`; `pause` `|=0x20`; `load` `&=0xcf`; deliverNotification toggles `0x02/0x04/0x10` |

> Note on `setMediaPlayerProxy` (@ `0x372a4ef0`): it releases the *old* object
> at `[this+8]`, retains the new proxy, and the brief's prior RE places the
> controller at `this+8`. In this build `setMediaPlayerProxy` actually
> store/release-cycles **`[this+8]`** (not `+0x10`) while `load`/`cancelLoad`
> msgSend `[this+0x10]`. Reading: `+8` and `+0x10` are the two ObjC handles
> (controller + proxy); both are confirmed live ObjC objects. The forwarding
> verbs (play/pause/seek/setRate/currentTime/duration/paused/hasVideo/hasAudio/
> seeking/volume) ALL target `[this+8]` — so **`this+8` is the call target for
> Phase-2/3 method interposition**, regardless of which one we label "controller".

### Per-method summary (key methods disassembled)

| method | vmaddr | reads | sends to | notes |
|--------|--------|-------|----------|-------|
| `C2`/ctor `...iPhoneC2EPNS_11MediaPlayerE` | `0x372a3488` | — | (alloc/init objc) | `str r1,[this+4]` saves `MediaPlayer*`; sets up vtable + WTF members; `str r4,[this+0xc]` |
| `create` `...6createEPNS_11MediaPlayerE` | `0x372a33d8` | — | operator new + ctor | factory |
| `load(String)` | `0x372e11a0` | `+0x10`,`+0x14`,`+0x18`,`+0x2c` | proxy `[+0x10]`, controller `[+8]` | sets `m_networkState=2` (Loading) → calls `networkStateChanged`; conditionally calls `readyStateChanged`; clears flags `&=0xcf` |
| `cancelLoad` | `0x372df990` | `+8`,`+0xc`,`+0x10` | item `[+0xc]`, proxy `[+0x10]`, controller `[+8]` | three msgSends; early-out if `[+8]==0` |
| `play` | `0x377135d4` | `+8`,`+0x1c`,`+0x2c` | controller `[+8]` | clears paused bit `&=0xdf`; guarded by `++/--[+0x1c]`; if `[+8]==0` calls `addDeferredRequest` (`0x372e0bf0`) |
| `pause` | `0x37713318` | `+8`,`+0x1c`,`+0x2c` | controller `[+8]` | sets paused bit `|=0x20`; guarded counter |
| `seek(float)` | `0x37713280` | `+8`, vtable `+0x3c` | controller `[+8]` | clamps via `[vtable+0x3c]()` (maxTimeSeekable-style), then `objc_msgSend(ctrl, sel, (double)time)` — **single `double` arg, setCurrentTime:-style; NOT a CMTime tolerance seek** |
| `setRate(float)` | `0x37713184` | `+8` | controller `[+8]` | `vstr s0,[+0x24]`; forwards `(float)rate` |
| `currentTime` | `0x372dfe70` | `+8` | controller `[+8]` | msgSend returns `double` (r0:r1→d16), `vcvt.f64→f32`; returns 0.0 if `[+8]==0` |
| `duration` | `0x377132cc` | `+8` | controller `[+8]` | msgSend returns `double`; if ==-1.0 returns `+Inf` (`0x7f800000`); 0 if no controller |
| `paused` | `0x372eb86c` | `+8` | controller `[+8]` | msgSends a rate selector; returns `rate==0.0` (default true if no ctrl) |
| `seeking` | `0x37713258` | `+8` | controller `[+8]` | bool msgSend |
| `hasVideo` | `0x37713230` | `+8` | controller `[+8]` | bool msgSend |
| `hasAudio` | `0x37713208` | `+8` | controller `[+8]` | bool msgSend |
| `naturalSize` | `0x37713598` | `+8` | controller `[+8]` | `objc_msgSend_stret` (struct ret) → IntSize |
| `networkState` | `0x372ea4ac` | `+0x14` | — | `ldr r0,[r0,#0x14]; bx lr` (pure getter) |
| `readyState` | `0x37712d64` | `+0x18` | — | `ldr r0,[r0,#0x18]; bx lr` (pure getter) |
| `setVolume(float)` | `0x377131b0` | `+8` | controller `[+8]` | |
| `volume` | `0x377131d8` | `+8` | controller `[+8]` | |
| `setMuted(bool)` | `0x37713160` | `+8` | controller `[+8]` | |
| `deliverNotification(type)` | `0x372ea7bc` | `+0x14`,`+0x18`,`+0x28`,`+0x2c` | dispatches WebCore callbacks (Section 3) | the state machine — see Sections 3+4 |
| `setMediaPlayerProxy` | `0x372a4ef0` | `+8` | retain/release | calls `processPendingRequests` (`0x372a4f9c`) |
| `D0`/`D1`/`D2` dtors | `0x372e9a58` / `0x377135d0` / `0x37713758` | | | |

Other exported methods (offsets/getters, not deep-disassembled): `supportsType`
`0x372dce08`, `getSupportedTypes` `0x37712d8c`, `maxTimeSeekable` `0x37713110`,
`bytesLoaded` `0x37713140`, `buffered` `0x377133a8`, `setSize` `0x37712d68`,
`setVisible` `0x37712d6c`, `paint` `0x37712d88`, `prepareToPlay` `0x37713354`,
`setControls` `0x37713620`, `attributeChanged` `0x3771369c`, `setPoster`
`0x372e0a84`, `enterFullScreen` `0x377130e8`, `exitFullScreen` `0x377130c0`,
`hasClosedCaptions` `0x37713058`, `setClosedCaptionsVisible` `0x37713040`,
`readyForPlayback` `0x3771307c`, `supportsFullscreen` `0x377138c0`,
`supportsAcceleratedRendering` `0x372a7fbc`, `addDeferredRequest` `0x372e0bf0`,
`processDeferredRequests` `0x372e9f40`, `processPendingRequests` `0x372a4f9c`,
`registerMediaEngine` `0x372a1e80`.

---

## Section 2 — The `MediaPlayer*` back-pointer offset  ✅ RESOLVED

Disassembling the ctor `__ZN7WebCore24MediaPlayerPrivateiPhoneC2EPNS_11MediaPlayerE`
@ `0x372a3488`:

```
0x372a34bc:  str.w r2, [ip]      ; ip=this  -> [this+0] = C++ vtable
0x372a34c2:  str   r1, [r0, #4]  ; r0=this, r1=MediaPlayer* arg  ->  [this+4] = m_player
0x372a3538:  str   r4, [r0, #0xc]; result of an objc alloc -> [this+0xc]
```

**`m_player` (the saved `MediaPlayer*`) is at `this+4`.** Corroborated three
ways: every `*Changed` dispatch in `deliverNotification` loads `[this+4]` as the
`MediaPlayer*` argument before `bl MediaPlayer::*Changed` (e.g. `0x372ea9be:
ldrne r0,[r5,#4]; blne 0x372ea400`), and `load` does the same
(`0x372e128e: ldr r0,[r1,#4]; ... bl 0x372ea400`).

Phase-2/3 contract: after a `MediaPlayerPrivateiPhone` instance is in hand,
`*(MediaPlayer**)((char*)self + 4)` is the object to call the WebCore callbacks
on.

---

## Section 3 — WebCore callback entry points  ✅ RESOLVED

The callbacks WebCore's backend invokes (Phase-2 calls these via `MSFindSymbol`
with the saved `MediaPlayer*` as `this`). Mach-O leading-`_` form:

| C++ callback | mangled (Mach-O) | vmaddr |
|--------------|------------------|--------|
| `MediaPlayer::networkStateChanged()` | `__ZN7WebCore11MediaPlayer19networkStateChangedEv` | `0x372ea400` |
| `MediaPlayer::readyStateChanged()`   | `__ZN7WebCore11MediaPlayer17readyStateChangedEv`   | `0x3771267c` |
| `MediaPlayer::timeChanged()`         | `__ZN7WebCore11MediaPlayer11timeChangedEv`         | `0x37712694` |
| `MediaPlayer::volumeChanged(float)`  | `__ZN7WebCore11MediaPlayer13volumeChangedEf`       | `0x377126ac` |
| `MediaPlayer::sizeChanged()` *(see note)* | — (not a standalone symbol) | — |
| `MediaPlayer::durationChanged()` *(see note)* | — (not a standalone symbol) | — |
| `MediaPlayer::rateChanged()` *(see note)* | — (not a standalone symbol) | — |
| `MediaPlayer::repaint()` *(see note)* | — (not a standalone symbol) | — |

Verified by reading `deliverNotification`'s tail dispatch (byte-exact):
```
0x372ea9b8: ldr  r0,[r5,#0x14]      ; new m_networkState
0x372ea9ba: cmp  r0, r8            ; vs old
0x372ea9be: ldrne r0,[r5,#4]       ; this+4 = MediaPlayer*
0x372ea9c0: blne 0x372ea400        ; -> MediaPlayer::networkStateChanged
0x372ea9c4: ldr  r0,[r5,#0x18]      ; new m_readyState
0x372ea9c8: cmp  r0, fp            ; vs old
0x372ea9ca: ldrne r0,[r5,#4]
0x372ea9cc: blne 0x3771267c        ; -> MediaPlayer::readyStateChanged
```
and inline: `0x372ea952: bl 0x37712694` (timeChanged, arg=`[this+4]`),
`0x372ea96a: bl 0x377126ac` (volumeChanged, after a vtable getter `[vtbl+0x60]`).

**Note (sizeChanged / durationChanged / rateChanged / repaint):** in this
WebKit-534 build these are NOT exported as `WebCore::MediaPlayer::*` symbols —
they are inline forwarders on the *client* side. The corresponding entry points
that DO exist are on `MediaPlayerClient` / `HTMLMediaElement`:
- `__ZN7WebCore17MediaPlayerClient26mediaPlayerDurationChangedEPNS_11MediaPlayerE` `0x374d5dac`
- `__ZN7WebCore17MediaPlayerClient22mediaPlayerRateChangedEPNS_11MediaPlayerE`     `0x374d5db0`
- `__ZN7WebCore17MediaPlayerClient31mediaPlayerPlaybackStateChangedEPNS_11MediaPlayerE` `0x374d5db4`
- `__ZN7WebCore17MediaPlayerClient18mediaPlayerRepaintEPNS_11MediaPlayerE`         `0x374d5dbc`
- `__ZN7WebCore17MediaPlayerClient22mediaPlayerSizeChangedEPNS_11MediaPlayerE`     `0x374d5dc0`
- `__ZN7WebCore16HTMLMediaElement26mediaPlayerDurationChangedEPNS_11MediaPlayerE`  `0x374d463c` (+thunk `0x374d45fc`)
- `__ZN7WebCore16HTMLMediaElement22mediaPlayerRateChangedEPNS_11MediaPlayerE`      `0x374d3df0` (+thunk `0x374d3d8c`)
- `__ZN7WebCore16HTMLMediaElement22mediaPlayerSizeChangedEPNS_11MediaPlayerE`      `0x374d3650` (+thunk `0x374d3624`)
- `__ZN7WebCore16HTMLMediaElement18mediaPlayerRepaintEPNS_11MediaPlayerE`          `0x374d3c58` (+thunk `0x374d3c20`)
- `__ZN7WebCore16HTMLMediaElement31mediaPlayerPlaybackStateChangedEPNS_11MediaPlayerE` `0x374d59ac` (+thunk `0x374d5964`)

For Phase-2 the **four primary callbacks** (`networkStateChanged`,
`readyStateChanged`, `timeChanged`, `volumeChanged`) are sufficient to drive the
HTMLMediaElement state machine: WebCore re-reads `duration()`/`naturalSize()`
lazily on the readyState/network transitions, so duration/size propagate via
`readyStateChanged`. If a discrete `durationChanged`/`sizeChanged` notification
is needed, call `MediaPlayer::*Changed` does not exist — instead route through
the `MediaPlayer*`→client by triggering a readyState bump, OR (cleaner) call the
`HTMLMediaElement::mediaPlayer*Changed(MediaPlayer*)` symbols above with the
element pointer (obtainable from `MediaPlayer`'s client field). **Phase-1.2
on-device confirm:** verify a `setCurrentTime:`-driven seek + a metadata-ready
event surface `duration`/`naturalSize` to the JS `<video>` via readyState alone.

---

## Section 4 — WebCore MediaPlayer enum values  ✅ RESOLVED (one correction)

Extracted byte-exact from the `mov #N; str [this+0x14|0x18]` pairs inside
`deliverNotification` (@ `0x372ea7bc`) and `load` (@ `0x372e11a0`).

**Values WebCore actually writes:**
- `m_networkState` (`this+0x14`): observed writes **1, 2, 3, 4, 6**.
- `m_readyState`   (`this+0x18`): observed writes **0, 1, 2, 3, 4**.

These match the standard WebKit-534 `MediaPlayer` enums:

```
enum NetworkState { Empty=0, Idle=1, Loading=2, Loaded=3,
                    FormatError=4, NetworkError=5, DecodeError=6 };
enum ReadyState  { HaveNothing=0, HaveMetadata=1, HaveCurrentData=2,
                   HaveFutureData=3, HaveEnoughData=4 };
```

Cross-check vs `mmurl.h`'s `mm_status_to_states` (lines 33–41): the **comment is
correct** (Empty=0…DecodeError=6, HaveNothing=0…HaveEnoughData=4). The **code**
uses `net=2/ready=0` (Loading/HaveNothing) for unknown, `net=3/ready=4`
(Loaded/HaveEnoughData) for ready, `net=6/ready=0` (DecodeError/HaveNothing) for
failed — all consistent with the disassembly. **No change to `mmurl.h` is
required.**

Evidence (selected, from the `tbb`-driven switch):
```
load:       movs r0,#2; str [this+0x14]                 ; -> Loading=2
type 4:     movs r4,#4; str [this+0x14]; str 0 [+0x18]  ; -> NetworkError/FormatError path, ready HaveNothing
type 12:    movs r4,#6; str [this+0x14]                 ; -> DecodeError=6
type 9:     [this+0x28]>=2 ? ready=4 : ready=2          ; HaveEnoughData / HaveCurrentData
type 17:    net=3 (Loaded); ready=4 (HaveEnoughData)
type 13/16: net=2; ready=3 (HaveFutureData) / ...
```
The `tbb` jump table at `0x372ea800` maps the 23
`MediaPlayerProxyNotificationType` values (1..0x17) to these handlers; full
table decoded in the working notes. **One nuance:** `NetworkError=5` is never
written by `deliverNotification` in this build (errors collapse to FormatError=4
or DecodeError=6) — harmless for our status mapping. **Phase-1.2 confirm:** none
needed for enum integers; they are byte-exact.

---

## Section 5 — Controller-creation / plugin-view site  ⚠️ CANDIDATES (cross-image; finish on-device)

The web `<video>` is driven through the **`WebMediaPlayerProxy` → MediaPlayer.framework
`MPAVController`/`MPAVItem` → `MPVideoView`** path. The creation chain
(byte-exact, WebCore side):

```
HTMLMediaElement::createMediaPlayerProxy()  @ 0x372a3114
  - malloc(0x68); MediaPlayer ctor (0x372a32bc); store proxy at HTMLMediaElement+0x14c
  - bl 0x372a3d54  = SubframeLoader::loadMediaPlayerProxyPlugin(...)
  - store returned Widget* (plugin view) at HTMLMediaElement+0x150

SubframeLoader::loadMediaPlayerProxyPlugin  @ 0x372a3d54
  - ldr r1,[frameLoaderClient + 0x2f0]; bl 0x37208d58   ; vtable call ->
        FrameLoaderClient::createMediaPlayerProxyPlugin(...)  [impl in WebKit.framework]
```

`FrameLoaderClient::createMediaPlayerProxyPlugin` is **implemented in
WebKit.framework**, not WebCore (WebCore only has the abstract
`EmptyFrameLoaderClient` stubs at `0x377c0e80`+ and the `FrameLoader::show/hide`
shims `0x372a7f98`/`0x3749a52c`). The concrete native-view class name is
therefore not in WebCore's `__TEXT`. String-scanning the WebKit and MediaPlayer
images by vmaddr range gives the hookable symbols/strings:

**WebKit.framework — plugin-container / media-layer plumbing (hook candidates):**
- `_webPluginContainerSetMediaPlayerProxy:forElement:`  ← proxy attach point
- `_setMediaLayer:forPluginView:`                       ← **the layer slot-in point** (Phase-3 AVPlayerLayer goes here)
- `superlayerForPluginView:`
- `_redirectDataToManualLoader:forPluginView:`
- class `@"WebVideoFullscreenController"`

**MediaPlayer.framework — controller/item/view classes + selectors:**
- Controller class **`MPAVController`** (ivars `_player`, `_avController`,
  `_currentItem`); item class **`MPAVItem`** (`initWithMPAVItem:`,
  `setMPAVItem:`, `MPAVItemType{Video,Audio,Unknown}`).
- Native video view class **`MPVideoView`** (`@"MPVideoView"`, ivar
  `_videoView`); controllers `MPVideoViewController`,
  **`MPInlineVideoViewController`** + `MPInlineVideoOverlay` (the inline-web-video
  path), `MPMoviePlayerVideoViewController`, fullscreen variants.
- View lifecycle selectors (Phase-3 **neuter** targets): `setVideoView:`,
  `displayVideoView`, `displayVideoViewOnScreen`, `_inflightVideoView`,
  `_tearDownVideoView`, `setVideoViewController:`,
  `_initializeVideoViewController:orientation:`.
- Controller selectors that fire the WebCore notifications:
  `_postMPAVControllerItemReadyToPlayNotificationWithItem:`,
  `_postMPAVControllerSizeDidChangeNotificationWithItem:`, and the
  `MPAVController*Notification` name set (`...PlaybackStateChanged`,
  `...RateDidChange`, `...ItemReadyToPlay`, `...SizeDidChange`,
  `...TimeDidJump`, `...ItemPlaybackDidEnd`, etc.) — these are what
  `WebMediaPlayerProxy` observes and turns into `deliverNotification(type)`.

**Best Phase-3 plan from static evidence:**
1. Hook WebKit's `-_setMediaLayer:forPluginView:` (and/or `superlayerForPluginView:`)
   to substitute our `AVPlayerLayer` for the `MPVideoView`'s CALayer.
2. Neuter `MPVideoView`/`MPInlineVideoViewController` playback by no-op'ing
   `displayVideoView*` / `setVideoView:` so MediaPlayer.framework doesn't drive
   its own decode while AVPlayer does.
3. Keep `MPAVController` alive as the **command/notification surface**: the
   backend forwards play/pause/seek (`setCurrentTime:`-style)/setRate to
   `[this+8]`; we either (a) interpose those selectors on the live `MPAVController`
   to drive our `AVPlayer`, or (b) replace `[this+8]` with a shim object exposing
   the same selectors. Option (a) is lighter and matches the forwarding map.

**What Phase-1.2 (on-device observe) MUST confirm** — these can't be pinned
statically because the concrete view-creation body lives in WebKit and the exact
runtime wiring depends on the inline-vs-fullscreen branch:
- Which concrete view class the inline web `<video>` actually instantiates
  (`MPVideoView` vs `MPInlineVideoViewController`'s contained view) and the
  exact `forPluginView:` argument identity.
- The precise selector names on `[MediaPlayerPrivateiPhone this+8]` for
  play/pause/seek/setRate/currentTime/duration/rate (static selref slots in this
  v1 cache don't resolve to clean names — record live via a Cydia Substrate
  selector-logging probe; the `mmvprobe.dylib` scaffold already exists in scratch).
- Whether `[this+8]` is the `MPAVController` or the `WebMediaPlayerProxy` at the
  moment our forwarding hooks fire (the disasm shows BOTH `+8` and `+0x10` are
  live ObjC handles; the forwarding verbs use `+8`).

---

## Tooling / reproducibility
- `analyze_cache.py` (image enumeration + per-image marker scan, excludes shared
  `__LINKEDIT`), `symgrep.py` (WebCore symtab substring grep, vmaddr-sorted),
  `disasm2.py` (Thumb-2 disasm + PC-rel literal/selref resolution),
  `strscan.py` (per-image `__TEXT/__DATA` string scan by vmaddr range — the v1
  cache's per-image LC_SEGMENT `fileoff` is unreliable, so scan via the
  vm→file mappings).
- Host Python 3.14 + capstone. Cache: 199 MB `dyld_v1   armv7`.
