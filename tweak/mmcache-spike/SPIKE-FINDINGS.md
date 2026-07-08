# mmcache Spike — Findings (Plan 2 Task 1)

**Verdict: PASS.** The client-pull cache mechanism is validated end-to-end on a physical
iPad-1 (sign1screen1.home.lan, iOS 5.1, with mmvideo installed), 2026-07-08.

## What was proven

1. **The `mmcache://` download bridge works.** A hidden-iframe nav to
   `mmcache://fetch?token=&url=` is intercepted by a `%hook WebAppController` /
   `webView:shouldStartLoadWithRequest:` — **coexisting with the mmws hook** (both hook the
   same method; `%orig` chaining works; no conflict, no load crash). Native trace:
   ```
   [spk] webview captured
   [spk] fetch token=spk1 url=http://192.168.1.60:3000/media/server/videos/taptest_480.mp4
   [spk] download OK token=spk1 bytes=3183302 -> /var/mobile/Media/mmcache/spk1.mp4
   ```
2. **`NSData dataWithContentsOfURL:` is the download API** (iOS-5 has NO `NSURLSession`).
   On a background `dispatch_get_global_queue` via `dispatch_async_f` (no Blocks). The write
   was **byte-exact**: device file 3,183,302 == server source 3,183,302.
3. **`__mmCacheDone(token, bytes)` fires back into JS** via
   `stringByEvaluatingJavaScriptFromString` (confirmed on-screen: `bytes=3183302`).
4. **The tweak loads clean** — the symbol-clean build (no C++ unwind, no static ObjC class,
   only bindable libSystem/ObjC/CF/Substrate symbols) held on-device; no SIGKILL.

## The critical correction (why the design changes)

**A raw `file://` src does NOT play; use mmvideo's `http://127.0.0.1:8080/<name>` convention.**

- Setting `<video src="file:///var/mobile/Media/mmcache/spk1.mp4">` did nothing — WebKit
  blocks a `file://` media resource from an `http://`-origin page (cross-origin), so
  `MediaPlayerPrivateiPhone` never engages and mmvideo's hook never fires.
- Setting `<video src="http://127.0.0.1:8080/spk1.mp4">` (with the file at
  `/var/mobile/Media/MosaicMeshCache/spk1.mp4`) **played, fullscreen, no tap** — mmvideo's
  `mm_url_to_path` maps `http://127.0.0.1:8080/<name>` → `file://MosaicMeshCache/<name>` and
  AVPlayer plays the LOCAL file. This mapping happens in the WebKit MediaPlayer hook
  **before any network fetch**, so lighttpd is not involved in playback (lighttpd was up on
  the test device via launchd respawn, but mmvideo intercepts upstream of it — established
  by `mm_engine_load`/`mm_url_to_path` in tweak/mmvideo).

## Design corrections for Plan 2 (applied)

- **`localSrc(token)` returns `http://127.0.0.1:8080/<token>.mp4`**, NOT `file://`.
  (Fixed in `js/mmCacheBackendMmvideo.js` + its test.)
- **The native `fetchToCache` downloads into `/var/mobile/Media/MosaicMeshCache/<token>.mp4`**
  (the dir `mm_url_to_path` maps), NOT a new `mmcache/`. This also shares the dir with the
  current lighttpd cache — the pull just fills the same dir the existing cache uses.
- Download API: `NSData dataWithContentsOfURL:` on a bg queue (no `NSURLSession`, no Blocks).
- The `mmcache://` hook can coexist with mmws as a second `WebAppController` hook — OR the
  handler could fold into a shared bridge; coexistence is proven either way.
- The spike ran in the WEBCLIP (com.apple.webapp), not Safari — `WebAppController` is the
  webclip's delegate; Safari uses a different web stack (the hook never fires there).

## Reusable artifacts

- `tweak/mmcache-spike/` — the throwaway bridge tweak (builds via `wsl -d Ubuntu bash -lc
  './build.sh'`; note the default WSL distro is docker-desktop — theos is in **Ubuntu**).
- `mmcache_spike.html` — the on-device test page (served at `/mmcache_spike`).
- Deploy pattern: scp dylib+plist to `/Library/MobileSubstrate/DynamicLibraries/`; to test
  in the webclip, repoint its `Info.plist` `<string>` URL + respring (backup + revert after).
