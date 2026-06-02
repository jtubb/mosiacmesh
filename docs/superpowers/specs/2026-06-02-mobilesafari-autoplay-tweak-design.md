# MobileSafari Autoplay Tweak — Design

> **STATUS: CANCELLED 2026-06-02.** During plan execution Task 3 (fetch iPhone SDK 5.1) discovered that iOS 5 SDK is not archived in any public GitHub mirror — the oldest in `theos/sdks` is iPhoneOS9.3, and obtaining the real 5.1 SDK requires extracting from a Xcode 4.6.3 install (Apple Developer account + Mac + manual work). Operator chose to pivot to a Veency-connection-pool approach in the server instead (see `docs/superpowers/plans/2026-06-02-veency-connection-pool.md`). This spec is preserved for reference — the design itself is sound, only the SDK-acquisition risk materialised. Tasks 1–2 of the cancelled plan completed (apt prereqs + theos clone in WSL + `tools/tweak/.gitignore` committed); those changes are harmless and ready if you ever revisit iOS tweak work.

## Goal

Eliminate the iOS-5-user-gesture gate that forces MosaicMesh to
inject a synthetic tap before HTML5 video can play. A small
MobileSubstrate tweak hooks `WebPreferences` inside MobileSafari and
makes `mediaPlaybackRequiresUserGesture` always return `NO`. From
JavaScript, `video.play()` then just works.

Net effect on MosaicMesh for the current iPad-1 / iOS 5.1.1 fleet:
the JS gate-detection path no longer triggers, so `NEEDS_ARM` is
never emitted by these clients, so `_auto_arm_client` is never
invoked for them. Video arms instantly when PREPARE is processed.

The `_auto_arm_client` / `NEEDS_ARM` / `armPending` machinery
**stays in the server**, because it's still wanted as a fallback
for future device classes (iOS 6+ iPads, Android, Fire tablets,
etc.) where the autoplay tweak doesn't apply and a real tap is
genuinely required.

## Why this, not the alternatives we considered

This spec was originally drafted to build a custom Activator
listener that injected a centre-tap (the "tap-listener" design).
Mid-review the operator pointed out the simpler question: **why
satisfy the gesture gate instead of removing it?**

| | Inject a synthetic tap | Patch the gate (this spec) |
|---|---|---|
| Per-video-start cost on iPad-1 | ~700 ms (SSH + activator round-trip) | 0 ms (gate is gone) |
| Server change | Rewrite `_auto_arm_client` | None — iPad-1 just stops emitting `NEEDS_ARM`, so the existing path is never exercised. `_auto_arm_client` stays for future non-iOS-5 device classes. |
| Network failure modes (iPad-1) | SSH timeout, packet loss, retry logic | None — no network call per arm |
| Tweak complexity | Activator listener + IOHIDPostEvent | Single `%hook` on a getter |
| Mental model | "Server taps the screen" | "iPad just plays video" |

The previously-rejected alternatives (SimulateTouch needs iOS 6+,
AutoTouch v1 needs per-iPad GUI macro recording, Veency-pool keeps a
VNC stack hot) are all moot under the patch-the-gate approach
because the iPad never needs to receive a tap in the first place.

## Architecture

```
┌─────────────────────────┐         ┌─────────────────────────────────┐
│ server.py               │  PREPARE │ iPad (iOS 5.1.1, jailbroken)   │
│                         │ ───────▶ │                                 │
│ _begin_prepare(display) │          │ MobileSafari running our page   │
│   broadcasts PREPARE    │          │   ↓ JS: video.play()            │
│                         │          │ HTMLMediaElement::playInternal()│
│ (_auto_arm_client,      │          │   ↓ checks settings()->         │
│  NEEDS_ARM, AUTO_ARM    │          │     mediaPlaybackRequires       │
│  all stay — dormant     │          │     UserGesture() == NO         │
│  for tweaked iPads)     │          │                                 │
│                         │          │   ↓                             │
│                         │          │ Video plays                     │
└─────────────────────────┘          └─────────────────────────────────┘
                                              ▲
                                              │ Tweak active because of:
                                              │
                                     ┌────────┴────────────────────┐
                                     │ Library/MobileSubstrate/    │
                                     │   DynamicLibraries/         │
                                     │   MosaicMeshAutoplay.dylib  │
                                     │   .plist  (filter: MobileSafari) │
                                     └─────────────────────────────┘
```

## Components

### 1. The tweak source (`tools/tweak/MosaicMeshAutoplay/`)

A theos Logos project. Source files:

- `Tweak.xm` — the entire payload:

  ```objc
  %hook WebPreferences
  - (BOOL)mediaPlaybackRequiresUserGesture { return NO; }
  - (BOOL)mediaPlaybackAllowsAirPlay { return YES; }    // bonus
  %end
  ```

  Optional second hook on the `UIWebView` public bridge in case
  any in-Safari helper UIs read through that path:
  ```objc
  %hook UIWebView
  - (BOOL)mediaPlaybackRequiresUserAction { return NO; }
  %end
  ```

- `MosaicMeshAutoplay.plist` — MS bundle filter:
  ```xml
  { Filter = { Bundles = ( "com.apple.mobilesafari" ); }; }
  ```

- `control` — deb metadata:
  ```
  Package: com.mosaicmesh.autoplay
  Architecture: iphoneos-arm
  Depends: mobilesubstrate (>= 0.9.5000), firmware (>= 5.0), firmware (<< 7.0)
  Description: Disable HTML5 video user-gesture gate in MobileSafari
   for MosaicMesh display-wall iPads.
  ```

- `Makefile` — theos boilerplate; armv7 + iOS 5.0 SDK minimum.
- `entitlements.plist` — empty (no special entitlements needed; the
  hook runs inside MobileSafari's own process).

### 2. The build pipeline (`tools/tweak/build.sh`)

Shell script run from WSL2 Ubuntu. Idempotent.

Bootstrap (one-time, if missing):
- Install OS packages: `build-essential fakeroot xz-utils ldid`
- Clone theos: `git clone --recursive https://github.com/theos/theos.git ~/theos`
- Set `$THEOS` in `~/.bashrc`
- Fetch the iPhone SDK 5.1 from the community `theos/sdks` repo
  (`https://github.com/theos/sdks/raw/master/iPhoneOS5.1.sdk.tbz2`),
  SHA-pinned, placed at `$THEOS/sdks/iPhoneOS5.1.sdk`. If GitHub
  unreachable the build aborts with a clear "fetch manually from
  <url>, drop at <path>" message rather than silently degrading.

Build (every run):
- `make package FINALPACKAGE=1` inside the project dir
- Output: `tools/tweak/packages/com.mosaicmesh.autoplay_<ver>_iphoneos-arm.deb`

Source AND the built .deb are both checked into the repo. That way
deploy never requires the build chain; only re-builds do.

### 3. Onboarding integration (`tools/onboard_devices.ps1`)

New step **5.4c** after Veency-plist (5.4b), before respring (5.5).
Uses the existing `$scp`/`$ssh` helpers (already wired at lines
179–182 for the apt7 bootstrap path):

```powershell
# 5.4c) deploy + install the mosaicmesh autoplay tweak (.deb
#       shipped in the repo, NOT in an apt repo). Mirrors the apt7
#       scp+dpkg idiom already in this script at ~line 537.
if ($status -eq "OK" -and $pkgsToInstall -and $scp) {
    $debGlob = Join-Path $PSScriptRoot 'tweak/packages/com.mosaicmesh.autoplay_*.deb'
    $deb = Get-Item $debGlob -ErrorAction SilentlyContinue |
           Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($deb) {
        & $scp -i $KeyPath -P $p @sshLegacy $deb.FullName "${User}@${hostName}:/tmp/mma.deb" 2>&1 | Out-Null
        $tOut = (& $ssh -i $KeyPath -p $p @sshLegacy "${User}@${hostName}" `
                 "dpkg -i /tmp/mma.deb && rm /tmp/mma.deb && echo AUTOPLAY_INSTALLED" 2>&1) | Out-String
        if ($tOut -match 'AUTOPLAY_INSTALLED') {
            Write-Host "  autoplay-tweak: installed" -ForegroundColor Green
        } else {
            Write-Host "  autoplay-tweak: $($tOut.Trim() -replace '\s+',' ')" -ForegroundColor Yellow
        }
    } else {
        Write-Host "  autoplay-tweak: .deb not found at $debGlob (skipped)" -ForegroundColor DarkYellow
    }
}
```

No apt-cache pollution, no separate repo to host.

### 4. Server changes (`server.py`)

**None functionally.** The whole arm path stays — kept as the
fallback mechanism for future device classes that genuinely need a
tap (iOS 6+ where SimulateTouch works, Android via ADB, Fire
tablets via an as-yet-undecided path, etc.).

What changes is only *which iPads exercise it*: with the autoplay
tweak loaded inside MobileSafari, the iPad-1 JS gate-detection
never fires, the client never emits `NEEDS_ARM`, and
`_auto_arm_client` is therefore never invoked for these clients.
The path stays warm and tested for any non-tweaked device that
joins later.

Recommended (non-functional) doc-only update:

- Update the `_auto_arm_client` docstring (server.py:1718) and the
  `NEEDS_ARM` handler comment (server.py:~2289) to note that on
  iOS-5 iPads with `com.mosaicmesh.autoplay` installed this path
  is dormant — that's the expected steady state for the iPad-1
  fleet, not a bug. Without this comment, a future debugger will
  see "VNC tap code that hasn't fired in months" and assume it's
  broken.

Client-side (`index.html`): the JS path that emits `NEEDS_ARM`
stays unchanged. On a tweaked iPad it simply never fires (because
`video.play()` succeeds and the page emits `READY` instead). On a
hypothetical future non-tweaked device it still works.

`armPending` tracking in `Display` state and the `AUTO_ARM` flag
also stay — they're the dormant fallback's machinery.

### 5. requirements.txt

No change.

## Risk areas + mitigations

1. **iPhone SDK 5.1 acquisition.** Same as the original spec —
   pinned to `theos/sdks` GitHub repo, build script documents the
   exact tarball SHA.

2. **The `WebPreferences` class is private API.** It's been stable
   across iOS 4-8 (it's the same WebKit interface the public
   `WebPreferences` desktop class came from). On iOS 5.1.1, frozen
   forever. No moving target.

3. **Bundle filter on `com.apple.mobilesafari` only.** This narrow
   filter means the hook never loads in other apps. Safer (no
   cross-app side effects) but means if you ever ship a custom
   WebView app for MosaicMesh, you'd need to widen the filter or
   replicate.

4. **MobileSafari crash on tweak load.** If the hook is wrong (e.g.,
   `WebPreferences` is in a different framework than expected on
   iOS 5.1.1), MobileSafari will refuse to launch. Mitigation:
   first install + respring is performed on a SINGLE iPad via
   onboarding with `-Hosts <one-ip>`, the operator visually
   confirms Safari relaunches and loads the MosaicMesh page,
   THEN the fleet-wide install proceeds.

5. **Other media may now auto-play.** Any web page loaded in
   MobileSafari can now auto-play video/audio without a tap. This
   matters zero for our use case (the iPads only ever load the
   MosaicMesh page) but worth knowing.

6. **WebKit also checks gesture for fullscreen and audio.** Our
   hook covers the video.play() path because that reads through
   `mediaPlaybackRequiresUserGesture`. If we later need
   `<video webkitenterfullscreen>` to autoplay, that's a different
   private API gate (`requestKeyboardSelectionInteraction`-style)
   and a separate hook. We don't currently use fullscreen, so out
   of scope.

## What this design does NOT cover

- Tap injection. Whole removed approach.
- Activator integration. Same.
- A fallback when the tweak fails to load. We rely on the
  single-iPad pilot install step to catch breakage.
- Hosting the .deb in an apt repo. `dpkg -i` directly.

## Acceptance criteria

1. WSL2 Ubuntu build chain produces
   `tools/tweak/packages/com.mosaicmesh.autoplay_X.X_iphoneos-arm.deb`
   without manual intervention beyond the one-time bootstrap.
2. Pilot install on a single iPad via `onboard_devices.ps1 -Hosts
   <ip> -InstallTweaks` results in `dpkg -l | grep
   com.mosaicmesh.autoplay` returning the installed package, AND
   MobileSafari relaunches successfully (loads the MosaicMesh page,
   no SpringBoard crash).
3. On that piloted iPad, calling `video.play()` from JavaScript in
   the MosaicMesh page begins playback **without any synthetic tap
   or `activator send`**. Verified by the operator observing the
   wall video starting unattended after PREPARE.
4. Server log during a normal PLAY cycle on the tweaked iPad shows
   NO `_auto_arm_client` invocation and NO `NEEDS_ARM` message
   received from that client. (The code paths still exist, but the
   tweaked iPad never reaches them.)
5. Fleet-wide `onboard_devices.ps1 -InstallTweaks` install across
   the 24-iPad fleet results in 24/24 `dpkg -l | grep
   com.mosaicmesh.autoplay` confirmations (modulo any iPads with
   pre-existing SSH/connectivity issues — those need their own
   fixes regardless of this work).
