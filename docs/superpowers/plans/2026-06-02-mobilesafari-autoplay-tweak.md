# MobileSafari Autoplay Tweak Implementation Plan

> **STATUS: CANCELLED 2026-06-02 after Tasks 1–2.** Task 3 discovered iOS 5 SDK isn't archived publicly anywhere on GitHub (theos/sdks oldest is iPhoneOS9.3.sdk; iOS 5.1 SDK requires Xcode 4.6.3 extraction from a Mac with an Apple Developer account). Operator chose to pivot to a Veency-connection-pool approach instead. See `docs/superpowers/plans/2026-06-02-veency-connection-pool.md`. Tasks 1–2 completed cleanly (apt prereqs installed in WSL, theos cloned, ldid built from Procursus source, `tools/tweak/.gitignore` committed at `f6330ff`). Those changes are harmless and ready if you ever revisit iOS tweak work.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, package, and fleet-deploy a MobileSubstrate tweak (`com.mosaicmesh.autoplay`) that disables iOS 5 MobileSafari's HTML5-media user-gesture gate, eliminating the need for synthetic taps on the iPad-1 / iOS 5.1.1 fleet.

**Architecture:** Single-file Logos tweak (`%hook WebPreferences` → `return NO;`), built in WSL2 Ubuntu via theos + iPhoneOS5.1 SDK, packaged as a `.deb` shipped in-repo at `tools/tweak/packages/`. Deployed via `dpkg -i` from the existing onboarding script. Server code stays unchanged (the existing `_auto_arm_client` / `NEEDS_ARM` path remains as fallback for future non-iOS-5 device classes); the tweaked iPads simply never reach those paths.

**Tech Stack:** Objective-C (Logos preprocessor), theos build system, MobileSubstrate, dpkg, PowerShell (onboarding glue), OpenSSH, iPhoneOS5.1.sdk.

**Spec:** `docs/superpowers/specs/2026-06-02-mobilesafari-autoplay-tweak-design.md`

---

## File structure

Files this plan creates or modifies:

- Create: `tools/tweak/MosaicMeshAutoplay/Tweak.xm`
- Create: `tools/tweak/MosaicMeshAutoplay/Makefile`
- Create: `tools/tweak/MosaicMeshAutoplay/MosaicMeshAutoplay.plist`
- Create: `tools/tweak/MosaicMeshAutoplay/control`
- Create: `tools/tweak/MosaicMeshAutoplay/entitlements.plist` (empty)
- Create: `tools/tweak/build.sh` (idempotent WSL build wrapper)
- Create: `tools/tweak/README.md` (how to rebuild + redeploy)
- Create: `tools/tweak/packages/com.mosaicmesh.autoplay_0.1.0_iphoneos-arm.deb` (build output, committed)
- Create: `tools/tweak/.gitignore` (only `.theos/`, `obj/`, build intermediates)
- Modify: `tools/onboard_devices.ps1` (add step 5.4c: scp + dpkg the .deb)
- Modify: `server.py` (docstring/comment updates only on `_auto_arm_client` at ~1718 and `NEEDS_ARM` handler at ~2289)

---

## Task 1: Install WSL2 Ubuntu build prerequisites

**Files:** none (system packages only)

- [ ] **Step 1: Verify which tools are missing**

Run from a PowerShell or Bash window on the Windows host:

```bash
powershell.exe -NoProfile -Command "wsl.exe -d Ubuntu -- bash -c 'for tool in make fakeroot xz gcc clang ldid theos; do command -v \$tool >/dev/null && echo \"  \$tool: HAVE\" || echo \"  \$tool: MISSING\"; done'"
```

Expected: `make`, `fakeroot`, `xz`, `gcc`, `clang`, `ldid`, `theos` will report MISSING on a fresh Ubuntu install. (Verified 2026-06-02 in this repo's host environment.)

- [ ] **Step 2: Install base build tools (apt)**

```bash
powershell.exe -NoProfile -Command "wsl.exe -d Ubuntu -- bash -c 'sudo apt-get update && sudo apt-get install -y build-essential fakeroot xz-utils perl git curl wget'"
```

Expected: apt fetches and installs without errors. `build-essential` brings `make`, `gcc`, `g++`. The user may be prompted for their sudo password — that's expected on a fresh WSL distro.

- [ ] **Step 3: Verify base tools are now present**

```bash
powershell.exe -NoProfile -Command "wsl.exe -d Ubuntu -- bash -c 'for tool in make fakeroot xz gcc; do command -v \$tool >/dev/null && echo \"  \$tool: \$(command -v \$tool)\" || echo \"  \$tool: STILL MISSING\"; done'"
```

Expected: all four print a path (e.g. `make: /usr/bin/make`). None should say STILL MISSING.

- [ ] **Step 4: Install ldid (used for fake-signing the .deb)**

ldid is not packaged in Ubuntu's default repos for our purposes; build from source via theos's bundled copy is preferred (Task 2 handles this). For now, verify the path is clear:

```bash
powershell.exe -NoProfile -Command "wsl.exe -d Ubuntu -- bash -c 'command -v ldid && echo HAVE-LDID || echo NEED-LDID-FROM-THEOS'"
```

Expected: `NEED-LDID-FROM-THEOS` (theos's bootstrap step in Task 2 will install it via its `bin/` directory).

- [ ] **Step 5: Commit a marker file recording the toolchain prep**

No source files were modified this task; nothing to commit. Move on.

---

## Task 2: Bootstrap theos in WSL2 Ubuntu

**Files:**
- Modify: `~/.bashrc` (WSL home, not the repo) — add `$THEOS` export

- [ ] **Step 1: Verify $THEOS is not already set / theos not already cloned**

```bash
powershell.exe -NoProfile -Command "wsl.exe -d Ubuntu -- bash -c 'echo THEOS=\$THEOS; test -d ~/theos && echo theos-dir-EXISTS || echo theos-dir-MISSING'"
```

Expected on a fresh install: `THEOS=` (empty) and `theos-dir-MISSING`. If theos already exists, skip the clone step but still verify `$THEOS` is exported.

- [ ] **Step 2: Clone theos with all submodules**

```bash
powershell.exe -NoProfile -Command "wsl.exe -d Ubuntu -- bash -c 'git clone --recursive https://github.com/theos/theos.git ~/theos'"
```

Expected: clone completes; `~/theos/bin/`, `~/theos/makefiles/`, `~/theos/vendor/` populated. ~50MB download. The `--recursive` is critical — theos has submodules (headers, vendor libs) that the build needs.

- [ ] **Step 3: Export $THEOS in shell startup**

```bash
powershell.exe -NoProfile -Command "wsl.exe -d Ubuntu -- bash -c 'grep -q \"export THEOS=\" ~/.bashrc || echo \"export THEOS=\\\$HOME/theos\" >> ~/.bashrc'"
```

Expected: idempotent — runs cleanly whether the line already exists or not. After this, new interactive shells will have `$THEOS` set.

- [ ] **Step 4: Verify theos's bundled ldid is usable**

```bash
powershell.exe -NoProfile -Command "wsl.exe -d Ubuntu -- bash -c 'ls -la ~/theos/toolchain/linux/iphone/bin/ldid 2>/dev/null || ls -la ~/theos/bin/ldid 2>/dev/null || echo NO-LDID-IN-THEOS'"
```

Expected on a fresh theos: theos ships ldid in `~/theos/toolchain/linux/iphone/bin/` once a toolchain is installed, OR you may need to install ldid separately. If `NO-LDID-IN-THEOS` is reported, install via:

```bash
powershell.exe -NoProfile -Command "wsl.exe -d Ubuntu -- bash -c 'sudo apt-get install -y ldid 2>/dev/null || (cd /tmp && git clone https://github.com/ProcursusTeam/ldid.git && cd ldid && make -j\$(nproc) && sudo make install)'"
```

(Procursus's ldid fork is the maintained one as of 2026; the older saurik repo is unmaintained.)

- [ ] **Step 5: Commit the .gitignore for the tweak directory (no theos files committed)**

Create `tools/tweak/.gitignore`:

```gitignore
# theos build intermediates
.theos/
obj/
*.o
*.a
*.dylib.unsigned

# We DO check in the final .deb (packages/*.deb) so deploy doesn't require the build chain.
# We DO check in source files (Tweak.xm, Makefile, control, *.plist).
```

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
mkdir -p tools/tweak
# Write tools/tweak/.gitignore with the content above (use your editor / Write tool).
git add tools/tweak/.gitignore
git commit -m "chore(tweak): gitignore theos build intermediates"
```

Expected: a single-line addition to the index, clean commit.

---

## Task 3: Fetch and install the iPhone SDK 5.1

**Files:**
- Create: `~/theos/sdks/iPhoneOS5.1.sdk/` (WSL home, not repo)

- [ ] **Step 1: Verify the SDK isn't already present**

```bash
powershell.exe -NoProfile -Command "wsl.exe -d Ubuntu -- bash -c 'ls -d ~/theos/sdks/iPhoneOS5.1.sdk 2>/dev/null && echo HAVE-SDK || echo NEED-SDK'"
```

Expected on first run: `NEED-SDK`. If `HAVE-SDK`, skip the download steps below.

- [ ] **Step 2: Download iPhoneOS5.1.sdk.tbz2 from the theos/sdks GitHub repo**

```bash
powershell.exe -NoProfile -Command "wsl.exe -d Ubuntu -- bash -c 'mkdir -p ~/theos/sdks && cd ~/theos/sdks && curl -fsSL -o iPhoneOS5.1.sdk.tbz2 https://github.com/theos/sdks/raw/master/iPhoneOS5.1.sdk.tbz2'"
```

Expected: ~80MB download. If GitHub redirects to LFS and the raw URL fails, try the LFS-aware URL:

```bash
powershell.exe -NoProfile -Command "wsl.exe -d Ubuntu -- bash -c 'cd ~/theos/sdks && curl -fsSL -o iPhoneOS5.1.sdk.tbz2 https://media.githubusercontent.com/media/theos/sdks/master/iPhoneOS5.1.sdk.tbz2'"
```

If both fail, the SDK can be extracted manually from an Xcode 4.6.3 install (the last Xcode that shipped iOS 5 SDK) — see `tools/tweak/README.md` (Task 11) for the manual path.

- [ ] **Step 3: Verify the download size (sanity check, not SHA pin — theos/sdks doesn't publish SHAs)**

```bash
powershell.exe -NoProfile -Command "wsl.exe -d Ubuntu -- bash -c 'ls -la ~/theos/sdks/iPhoneOS5.1.sdk.tbz2'"
```

Expected: file size between 50MB and 150MB. If it's under 10MB you got an HTML error page instead of the tarball — re-fetch.

- [ ] **Step 4: Extract the SDK**

```bash
powershell.exe -NoProfile -Command "wsl.exe -d Ubuntu -- bash -c 'cd ~/theos/sdks && tar -xjf iPhoneOS5.1.sdk.tbz2 && rm iPhoneOS5.1.sdk.tbz2'"
```

Expected: directory `~/theos/sdks/iPhoneOS5.1.sdk/` containing `SDKSettings.plist`, `System/`, `usr/`, `Symbols/`.

- [ ] **Step 5: Verify the SDK is recognized by theos**

```bash
powershell.exe -NoProfile -Command "wsl.exe -d Ubuntu -- bash -c 'ls ~/theos/sdks/iPhoneOS5.1.sdk/System/Library/Frameworks/ | head -10'"
```

Expected: list of frameworks including `UIKit.framework`, `CoreFoundation.framework`, `Foundation.framework`. If the directory is empty or missing, the extraction failed.

---

## Task 4: Scaffold the MosaicMeshAutoplay project

**Files:**
- Create: `tools/tweak/MosaicMeshAutoplay/control`
- Create: `tools/tweak/MosaicMeshAutoplay/Makefile`
- Create: `tools/tweak/MosaicMeshAutoplay/MosaicMeshAutoplay.plist`
- Create: `tools/tweak/MosaicMeshAutoplay/entitlements.plist`

- [ ] **Step 1: Create the project directory**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
mkdir -p tools/tweak/MosaicMeshAutoplay
```

- [ ] **Step 2: Write the `control` file (Debian package metadata)**

Create `tools/tweak/MosaicMeshAutoplay/control` with exactly:

```
Package: com.mosaicmesh.autoplay
Name: MosaicMesh Autoplay
Version: 0.1.0
Architecture: iphoneos-arm
Description: Disable HTML5 media user-gesture gate in MobileSafari, so
 MosaicMesh display-wall iPads (iPad-1 / iOS 5.1.1) auto-play video
 without requiring a synthetic tap. Patches -[WebPreferences
 mediaPlaybackRequiresUserGesture] to always return NO. Bundle
 filter is com.apple.mobilesafari only -- no other apps affected.
Maintainer: MosaicMesh <noreply@mosaicmesh.local>
Author: MosaicMesh
Section: Tweaks
Depends: mobilesubstrate (>= 0.9.5000), firmware (>= 5.0), firmware (<< 7.0)
```

The trailing newline matters for dpkg — make sure the file ends with `\n`.

- [ ] **Step 3: Write the MobileSubstrate bundle filter `MosaicMeshAutoplay.plist`**

Create `tools/tweak/MosaicMeshAutoplay/MosaicMeshAutoplay.plist` with exactly:

```xml
{ Filter = { Bundles = ( "com.apple.mobilesafari" ); }; }
```

This is the OpenStep-format plist that MobileSubstrate reads to decide which processes to inject the dylib into. Only MobileSafari gets hooked.

- [ ] **Step 4: Write the (empty) entitlements file**

Create `tools/tweak/MosaicMeshAutoplay/entitlements.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
</dict>
</plist>
```

Empty by design — the hook runs inside MobileSafari's own process, inherits its entitlements, doesn't need anything extra.

- [ ] **Step 5: Write the theos Makefile**

Create `tools/tweak/MosaicMeshAutoplay/Makefile`:

```makefile
TARGET = iphone:clang:5.1:5.0
ARCHS = armv7

include $(THEOS)/makefiles/common.mk

TWEAK_NAME = MosaicMeshAutoplay
MosaicMeshAutoplay_FILES = Tweak.xm
MosaicMeshAutoplay_FRAMEWORKS = Foundation
MosaicMeshAutoplay_PRIVATE_FRAMEWORKS = WebKit
MosaicMeshAutoplay_CFLAGS = -fobjc-arc

include $(THEOS_MAKE_PATH)/tweak.mk

after-install::
	install.exec "killall -9 MobileSafari"
```

`TARGET = iphone:clang:5.1:5.0` means: build with clang against the iPhoneOS 5.1 SDK, with a deployment target of iOS 5.0. `ARCHS = armv7` produces an armv7-only binary (iPad-1 is armv7; no thumb/arm64).

`after-install::` is the action theos runs when you `make install` (deploy to a device via theos's built-in device path) — we don't use that, but leaving it in doesn't hurt and helps if you ever invoke `make install` directly during development.

- [ ] **Step 6: Commit the scaffold (no Tweak.xm yet, that's Task 5)**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
git add tools/tweak/MosaicMeshAutoplay/control
git add tools/tweak/MosaicMeshAutoplay/Makefile
git add tools/tweak/MosaicMeshAutoplay/MosaicMeshAutoplay.plist
git add tools/tweak/MosaicMeshAutoplay/entitlements.plist
git commit -m "feat(tweak): scaffold MosaicMeshAutoplay project (control + Makefile + filter)"
```

Expected: 4 files added, one clean commit.

---

## Task 5: Write Tweak.xm (the actual hook)

**Files:**
- Create: `tools/tweak/MosaicMeshAutoplay/Tweak.xm`

- [ ] **Step 1: Write the Logos source**

Create `tools/tweak/MosaicMeshAutoplay/Tweak.xm`:

```objc
//
// MosaicMeshAutoplay -- disable iOS 5 HTML5-media user-gesture gate
// inside MobileSafari so MosaicMesh display-wall iPads can auto-play
// video.play() / audio.play() from JavaScript without requiring a
// real touch event.
//
// The gate lives in WebCore: HTMLMediaElement::playInternal() returns
// early when settings()->mediaPlaybackRequiresUserGesture() is true
// AND ScriptController::processingUserGesture() is false. Setting
// the WebPreferences flag to NO collapses the first half of the AND,
// the check passes, and play() proceeds.
//
// Filter: com.apple.mobilesafari only (see MosaicMeshAutoplay.plist).
// Other UIWebView-hosting apps on the iPad are unaffected.
//

#import <Foundation/Foundation.h>

@interface WebPreferences : NSObject
- (BOOL)mediaPlaybackRequiresUserGesture;
- (void)setMediaPlaybackRequiresUserGesture:(BOOL)flag;
@end

%hook WebPreferences

- (BOOL)mediaPlaybackRequiresUserGesture {
    // Always-no, regardless of what the application or WebKit
    // defaults set. MobileSafari constructs WebPreferences
    // internally during launch and during private-mode toggles --
    // hooking the getter (rather than the setter) means we don't
    // care WHEN it's set, only what's read at the gate check.
    return NO;
}

%end
```

- [ ] **Step 2: Sanity-check the file (basic grep for the hook directive)**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
grep -nE "^%hook |^%end" tools/tweak/MosaicMeshAutoplay/Tweak.xm
```

Expected output: two lines — one `%hook WebPreferences` and one `%end`. If you see imbalance, the Logos preprocessor will fail at build.

- [ ] **Step 3: Commit Tweak.xm**

```bash
git add tools/tweak/MosaicMeshAutoplay/Tweak.xm
git commit -m "feat(tweak): hook WebPreferences mediaPlaybackRequiresUserGesture to return NO"
```

---

## Task 6: First build of the .deb

**Files:**
- Create: `tools/tweak/packages/com.mosaicmesh.autoplay_0.1.0_iphoneos-arm.deb` (output, committed)

- [ ] **Step 1: Run the theos build**

```bash
powershell.exe -NoProfile -Command "wsl.exe -d Ubuntu -- bash -c 'cd /mnt/c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh/tools/tweak/MosaicMeshAutoplay && export THEOS=\$HOME/theos && make package FINALPACKAGE=1 2>&1 | tail -40'"
```

Expected: theos compiles `Tweak.xm` via clang, links against UIKit and WebKit headers, signs with ldid, packages into `packages/com.mosaicmesh.autoplay_0.1.0_iphoneos-arm.deb`. Final line should be something like `Done.` or `==> Packaging...`.

Common failure modes:
- **`make: command not found`** → Task 1 incomplete.
- **`No such file or directory: ...iPhoneOS5.1.sdk/...`** → Task 3 incomplete.
- **`Tweak.xm:N: error: unknown type name 'WebPreferences'`** → typo in the `@interface` declaration in Tweak.xm.
- **`ldid: command not found`** → Task 2 step 4 incomplete.

- [ ] **Step 2: Locate the built .deb**

```bash
powershell.exe -NoProfile -Command "wsl.exe -d Ubuntu -- bash -c 'ls -la /mnt/c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh/tools/tweak/MosaicMeshAutoplay/packages/'"
```

Expected: a single file `com.mosaicmesh.autoplay_0.1.0_iphoneos-arm.deb`, around 4-10KB (it's a tiny tweak).

- [ ] **Step 3: Move the .deb into the shared packages dir at the tweak level (not per-project)**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
mkdir -p tools/tweak/packages
mv tools/tweak/MosaicMeshAutoplay/packages/com.mosaicmesh.autoplay_0.1.0_iphoneos-arm.deb tools/tweak/packages/
rmdir tools/tweak/MosaicMeshAutoplay/packages 2>/dev/null
```

Why a separate dir: future tweaks (if we build more) drop their .debs alongside this one. Onboarding's step 5.4c (Task 9) globs `tools/tweak/packages/com.mosaicmesh.autoplay_*.deb` to find the latest version.

- [ ] **Step 4: Inspect the .deb's contents to confirm structure**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
python -c "
import lzma, gzip, tarfile, io
with open('tools/tweak/packages/com.mosaicmesh.autoplay_0.1.0_iphoneos-arm.deb','rb') as f:
    f.read(8)
    while True:
        hdr = f.read(60)
        if len(hdr) < 60: break
        name = hdr[0:16].rstrip().rstrip(b'/').decode()
        size = int(hdr[48:58].rstrip())
        blob = f.read(size)
        if size % 2: f.read(1)
        if name.startswith('data.tar'):
            raw = lzma.decompress(blob) if name.endswith(('.xz','.lzma')) else gzip.decompress(blob)
            for m in tarfile.open(fileobj=io.BytesIO(raw)).getmembers():
                if m.isfile(): print(f'  {m.size:>9}  {m.name}')
"
```

Expected output (file sizes will vary slightly):

```
     XXXX  ./Library/MobileSubstrate/DynamicLibraries/MosaicMeshAutoplay.dylib
       XX  ./Library/MobileSubstrate/DynamicLibraries/MosaicMeshAutoplay.plist
```

Exactly two files. If you see additional files (e.g. lingering .h files, dSYMs), check the Makefile didn't pull in extras.

- [ ] **Step 5: Commit the .deb**

```bash
git add tools/tweak/packages/com.mosaicmesh.autoplay_0.1.0_iphoneos-arm.deb
git commit -m "build(tweak): com.mosaicmesh.autoplay 0.1.0 .deb"
```

Note: yes, we commit a binary artifact. It's tiny (~5KB), version-pinned, and the alternative (require every operator to set up theos before they can deploy) is much worse.

---

## Task 7: Pilot install on iPad .82 (one device, manual verification)

**Files:** none modified in the repo; on-device install only.

- [ ] **Step 1: SCP the .deb to iPad .82**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
scp -i /c/Users/jtubb.SOLUTIONS/.ssh/mosaic_ipad \
    -o HostKeyAlgorithms=+ssh-rsa \
    -o PubkeyAcceptedAlgorithms=+ssh-rsa \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=accept-new \
    tools/tweak/packages/com.mosaicmesh.autoplay_0.1.0_iphoneos-arm.deb \
    root@192.168.1.82:/tmp/mma.deb
```

Expected: scp completes, no errors. (.82 is the iPad we used for inspection earlier; known-healthy.)

- [ ] **Step 2: dpkg -i on the iPad, capture output**

```bash
ssh -i /c/Users/jtubb.SOLUTIONS/.ssh/mosaic_ipad \
    -o HostKeyAlgorithms=+ssh-rsa \
    -o PubkeyAcceptedAlgorithms=+ssh-rsa \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=accept-new \
    root@192.168.1.82 \
    "dpkg -i /tmp/mma.deb && dpkg -l com.mosaicmesh.autoplay && echo INSTALLED_OK"
```

Expected: `INSTALLED_OK` at the end, plus a dpkg listing line beginning with `ii  com.mosaicmesh.autoplay  0.1.0`. If you get `dependency problems`, the `Depends:` field in `control` is asking for something that isn't installed — investigate the specific message.

- [ ] **Step 3: Respring SpringBoard so MobileSubstrate loads the new dylib**

```bash
ssh -i /c/Users/jtubb.SOLUTIONS/.ssh/mosaic_ipad \
    -o HostKeyAlgorithms=+ssh-rsa \
    -o PubkeyAcceptedAlgorithms=+ssh-rsa \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=accept-new \
    root@192.168.1.82 \
    "killall MobileSafari 2>/dev/null; killall SpringBoard 2>/dev/null; sleep 1; echo RESPRUNG"
```

Expected: `RESPRUNG`. The iPad screen will flash black for ~3 seconds while SpringBoard restarts.

- [ ] **Step 4: Visually confirm MobileSafari relaunches cleanly**

This step is **manual** — there is no SSH-driven way to verify Safari rendering. Walk to iPad .82, observe:

1. Did SpringBoard come back? (icons visible, no boot-loop)
2. Open Safari (tap its icon, or wait for the LaunchDaemon at `/Library/LaunchDaemons/com.mosaicmesh.autolock-off.plist` to fire `uiopen` after 30s)
3. Does the MosaicMesh page load?
4. Does the video element start playing **without you tapping the screen**?

If 1 fails (no SpringBoard) → the tweak is crashing SpringBoard. SSH in (`ssh root@192.168.1.82`) — sshd runs independently of SpringBoard — and uninstall: `dpkg -r com.mosaicmesh.autoplay; killall SpringBoard`. Then debug the Tweak.xm symbol declarations.

If 2-3 fail but 1 succeeds → unrelated to this work; check server state.

If 4 fails (video stays paused, needs tap) → the hook didn't take effect. Check `dmesg | grep -i substrate` on the iPad for MS load errors, and verify `dpkg -L com.mosaicmesh.autoplay` shows both the .dylib and .plist landed in `/Library/MobileSubstrate/DynamicLibraries/`.

- [ ] **Step 5: If video autoplays, commit a verification note**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
# No code change for this step. The verification is captured by your
# proceeding to Task 8 instead of stopping here. If you want a record:
git commit --allow-empty -m "verify(tweak): autoplay pilot on iPad 192.168.1.82 -- video autoplays without tap"
```

(Optional. Skip if you don't want an empty commit.)

---

## Task 8: Server-side verification that the tweaked iPad never triggers _auto_arm_client

**Files:** none modified.

- [ ] **Step 1: Restart the MosaicMesh server (clean log baseline)**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
powershell.exe -NoProfile -Command "Get-Process python -ErrorAction SilentlyContinue | Where-Object { (Get-CimInstance Win32_Process -Filter \"ProcessId=\$(\$_.Id)\").CommandLine -match 'server\.py' } | Stop-Process -Force"
sleep 2
rm -f server.out server.err
nohup python server.py -p 3000 -v > server.out 2> server.err < /dev/null &
sleep 3
```

Expected: previous server.py exits; new one starts; `server.err` begins fresh.

- [ ] **Step 2: Fire a PLAY cycle on iPad .82 only**

(Replace `<Test Group>` with whatever display group .82 is in.) Use the existing one-shot tool:

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
python tools/start_all_probe.py "Test Group" 30
```

Or, if you want a play-only (not a re-start), use `tools/run_and_collect.py` per its docstring.

- [ ] **Step 3: Inspect server.err for any auto-arm activity targeting .82**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
grep -nE "auto-arm|NEEDS_ARM" server.err | head -20
```

Expected for the tweaked iPad (.82): **zero matches.** If you see `auto-arm: jecpgri3ygzgds4i tapped via ...` or `NEEDS_ARM received from jecpgri3ygzgds4i`, the tweak isn't taking effect — return to Task 7 step 4 and check `dpkg -L` / `dmesg`.

If other (un-tweaked) iPads in the fleet show NEEDS_ARM/auto-arm activity, that's expected — they don't have the tweak yet.

- [ ] **Step 4: Commit the verification observation (optional empty commit)**

Skip or add an empty commit as in Task 7 step 5.

---

## Task 9: Integrate the .deb install into onboard_devices.ps1

**Files:**
- Modify: `tools/onboard_devices.ps1` (insert step 5.4c)

- [ ] **Step 1: Locate the insertion point in onboard_devices.ps1**

The new step goes after the Veency-plist block (5.4b, ends at line ~702 in current HEAD) and before the respring block (5.5, starts at line ~709). Verify:

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
grep -nE "# 5\.4b|# 5\.5|^\s*}$" tools/onboard_devices.ps1 | head -10
```

Find the line containing `# 5.5) respring after successful tweak install` — your new block goes immediately before it.

- [ ] **Step 2: Insert step 5.4c**

Insert exactly this PowerShell block between the closing `}` of the 5.4b Veency block and the comment `# 5.5) respring`:

```powershell
    # 5.4c) deploy + install the mosaicmesh autoplay tweak. The .deb
    #       ships in-repo at tools/tweak/packages/ (we host no apt
    #       repo for our own tweaks; dpkg -i directly). Mirrors the
    #       apt7 bootstrap scp+dpkg idiom already used at ~line 537.
    #
    #       The tweak hooks WebPreferences mediaPlaybackRequiresUserGesture
    #       inside MobileSafari and forces it NO, so HTML5 video.play()
    #       from the MosaicMesh page doesn't need a synthetic touch
    #       event to satisfy iOS 5's user-gesture gate. See
    #       docs/superpowers/specs/2026-06-02-mobilesafari-autoplay-tweak-design.md
    if ($status -eq "OK" -and $pkgsToInstall -and $scp) {
        $debGlob = Join-Path $PSScriptRoot 'tweak/packages/com.mosaicmesh.autoplay_*.deb'
        $deb = Get-Item $debGlob -ErrorAction SilentlyContinue |
               Sort-Object LastWriteTime -Descending |
               Select-Object -First 1
        if ($deb) {
            try {
                & $scp -i $KeyPath -P $p @sshLegacy $deb.FullName `
                       "${User}@${hostName}:/tmp/mma.deb" 2>&1 | Out-Null
                $tOut = (& $ssh -i $KeyPath -p $p @sshLegacy `
                         "${User}@${hostName}" `
                         "dpkg -i /tmp/mma.deb && rm /tmp/mma.deb && echo AUTOPLAY_OK" `
                         2>&1) | Out-String
                if ($tOut -match 'AUTOPLAY_OK') {
                    Write-Host "  autoplay-tweak: installed ($($deb.Name))" -ForegroundColor Green
                } else {
                    $msg = ($tOut.Trim() -replace '\s+', ' ')
                    Write-Host "  autoplay-tweak: $msg" -ForegroundColor Yellow
                }
            } catch {
                Write-Host "  autoplay-tweak failed: $($_.Exception.Message)" -ForegroundColor Yellow
            }
        } else {
            Write-Host "  autoplay-tweak: .deb not found at $debGlob (skipped)" `
                       -ForegroundColor DarkYellow
        }
    }

```

(There's an intentional blank line at the end, before the `# 5.5)` comment.)

- [ ] **Step 3: Syntax-check by parsing the script**

```bash
powershell.exe -NoProfile -Command "Get-Command -Syntax 'C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh\tools\onboard_devices.ps1' 2>&1 | Out-Null; \$? "
```

Expected: `True`. If `False`, a syntax error in the inserted block — most likely a stray backtick or mis-paired brace.

Alternative: just try invoking with `-WhatIf` or with no arguments to see if it loads:

```bash
powershell.exe -NoProfile -Command ". 'C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh\tools\onboard_devices.ps1' -Hosts 'nope-dryrun' -InstallTweaks 2>&1 | Select-Object -First 5"
```

Expected: it parses without complaining; may error later about reaching the host, but it should at least *parse*.

- [ ] **Step 4: Commit the onboarding change**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
git add tools/onboard_devices.ps1
git commit -m "feat(onboard): step 5.4c deploys com.mosaicmesh.autoplay .deb via dpkg"
```

---

## Task 10: Update server.py docstrings noting the dormant fallback

**Files:**
- Modify: `server.py` (lines ~1718 and ~2289 only — comments/docstrings)

- [ ] **Step 1: Locate the _auto_arm_client docstring**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
grep -nE 'def _auto_arm_client|elif\(msg\["REQUEST"\] == "NEEDS_ARM"\)' server.py
```

Note the line numbers — they're approximate (1718 / 2289 in current HEAD).

- [ ] **Step 2: Update the _auto_arm_client docstring**

Find the current docstring (starts at `"""Deliver one Veency VNC tap (screen centre)...` immediately after `async def _auto_arm_client(client_key):`). Replace it with:

```python
    """Deliver one Veency VNC tap (screen centre) to arm an un-armed iOS
    device. Best-effort: missing vncdo / no IP / failure just logs -- the
    PREPARE timeout covers a device that can't be armed.

    NOTE (2026-06-02): On the iPad-1 / iOS 5.1.1 fleet this path is
    expected to be DORMANT -- those iPads have the
    `com.mosaicmesh.autoplay` MobileSubstrate tweak installed (built
    from tools/tweak/MosaicMeshAutoplay/, deployed by onboard step
    5.4c), which hooks WebPreferences.mediaPlaybackRequiresUserGesture
    to return NO. Their JS gate-detection never fires, so they never
    emit NEEDS_ARM, so we never call this function for them. If this
    path starts firing for an iPad-1, the tweak failed to load --
    investigate /Library/MobileSubstrate/DynamicLibraries/ on that
    device. This path remains intentionally for future non-iOS-5
    device classes (iOS 6+, Android, Fire tablets) that genuinely
    need a synthesized tap.

    Captures vncdo stderr and checks the exit code so an auth failure
    (wrong VEENCY_PASSWORD, veency not running, port closed) is logged
    as a failure instead of a silent "tapped". The previous version
    DEVNULL'd stderr and didn't check rc, so a wrong password produced
    a misleading 'auto-arm: tapped' line in the log even though the
    iPad never received the click."""
```

(Use the Edit tool: `old_string` = the entire current docstring including triple quotes, `new_string` = the block above with triple quotes.)

- [ ] **Step 3: Add a comment above the NEEDS_ARM handler**

Find the line `elif(msg["REQUEST"] == "NEEDS_ARM"):` in `msg_response` (approx line 2289). Replace it with:

```python
    elif(msg["REQUEST"] == "NEEDS_ARM"):
        # NOTE (2026-06-02): tweaked iPad-1s do not emit NEEDS_ARM
        # because com.mosaicmesh.autoplay disables the user-gesture
        # gate inside MobileSafari. This handler stays as the fallback
        # for any future client class (iOS 6+, Android, etc.) whose
        # browser still requires a synthesized tap. See
        # _auto_arm_client docstring for the longer context.
```

(Then leave the existing handler body that follows that elif unchanged.)

- [ ] **Step 4: Verify server.py still parses**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
python -c "import ast; ast.parse(open('server.py').read()); print('OK')"
```

Expected: `OK`. If you get a SyntaxError, you broke a docstring quote — check unescaped quote characters.

- [ ] **Step 5: Run the unit test suite as a sanity check**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
python pytest_runner.py --unit 2>&1 | tail -20
```

Expected: same pass/fail counts as before this change (you're only editing comments/docstrings; nothing semantic should differ).

- [ ] **Step 6: Commit**

```bash
git add server.py
git commit -m "docs(server): note _auto_arm_client / NEEDS_ARM are dormant for autoplay-tweaked iPads"
```

---

## Task 11: Write the build script and README

**Files:**
- Create: `tools/tweak/build.sh`
- Create: `tools/tweak/README.md`

- [ ] **Step 1: Write `tools/tweak/build.sh`**

This is the idempotent WSL build wrapper. Create `tools/tweak/build.sh`:

```bash
#!/usr/bin/env bash
# Build the MosaicMeshAutoplay .deb in WSL2 Ubuntu. Idempotent:
# safe to re-run; only does work if something is missing or
# Tweak.xm has changed since the last build.
#
# Run from anywhere on the Windows side via:
#     wsl.exe -d Ubuntu bash /mnt/c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh/tools/tweak/build.sh
# or from within WSL:
#     bash $(wslpath 'C:\Users\jtubb.SOLUTIONS\Documents\mosiacmesh\tools\tweak\build.sh')

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TWEAK_DIR="${REPO_ROOT}/tools/tweak/MosaicMeshAutoplay"
PACKAGES_DIR="${REPO_ROOT}/tools/tweak/packages"

# Step 1: base apt packages
need_apt=()
for tool in make fakeroot xz gcc perl git curl; do
    command -v "$tool" >/dev/null || need_apt+=("$tool")
done
if [ ${#need_apt[@]} -gt 0 ]; then
    echo "==> installing missing apt packages: ${need_apt[*]}"
    sudo apt-get update
    sudo apt-get install -y build-essential fakeroot xz-utils perl git curl
fi

# Step 2: ldid
if ! command -v ldid >/dev/null; then
    echo "==> installing ldid (Procursus fork)"
    sudo apt-get install -y ldid 2>/dev/null || {
        tmp=$(mktemp -d) && cd "$tmp"
        git clone --depth 1 https://github.com/ProcursusTeam/ldid.git
        cd ldid && make -j"$(nproc)" && sudo make install
        cd "$REPO_ROOT"
    }
fi

# Step 3: theos
export THEOS="${THEOS:-$HOME/theos}"
if [ ! -d "$THEOS" ]; then
    echo "==> cloning theos to $THEOS"
    git clone --recursive https://github.com/theos/theos.git "$THEOS"
fi

# Step 4: iPhone SDK 5.1
SDK="$THEOS/sdks/iPhoneOS5.1.sdk"
if [ ! -d "$SDK" ]; then
    echo "==> fetching iPhoneOS5.1.sdk.tbz2 from theos/sdks"
    mkdir -p "$THEOS/sdks"
    cd "$THEOS/sdks"
    if ! curl -fsSL -o iPhoneOS5.1.sdk.tbz2 \
         https://github.com/theos/sdks/raw/master/iPhoneOS5.1.sdk.tbz2; then
        echo "ERROR: GitHub fetch failed. Manually download the SDK from"
        echo "       https://github.com/theos/sdks/raw/master/iPhoneOS5.1.sdk.tbz2"
        echo "       (or extract from Xcode 4.6.3 -- see README.md), drop at"
        echo "       $THEOS/sdks/iPhoneOS5.1.sdk.tbz2 and re-run."
        exit 1
    fi
    tar -xjf iPhoneOS5.1.sdk.tbz2
    rm iPhoneOS5.1.sdk.tbz2
    cd "$REPO_ROOT"
fi

# Step 5: build
echo "==> building MosaicMeshAutoplay"
cd "$TWEAK_DIR"
make clean >/dev/null 2>&1 || true
make package FINALPACKAGE=1

# Step 6: move .deb into the shared packages dir
mkdir -p "$PACKAGES_DIR"
BUILT_DEB=$(ls packages/com.mosaicmesh.autoplay_*.deb 2>/dev/null | head -1)
if [ -z "$BUILT_DEB" ]; then
    echo "ERROR: build appeared to succeed but no .deb in $TWEAK_DIR/packages/"
    exit 1
fi
mv "$BUILT_DEB" "$PACKAGES_DIR/"
rmdir packages 2>/dev/null || true

echo "==> done: $PACKAGES_DIR/$(basename "$BUILT_DEB")"
```

Make it executable:

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
chmod +x tools/tweak/build.sh
```

(On Windows the `chmod` is a no-op but git records the +x flag.)

- [ ] **Step 2: Write `tools/tweak/README.md`**

Create `tools/tweak/README.md`:

```markdown
# MosaicMesh tweaks

iOS jailbreak tweaks built for the iPad-1 / iOS 5.1.1 fleet.

| Tweak | Package | Purpose |
|-------|---------|---------|
| MosaicMeshAutoplay | `com.mosaicmesh.autoplay` | Disable HTML5 media user-gesture gate in MobileSafari so the MosaicMesh display page autoplays video without a tap. Spec: `docs/superpowers/specs/2026-06-02-mobilesafari-autoplay-tweak-design.md` |

## Deploy

The built `.deb` ships in `tools/tweak/packages/`. **You don't need
the build chain to deploy** — onboarding's step 5.4c picks up
whatever `.deb` is in `packages/` and `dpkg -i`s it:

```powershell
.\tools\onboard_devices.ps1 -Hosts 192.168.1.82 -InstallTweaks
```

For a fleet-wide install across all known iPads in the discovery
inventory:

```powershell
.\tools\onboard_devices.ps1 -InstallTweaks
```

## Rebuild

Required only when source under `MosaicMeshAutoplay/` changes.

Requires WSL2 Ubuntu. From the Windows side:

```powershell
wsl.exe -d Ubuntu bash /mnt/c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh/tools/tweak/build.sh
```

The first run bootstraps everything (apt packages, ldid, theos,
iPhoneOS5.1.sdk); subsequent runs only rebuild what changed.
Output lands at `tools/tweak/packages/com.mosaicmesh.autoplay_X.X.X_iphoneos-arm.deb`.
Commit the new `.deb` after rebuild.

## SDK manual install (if GitHub fetch fails)

The build script downloads `iPhoneOS5.1.sdk.tbz2` from the community
`theos/sdks` GitHub repo. If GitHub blocks or moves the file:

1. Install Xcode 4.6.3 (last Xcode that shipped iOS 5 SDK) on a Mac
   from Apple's developer.apple.com archive.
2. Copy `Xcode.app/Contents/Developer/Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS5.1.sdk/`
   to a tarball.
3. Place at `~/theos/sdks/iPhoneOS5.1.sdk/` in WSL.

## Hook architecture

`Tweak.xm` hooks one Objective-C method:

```objc
%hook WebPreferences
- (BOOL)mediaPlaybackRequiresUserGesture { return NO; }
%end
```

`WebPreferences` is in `/System/Library/PrivateFrameworks/WebKit.framework`.
The bundle filter (`MosaicMeshAutoplay.plist`) restricts injection to
`com.apple.mobilesafari` only — no other apps are affected.

## Uninstall

```bash
ssh root@<ip> "dpkg -r com.mosaicmesh.autoplay && killall SpringBoard"
```
```

- [ ] **Step 3: Commit the build script + README**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
git add tools/tweak/build.sh tools/tweak/README.md
git commit -m "docs(tweak): build.sh wrapper + README"
```

---

## Task 12: Fleet-wide install

**Files:** none modified; on-device deployment only.

- [ ] **Step 1: Take a snapshot of current fleet state**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
curl -s http://localhost:3000/api/discovery/devices | python -c "
import json, sys
data = json.load(sys.stdin)
devs = data.get('devices', data) if isinstance(data, dict) else data
online = [d for d in devs if d.get('isOnline')]
print(f'online iPads: {len(online)}/{len(devs)}')
for d in online:
    print(f\"  {d.get('friendlyName'):24s}  {d.get('ip'):15s}  {d.get('clientKey')}\")
" > /tmp/pre-tweak-fleet.txt
cat /tmp/pre-tweak-fleet.txt | head -30
```

Expected: count of online iPads — should be ~23-24.

- [ ] **Step 2: Run onboarding with -InstallTweaks across the fleet**

(Run from a PowerShell window so you see colour output:)

```powershell
.\tools\onboard_devices.ps1 -InstallTweaks
```

Expected per iPad: green `autoplay-tweak: installed (com.mosaicmesh.autoplay_0.1.0_iphoneos-arm.deb)`. Any yellow output means dpkg returned non-zero — investigate that specific iPad.

The known previously-failing iPads from this session are:
- `192.168.1.84` (`qfx7s4kipa6k1emi` / sign1screen10) — WiFi packet loss, SSH may timeout
- `192.168.1.50` (`ei49puuugjznz5mi` / sign1screen1) — known_hosts was stale, fixed in this session

If `.84` still times out after Insomnia install (which is part of this run too), retry it individually after a power cycle.

- [ ] **Step 3: Verify package installed on each reachable iPad**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
# Loop over online IPs from the snapshot file
for ip in $(awk '{print $3}' /tmp/pre-tweak-fleet.txt | grep "^192\."); do
    result=$(ssh -i /c/Users/jtubb.SOLUTIONS/.ssh/mosaic_ipad \
                 -o HostKeyAlgorithms=+ssh-rsa \
                 -o PubkeyAcceptedAlgorithms=+ssh-rsa \
                 -o IdentitiesOnly=yes \
                 -o StrictHostKeyChecking=accept-new \
                 -o ConnectTimeout=5 \
                 -o BatchMode=yes \
                 root@$ip "dpkg -l com.mosaicmesh.autoplay 2>/dev/null | awk '/^ii/ {print \$3}' || echo NOT-INSTALLED" 2>&1)
    echo "  $ip: $(echo "$result" | tail -1)"
done | tee /tmp/post-tweak-fleet.txt
```

Expected: each line shows `0.1.0` (installed) or `NOT-INSTALLED` (failed). Tally: if 22+/24 are `0.1.0` you're done; if more failures, retry the failed ones.

- [ ] **Step 4: Trigger a fleet-wide PLAY and verify zero NEEDS_ARM for tweaked iPads**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
# Truncate the log to start fresh
> server.err
python tools/start_all_probe.py "Test Group" 30
sleep 5
echo "--- NEEDS_ARM messages received during burst ---"
grep -nE "NEEDS_ARM|auto-arm" server.err | head -20
echo "(empty means perfect)"
```

Expected: empty grep output. Every `NEEDS_ARM` or `auto-arm` line corresponds to a non-tweaked iPad (failure or skipped) — cross-reference the IP against your post-tweak install list.

- [ ] **Step 5: Commit a verification observation**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
git commit --allow-empty -m "verify(tweak): fleet-wide install -- N/24 iPads no longer emit NEEDS_ARM"
```

(Replace `N` with the actual count from step 4. Skip if you don't want an empty commit.)

---

## Task 13: Final cleanup + close out

**Files:** none modified.

- [ ] **Step 1: Confirm no build artifacts leaked into the repo**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
git status tools/tweak/
```

Expected: clean. If you see `.theos/`, `obj/`, or `*.dylib.unsigned` listed as untracked, the `.gitignore` is missing entries — add them.

- [ ] **Step 2: Confirm the final shape of tools/tweak/**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
find tools/tweak -type f -not -path '*/.*' | sort
```

Expected output (exactly):

```
tools/tweak/MosaicMeshAutoplay/Makefile
tools/tweak/MosaicMeshAutoplay/MosaicMeshAutoplay.plist
tools/tweak/MosaicMeshAutoplay/Tweak.xm
tools/tweak/MosaicMeshAutoplay/control
tools/tweak/MosaicMeshAutoplay/entitlements.plist
tools/tweak/README.md
tools/tweak/build.sh
tools/tweak/packages/com.mosaicmesh.autoplay_0.1.0_iphoneos-arm.deb
```

If extra files appear (cached .o, intermediate .a), the build script is leaving cruft — add to `.gitignore`.

- [ ] **Step 3: View the full set of commits this plan produced**

```bash
cd /c/Users/jtubb.SOLUTIONS/Documents/mosiacmesh
git log --oneline $(git merge-base HEAD main)..HEAD
```

Expected: a tight series of commits matching the plan's commit messages. If anything looks malformed, consider a rebase to tidy before merging to main.

- [ ] **Step 4: Done**

The feature branch is now ready for review / merge to main. Confirm spec acceptance criteria from the spec doc:

1. ✅ Build chain produces the .deb without manual intervention (Task 11's build.sh, after the one-time bootstrap)
2. ✅ Pilot install on one iPad confirmed MobileSafari relaunches + page loads (Task 7)
3. ✅ Video autoplays without synthetic tap on tweaked iPad (Task 7 step 4)
4. ✅ Server log shows zero `NEEDS_ARM` / `_auto_arm_client` from tweaked iPads (Task 8 + Task 12 step 4)
5. ✅ Fleet-wide install at N/24 iPads (Task 12 step 3)
