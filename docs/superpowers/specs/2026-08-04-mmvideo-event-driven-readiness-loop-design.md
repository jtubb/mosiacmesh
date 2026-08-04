# mmvideo: Event-Driven Readiness + Looping (KVO / DidPlayToEnd) — Design

**Date:** 2026-08-04
**Status:** Approved (brainstorming) → ready for implementation plan
**Scope:** native dylib change — `tweak/mmvideo/MMTransplantEngine.m` (+ `.h`), and the follow-up JS retirement in `index.html` / `js/eloopflag.js`.

## Problem

The mmvideo transplant (AVPlayer under WebKit's `MediaPlayerPrivateiPhone` on iOS 5.1) detects readiness and end-of-item by **polling `AVPlayerItem.status` inside the periodic time-observer** (`addPeriodicTimeObserverForInterval`). That observer only fires **while the timebase is advancing (playing)**. Two production freezes follow directly:

- **Cold-load freeze (`verr=3`):** on a fresh load the player isn't playing yet, so the periodic observer never fires, so `status` is never polled, so WebCore's `readyState`/`loadedmetadata` never advances — the element hangs at `rs:0` forever. A chicken-and-egg: it has to be playing to notice it *can* play. (Confirmed on-wall: a multi-item demo, which cold-loads every item boundary, stalled screens en masse; a DOM-layer `re-load()` watchdog could not recover the *persistent* element and was reverted.)
- **Loop stall:** there is no end-of-item event; looping is faked by a heuristic (`MMTransplantEngine.m:143–149`: if `currentTime` is pinned near the end for 6 ticks, seek to 0), which also only runs while the observer is firing.

The native player detects both **event-driven and playback-independent** (KVO on `AVPlayerItem.status`; `AVPlayerItemDidPlayToEndTimeNotification`). This design adopts those native mechanisms.

## Prerequisite reality (post-audit)

`tweak/mmvideo/AUDIT-races-leaks.md` describes a WebThread-owned lifetime racing main-queue consumers (`C1`, the leading crash suspect). **That is already largely fixed:** `Tweak.x` now runs *all* engine operations on the main queue — `h_load`/`h_seek`/`h_setRate`/`h_play`/`h_pause` each `dispatch_async(main, …)`, and `dropEngine` marks-dead then `dispatch_async(main, …)` to `mm_engine_pause` + `mm_engine_free` (the *"all-AV-on-main"* comments). So teardown/free and the periodic observer are already serialized on one queue.

**Load-bearing invariant this design preserves:** all engine access is on the main queue; any callback that fires *off*-main (`status` KVO fires on AVFoundation's queue) must `dispatch_async` to the main queue and check `e->dead` before touching the engine. The `dead` flag is **not** a band-aid — it is the correct guard for in-flight async callbacks vs teardown, and it stays.

## Architecture

Split the current `mm_engine_poll` **by cause, not by clock**:

- **Readiness** — `status → net/ready`, the WebCore ivar-mirror writes at `+0x14`/`+0x18`, and firing `netChanged`/`readyChanged` — moves to a **KVO handler** on `AVPlayerItem.status`. Event-driven; fires whether or not playback is advancing. **This is the cold-load fix.**
- **Time** — `currentTime`/`duration` caching, the `+0x24` rate mirror, and `timeChanged` — **stays** in the periodic time-observer (legitimately playback-coupled: time only matters while playing).
- The **stall heuristic (`:143–149`) is deleted**; end-of-item becomes an event (Component 2).

Both readiness and time publishers keep the existing **`WebThreadLock` parking dance** around their WebCore ivar writes + `*Changed` callbacks (the guard against the WebThread `CFRunLoopTimer` SIGTRAP).

Refactor: extract from today's `mm_engine_poll` two functions —
- `mm_engine_publish_ready(MMEngine *e)` — reads `status`, computes `net`/`ready` via `mm_status_to_states`, writes `+0x14`/`+0x18` under the WebThreadLock, fires `netChanged`/`readyChanged` on change. Called from the KVO handler (on main).
- `mm_engine_publish_time(MMEngine *e)` — the currentTime/duration cache + `g_curTime`/`g_curDuration` publish + `+0x24` rate mirror + `timeChanged`. Called from the periodic observer block (on main), minus the deleted status poll and stall heuristic.

## Component 1 — KVO status observer (readiness)

A minimal ObjC class in `MMTransplantEngine.m`:

```objc
@interface MMStatusObserver : NSObject { @public MMEngine *eng; }
@end
@implementation MMStatusObserver
- (void)observeValueForKeyPath:(NSString *)kp ofObject:(id)o
        change:(NSDictionary *)c context:(void *)ctx {
    MMEngine *e = eng;                      // captured at init (raw C ptr, same as the block)
    dispatch_async(dispatch_get_main_queue(), ^{
        if (!e || e->dead) return;          // in-flight-vs-teardown guard (parity w/ observer block)
        mm_engine_publish_ready(e);
    });
}
@end
```

- One instance **per engine**, stored in a new `void *statusObserver;` field on `MMEngine`, retained.
- In `mm_engine_load`, after `item`/`player` are built:
  `MMStatusObserver *so = [MMStatusObserver new]; so->eng = e; [item addObserver:so forKeyPath:@"status" options:0 context:e]; e->statusObserver = (__bridge_retained void *)so;`
- `AVPlayerItem.status` is KVO-compliant on iOS 5. The marshal to main is mandatory (KVO fires on AVFoundation's internal queue); the marshal + `dead` check put it at exact parity with the existing periodic-observer block's safety.
- Initial readiness: `addObserver` with `options:0` does **not** deliver an initial value, but AVFoundation posts a `status` change as the item prepares, so the first real transition drives the callback. (If the item is *already* `ReadyToPlay` before the observer is added — not possible here, the observer is added synchronously right after `playerItemWithURL:` while status is `Unknown` — a one-shot `mm_engine_publish_ready` right after add would cover it; include it as a belt-and-suspenders call.)

## Component 2 — DidPlayToEnd loop observer (looping)

Block-based (no class needed; `addObserverForName:object:queue:usingBlock:` exists on iOS 5):

```objc
id endTok = [[NSNotificationCenter defaultCenter]
    addObserverForName:AVPlayerItemDidPlayToEndTimeNotification
    object:item queue:[NSOperationQueue mainQueue]
    usingBlock:^(NSNotification *n){
        if (!e || e->dead) return;                    // main queue already; dead-checked
        [PL(e) seekToTime:kCMTimeZero toleranceBefore:kCMTimeZero toleranceAfter:kCMTimeZero];
        PL(e).rate = (e->desiredRate > 0.0f ? e->desiredRate : 1.0f);
    }];
e->endObserver = (__bridge_retained void *)endTok;      // new MMEngine field
```

- Delivered on the main queue, dead-checked — same safety envelope.
- This is the native loop primitive and, per the loop-ownership decision, **the single loop authority** (replaces the stall heuristic *and*, after validation, the DOM handler).

## Component 3 — I7 teardown leak + observer removal

`mm_engine_teardown` (runs on main) gains, in correct order:

1. **KVO remove before item release** (mandatory, else crash): if `statusObserver` && `item`, `[IT(e) removeObserver:(MMStatusObserver*)so forKeyPath:@"status"]`; then release/clear `statusObserver`.
2. **Notification token remove:** `[[NSNotificationCenter defaultCenter] removeObserver:endTok]`; clear `endObserver`.
3. **I7 — unparent the layer:** before the existing `__bridge_transfer` release of `e->layer`, call `[vlayer removeFromSuperlayer]` so the host `FigPluginView` stops retaining stacked AVPlayerLayers (the per-reload leak that also keeps old AVPlayers — and their observers — alive).
4. Then the existing periodic `timeObserver` removal + item/player/layer releases.

## Component 4 — DOM loop retirement (follow-up)

After the native path is validated on the canary, a **separate small JS commit** removes the DOM loop handler: delete `onVideoEnded` + the `_mmExplicitLoop` branch in `index.html:showItem` and `js/eloopflag.js` (and its `<script>` include). Gated on native validation — not part of the dylib change. Single loop owner (native) results.

## Non-goals / out of scope

- No change to the confinement model (already correct) or the `dead`-flag guard (correct, stays).
- No change to the getters (`g_curTime`/`g_curDuration` stable-globals path stays).
- Not fixing the other audit items (`I3` torn doubles, `I4`/`I5` `gCurrentEngine` window, `C2` dict) — out of scope; only `I7` is folded in because teardown is already being edited.
- Not touching the rate/CMTimebase path.

## Build, deploy, validate

- **Build:** dylib on the WSL theos host (`tweak/mmvideo/build.sh`; CRLF-stripping copy from `/mnt/c`). Windows box has no iOS toolchain — this is a build handoff.
- **Deploy:** **canary ONE screen** (Windows OpenSSH, `_exec_ssh` path); never mass-relaunch (PSM). Backup the on-device dylib.
- **Validate on the canary:**
  1. **Cold-load fixed** — run the multi-item demo (every item boundary cold-loads, the case that just failed): screens reach `rs≥1` and play; no permanent `rs:0` stalls.
  2. **Loop clean** — single-item playlist loops with zero end-of-item stalls (the case the DOM fix currently covers).
  3. **I7** — `FigPluginView.layer.sublayers.count` flat across many reloads (was growing).
  4. **No regression** — soak for the WebThread `EXC_BREAKPOINT`; none over the window.
- **Then:** wave rollout, and the Component-4 DOM retirement commit.

## Files

- Modify: `tweak/mmvideo/MMTransplantEngine.m` — `MMStatusObserver` class; `statusObserver`/`endObserver` fields (in the struct, also `.h` if the struct is shared); split `mm_engine_poll` → `mm_engine_publish_ready` + `mm_engine_publish_time`; KVO + notification wiring in `mm_engine_load`; teardown removals + `removeFromSuperlayer`; delete the stall heuristic.
- Modify (follow-up commit): `index.html`, `js/eloopflag.js` — retire the DOM loop handler.
