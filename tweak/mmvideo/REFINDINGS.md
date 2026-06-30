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

---

## Phase 1.2 — On-device runtime confirmation (2026-06-29, screen14, observe-only)

Installed an observe-only tweak hooking `MediaPlayerPrivateiPhone::{load,play,seek,setRate}`; navigated MobileSafari to a cached `full_*.mp4` page; dumped `this+8`/`this+0xc` from inside the live process. Tweak removed after.

**Confirmed:**
- All four hook symbols resolve + hook at runtime (slid addrs this boot: `seek=0x37a50281`, `setRate=0x37a50185`, `play=0x37a505d5`, `load=0x3761e1a1`). `MSHookFunction` on the C++ mangled names works.
- `this+8` and `this+0xc` are populated as the field map predicts (non-nil after `load`).

**Surprise (refines Section 1 + Section 5):**
- At **load time**, `this+8` = **`FPVMediaPlayerHelper`** (a fullscreen-player *setup helper*; responds to NONE of setCurrentTime:/setRate:/play/pause/view/layer/…). `this+0xc` = **`WebCoreMediaPlayerNotificationHelper`** (a WebKit→WebCore notification bridge), NOT `MPAVItem`.
- This matches the static `setMediaPlayerProxy` note: `this+8` is REASSIGNED. The load-time occupant is the FPV fullscreen-player helper; the real playback controller (the `setCurrentTime:`/`setRate:` target inferred statically) is wired in only once playback actually begins — which the observe pass could NOT trigger (iOS-5 muted-autoplay needs a user gesture, so `play`/`seek` never fired).

**Consequence for the design (no blocker):** the transplant hooks `MediaPlayerPrivateiPhone` and **replaces** the engine with our `AVPlayer` — it does NOT need to drive the original controller, so the original's class/selectors are not on the critical path. The two items still genuinely unconfirmed — (a) the playing-state controller class, (b) the native video VIEW/LAYER that hosts the picture (the Section-5 AVPlayerLayer slot-in target) — are naturally observed during **Phase 3** on first real playback (our transplant code runs then, with temporary logging), or via a gesture-driven playback observe (VNC autotap / a server PLAY) if we want them earlier. The "FPV fullscreen-player helper" finding also corroborates that the original path is the forced-fullscreen movie player our inline transplant supersedes.

---

## Section 6 — URL extraction for the load hook  ✅ RESOLVED (Phase-3 prep)

`MediaPlayerPrivateiPhone::load(const WTF::String&)` (`0x372e11a0`) does NOT read the URL chars itself — it forwards the `String&` to `SubframeLoader::loadMediaPlayerProxyPlugin` (`0x372a3d54`). So the hook extracts the URL from the `String&` arg directly via WebKit's own conversion (no `StringImpl` offset-parsing):

- **Primary:** `WTF::String::createCFString() const` — symbol `__ZNK3WTF6String14createCFStringEv` (in the cache; `MSFindSymbol(NULL, …)` resolves it in the web process — WTF lives in JavaScriptCore, loaded there). ABI: `CFStringRef f(const WTF::String* this)`. The `load` hook's 2nd arg (`r1`) IS the `const String&` = a `String*`, so call `createCF(stringRef)` → a **+1-retained `CFStringRef`** → `NSString *url = (__bridge_transfer NSString*)cf;`. Guard nil (empty/null String → NULL or empty).
- **Fallbacks if needed:** `__ZNK3WTF6String29charactersWithNullTerminationEv` (returns `UChar*`, 16-bit NUL-terminated) or `__ZNK3WTF6String4utf8Eb` (returns a `WTF::CString`).

**Phase-3.1 load-hook flow:** `h_load(void* this, void* stringRef)`: call `o_load(this, stringRef)` (let WebCore set up its state machine), extract `url` via `createCF(stringRef)`, then create/lookup the `MMTransplantEngine` for `this` (`initWithMediaPlayer:*(void**)((char*)this+4)`), and `[engine loadURL:url]`. (The original proxy/controller path is neutered in Task 3.2.)

## §7 — Phase 3.2 on-device observation (2026-06-29, screen14 192.168.1.94)

**Hook layer CONFIRMED (Finding #2 resolved).** With a reliably-loading observe
dylib hooking `MediaPlayerPrivateiPhone::{load,play,seek,setRate}`, a real tapped
`<video>` produced:
```
HOOK setRate fired   -> this+8 = FPVMediaPlayerHelper, +0xc = WebCoreMediaPlayerNotificationHelper, +0x10 = __NSCFDictionary
HOOK play   fired   -> this+8 = FPVMediaPlayerHelper
```
So `play` AND `setRate` DO route through `MediaPlayerPrivateiPhone` — the Phase-3.1
interception layer is at the correct seam. (`play()` also drives a `setRate` — the
speed-nudge primitive is on this same path.) Earlier the live display client showed
`load` but never `play` purely because of the RENDER GATE (uncalibrated single-screen
group => FULL never render-ready => PLAY blocked => client idles on the clock), NOT
because play bypasses this layer.

**Controller = `FPVMediaPlayerHelper` at `this+8`** (confirms §Phase-1.2). `this+0xc`
= `WebCoreMediaPlayerNotificationHelper`, `this+0x10` = an `__NSCFDictionary`. NOTE:
at `load` time `this+8` is NIL (controller created during/after load); it is wired by
play/seek time. `m_player` (WebCore::MediaPlayer, C++) stays at `this+4`.

**STILL NEEDED for 3.2 (slot-in target):** FPVMediaPlayerHelper's video view/layer +
AVPlayer accessors. It is NOT in the pulled dyld_shared_cache_armv7 (string count=0,
while WebCoreMediaPlayerNotificationHelper=15, MediaPlayerPrivateiPhone=52) — so it is
a non-cached / dynamically-registered class. Offline class-dump needs its defining
binary located first; or a careful on-device @selector probe.

## §8 — Substrate dylib LOAD constraints on iOS 5.1 (HARD-WON, applies to the real tweak)

The "intermittent SubstrateLoader crash" from the bank was NOT the launch watchdog.
Root cause: under `-Wl,-undefined,dynamic_lookup` (flat namespace), dyld must bind
EVERY undefined symbol by searching loaded images at our load time. Substrate injects
the tweak EARLY in MobileSafari launch, before WebKit lazily loads libstdc++. So:
- **C++ SjLj unwind symbols** (`___gxx_personality_sj0`, `__Unwind_SjLj_*`) pulled in
  by ObjC++ (.xm) + ARC must bind to libstdc++ => NOT yet loaded => image SIGKILLed
  pre-`%ctor` (crash stack is pure dyld bind under SubstrateLoader; crash report shows
  `TASK_DYLD_INFO failed` with NO "Symbol not found" = external kill during bind).
- **FIX (proven, 4/4 reliable loads):** compile as PLAIN ObjC (`.x`, not `.xm`) with
  NO ARC (`-fobjc-arc` removed). Removes all C++/libstdc++ + ARC-runtime deps; ObjC
  uses libobjc's personality (always loaded). Frameworks: Foundation only.
- **Also unbindable here:** `sel_registerName`, `objc_getClass`, `NSSelectorFromString`
  (adding any one => same pre-ctor SIGKILL). USE compile-time `@selector()` literals +
  `objc_msgSend` + `respondsToSelector:` + `NSStringFromClass([x class])` instead
  (those bind fine). Avoid ALL dynamic name->sel/class resolution.
- Residual fragility: even symbol-identical .x builds occasionally differ at load when
  selref/section content grows (v9 loaded, v10 with extra @selector literals crashed).
  Keep the tweak's selref/section footprint minimal; for the real transplant strongly
  prefer two-level-namespace linking (link AVFoundation/CoreMedia/libobjc explicitly,
  reserve dynamic_lookup ONLY for the runtime-resolved WebCore MSFindSymbol targets).
- Deferred-hook install (`%ctor` -> dispatch_after bg) is still good practice but was
  NOT the fix; the bind happens before `%ctor` regardless.

## §9 — SLOT-IN TARGET resolved: FigPluginView (2026-06-29, on-device ivar walk)

`FPVMediaPlayerHelper` (= the controller at `MediaPlayerPrivateiPhone+0x8`) is itself
NOT in the shared cache (dynamic/non-cached WebKit-media-plugin class). Walking its
ivars on a live tapped `<video>` (guarded *(void**) + NSStringFromClass([class])):
```
FPVMediaPlayerHelper +0x4  = FigPluginView          <- NATIVE VIDEO VIEW (render surface)
                     +0x8  = WebPluginController
                     +0xc  = DOMHTMLElement          (the <video> DOM element)
                     +0x10 = NSLock
                     +0x14 = (primitive — [class] crashed here; stop the walk at +0x10)
```
So **`FPV` = `FigPluginView`**, and the full media path is:
web `<video>` -> `MediaPlayerPrivateiPhone` -> WebMediaPlayerProxy plugin ->
`WebPluginController` + **`FigPluginView`** (FigKit/CoreMedia native view that renders
the video). `FigPluginView` is also absent from the cache (count=0; `WebPluginController`
=51, `AVPlayerLayer`=102, `setPlayer:`=49 ARE present).

**Phase-3.2 SLOT-IN PLAN (concrete):** reach `FigPluginView` via `controller(this+8)+0x4`;
it is a UIView-family render surface, so add our `AVPlayerLayer` into `figPluginView.layer`
(sublayer sized to bounds) and neuter the original Fig/MPAVController playback so it
doesn't double-render. Engine already builds the `AVPlayer`+`AVPlayerLayer` (Phase 2).
Confirm `FigPluginView`'s superclass + its own layer/bounds at 3.2 impl time with a
MINIMAL on-device probe (one `@selector` at a time — v10 proved an @selector explosion
trips the load threshold; v9/v11's guarded slot-class-dump is the safe idiom).

OBSERVE-TOOLING NOTE: messaging `[class]` on a guarded-but-non-object ivar slot crashes
at RUNTIME (e.g. `+0x14` above) — but the log is `fclose`-flushed per line, so the dump
up to the bad slot survives the crash. Cap the walk (gDumps) and read partial output.

## §10 — Phase 3.2a: engine converted to loadable plain-ObjC (2026-06-29)

LOAD-GATE PROVEN (early, clean device): a plain-ObjC (.x) + ARC dylib that LINKS
AVFoundation/CoreMedia/QuartzCore loads on iOS 5.1 and creates AVPlayer/AVPlayerItem/
AVPlayerLayer + the zero-tolerance seek API (scratchpad `avsmoke.x`, "AVFoundation
LINK+LOAD OK"). So the engine conversion is mechanical.

DONE (repo, branch feat/ios5-avplayer-transplant, UNCOMMITTED working tree):
- `MMTransplantEngine.mm` -> `MMTransplantEngine.m`, `Tweak.xm` -> `Tweak.x` (plain
  ObjC; ObjC++ was the only reason libstdc++/`___gxx_personality_sj0` got pulled).
- Makefile: `_FILES = Tweak.x MMTransplantEngine.m`; dropped `UIKit` from FRAMEWORKS
  (not needed — slot-in reaches the view's CALayer via QuartzCore + objc_msgSend; and
  linking UIKit was a load-crash suspect); added `-fno-objc-arc-exceptions`.
- Engine: `__weak` -> `__unsafe_unretained` in the periodic-time-observer block —
  `__weak` emits ARC SjLj cleanup (`__Unwind_SjLj_*`); safe here since teardown/dealloc
  removes the observer before self dies. Build is now symbol-CLEAN (no libstdc++, no
  unwind, no UIKit; deps == the proven avsmoke).
- Hook install DEFERRED off `%ctor` (dispatch_after bg), side-table inited there.

NOT YET CLEANLY VALIDATED (the open item): the FULL real tweak's on-device LOAD. The
session's on-device tests got CONFOUNDED:
- **launchd relaunch throttle**: after many rapid `killall MobileSafari; uiopen`
  cycles, MobileSafari stops launching even with the PROVEN-good avsmoke dylib (control
  test failed in the degraded state). RESET with `killall SpringBoard` (respring) +
  ~25s settle; SPACE OUT tests; don't storm relaunches.
- **MobileSubstrate safe-mode**: after a tweak crashes MobileSafari at load, Substrate
  disables injection and relaunches Safari CLEAN -> "MobileSafari up + no `/tmp/MMCTOR`"
  means it crashed + entered safe-mode (NOT "loaded fine"). Respring to exit.
RESUME PROTOCOL (fresh, respring'd device, controlled): (1) deploy `avsmoke` first,
confirm `MMCTOR` + "AVFoundation LINK+LOAD OK" => device healthy; (2) THEN deploy the
real `mmvideo_real.dylib`, ONE launch, check `MMCTOR` + "[mmvideo] hooks installed";
respring between; treat "safari up + no MMCTOR" as a crash. If the real tweak genuinely
crashes on a healthy device while avsmoke loads, bisect by adding pieces to avsmoke
(empty ObjC class -> +engine class -> +12 hooks) to find the load-threshold trigger.

## §11 — ROOT CAUSE: a static ObjC class crashes the dylib load (2026-06-29)

CONTROLLED A/B/A on a healthy device (respring between each, avsmoke control LOADED
first to prove the device wasn't throttled):
```
A  avsmoke (no ObjC class)      -> LOADED
B  avsmoke + ONE empty class    -> CRASHED at load (no /tmp/MMCTOR, 0 safari procs)
A  avsmoke (no ObjC class)again -> LOADED
```
The ONLY delta between A and B is a single trivial `@interface MMTestEngine : NSObject`
(one no-op method). So: **a statically-defined ObjC class in the Substrate-injected
dylib deterministically SIGKILLs the load on this iOS-5.1 device.** Almost certainly the
iPhoneOS9.3-SDK toolchain emits ObjC2 class metadata (__objc_classlist/__objc_data/
class_ro_t layout) in a NEWER ABI than the iOS-5.1 objc runtime expects; the runtime
mis-parses it during `map_images` at image load (pre-%ctor) and the process dies.

CONSEQUENCE: this is the REAL blocker for the transplant. `MMTransplantEngine` is a
static ObjC class -> the real tweak crashes regardless of the §8 plain-ObjC fix. (Two
INDEPENDENT load-crash causes exist: §8 C++ SjLj unwind from ObjC++/ARC, fixed by plain
ObjC; and §11 static ObjC class metadata, NOT fixed by plain ObjC.) The class-free
observe builds (v7/v9/v11) + avsmoke loaded precisely because they define NO ObjC class.

CAVEAT on earlier conclusions: the v8/v10 symbol/threshold conclusions (sel_registerName
/objc_getClass "don't bind"; @selector-count "threshold") were from UNCONTROLLED tests
(no avsmoke control; the launchd-throttle + safe-mode confound was not yet known) and are
UNRELIABLE — re-validate any of them with the §10 control protocol before relying on them.

FIX OPTIONS for next session (design decision — engine needs to be an ObjC object for
AVPlayer KVO + the time-observer block):
1. RUNTIME class creation: build MMTransplantEngine at runtime via objc_allocateClassPair
   + class_addIvar/class_addMethod + objc_registerClassPair in the deferred install (NO
   static __objc metadata for the 5.1 runtime to mis-read). MUST first verify those
   libobjc symbols bind under the §10 control protocol.
2. CLASS-FREE C engine: hold AVPlayer/AVPlayerItem/AVPlayerLayer as retained CFTypeRef/
   void*; drive via objc_msgSend; replace KVO status-observation with the periodic time
   observer's block (already block-based) + polling AVPlayerItem.status, so no ObjC
   observer object is needed.
3. Older SDK (iPhoneOS6.x/5.x) that emits 5.1-compatible ObjC metadata — reopens the
   toolchain gate; least preferred.

## §12 — Redesign validation: runtime-class BLOCKED, class-free C is the path (2026-06-29)

Tested fix-option-1 (runtime class via objc_allocateClassPair) under the §10 control
protocol: `avsmoke(no class)=LOADED` then `avsmoke3(builds a class at runtime)=CRASHED
at load`. avsmoke3 has NO static ObjC class, so the crash is the **objc runtime
class-creation symbols failing to flat-bind** (`objc_allocateClassPair`,
`class_addMethod`, `class_addIvar`, `objc_registerClassPair`, `object_setIvar`,
`class_getInstanceVariable`) — referenced via `-Wl,-undefined,dynamic_lookup`, resolved
at LOAD even though used later. All ARE present as strings in the cache, but so are
`objc_getClass`/`sel_registerName` (which also don't bind); only the hot public
`objc_msgSend` flat-binds. String-in-cache != exported in the flat-namespace export
trie. **=> Fix option 1 (runtime class) is NOT viable under dynamic_lookup.**

DECISION: **Fix option 2 — CLASS-FREE C engine.** avsmoke PROVES the foundation: it
creates + uses AVPlayer/AVPlayerItem/AVPlayerLayer + zero-tol seek with NO ObjC class,
only `objc_msgSend` on AVFoundation objects (which binds). The engine becomes C
functions over AVFoundation objects held as retained `void*`/CFTypeRef:
- create: `((id(*)(id,SEL,id))objc_msgSend)([AVPlayer class], @selector(playerWithPlayerItem:), item)` etc. — all via `@selector()` literals (compile-time selrefs) + `objc_msgSend` (NO sel_registerName/objc_getClass; use `[AVPlayer class]` compile-time class refs).
- seek/rate/play/pause/currentTime/duration: direct `objc_msgSend` to the AVPlayer.
- state: REPLACE KVO (needs an ObjC observer object) with the already-block-based
  `addPeriodicTimeObserverForInterval:` block + POLL `AVPlayerItem.status`/`.duration`
  in that block; fire the WebCore `*Changed` callbacks from there. No observer class.
- the WebCore callback fn pointers stay as today (MSFindSymbol C function pointers).
- memory: manual retain/release the AVFoundation objects (CFRetain/CFRelease or
  `objc_msgSend(@selector(retain/release))`); the tweak file stays plain ObjC, NO ARC
  object ivars-in-a-class (there's no class).
CONSTRAINT for the rewrite: the dylib must define NO ObjC `@interface`/`@implementation`
(static class metadata crashes §11) and reference NONE of the non-binding objc runtime
symbols (§12) — only `objc_msgSend`, `@selector()` literals, `[KnownClass class]`,
`NSStringFromClass`, Foundation/AVFoundation/CoreMedia/QuartzCore via messages.

## §13 — ROOT CAUSE of the load-crash saga: missing compiler-rt builtins (2026-06-29)

The real engine (class-free C, no ObjC class, no objc-runtime calls) STILL crashed at
load. Controlled bisects narrowed it symbol-by-symbol; the decisive one: avsmoke + a
SINGLE `(double)cmtime.value / cmtime.timescale` (manual CMTime math) CRASHED, and its
only new undefined symbol was **`___floatdidf`** (the compiler-rt soft-float builtin for
int64->double). The L1ghtmann toolchain **ships NO `libclang_rt`** (no builtins archive
anywhere under `~/theos/toolchain`), so the compiler-emitted builtin call is left
UNDEFINED; `-Wl,-undefined,dynamic_lookup` then defers it to flat-namespace load-time
resolution, but builtins don't exist on-device (they're meant to be statically linked)
-> SIGKILL before %ctor.

**FIX VALIDATED:** hand-providing `__floatdidf` (32-bit int->double via VFP vcvt + double
arithmetic, no recursion into the builtin) made the same dylib **LOAD + compute
correctly** ("manual secs=3.000000"). So the whole saga of "symbol X crashes the load"
is largely the toolchain leaving compiler-rt builtins + (under dynamic_lookup) some lib
symbols unresolved. This likely also explains earlier `CMTimeGetSeconds`/`objc_msgSend_
stret` crashes (their double/struct handling pulls builtins) — to be re-confirmed once
builtins are provided.

```c
// the validated stopgap (avoids recursing into the missing builtin):
double __floatdidf(long long a){
    int hi = (int)(a >> 32); unsigned lo = (unsigned)a;
    return (double)hi * 4294967296.0 + (double)lo;   // (double)int32/uint32 = vcvt, no libcall
}
```

PATH FORWARD (engine is otherwise ready — class-free C from a73dc69+rewrite):
1. Provide compiler-rt builtins. BEST: obtain `libclang_rt.builtins` for armv7-iOS (from
   an Xcode toolchain or by building compiler-rt) and add to LDFLAGS — gives ALL builtins
   at once. STOPGAP: a small `mmbuiltins.c` hand-implementing the handful the engine pulls
   (`__floatdidf`, likely `__floatdisf`/`__divdf3`/`__muldf3`/`__floatundidf`).
2. Re-validate (control protocol) whether `objc_msgSend_stret`/`CMTimeGetSeconds` then
   load; if not, prefer the builtin-free manual path (CMTime value/timescale; currentTime
   from the observer block's CMTime param; duration via the original `o_duration`).
3. Consider switching off `dynamic_lookup` to two-level namespace once builtins are
   statically linked (only libSystem C funcs the 9.3-SDK tbd omits need per-symbol `-U`).
This is a TOOLCHAIN-LINKING fix, NOT a device incompatibility — the route is viable.

## §11-RETEST (TODO, fold into §13 builtins work)

§11 (static ObjC class crashes the load) was proven ONLY under `-Wl,-undefined,dynamic_lookup`
+ no compiler-rt builtins. Now that §13 identifies missing builtins as the float-path cause,
§11's status is OPEN: it is either (a) a genuine ObjC2 class-metadata ABI mismatch (9.3 SDK
vs 5.1 runtime at map_images) — classful stays dead regardless of linking; or (b) an
unresolved class-runtime symbol (`_OBJC_METACLASS_$_NSObject`, `__objc_empty_cache`, …) not
flat-binding under dynamic_lookup — which proper two-level linking + builtins would fix.
RE-TEST (one control cycle, after builtins are linked): build "avsmoke + one empty
`@interface :NSObject`" with two-level namespace + builtins; deploy avsmoke control first,
respring, then the class build. LOADED => classful is an option for future tweaks; CRASHED
=> §11 confirmed as a hard metadata-ABI wall and class-free is vindicated. Either way the
shipping engine stays CLASS-FREE (already written + clean); this only informs future work.

## §14 — LOAD GATE PASSED: class-free engine loads + hooks install (2026-06-29)

The class-free transplant engine now LOADS on iOS 5.1 and installs all 12
`MediaPlayerPrivateiPhone` hooks (MobileSafari stays alive; control protocol: avsmoke
LOADED, real engine ctor + "hooks installed (deferred)" with all 12 symbols resolved).
NOTE: the real `Tweak.x` `%ctor` logs to `/tmp/mmvideo.log`, it does NOT write
`/tmp/MMCTOR` — so the control script's "no MMCTOR => CRASHED" verdict is a FALSE
NEGATIVE for the real tweak; check the log + `ps MobileSafari` instead.

THE COMPLETE FIX STACK (all four required):
1. CLASS-FREE C engine — no static ObjC `@interface`/`@implementation` (§11 metadata crash).
2. `mmbuiltins.c` — hand-provides compiler-rt int64<->fp builtins the toolchain omits (§13).
3. MANUAL CMTime math — NO `objc_msgSend_stret` / `CMTimeGetSeconds` (both crash the load,
   §13): currentTime cached from the observer block's CMTime param (value/timescale via the
   provided `__floatdidf`); duration/maxTimeSeekable routed to the ORIGINAL engine
   (`o_duration`/`o_maxTimeSeekable`) in Tweak.x; paused tracked via a flag (no `.rate` read).
4. TWO-LEVEL namespace linking (Makefile: dropped `-Wl,-undefined,dynamic_lookup`), with
   per-symbol `-Wl,-U,...` ONLY for the libSystem C funcs the 9.3-SDK tbd omits (memset/
   memcpy/memmove/calloc/free/str*/`*_chk`/`stack_chk_*`). This binds objc/CoreMedia/
   Foundation/Substrate symbols to their dylib at link time instead of failing flat-namespace
   load-time lookup (the real reason "present-on-device" symbols crashed under dynamic_lookup).

NEXT: validate PLAYBACK (tap a `<video>` -> h_load fires -> mm_engine_create+load -> AVPlayer
plays; seek/rate exercised) then Phase 3.2b (FigPluginView layer slot-in + neuter, §9).

## §15 — Playback path crashes on FIRST execution (2026-06-30, to debug next)

LOAD GATE is solid (§14, committed bab3b5e): dylib loads, all 12 hooks install,
MobileSafari healthy. But on first REAL video load, the engine's playback path crashes
MobileSafari — this code had NEVER run before (the class-based versions died at load).
The crash-loop signature: a test page with `preload="auto"` triggers the buggy h_load on
PAGE LOAD (no tap needed) -> crash -> Substrate safe-mode + launchd throttle (the device
gets very sticky; respring `killall SpringBoard` + ~45s to recover; use a FRESH device).

The path that crashes (h_load -> mm_engine_create -> mm_engine_load), candidates:
- `gCreateCF(strRef)` URL extraction (WTF::String::createCFString) — RE'd (§6) but never
  runtime-exercised; the hook arg `strRef` being the WTF::String* is unverified at runtime.
- `*(void**)((char*)self+4)` m_player offset feeding mm_engine_create.
- AVPlayer/Item/Layer creation or the periodic-observer block / __bridge_retained casts.
GRANULAR LOGGING IS STAGED (uncommitted working tree on bab3b5e): h_load logs ENTER/
o_load done/url=…/engine created/mm_engine_load returned; mm_engine_load logs enter/url
parsed/item created/player created/layer created/observer added. Next session: on a FRESH
respring'd device, use a `preload="none"` test page (so load happens only on a controlled
tap, no crash-loop), deploy, tap ONCE, read the granular trace to pinpoint the crash line.

## §16 — Playback crash isolated to the const-getter hooks (2026-06-30, screen15)

The first-playback crash is NOT in our engine path — `h_load` NEVER fires (no `ENTER`
log). It crashes earlier, in WebCore's pre-load media-state POLLING of the const getters
we hook (`networkState`/`readyState`/…):
- Background-queue hook install -> `EXC_BAD_ACCESS at 0x30` inside
  `MediaPlayerPrivateiPhone::networkState` (our hook addr +0x33), `r0`(self)=`0x0`.
  Hypothesis was a concurrent MSHookFunction-overwrite race with the main thread.
- Switching the install to the MAIN queue (serializes the overwrite with WebCore's
  main-thread media calls) MOVED the crash (now `EXC_BAD_ACCESS at 0xe00df8e0` in
  `libsystem_c` called from the `networkState`/`readyState` region) but did NOT fix it.
=> The const-getter hooks themselves destabilize WebCore's getter polling, independent
of install timing. (Hooking `__ZNK...` const virtual getters via MSHookFunction on this
WebCore is the suspect — likely the small-method prologue/trampoline or the polling
re-entrancy.)

FIX STAGED (built, NOT yet validated — screen15 throttled out): hook ONLY the ACTION
methods (load/play/pause/seek/setRate/cancelLoad); leave the 6 getters ORIGINAL (the
original engine loads via `o_load` and reports state). `-Wno-unused-function` added
(the unused h_* getter fns). Install kept on the MAIN queue.
RESUME (FRESH device — screen15+screen14 both throttled from cycling; the heavy
rebuild→deploy→respring→settle→tap loop degrades a device after ~5 cycles, needs ~50s
respring to recover): deploy the action-hooks-only build, tap ONCE, confirm `h_load:
ENTER` fires + the `[eng] load:` trace + audio. If so, the getter hooks are confirmed;
re-add them carefully (try NULL-self guards in h_*; or update engine state and let
WebCore read it a different way; or hook fewer getters). Then Phase 3.2b slot-in.

## §17 — Playback crash FIXED + engine drives AVPlayer; item stuck Unknown on http (2026-06-30)

PLAYBACK CRASH RESOLVED: the §16 fix (hook only the 6 ACTION methods, leave the const
getters ORIGINAL; main-queue install) WORKS. On a tap (with the test page setting `src`
on-tap so `load` fires AFTER hooks), the full transplant path executes with NO crash:
```
h_load: ENTER -> o_load done -> url=http://.../full_*.mp4 (gCreateCF extraction CORRECT)
 -> engine created -> [eng] load: item/player/layer created, observer added — DONE
h_play -> engine ; h_setRate -> engine   (repeated — WebCore retries; see below)
```
So: h_load fires, URL extraction works, AVPlayer/Item/Layer build cleanly, play/setRate
route into our engine. The earlier first-playback "crash" was the CONST-GETTER hooks
destabilizing WebCore's pre-load media polling — confirmed fixed by dropping them (§16).

BUT NO OUTPUT (nothing plays): `[eng] play: item.status=0 rate=1.00 err=nil` every time.
AVPlayerItemStatus stays **0 = Unknown** (never 1=ReadyToPlay, never 2=Failed), no error;
player rate=1.0 (play took). The item never finishes loading -> nothing decodes. (On
screen the QuickTime-logo placeholder is the ORIGINAL MPAVController, which loaded via
o_load but doesn't advance since we route play to the engine.)

LEADING HYPOTHESIS (test next, NO device-cycle guess): the test uses the **http server
URL**; iOS-5 AVPlayer progressive-HTTP load is exactly the case that stalls at Unknown.
The transplant is DESIGNED for **local `file://`** (mm_url_to_path rewrites localhost-cache
URLs -> file://); a LOCAL asset loads to ReadyToPlay near-instantly. NEXT: push a small mp4
to the device and test with a `file://` URL (the engine's real path) — expect status->1.
Also: WebCore retries play/setRate because we dropped the state getters, so it never sees
"playing" — re-add getter feedback AFTER playback works (carefully; they crashed pre-load).

DEVICE DISCIPLINE (reaffirmed, crash-log-confirmed — NOT a vague "throttle"): repeated
`killall MobileSafari`+`uiopen` cycling triggers crash-induced SIGKILL-at-launch backoff
+ Substrate safe-mode after ~3 cycles per device (crash report = `TASK_DYLD_INFO failed`,
no frames, no mmvideo in trace). Quiet-waiting helps only partly; a POWER-CYCLE/REBOOT
fully clears it. Rotate freshly-rebooted devices; batch hypotheses per dylib to spend
fewer cycles. screen14=.94 screen15=.67 screen16=.63 (all Test Group / cycled this session).

## §18 — file:// VALIDATED: AVPlayer plays a local cached file (2026-06-30, screen15)

Pushed a device-compatible mp4 to the cache dir (`/var/mobile/Media/MosaicMeshCache/
mmtest.mp4`); test page src = `http://127.0.0.1:8080/mmtest.mp4` (set on-tap). Trace:
```
h_load: url=http://127.0.0.1:8080/mmtest.mp4   -> mm_url_to_path -> file:///var/mobile/Media/MosaicMeshCache/mmtest.mp4
[eng] load: item/player/layer created — DONE
t+1s item.status=1 ; play: status=1 rate=1.00 err=nil ; t+2..8s status=1
```
**AVPlayerItem.status = 1 (ReadyToPlay), rate=1.0, no error** — the LOCAL file loaded and
the AVPlayer is playing. CONFIRMS §17: the http URL stalled at status=0 (Unknown) because
iOS-5 AVPlayer progressive-HTTP load doesn't progress here; the **local file:// path (the
transplant's intended path via mm_url_to_path) WORKS**. The injected AVPlayer now decodes
video on a 1st-gen iPad — the core capability (frame-accurate seek + rate) is unlocked.

REMAINING (now ordinary work, no more load/RE mysteries):
1. Phase 3.2b — slot `engine.playerLayer` (AVPlayerLayer) into `FigPluginView`
   (controller this+8, ivar +0x4) so the video is VISIBLE; neuter the original.
2. Re-add getter feedback (networkState/readyState/currentTime/duration/paused) so WebCore
   sees "playing" + correct time (it currently retries play/setRate forever, and
   <video>.currentTime/duration are the original's) — carefully, since the const-getter
   hooks crashed pre-load (§16); options: install getters only AFTER first load, or guard.
3. For HTTP sources (non-cached), AVPlayer stalls — but the cache-push pipeline already
   makes FULL/SEGMENT content local, so the file:// path covers the real use case. (Could
   revisit http streaming later if needed.)
4. Remove the temporary status/diagnostic logging.

## §18b — PLAYBACK CONFIRMED: playhead advances (2026-06-30, rebooted screen15)

Definitive proof (not inferred): the periodic time-observer (fires only while the player
actually advances) logged currentTime CLIMBING on a local file:// asset:
```
TICK t=0.00 →0.01 →0.04 →0.31 →0.56 →0.80 →1.00 →1.29   (item.status=1, no error)
```
=> the injected AVPlayer is DECODING a local video in real time on a 1st-gen iPad. Core
transplant capability proven end-to-end (load → ReadyToPlay → decode/advance); frame-
accurate seek + variable rate are now reachable. Not visible only because the
AVPlayerLayer isn't slotted on-screen yet (3.2b). NEXT unchanged: 3.2b layer slot-in
(visible video) + neuter original + re-add getter feedback + verify seek/rate + strip
diagnostics. (NOTE 2026-06-30: server device roster/settings.dat appears clobbered —
unrelated to the transplant; worth a separate look.)
