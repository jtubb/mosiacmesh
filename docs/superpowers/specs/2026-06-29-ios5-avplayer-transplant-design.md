# iOS-5 AVPlayer Video-Engine Transplant — Design

**Date:** 2026-06-29
**Status:** design (feasibility proven on hardware; see "Feasibility" below)
**Goal:** Give the iPad-1 display client **frame-accurate seeking** and **variable-rate (speed-nudge) playback** for `<video>`, by replacing the OS media controller under WebKit's `<video>` element with an `AVPlayer`, delivered as a MobileSubstrate tweak — **with zero change to the existing web client, server, or boot flow.**

---

## Background & why this design

The display client (`index.html`, ES5) plays cached video through a single persistent HTML5 `<video>`, synced to a shared clock (`GoTime`) by a JS drift-correction loop that nudges `video.currentTime` / `video.playbackRate`. Two hard limits make sync coarse:

- **Seek is keyframe-only** — `video.currentTime=` snaps to the 250 ms keyframe grid (±125 ms); no sub-keyframe control.
- **`playbackRate` is ignored** — so the drift loop cannot do smooth short catch-ups, only hard seeks.

These are **WebKit/Safari-5.1 restrictions, not hardware limits.** Static disassembly of the iOS-5.1 shared cache (WebKit 534.46) established the exact playback path:

```
<video>.currentTime= → HTMLMediaElement::seek → MediaPlayer::seek → MediaPlayerPrivateiPhone::seek(float)
<video>.playbackRate= → HTMLMediaElement::setPlaybackRate → MediaPlayer::setRate → MediaPlayerPrivateiPhone::setRate(float)
```

`MediaPlayerPrivateiPhone::seek` was disassembled and **calls `[controller setCurrentTime:(double)]`** (single double arg — *not* a `CMTime`-tolerance seek) on an **`MPAVController`/`MPAVItem`** (MediaPlayer.framework), via a `WebMediaPlayerProxy` plugin. `MPAVController` has **no tolerance-seek API** — so a thin hook can't add frame-accuracy; the capable player (`AVPlayer`, with `seekToTime:toleranceBefore:toleranceAfter:` + `rate`, both iOS-5.0+) must be put *into* the path. That is the transplant.

Alternatives considered and rejected: a pure-web JPEG-frame player (composite/RAM walls on the A4 — see `ipad1-frame-player-not-viable`); a thin route-1 hook (dead — `MPAVController` can't do it); a standalone native app (route 2 — works but rewrites the shell + needs a JS↔native bridge + boot changes). The transplant tweak is preferred because it keeps the entire web client and its compositing intact.

## Feasibility — proven on hardware (2026-06-29)

- **Toolchain:** WSL Ubuntu + Theos + L1ghtmann iOS toolchain (clang 11.1) + iPhoneOS9.3 SDK + `ldid`. Target `iphone:clang:9.3:5.1`, `ARCHS=armv7`. (SDK gotcha: add a stub `usr/lib/system/liblaunch.tbd`; libSystem reexports it but the stub is omitted.)
- **Load:** an `armv7`/min-5.1 dylib built this way **loads on iOS-5.1 `dyld`** (it skips `LC_VERSION_MIN_IPHONEOS`/`LC_DATA_IN_CODE`).
- **Inject + resolve:** an observe-only tweak injected into MobileSafari (launch-time MobileSubstrate) and `MSFindSymbol` resolved both hook targets at runtime: `MediaPlayerPrivateiPhone::seek` and `::setRate`. (See `ipad1-native-player-replacement` memory for the build recipe + the proof.)

No feasibility unknowns remain; what follows is engineering.

## Non-goals

- No change to the web client (`index.html`/`GoTime.js`/`mosiacmesh.js`), the Python server, the render pipeline, or the boot/launch flow.
- No change to the JS clock-sync loop in v1 (it keeps calling `currentTime=`/`playbackRate=`; it simply gets precise actuators). Relaxing its keyframe-grid/cooldown tuning is a documented fast-follow, not v1.
- Not targeting non-iPad-1 / non-iOS-5.1 devices.

## Architecture

A single MobileSubstrate dylib (`mmvideo.dylib`) + filter plist, injected into the display-client process (MobileSafari / WebApp). It hooks the WebCore C++ media backend and transplants an `AVPlayer`.

```
WebCore HTMLMediaElement  (unchanged)
        │  seek()/setRate()/play()/pause()/load()/currentTime()/duration()/…
        ▼
MediaPlayer  (unchanged)  → m_private->X()
        ▼
MediaPlayerPrivateiPhone  ──HOOKED──▶  TransplantEngine (ours)
   (original MPAVController                 ├─ AVPlayer  (file:// decode, zero-tol seek, rate)
    path neutered)                          ├─ AVPlayerLayer  (slotted into the plugin view)
                                            └─ KVO/time-observers → drive WebCore client callbacks
```

### Component 1 — `TransplantEngine` (one per `<video>` backend instance)
Owns an `AVPlayer` + `AVPlayerItem` + `AVPlayerLayer`, and the state needed to satisfy WebCore. A side-table maps each hooked `MediaPlayerPrivateiPhone*` → its `TransplantEngine`. Responsibilities:
- **load(url):** derive the local path, create `AVPlayerItem` (file://), `AVPlayer`, `AVPlayerLayer`; KVO-observe `AVPlayerItem.status`, `.duration`, `.presentationSize`, `AVPlayer.rate`; add a periodic time observer (~4 Hz, matching the current `timeupdate` cadence).
- **play/pause:** `AVPlayer play/pause` (no user gesture needed — see Gesture).
- **seek(t):** `[avPlayer seekToTime:CMTimeMakeWithSeconds(t, NSEC_PER_SEC) toleranceBefore:kCMTimeZero toleranceAfter:kCMTimeZero]` — **frame-accurate**.
- **setRate(r):** `avPlayer.rate = r` (honor `currentItem.canPlayFastForward/SlowForward` for r outside [0,1]; clamp otherwise).
- **currentTime/duration/paused/hasVideo/hasAudio/naturalSize/buffered/volume/muted:** read from the `AVPlayer`/item.

### Component 2 — WebCore callback bridge
WebCore expects the backend to notify its `MediaPlayer*` client. We recover that pointer from the hooked instance (the `create(MediaPlayer*)` factory receives it; cache it in the side-table) and call, from our KVO/observers:
- `MediaPlayer::networkStateChanged()` / `readyStateChanged()` as `AVPlayerItem.status` advances (Unknown→ReadyToPlay→Failed) and buffering changes.
- `MediaPlayer::durationChanged()` on duration KVO; `sizeChanged()` on presentationSize.
- `MediaPlayer::timeChanged()` from the periodic observer (drives `timeupdate`) and on seek completion.
- `MediaPlayer::rateChanged()` / `playbackStateChanged()` on rate KVO; `ended` when the item reaches end.
The exact callback symbols/vtable slots are resolved during implementation (Component-3 RE), modeled on the disassembled `MediaPlayerPrivateiPhone` call sites.

### Component 3 — Interception mechanics
`%ctor`:
1. `MSHookFunction` on `MediaPlayerPrivateiPhone::{load, seek, setRate, play, pause, currentTime, duration, paused, …}` (mangled C++ symbols via `MSFindSymbol`; the leaf class + methods are already located).
2. Hook the **controller-creation / proxy-plugin-view-creation** site so the native video view hosts our `AVPlayerLayer` and the original `MPAVController` is **neutered** (created-but-unused, or its load/play no-op'd) — researched in Implementation Phase 1.
3. Each hooked method consults the side-table and drives the `TransplantEngine`.

### Component 4 — Compositing & transitions (inherited, no new code)
Our `AVPlayerLayer` is installed as the content of the *same* native plugin view WebKit positions per the `<video>` element's box. WebKit keeps driving that view's frame/transform/opacity from the element's CSS, so **every existing transition (fade, slide/zoom, iris/dissolve/wipe) and the bg overlays keep working unchanged** — they were never coupled to the decoder (they only set `opacity`/`transform` on the element + draw overlays above it).

### Component 5 — Source URL
The web client sets `<video>.src` to `http://127.0.0.1:8080/seg_*.mp4` (localhost lighttpd). `load()` **rewrites** this to `file:///var/mobile/Media/MosaicMeshCache/seg_*.mp4` for direct `AVPlayer` file decode (no lighttpd hop for video). Mapping: strip the `http://127.0.0.1:8080/` prefix → prepend the cache dir. Non-cache/non-localhost URLs (rare) pass through as-is to `AVPlayer` over http.

### Component 6 — Autoplay + fullscreen restrictions (both eliminated by the transplant)
Two iOS-5 web-`<video>` restrictions are removed as inherent consequences of putting an in-app `AVPlayer` in the path (not as separate WebKit patches):
- **Autoplay / user-gesture:** iOS-5 web `<video>` needs a user gesture to start; an **in-app `AVPlayer` does not**. v1 auto-`play()`s programmatically, eliminating the tap-to-start / VNC-autotap / arm dance for video. (Opt-out tweak pref `AutoPlay=0` restores the arm flow for parity/debug.)
- **Forced fullscreen → force-inline:** iOS-5 forces `<video>` into a fullscreen movie player (the proxy-plugin's behavior). The transplant renders into an **inline `AVPlayerLayer`**, so video is **always inline by construction** — forced-fullscreen never triggers. This is the desired wall behavior (no screen ever flips to player chrome) and supersedes the earlier "fullscreen parity" framing.

### Component 7 — Errors & fallback
- `AVPlayerItem` failure → map to `MediaPlayer::networkStateChanged(FormatError/DecodeError)` so the `<video>` `error` event fires as today (preserving the `CACHE_LOCAL_FAIL` client behavior on a bad localhost/file load).
- If `TransplantEngine` cannot initialize at all (unexpected), **un-neuter the original `MPAVController`** so playback degrades to today's behavior rather than going black. A tweak pref can force-disable the transplant fleet-wide without uninstalling.

### Component 8 — Packaging & deploy
- Deliverable: `mmvideo.dylib` + `mmvideo.plist` (Filter → `com.apple.mobilesafari`, `com.apple.WebApp`), packaged as a `.deb` (`com.mosaicmesh.mmvideo`) for clean `dpkg` install/remove.
- **No server, web-client, or boot change.** The display client launches exactly as today; substrate injects the tweak.
- Rollout staged one device → group (mirrors the existing RELOAD staging), each install gated on explicit authorization (production-device persistence).

## Data flow (the payoff)

```
GoTime drift loop (JS, unchanged)
  └ video.currentTime = clockTarget   → … → TransplantEngine.seek → AVPlayer zero-tolerance seek   (frame-accurate)
  └ video.playbackRate = nudge        → … → TransplantEngine.setRate → AVPlayer.rate               (speed-nudge)
periodic time observer → MediaPlayer::timeChanged → WebCore → JS 'timeupdate' → drift loop reads currentTime
```

## Testing & validation

- **Pure logic (host, no device):** URL→file-path rewrite; `AVPlayerItem.status`→`networkState/readyState` mapping; rate clamping. Node/py unit tests on the extracted pure functions.
- **On-device parity (gated install, one screen):** each playmode (SEGMENT/INDIVIDUAL/FULL), play/pause/loop/seek, error on a missing file, fullscreen, and transition rendering — confirm parity with today.
- **Payoff measurement:** frame-accurate seek + speed-nudge measured via the existing `?tdbg` on-device drift `err` (target: tighter than today's ~11 ms median, with no keyframe-snap residual); speed-nudge smoothness.
- **On-wall:** cross-screen sync improvement across the group; AP load unchanged (still cache-served, now file://).
- **Rollback drill:** `dpkg remove` + restart restores today's behavior on a device.

## Risks & open items (resolved during implementation)

1. **Controller-creation hook site** (Component 3.2) — exact symbol/flow for neutering `MPAVController` + hosting our layer; map via continued static RE + an observe tweak. *Highest-uncertainty item.*
2. **WebCore callback vtable slots** (Component 2) — confirm the `MediaPlayer::*Changed` entry points from the disassembly.
3. **AVPlayerLayer in the plugin view** — confirm WebKit's plugin-view layer is one we can add a sublayer to and that CSS transforms still apply.
4. **mediaserverd interaction** — `AVPlayer` decode is brokered by `mediaserverd`; confirm no conflict with the (neutered) `MPAVController` also holding a session.
5. **Memory** — one `AVPlayer` per `<video>`; the client uses a single persistent `<video>`, so one engine — fine on 256 MB.

## Success criteria

A `.deb`-installable tweak that, on the iPad-1 fleet, makes `<video>` playback **behaviorally identical to today across all playmodes/transitions/errors**, while seeks become **frame-accurate** and `playbackRate` **takes effect** — with the web client, server, and boot flow byte-for-byte unchanged, and a clean `dpkg`-remove rollback.
