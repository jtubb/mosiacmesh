# mmvideo Event-Driven Readiness + Looping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace mmvideo's playback-coupled `AVPlayerItem.status` poll (the `verr=3` cold-load root cause) with event-driven KVO, and replace the near-end stall heuristic with a real `DidPlayToEnd` observer, so cold loads reach ready and single-item playlists loop without the native engine ever needing to be "already playing" to notice it can play or that it ended.

**Architecture:** Split the single `mm_engine_poll` **by cause**: readiness (`status → net/ready`, WebCore ivar mirror `+0x14`/`+0x18`, `net/readyChanged`) moves to a KVO handler on `AVPlayerItem.status` — event-driven, fires whether or not playback advances; time (`m_rate` mirror `+0x24`, `timeChanged`) stays in the periodic time-observer, legitimately playback-coupled. Looping becomes an `AVPlayerItemDidPlayToEndTimeNotification` observer. Both new observers marshal to the main queue and dead-check before touching the engine — exact parity with the existing periodic-observer block. A gated follow-up JS commit retires the DOM `_mmExplicitLoop` workaround once native looping is validated on-wall.

**Tech Stack:** C-only MobileSubstrate/Logos tweak compiled as ObjC + ARC (`tweak/mmvideo/MMTransplantEngine.m`, `Tweak.x`); AVFoundation / CoreMedia / WebCore private symbols via `MSFindSymbol`/`MSHookFunction`; built with theos on a WSL host (`build.sh`); ES5/jQuery-1.x client JS (`index.html`, `js/eloopflag.js`) for the retirement commit.

## Global Constraints

- **No automated test harness exists for this dylib.** AVFoundation KVO cannot be unit-tested on the Windows dev box. The per-task gate is a **clean `build.sh`** (compiles; the two symbol audits still report `CLEAN`/`NONE`). The integration gate is the **on-wall canary** in Task 4. Do not invent pytest/node tests for the native tasks.
- **Build only on the WSL theos host:** `bash tweak/mmvideo/build.sh` (it CRLF-strips, `make clean && make`, and prints `DYLIB=…` plus the two undefined-symbol audits). The Windows box has no iOS toolchain.
- **The engine is main-queue-confined.** All engine ops already run on the main queue (`Tweak.x` `dispatch_async(main, …)` in every hook + `dropEngine`). Any callback that fires **off**-main (KVO fires on AVFoundation's internal queue) MUST `dispatch_async(dispatch_get_main_queue(), …)` and check `if (e->dead) return;` before touching `e`. The `dead` flag is the correct guard — keep it.
- **WebThreadLock discipline is load-bearing.** Every write to the WebCore ivar mirror (`backend + 0x14/0x18/0x24`) and every `*Changed(e->mp)` callback must happen inside the existing park dance: `if (gWTLock && (!gWTIsCurrent || !gWTIsCurrent())) { gWTLock(); … gWTUnlock(); }`. This guards the WebThread `CFRunLoopTimer` over-release SIGTRAP. Do not drop it when splitting the poll.
- **No getter prologue patching.** Do not hook or rewrite the WebCore const getters (`currentTime`/`duration`/`networkState`/`readyState`) — they are polled from the QuickTime plugin's own thread and a prologue rewrite races it into SIGBUS (§21). Readiness reaches WebCore only via the ivar mirror + `*Changed` callbacks, as today.
- **`struct MMEngine` lives in `MMTransplantEngine.m`** (opaque `typedef` in the `.h`). New fields are `.m`-only — no `.h` change.
- **Canary-first, never mass-relaunch.** Deploy to exactly one screen; a fleet-wide relaunch trips the AP PSM/flood-protection. Back up the on-device dylib before overwriting.
- **`I7` (`removeFromSuperlayer` on teardown) is ALREADY in the tree** (`MMTransplantEngine.m:107`, `// I7: unparent`). The spec listed it as open; it is not. The teardown work in this plan is limited to removing the two NEW observers. Do not re-add `removeFromSuperlayer`.

---

### Task 1: Split `mm_engine_poll` into `mm_engine_publish_ready` + `mm_engine_publish_time` (behavior-preserving refactor)

Pure refactor. No new observers yet; the periodic time-observer block calls both new functions, so runtime behavior is byte-for-byte identical to today. This isolates the risky mechanical split from the behavioral changes in Tasks 2–3.

**Files:**
- Modify: `tweak/mmvideo/MMTransplantEngine.m:73-100` (replace `mm_engine_poll`), `:150` (observer block's `mm_engine_poll(eng);` call site)

**Interfaces:**
- Consumes: `e->item`, `e->net`, `e->ready`, `e->backend`, `e->playing`, `e->desiredRate`, `e->netChanged/readyChanged/timeChanged`, `e->mp`; `IT(e)`, `mm_status_to_states`, `gWTLock/gWTUnlock/gWTIsCurrent`.
- Produces: `static void mm_engine_publish_ready(MMEngine *e)` and `static void mm_engine_publish_time(MMEngine *e)` — both main-queue, both self-contained WebThreadLock. Tasks 2 and 3 call `mm_engine_publish_ready` from the KVO handler.

- [ ] **Step 1: Replace `mm_engine_poll` (lines 73-100) with the two split functions**

Delete the whole `mm_engine_poll` function (the block comment on 73-74 and the function 75-100) and put in its place:

```objc
// Readiness publisher (Component 1) — status -> net/ready, mirror WebCore ivars +0x14/+0x18,
// fire net/readyChanged. Called from the KVO status observer AND once right after the observer
// is added. Runs on the MAIN queue (KVO marshals there); holds the WebThreadLock across the
// ivar writes + callbacks (parks the WebThread — the CFRunLoopTimer over-release SIGTRAP guard).
static void mm_engine_publish_ready(MMEngine *e) {
    if (!e || !e->item) return;
    int net = e->net, ready = e->ready;
    long st = (long)[IT(e) status];           // AVPlayerItemStatus
    mm_status_to_states((int)st, &net, &ready);
    BOOL changed = (net != e->net) || (ready != e->ready);
    e->net = net; e->ready = ready;
    BOOL wtLocked = NO;
    if (gWTLock && (!gWTIsCurrent || !gWTIsCurrent())) { gWTLock(); wtLocked = YES; }
    if (e->backend) {
        *(volatile int *)((char *)e->backend + 0x14) = net;      // m_networkState (atomic ivar mirror)
        *(volatile int *)((char *)e->backend + 0x18) = ready;    // m_readyState
    }
    if (changed) {
        if (e->netChanged)   e->netChanged(e->mp);
        if (e->readyChanged) e->readyChanged(e->mp);
    }
    if (wtLocked && gWTUnlock) gWTUnlock();
}

// Time publisher — mirror m_rate +0x24, fire timeChanged. Called from the periodic time-observer
// block (main queue); legitimately playback-coupled. Same WebThreadLock discipline.
static void mm_engine_publish_time(MMEngine *e) {
    if (!e) return;
    float rr = e->playing ? (e->desiredRate > 0.0f ? e->desiredRate : 1.0f) : 0.0f;
    BOOL wtLocked = NO;
    if (gWTLock && (!gWTIsCurrent || !gWTIsCurrent())) { gWTLock(); wtLocked = YES; }
    if (e->backend) {
        *(volatile float *)((char *)e->backend + 0x24) = rr;     // m_rate (paused = rate==0)
    }
    if (e->timeChanged) e->timeChanged(e->mp);
    if (wtLocked && gWTUnlock) gWTUnlock();
}
```

- [ ] **Step 2: Update the observer block's call site (line 150)**

In `mm_engine_load`, the last statement inside the periodic-observer block is `mm_engine_poll(eng);`. Replace that single line with both calls (time first, then readiness — order is immaterial, they take separate locks):

```objc
                         mm_engine_publish_time(eng);
                         mm_engine_publish_ready(eng);
```

- [ ] **Step 3: Build on the WSL theos host**

Run: `bash tweak/mmvideo/build.sh`
Expected: compiles; prints `DYLIB=…`; `== C++ unwind symbols? ==` → `CLEAN (plain ObjC)`; `== remaining undefined compiler-rt builtins? ==` → `NONE (builtins resolved)`. No reference to `mm_engine_poll` remains (grep `mm_engine_poll tweak/mmvideo/MMTransplantEngine.m` → no matches).

- [ ] **Step 4: Commit**

```bash
git add tweak/mmvideo/MMTransplantEngine.m
git commit -m "refactor(mmvideo): split mm_engine_poll into publish_ready + publish_time"
```

---

### Task 2: KVO status observer — event-driven readiness (cold-load fix)

Add an ObjC KVO observer on `AVPlayerItem.status` so readiness is published on status transitions regardless of whether playback is advancing. This is the cold-load (`verr=3`) fix. After this, the periodic observer no longer publishes readiness.

**Files:**
- Modify: `tweak/mmvideo/MMTransplantEngine.m` — new `MMStatusObserver` class (near the top, after the `MMEngine` struct); new `statusObserver` field on `struct MMEngine`; wiring in `mm_engine_load`; removal in `mm_engine_teardown`; drop the redundant `mm_engine_publish_ready(eng)` call from the observer block.

**Interfaces:**
- Consumes: `mm_engine_publish_ready` (Task 1), `IT(e)`, `e->item`, `e->dead`.
- Produces: `e->statusObserver` (retained `MMStatusObserver *` as `void *`), torn down in `mm_engine_teardown` before the item is released.

- [ ] **Step 1: Add the `statusObserver` field to `struct MMEngine`**

In the struct (currently ends at line 47 with `float desiredRate;`), add:

```objc
    void *statusObserver; // Component 1: KVO on AVPlayerItem.status (retained MMStatusObserver*)
```

- [ ] **Step 2: Add the `MMStatusObserver` class**

Insert after the `PL()`/`IT()` inline helpers (after line 50), before `mm_secs`:

```objc
// KVO status observer (Component 1). AVPlayerItem.status is KVO-compliant on iOS 5. KVO fires
// on AVFoundation's internal queue, so marshal to the MAIN queue and dead-check (parity with the
// periodic-observer block) before touching the engine, then publish readiness the native way.
@interface MMStatusObserver : NSObject { @public MMEngine *eng; }
@end
@implementation MMStatusObserver
- (void)observeValueForKeyPath:(NSString *)kp ofObject:(id)obj
        change:(NSDictionary *)ch context:(void *)ctx {
    MMEngine *e = eng;
    dispatch_async(dispatch_get_main_queue(), ^{
        if (!e || e->dead) return;
        mm_engine_publish_ready(e);
    });
}
@end
```

(Forward-declare `mm_engine_publish_ready` above this class if the compiler orders it after — add `static void mm_engine_publish_ready(MMEngine *e);` just above the `@interface`.)

- [ ] **Step 3: Wire the observer in `mm_engine_load`**

In `mm_engine_load`, immediately after the three `e->item/player/layer = (__bridge_retained …)` assignments and the `e->net = 2; …` init line (line 128), before the `MMEngine *eng = e;` for the periodic observer, add:

```objc
        MMStatusObserver *so = [[MMStatusObserver alloc] init];
        so->eng = e;
        [item addObserver:so forKeyPath:@"status" options:0 context:e];
        e->statusObserver = (__bridge_retained void *)so;
        mm_engine_publish_ready(e);   // belt-and-suspenders: cover an already-ReadyToPlay item
```

- [ ] **Step 4: Remove the now-redundant readiness call from the observer block**

In the periodic-observer block, delete the `mm_engine_publish_ready(eng);` line added in Task 1 Step 2, leaving only `mm_engine_publish_time(eng);`. Readiness is now owned by KVO; the periodic block owns time only. (KVO fires on transitions; during steady playback readiness never changes, so nothing is lost.)

- [ ] **Step 5: Remove the observer in `mm_engine_teardown` (before the item is released)**

`mm_engine_teardown` releases `e->item` at line 108. KVO must be removed *before* that or the runtime crashes ("deallocated while observers were registered"). At the **top** of `mm_engine_teardown`, before the existing `timeObserver` removal, add:

```objc
    if (e->statusObserver) {
        if (e->item) [IT(e) removeObserver:(__bridge id)e->statusObserver forKeyPath:@"status"];
        id t = (__bridge_transfer id)e->statusObserver; e->statusObserver = NULL; (void)t;
    }
```

- [ ] **Step 6: Build**

Run: `bash tweak/mmvideo/build.sh`
Expected: compiles; `CLEAN` / `NONE` on both audits. Confirm the observer block now calls only `mm_engine_publish_time`.

- [ ] **Step 7: Commit**

```bash
git add tweak/mmvideo/MMTransplantEngine.m
git commit -m "feat(mmvideo): KVO on AVPlayerItem.status for event-driven readiness (cold-load fix)"
```

---

### Task 3: `DidPlayToEnd` loop observer + delete the stall heuristic

Replace the near-end stall heuristic (`MMTransplantEngine.m:143-149`) with a real end-of-item notification observer. Both changes ship in one commit so looping works at every commit boundary. Native becomes the single loop authority.

**Files:**
- Modify: `tweak/mmvideo/MMTransplantEngine.m` — new `endObserver` field; notification observer wired in `mm_engine_load`; removal in `mm_engine_teardown`; delete the stall-heuristic block (143-149); remove the now-unused `lastCt`/`stallTicks` fields and their init.

**Interfaces:**
- Consumes: `PL(e)`, `e->desiredRate`, `e->dead`; `AVPlayerItemDidPlayToEndTimeNotification`.
- Produces: `e->endObserver` (retained notification token as `void *`), torn down in `mm_engine_teardown` before the item is released.

- [ ] **Step 1: Add the `endObserver` field; remove `lastCt`/`stallTicks`**

In `struct MMEngine`: delete `double lastCt; int stallTicks;   // vtfix5: near-end stall watchdog` (line 45) and add:

```objc
    void *endObserver; // Component 2: AVPlayerItemDidPlayToEndTimeNotification token (retained)
```

- [ ] **Step 2: Wire the `DidPlayToEnd` observer in `mm_engine_load`**

After the periodic time-observer is installed (`e->timeObserver = (__bridge_retained void *)obs;`, line 152) and still inside the `@autoreleasepool`, add:

```objc
        MMEngine *engc = e;
        id endTok = [[NSNotificationCenter defaultCenter]
            addObserverForName:AVPlayerItemDidPlayToEndTimeNotification
            object:item
            queue:[NSOperationQueue mainQueue]
            usingBlock:^(NSNotification *n){
                if (!engc || engc->dead) return;   // main queue already; dead-checked
                [PL(engc) seekToTime:kCMTimeZero toleranceBefore:kCMTimeZero toleranceAfter:kCMTimeZero];
                PL(engc).rate = (engc->desiredRate > 0.0f ? engc->desiredRate : 1.0f);
            }];
        e->endObserver = (__bridge_retained void *)endTok;
```

- [ ] **Step 3: Delete the stall heuristic from the observer block**

In the periodic-observer block, delete the whole brace group at lines 143-149 (the `{ if (eng->playing && eng->curTime == eng->lastCt) eng->stallTicks++; … STALL-LOOP recovered near end … }` block). Also remove `e->lastCt = -1.0; e->stallTicks = 0;` from the init line 128 (leave `e->net = 2; e->ready = 0; e->dead = 0;`). The observer block should now be: dead-check, cache `curTime`, cache `curDuration`, publish globals, `mm_engine_publish_time(eng);`.

- [ ] **Step 4: Remove the observer in `mm_engine_teardown` (before the item is released)**

At the top of `mm_engine_teardown`, alongside the Task 2 `statusObserver` removal and before the item release, add:

```objc
    if (e->endObserver) {
        id t = (__bridge_transfer id)e->endObserver;
        [[NSNotificationCenter defaultCenter] removeObserver:t];
        e->endObserver = NULL; (void)t;
    }
```

- [ ] **Step 5: Build**

Run: `bash tweak/mmvideo/build.sh`
Expected: compiles; `CLEAN` / `NONE`. Grep confirms `stallTicks`, `lastCt`, and `STALL-LOOP` no longer appear in `MMTransplantEngine.m`.

- [ ] **Step 6: Commit**

```bash
git add tweak/mmvideo/MMTransplantEngine.m
git commit -m "feat(mmvideo): DidPlayToEnd loop observer; retire the near-end stall heuristic"
```

---

### Task 4: Build final dylib, canary-deploy to one screen, on-wall validation

The integration gate. All three code commits deploy together (one dylib). Validate on a single screen before any wider rollout.

**Files:**
- No source changes. Produces the deployable `tweak/mmvideo/*.dylib` and the on-wall verdict.

- [ ] **Step 1: Produce the final dylib**

Run: `bash tweak/mmvideo/build.sh` on the WSL host. Note the printed `DYLIB=…` path. Optionally copy it to a named artifact (`mmvideo_kvo.dylib`) to match the repo's dylib-per-milestone convention.

- [ ] **Step 2: Pick a canary + back up its current dylib**

Choose one OEB screen from the roster (`client.ip`). SSH via Windows OpenSSH (the `_exec_ssh` path), NOT Git Bash MinGW ssh:
`/c/Windows/System32/OpenSSH/ssh.exe -i /c/Users/jtubb.SOLUTIONS/.ssh/mosaic_ipad <legacy ssh-rsa opts> root@<ip>`
Back up the on-device dylib (`cp <installed path> <installed path>.bak`).

- [ ] **Step 3: Push the new dylib + relaunch ONLY the canary**

`cat > <installed path> < mmvideo_kvo.dylib` (binary push), then relaunch the webclip on that one screen (`sbdidlaunch`). Do not touch any other screen. Re-enable `?tdbg` telemetry if it was turned off, so `rs`/`net`/`err` are observable.

- [ ] **Step 4: Validate cold-load fixed (the failing case)**

Put the canary on a **multi-item demo playlist** (every item boundary cold-loads — the exact case the reverted DOM watchdog could not fix). Expected: each item reaches `rs≥1` and plays; **zero permanent `rs:0` stalls** across many boundary crossings. This is the primary success criterion.

- [ ] **Step 5: Validate looping clean**

Put the canary on a **single-item looping playlist**. Expected: loops with zero end-of-item stalls over many wraps (the case the DOM `_mmExplicitLoop` fix currently covers). Confirm the wrap is driven natively (looks identical with the DOM fix disabled — Task 5 will retire it).

- [ ] **Step 6: Validate no layer accumulation + no new SIGTRAP**

Across many reloads/item-boundaries on the canary, confirm `FigPluginView`'s host-layer `sublayers.count` stays flat (I7 already unparents; the KVO/notification changes must not regress it). Soak for the WebThread `EXC_BREAKPOINT`/SIGTRAP — none over the observation window. Check `/tmp/mmvideo.log` and the device crash logs.

- [ ] **Step 7: Record the verdict**

If all four criteria pass, the native change is validated — proceed to wave rollout (sequential, paced, never mass-relaunch) and unblock Task 5. If any fails, STOP: report the failure with the telemetry, do not roll out, and re-open the design. Update `memory/wall-cross-screen-sync-state.md` with the outcome.

---

### Task 5 (GATED on Task 4 pass): Retire the DOM `_mmExplicitLoop` loop workaround

A separate small JS commit, independently revertible, that removes the DOM-layer loop workaround now that native `DidPlayToEnd` owns looping on the iPad-1. Do NOT start until Task 4's loop criterion (Step 5) passes on-wall.

**Files:**
- Modify: `index.html:32` (remove the `<script>` include), `index.html:1161-1176` (remove `onVideoEnded`), `index.html:1198-1211` (collapse the `_mmExplicitLoop` branch)
- Delete: `js/eloopflag.js`

**Interfaces:**
- Consumes: nothing new. Removes `window._mmExplicitLoop`, `onVideoEnded`, `v._mmEndedBound`.
- Produces: single-item video path that sets native DOM `v.loop = (playback.loop && playback.items.length === 1)` unconditionally (the pre-workaround behavior; harmless on modern browsers, and on the iPad-1 the native engine owns the actual wrap).

- [ ] **Step 1: Confirm the on-iPad DOM/native loop coexistence in Task 4**

Before editing, confirm from Task 4 Step 5 that with the DOM element still setting `v.loop = true` on the canary, native looping is clean (no double-wrap, no stall). If Task 4 showed a conflict, set `v.loop = false` for the single-item iPad path instead of the collapse below and note it here. (Expected clean: the native AVPlayerLayer renders above the DOM and its `DidPlayToEnd` drives the wrap; the DOM `<video>` loop is cosmetic on-device.)

- [ ] **Step 2: Remove the `eloopflag.js` script include (index.html:32)**

Delete the line: `  <script src="/js/eloopflag.js"></script>`

- [ ] **Step 3: Remove `onVideoEnded` and its comment (index.html:1161-1176)**

Delete the comment block (1161-1165) and the entire `function onVideoEnded() { … }` (1166-1176).

- [ ] **Step 4: Collapse the `_mmExplicitLoop` branch (index.html:1198-1211)**

Replace the `if (window._mmExplicitLoop && playback.loop && playback.items.length === 1) { … } else { v.loop = (playback.loop && playback.items.length === 1); }` block (and its preceding FIX-path comment) with the single line:

```javascript
				v.loop = (playback.loop && playback.items.length === 1);
```

(Keep the surrounding `v.muted`/`v.volume`/`hideBlackout()` lines that follow.)

- [ ] **Step 5: Delete the flag file**

`git rm js/eloopflag.js`

- [ ] **Step 6: Verify no dangling references**

Run: `grep -rn "_mmExplicitLoop\|onVideoEnded\|eloopflag\|_mmEndedBound" index.html js/`
Expected: no matches.

- [ ] **Step 7: Node module-load smoke + manual reload**

Run: `python pytest_runner.py --js` (the module-load smoke must still pass; ES5-only client JS unchanged in spirit). Then reload the canary webclip and confirm single-item looping still wraps cleanly (now native-only).

- [ ] **Step 8: Commit**

```bash
git add index.html
git rm js/eloopflag.js
git commit -m "chore(client): retire DOM _mmExplicitLoop workaround; native DidPlayToEnd owns looping"
```

---

## Self-Review

**Spec coverage:**
- Architecture (split poll by cause) → Task 1. ✅
- Component 1 (KVO status observer, marshal + dead-check, belt-and-suspenders publish, teardown removal) → Task 2. ✅
- Component 2 (DidPlayToEnd, single loop authority, delete stall heuristic) → Task 3. ✅
- Component 3 (teardown observer removals) → Tasks 2 Step 5 + 3 Step 4. The I7 `removeFromSuperlayer` sub-item is **already in the tree** (line 107) — flagged in Global Constraints; no task, correctly. ✅
- Retirement (Component 4, separate gated JS commit) → Task 5. ✅
- Build/validate (WSL theos, canary, cold-load/loop/sublayers/SIGTRAP) → Task 4. ✅

**Placeholder scan:** No TBD/TODO; every code step carries the actual edit. Task 5's one open decision (DOM/native loop coexistence) is resolved by a concrete Task 4 observation with a stated fallback, not left vague. ✅

**Type/name consistency:** `mm_engine_publish_ready`/`mm_engine_publish_time` defined in Task 1, consumed by name in Task 2. `statusObserver`/`endObserver` fields, `MMStatusObserver` class, `AVPlayerItemDidPlayToEndTimeNotification`, `e->dead`, `e->desiredRate`, `IT(e)`/`PL(e)` all match the source read at plan time. Teardown removals reference the same field names added in load. ✅

**Note vs spec:** The spec's Component 3 overstated I7 as open; the tree already unparents the layer. The plan corrects this in Global Constraints and scopes teardown to the two new observers only. No functional gap.
