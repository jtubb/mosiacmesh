# iOS-5 AVPlayer Video-Engine Transplant — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `mmvideo.dylib` — a MobileSubstrate tweak that transplants an `AVPlayer` under WebKit's `<video>` on the iPad-1 fleet, giving frame-accurate seeking + variable-rate playback, with zero change to the web client / server / boot flow.

**Architecture:** A single Objective-C++ MobileSubstrate dylib injected into the display-client process. It `MSHookFunction`s WebCore's `MediaPlayerPrivateiPhone` C++ backend, neuters the original `MPAVController`, and drives an `AVPlayer` + `AVPlayerLayer` (slotted into WebKit's existing video plugin view) that services seek/rate/play/load and reports state back to WebCore via the original callback entry points.

**Tech Stack:** Objective-C++ (Logos/Theos `.xm`), AVFoundation, CydiaSubstrate (`MSHookFunction`/`MSFindSymbol`), Theos (WSL Ubuntu, clang 11.1, iPhoneOS9.3 SDK, `ldid`), deployed over SSH to jailbroken iOS 5.1.1 (armv7).

## Global Constraints

- Target triple: `iphone:clang:9.3:5.1`, `ARCHS = armv7` ONLY. (Build recipe + the `liblaunch.tbd` SDK stub fix: see memory `ipad1-native-player-replacement`.)
- Build host: WSL Ubuntu, `THEOS=$HOME/theos`. Run WSL via a script FILE (`wsl bash /mnt/c/.../x.sh`) — inline `bash -lc '…'` from PowerShell mangles long `/mnt/c` paths.
- Device fleet: jailbroken iPad-1 / iOS 5.1.1 / armv7. SSH key `~/.ssh/mosaic_ipad`, user `root`, legacy SSH opts (`HostKeyAlgorithms=+ssh-rsa`, `IdentitiesOnly=yes`), `scp -O` (legacy protocol — modern SFTP fails "path canonicalization").
- ZERO change to `index.html`, `js/`, `server.py`, `mosaicmesh/`, render pipeline, or boot flow. The tweak is self-contained.
- Cache dir on device: `/var/mobile/Media/MosaicMeshCache/`. Localhost media URL form: `http://127.0.0.1:8080/<name>.mp4`.
- Hook target symbols (Mach-O names, confirmed resolvable at runtime): `__ZN7WebCore24MediaPlayerPrivateiPhone4seekEf`, `__ZN7WebCore24MediaPlayerPrivateiPhone7setRateEf` (+ siblings located in Phase 1).
- **Every on-device install/run is a gated action** (production-device persistence) — each requires explicit user authorization at execution time. Default to install→test→remove unless told to leave installed.
- Commit trailer EXACTLY: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Work on branch `feat/ios5-avplayer-transplant` (never commit to `main`).

---

## File Structure

All new code lives under `tweak/mmvideo/` in the repo (version-controlled; built by pointing Theos at a WSL copy):

- `tweak/mmvideo/Makefile` — Theos tweak makefile (target/arch/files/frameworks).
- `tweak/mmvideo/mmvideo.plist` — substrate Filter (Bundles: MobileSafari, WebApp).
- `tweak/mmvideo/control` — `.deb` package metadata.
- `tweak/mmvideo/mmurl.h` — **pure C** URL→cache-file-path rewrite + rate clamp + `AVPlayerItem.status`→WebCore-state mapping. Header-only pure functions so they are host-unit-testable with no iOS deps.
- `tweak/mmvideo/tests/test_mmurl.c` — host C unit tests for `mmurl.h` (compiled with system `cc`, run on WSL/host — no device, no Theos).
- `tweak/mmvideo/tests/Makefile` — builds + runs `test_mmurl`.
- `tweak/mmvideo/MMTransplantEngine.h` / `.mm` — the `AVPlayer`/`AVPlayerLayer` wrapper + WebCore callback bridge (Objective-C++).
- `tweak/mmvideo/Tweak.xm` — `%ctor`, `MSHookFunction` installs, hooked method bodies, the per-backend side-table, controller-creation/plugin-view hook.
- `tweak/mmvideo/REFINDINGS.md` — **artifact produced by Phase 1**: exact symbols, vtable offsets, the `MediaPlayer*` back-pointer offset, the plugin-view class/creation site, mediaserverd notes. Phases 2–3 consume this by reference.
- `docs/superpowers/plans/2026-06-29-ios5-avplayer-transplant.md` — this plan (Phase-1 completion triggers a re-plan that appends final Phase 2–5 tasks).

Reusable host tooling already exists in scratchpad/memory (the dyld-cache parser `analyze_cache.py`, the Capstone disassembler `disasm.py`, the probe project `mmprobe/`, the WSL Theos toolchain at `~/theos`).

---

## Phase 0 — Project scaffold + pure logic (host-testable, full TDD)

Produces: a building no-op dylib + tested pure functions. No device needed.

### Task 0.1: Repo tweak project skeleton + Theos build

**Files:**
- Create: `tweak/mmvideo/Makefile`, `tweak/mmvideo/Tweak.xm`, `tweak/mmvideo/mmvideo.plist`, `tweak/mmvideo/control`

**Interfaces:**
- Produces: a Theos tweak named `mmvideo` that compiles to an `armv7` dylib. Consumed by every later task (they build via this).

- [ ] **Step 1: Create the Theos Makefile**

`tweak/mmvideo/Makefile`:
```make
export THEOS = $(HOME)/theos
TARGET = iphone:clang:9.3:5.1
ARCHS = armv7
include $(THEOS)/makefiles/common.mk
TWEAK_NAME = mmvideo
mmvideo_FILES = Tweak.xm
mmvideo_FRAMEWORKS = AVFoundation CoreMedia QuartzCore UIKit Foundation
mmvideo_CFLAGS = -fobjc-arc -Wno-deprecated-declarations
include $(THEOS)/makefiles/tweak.mk
```

- [ ] **Step 2: Create the substrate filter**

`tweak/mmvideo/mmvideo.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict><key>Filter</key><dict><key>Bundles</key><array>
  <string>com.apple.mobilesafari</string>
  <string>com.apple.WebApp</string>
</array></dict></dict>
</plist>
```

- [ ] **Step 3: Create the package control**

`tweak/mmvideo/control`:
```
Package: com.mosaicmesh.mmvideo
Name: mmvideo
Version: 0.0.1
Architecture: iphoneos-arm
Description: MosaicMesh AVPlayer video-engine transplant for iPad-1
Maintainer: MosaicMesh
Author: MosaicMesh
Section: Tweaks
Depends: mobilesubstrate
```

- [ ] **Step 4: Minimal Tweak.xm (no-op ctor that logs)**

`tweak/mmvideo/Tweak.xm`:
```objc
#import <substrate.h>
#import <stdio.h>
#import <unistd.h>
static void mmlog(const char *m){ FILE*f=fopen("/tmp/mmvideo.log","a"); if(f){fprintf(f,"%s\n",m);fclose(f);} }
%ctor { char b[128]; snprintf(b,sizeof(b),"[mmvideo] loaded pid=%d", getpid()); mmlog(b); }
```

- [ ] **Step 5: Build via the repo→WSL build script**

Create `tweak/mmvideo/build.sh` (run on WSL via `wsl bash /mnt/c/.../tweak/mmvideo/build.sh`):
```bash
#!/bin/bash
export THEOS=$HOME/theos
SRC="$(cd "$(dirname "$0")" && pwd)"
rm -rf ~/mmvideo && mkdir -p ~/mmvideo
cp "$SRC"/Tweak.xm "$SRC"/Makefile "$SRC"/mmvideo.plist "$SRC"/control ~/mmvideo/ 2>/dev/null
cp "$SRC"/MMTransplantEngine.* "$SRC"/mmurl.h ~/mmvideo/ 2>/dev/null
sed -i 's/\r$//' ~/mmvideo/Makefile ~/mmvideo/Tweak.xm 2>/dev/null
cd ~/mmvideo && make clean >/dev/null 2>&1 && make 2>&1 | grep -viE "tbd file|deprecated|Simulator" | tail -20
find ~/mmvideo -name '*.dylib' -not -path '*dSYM*'
```
Run it. Expected: `Linking tweak mmvideo (armv7)` + `Signing` + a `.dylib` path printed.

- [ ] **Step 6: Commit**
```bash
git add tweak/mmvideo/Makefile tweak/mmvideo/Tweak.xm tweak/mmvideo/mmvideo.plist tweak/mmvideo/control tweak/mmvideo/build.sh
git commit -m "feat(mmvideo): Theos tweak skeleton builds armv7 no-op dylib"
```

### Task 0.2: Pure URL→file-path rewrite (TDD, host C)

**Files:**
- Create: `tweak/mmvideo/mmurl.h`, `tweak/mmvideo/tests/test_mmurl.c`, `tweak/mmvideo/tests/Makefile`

**Interfaces:**
- Produces: `int mm_url_to_path(const char *url, char *out, size_t outlen)` — returns 1 and writes a `file://` path if `url` is a localhost cache URL, else returns 0 and leaves `out` empty. Consumed by `MMTransplantEngine load:` (Task 2.x).

- [ ] **Step 1: Write the failing test**

`tweak/mmvideo/tests/test_mmurl.c`:
```c
#include "../mmurl.h"
#include <string.h>
#include <assert.h>
#include <stdio.h>
int main(void){
    char out[512];
    // localhost cache URL -> file:// in the cache dir
    assert(mm_url_to_path("http://127.0.0.1:8080/seg_abc_0.mp4", out, sizeof out) == 1);
    assert(strcmp(out, "file:///var/mobile/Media/MosaicMeshCache/seg_abc_0.mp4") == 0);
    // full_ asset likewise
    assert(mm_url_to_path("http://127.0.0.1:8080/full_def_0.mp4", out, sizeof out) == 1);
    assert(strcmp(out, "file:///var/mobile/Media/MosaicMeshCache/full_def_0.mp4") == 0);
    // non-localhost passes through (return 0)
    assert(mm_url_to_path("http://192.168.1.60:3000/media/server/videos/x.mp4", out, sizeof out) == 0);
    // path traversal in name is rejected (return 0)
    assert(mm_url_to_path("http://127.0.0.1:8080/../../etc/passwd", out, sizeof out) == 0);
    printf("ok\n"); return 0;
}
```

`tweak/mmvideo/tests/Makefile`:
```make
test:
	cc -std=c99 -Wall -o /tmp/test_mmurl test_mmurl.c && /tmp/test_mmurl
```

- [ ] **Step 2: Run to verify it fails**

Run (WSL): `cd tweak/mmvideo/tests && make test`
Expected: compile error — `mmurl.h` / `mm_url_to_path` not found.

- [ ] **Step 3: Implement `mmurl.h`**

`tweak/mmvideo/mmurl.h`:
```c
#ifndef MMURL_H
#define MMURL_H
#include <string.h>
#include <stddef.h>
#define MM_CACHE_DIR "/var/mobile/Media/MosaicMeshCache/"
#define MM_LOCALHOST "http://127.0.0.1:8080/"
static int mm_name_is_safe(const char *n){
    if(!*n) return 0;
    for(const char *p=n; *p; ++p){ if(*p=='/'||*p=='\\') return 0; }
    if(strstr(n,"..")) return 0;
    return 1;
}
static int mm_url_to_path(const char *url, char *out, size_t outlen){
    if(out && outlen) out[0]=0;
    size_t pl = strlen(MM_LOCALHOST);
    if(strncmp(url, MM_LOCALHOST, pl) != 0) return 0;
    const char *name = url + pl;
    const char *q = strpbrk(name, "?#");
    char nbuf[256]; size_t nl = q ? (size_t)(q-name) : strlen(name);
    if(nl >= sizeof nbuf) return 0;
    memcpy(nbuf, name, nl); nbuf[nl]=0;
    if(!mm_name_is_safe(nbuf)) return 0;
    int n = snprintf(out, outlen, "file://%s%s", MM_CACHE_DIR, nbuf);
    return (n>0 && (size_t)n < outlen) ? 1 : 0;
}
#endif
```

- [ ] **Step 4: Run to verify it passes**

Run (WSL): `cd tweak/mmvideo/tests && make test`
Expected: `ok`

- [ ] **Step 5: Commit**
```bash
git add tweak/mmvideo/mmurl.h tweak/mmvideo/tests/test_mmurl.c tweak/mmvideo/tests/Makefile
git commit -m "feat(mmvideo): pure URL->cache-file-path rewrite + host tests"
```

### Task 0.3: Pure rate clamp + status→state mapping (TDD, host C)

**Files:**
- Modify: `tweak/mmvideo/mmurl.h` (add `mm_clamp_rate`, `mm_status_to_states`), `tweak/mmvideo/tests/test_mmurl.c` (add cases)

**Interfaces:**
- Produces: `float mm_clamp_rate(float r, int canFast, int canSlow)`; `void mm_status_to_states(int avStatus, int *net, int *ready)` mapping `AVPlayerItemStatus` {0 unknown,1 ready,2 failed} → WebCore `MediaPlayer::NetworkState`/`ReadyState` enum ints (values fixed in REFINDINGS Phase 1; use the spec's documented enum until then). Consumed by `MMTransplantEngine` KVO handlers (Task 2.x).

- [ ] **Step 1: Add failing test cases** (append to `test_mmurl.c` `main`, before `printf`):
```c
    assert(mm_clamp_rate(1.0f,0,0)==1.0f);
    assert(mm_clamp_rate(0.0f,0,0)==0.0f);
    assert(mm_clamp_rate(1.5f,0,0)==1.0f);   // no fast-fwd capability -> clamp to 1
    assert(mm_clamp_rate(1.5f,1,0)==1.5f);   // fast-fwd capable -> allowed
    assert(mm_clamp_rate(-1.0f,0,0)==0.0f);  // no reverse -> 0
    int net=-9, ready=-9; mm_status_to_states(1,&net,&ready);
    assert(net>=0 && ready>=0);              // ready item -> non-error net + a playable ready
```

- [ ] **Step 2: Run to verify it fails**
Run: `cd tweak/mmvideo/tests && make test` → compile error (functions undefined).

- [ ] **Step 3: Implement** (append to `mmurl.h` before `#endif`):
```c
static float mm_clamp_rate(float r, int canFast, int canSlow){
    if(r==1.0f || r==0.0f) return r;
    if(r>1.0f) return canFast ? r : 1.0f;
    if(r>0.0f) return canSlow ? r : 1.0f;
    return 0.0f; /* no reverse */
}
/* WebCore MediaPlayer enums (iOS5 WebKit534): NetworkState Empty=0 Idle=1 Loading=2
   Loaded=3 FormatError=4 NetworkError=5 DecodeError=6; ReadyState HaveNothing=0
   HaveMetadata=1 HaveCurrentData=2 HaveFutureData=3 HaveEnoughData=4. Confirm in
   REFINDINGS (Phase 1) against the disassembly; these are the documented values. */
static void mm_status_to_states(int avStatus, int *net, int *ready){
    if(avStatus==2){ *net=6; *ready=0; return; }      /* failed -> DecodeError */
    if(avStatus==1){ *net=3; *ready=4; return; }      /* ready  -> Loaded/HaveEnough */
    *net=2; *ready=0;                                  /* unknown-> Loading/HaveNothing */
}
```

- [ ] **Step 4: Run to verify it passes** → `ok`
- [ ] **Step 5: Commit**
```bash
git add tweak/mmvideo/mmurl.h tweak/mmvideo/tests/test_mmurl.c
git commit -m "feat(mmvideo): pure rate-clamp + AV-status->WebCore-state mapping + tests"
```

---

## Phase 1 — On-device reverse-engineering (discovery → `REFINDINGS.md`)

Produces: `tweak/mmvideo/REFINDINGS.md` with the exact symbols/offsets Phases 2–3 need. **Methodology is fully specified; outputs are discovered.** Uses static disassembly (host, no device) first, then a gated observe-tweak (device) to confirm.

### Task 1.1: Static-map the backend interface + callbacks (host disassembly)

**Files:**
- Create: `tweak/mmvideo/REFINDINGS.md`
- Uses: scratchpad `analyze_cache.py`, `disasm.py`, the pulled `dyld_shared_cache_armv7` (re-pull if absent, per memory recipe).

- [ ] **Step 1:** Disassemble every `MediaPlayerPrivateiPhone::*` symbol (extend `disasm.py`'s target list to all `MediaPlayerPrivateiPhone` symbols from the cache grep). For each, record: the vtable slot it overrides, the `this+offset` fields it reads (the controller is at `this+8`, the rate stored at `this+0x24` — confirm), and the `objc_msgSend` selectors it sends.
- [ ] **Step 2:** Find the `MediaPlayer*` back-pointer: disassemble `MediaPlayerPrivateiPhone::create`/ctor to see where the `MediaPlayer*` arg is stored (`this+offset`). Record the offset.
- [ ] **Step 3:** Find the WebCore callback entry points: locate `WebCore::MediaPlayer::{networkStateChanged,readyStateChanged,timeChanged,durationChanged,rateChanged,sizeChanged}` symbols (grep the cache for `_ZN7WebCore11MediaPlayer*Changed*`); record their mangled names (we call these directly with the saved `MediaPlayer*`).
- [ ] **Step 4:** Find the controller-creation / proxy-plugin-view site: disassemble `MediaPlayerPrivateiPhone::load` + `createMediaPlayerProxyPlugin` to see where `MPAVController`/`MPAVItem` and the native video view are instantiated. Record the hookable symbol(s) + the view class.
- [ ] **Step 5:** Write findings to `REFINDINGS.md` (one section per item above, with exact mangled names + offsets + the confirmed WebCore enum values). Commit:
```bash
git add tweak/mmvideo/REFINDINGS.md
git commit -m "docs(mmvideo): Phase-1 static RE of MediaPlayerPrivateiPhone + callbacks"
```

### Task 1.2: Confirm offsets/symbols live on-device (GATED observe tweak)

**Files:**
- Modify: `tweak/mmvideo/Tweak.xm` (temporary observe hooks — reverted before Phase 2), `tweak/mmvideo/REFINDINGS.md`

- [ ] **Step 1:** Extend `Tweak.xm` with OBSERVE-ONLY hooks (no behavior change): `MSHookFunction` `seek`/`setRate`/`load`; in each, log the args + `this`, and read the candidate `this+offset` fields + log them; resolve + log the WebCore callback symbol addresses via `MSFindSymbol`. Build (Task 0.1 build.sh).
- [ ] **Step 2:** GATED on-device: install→play a clip→read `/tmp/mmvideo.log`→remove (install/test/remove pattern; requires explicit auth). Confirm: the controller pointer offset, the rate offset, the `MediaPlayer*` offset, and that all callback symbols resolve.
- [ ] **Step 3:** Update `REFINDINGS.md` with the confirmed runtime values; revert the observe hooks from `Tweak.xm` (keep only the `%ctor`). Commit:
```bash
git add tweak/mmvideo/REFINDINGS.md tweak/mmvideo/Tweak.xm
git commit -m "docs(mmvideo): confirm RE offsets/symbols on-device; revert observe hooks"
```

### Task 1.3: RE-PLAN CHECKPOINT — DONE (2026-06-29)

- [x] `REFINDINGS.md` complete (Sections 1–4 byte-resolved + the Phase-1.2 on-device confirm). Resolved values are folded into the "Resolved values (Phase 1)" block below; Phase 2–3 reference them. The one item NOT statically pinned — the playing-state controller class + the native video VIEW/LAYER (the `AVPlayerLayer` slot-in target) — is deferred to **Phase-3 first-playback observation** (the transplant *replaces*, not drives, the original engine, so the original controller's class/selectors are off the critical path). Phase-1.2 surprise to carry forward: load-time `this+8` = `FPVMediaPlayerHelper` (fullscreen-player helper), reassigned to the real controller only at play time.

---

## Phase 2 — `MMTransplantEngine` (AVFoundation wrapper + WebCore bridge)

**Resolved values (Phase 1 — use these verbatim; full method-symbol table in `REFINDINGS.md` §1):**
- The hooked backend instance is a `MediaPlayerPrivateiPhone*` (call it `bp`). Field offsets (byte-exact):
  - `*(void**)((char*)bp + 4)` = **`WebCore::MediaPlayer*`** (the callback receiver, "m_player").
  - `+8` = controller (load-time `FPVMediaPlayerHelper`, play-time the real controller — we neuter it), `+0xc` = item/notification helper, `+0x14` = `m_networkState (int)`, `+0x18` = `m_readyState (int)`, `+0x24` = `m_rate (float)`, `+0x2c` = flag byte (paused = bit `0x20`).
- **WebCore callback symbols** (Mach-O leading `_`; call as `void(*)(void* mediaPlayer)` with the `+4` pointer):
  - `__ZN7WebCore11MediaPlayer19networkStateChangedEv`
  - `__ZN7WebCore11MediaPlayer17readyStateChangedEv`
  - `__ZN7WebCore11MediaPlayer11timeChangedEv`
  - (No standalone `durationChanged`/`sizeChanged`/`rateChanged` in this build — drive metadata/duration via `readyStateChanged`, position via `timeChanged`.)
- **Hook target symbols** (Mach-O; resolution+hooking confirmed on-device Phase 1.2): `__ZN7WebCore24MediaPlayerPrivateiPhone4seekEf`, `…7setRateEf`, `…4playEv`, `…4loadERKN3WTF6StringE` (+ `pause`/`currentTime`/`duration`/`paused` per `REFINDINGS.md` §1). C++ method ABI: `void f(void* this, …)`; `this`=r0 (unambiguous), seek/setRate float in the second slot (verify the float convention when wiring 3.1 — `this` is all the bridge strictly needs).
- **Enum ints** (confirmed): already encoded in `mmurl.h`'s `mm_status_to_states` (NetworkState 0–6, ReadyState 0–4).
- Makefile already carries the ObjC++ link flags (`-fno-exceptions`, `-Wl,-undefined,dynamic_lookup`).

> AVFoundation code is given below. Each item is a TDD-style task; its test is on-device + gated (touches AVFoundation + WebCore), so each needs explicit install authorization.

### Task 2.1: `MMTransplantEngine` lifecycle + `file://` load
- **Files:** Create `tweak/mmvideo/MMTransplantEngine.h` / `.mm`; add to `mmvideo_FILES`.
- **Produces (interface):**
  ```objc
  @interface MMTransplantEngine : NSObject
  - (instancetype)initWithMediaPlayer:(void*)webCoreMediaPlayer;  // saved for callbacks
  - (void)loadURL:(NSString*)url;     // uses mm_url_to_path -> AVPlayerItem(file://)
  - (void)play; - (void)pause; - (BOOL)paused;
  - (void)seekTo:(double)seconds;     // zero-tolerance
  - (void)setRate:(float)rate;        // mm_clamp_rate -> AVPlayer.rate
  - (double)currentTime; - (double)duration;
  - (CALayer*)playerLayer;            // AVPlayerLayer for the plugin view
  @end
  ```
- **Known AVFoundation core** (the parts not RE-dependent):
  ```objc
  // load: AVPlayerItem from a file:// URL; KVO status/duration/presentationSize
  NSURL *u = [NSURL URLWithString:fileUrl];
  self.item = [AVPlayerItem playerItemWithURL:u];
  self.player = [AVPlayer playerWithPlayerItem:self.item];
  self.layer = [AVPlayerLayer playerLayerWithPlayer:self.player];
  [self.item addObserver:self forKeyPath:@"status" options:0 context:0];
  // seek: frame-accurate
  CMTime t = CMTimeMakeWithSeconds(seconds, NSEC_PER_SEC);
  [self.player seekToTime:t toleranceBefore:kCMTimeZero toleranceAfter:kCMTimeZero];
  // rate: self.player.rate = mm_clamp_rate(rate, item.canPlayFastForward, item.canPlaySlowForward);
  // periodic time observer ~4Hz -> WebCore timeChanged (callback addr from REFINDINGS)
  ```
- **RE-dependent:** the `observeValueForKeyPath:` handlers call the WebCore `MediaPlayer::*Changed` entry points (addresses from REFINDINGS) on the saved `webCoreMediaPlayer`, using `mm_status_to_states`.
- **Test (on-device, gated):** a clip loads + plays via the engine in isolation (a temporary `%ctor` smoke that builds an engine on a known file and logs status transitions).

### Task 2.2: State-callback bridge → WebCore  — FOLDED INTO 2.1 (DONE)
- The KVO/time-observer → `MediaPlayer::{networkState,readyState,time}Changed` bridge ships inside `MMTransplantEngine` (Task 2.1). Its behavioral validation (WebCore JS sees correct `readyState`/`timeupdate`) requires the engine connected to a live `<video>`, so it runs as part of the Phase-3 on-device test.

---

## Phase 3 — Interception wiring (hooks + neuter + layer slot-in)

> Uses the "Resolved values (Phase 1)" block above. Symbols/offsets are exact; the layer slot-in target is discovered on first playback (Task 3.2).

### Task 3.1: Side-table + hook seek/setRate/play/pause/load/currentTime/duration
- `%ctor`: `MSHookFunction` each `MediaPlayerPrivateiPhone::*` (the Resolved-values symbols). A `std::map<void*, MMTransplantEngine*>` (or an `NSMapTable`) keyed on the backend `this*` (created in the `load` hook via `[[MMTransplantEngine alloc] initWithMediaPlayer:*(void**)((char*)this+4)]`) routes each call to its engine. `seek`→`[engine seekTo:t]`, `setRate`→`[engine setRate:r]`, `play`/`pause`/`currentTime`/`duration` likewise. Test (gated): seek is frame-accurate (measure via `?tdbg` drift `err` — no keyframe snap); `playbackRate` takes effect. **First-playback observation:** log the play-time `this+8` class + drill to its video view/layer (the deferred Section-5 item) for Task 3.2.

### Task 3.2: Neuter the original engine + slot `AVPlayerLayer` into the video view
- Using the play-time view/layer discovered in 3.1: suppress the original controller's load/play (so only our `AVPlayer` decodes) and insert `engine.playerLayer` into the native video view's layer (same frame/z-order WebKit already drives, so transitions/compositing are inherited). If the original engine can't be cleanly neutered, hide its view + overlay ours at the same rect. Test (gated): video renders via AVPlayer in the correct on-screen rect; existing transitions (fade/slide/iris) still composite correctly; force-inline holds (no fullscreen flip).

---

## Phase 4 — Parity hardening (errors, fallback, gesture, all playmodes)

### Task 4.1: Error mapping + `MPAVController` fallback
- Map `AVPlayerItem` failure → `networkStateChanged(DecodeError)` (preserves `<video>` error + the `CACHE_LOCAL_FAIL` path). If engine init throws/returns nil, un-neuter the original controller. Test (gated): a missing cache file behaves like today; force-disable pref degrades to MPAVController.

### Task 4.2: Gesture parity + auto-play bonus
- Add a tweak pref (`/var/mobile/Library/Preferences/com.mosaicmesh.mmvideo.plist`) `AutoPlay` (default on). When on, `AVPlayer play` without a gesture (eliminates VNC-autotap). When off, keep the arm flow. Test (gated): with AutoPlay on, video starts with no tap; off, parity with today.

### Task 4.3: Playmode parity sweep + force-inline
- Gated on-device sweep: SEGMENT, INDIVIDUAL, FULL each play/loop/seek correctly. Confirm video stays **inline** (force-inline; the transplant's inline `AVPlayerLayer` means forced-fullscreen never triggers — verify no screen flips to a fullscreen player). Record results in a parity checklist; fix gaps.

---

## Phase 5 — Packaging + staged on-wall validation

### Task 5.1: `.deb` packaging + install/remove scripts
- `make package` produces `com.mosaicmesh.mmvideo_0.0.1_iphoneos-arm.deb`. Add `tweak/mmvideo/deploy.sh` (scp `.deb` + `dpkg -i`) and `remove.sh` (`dpkg -r`). Test: install/remove round-trips on one device (gated); `dpkg -r` restores today's behavior.

### Task 5.2: Staged on-wall validation
- Gated: one device → confirm frame-accurate seek (`?tdbg` `err` tighter than today's ~11 ms, no keyframe-snap) + speed-nudge + cross-screen sync; then a small group; record metrics. Update memory `wall-desync-is-video-seek` with the new sync floor.

---

## Self-Review

**Spec coverage:** §1 interception→Tasks 3.1/3.2; §2 glue→Phase 2; §3 mechanics→3.1; §4 compositing→3.2; §5 file://→0.2; §6 payoff→3.1; §7 gesture→4.2; §8 errors/fallback→4.1; §9 deploy→5.1; §10 validation→4.3/5.2. Feasibility/build constraints→Global Constraints. All spec sections mapped.

**Placeholder scan:** Phase 0–1 tasks contain complete code/commands. Phases 2–5 are explicitly an outline pending the Task 1.3 re-plan (the RE outputs genuinely don't exist yet) — this is sequencing, not a hidden placeholder; the AVFoundation code that IS knowable is given, and the unknown values are named as REFINDINGS inputs. The re-plan checkpoint is an explicit task.

**Type consistency:** `mm_url_to_path`/`mm_clamp_rate`/`mm_status_to_states` signatures match between `mmurl.h`, the tests, and their Phase-2 consumers. `MMTransplantEngine` interface is used consistently in Phases 2–3.

**Known limitation (called out, not hidden):** Phases 2–5 cannot be fully bite-sized until Phase 1 RE lands; the plan makes that explicit with a re-plan gate rather than fabricating exact code for undiscovered internals.
