# mmvideo — Races & Leaks Audit

Read-and-reason review of the iOS-5 `mmvideo` transplant (AVPlayer under WebKit's
`MediaPlayerPrivateiPhone`). Scope: cross-thread **races** and memory/handle **leaks**.
This is analysis only — no fixes applied.

Files audited:
- `tweak/mmvideo/MMTransplantEngine.m`
- `tweak/mmvideo/Tweak.x`
- `tweak/mmvideo/MMTransplantEngine.h`, `tweak/mmvideo/mmurl.h`
- context: `tweak/mmvideo/REFINDINGS.md`

## Threading model (as established by the code + brief)

- **WebThread** (NOT the main thread on iOS-5 WebKit) runs the hooked action methods:
  `h_load`, `h_seek`, `h_play`, `h_pause`, `h_setRate`, `h_cancelLoad`, and the two
  vtable getters `h_vt_currentTime` / `h_vt_duration`.
- **Main queue** runs the periodic time-observer block (`queue:dispatch_get_main_queue()`):
  it writes `eng->curTime`/`eng->curDuration`, publishes `g_curTime`/`g_curDuration`,
  and calls `mm_engine_poll` (which writes the WebCore ivar-mirror at `+0x14/+0x18/+0x24`
  and fires the WebCore `*Changed` C callbacks).
- **QuickTime-plugin thread** may also call the vtable getters.
- `%ctor` install runs on the **main queue** (`dispatch_after … main_queue`).

The dangerous seam: **engine lifetime is managed on the WebThread** (`h_load` →
`dropEngine`/`mm_engine_teardown`/`mm_engine_free`), but the engine is **read and written
on the main queue** by the observer block, and **read on the QuickTime thread** by the
getters. None of the three teardown paths synchronizes with the main-queue observer.

---

# CRITICAL

## C1 — Observer block is a use-after-free of the freed `MMEngine` on the main queue

> **STATUS 2026-09-17 — FIXED (build `mmvideo_c1retire.dylib`), pending on-wall canary.**
>
> C1 was the live Web.app crash. Reports on sign1screen5 ran 1-3/month historically and hit
> **3 in one day** on 2026-09-17; screens .63/.64 were SSH-reachable with fresh crashes, i.e.
> crashed webclips, not dozing radios. Signature matches this section exactly:
> ```
> EXC_BAD_ACCESS (SIGSEGV)  KERN_INVALID_ADDRESS at 0xfffffffb
> Thread 0 (com.apple.main-thread)
>   0 WebCore        <- MediaPlayer::timeChanged(garbage mp)
>   1 mmvideo.dylib  <- mm_engine_poll
>   2 mmvideo.dylib  <- the periodic-time-observer block
>   3 AVFoundation
>   4 libdispatch.dylib
> ```
> **Why the shipped C1b `dead` flag never closed it:** the flag lives *inside the struct being
> freed*, so `if (eng->dead) return;` was itself a read of freed heap — and the next
> `mm_engine_create()` `calloc()`s the same size class, **zeroing** the recycled block, so the
> guard reads back 0 and opens. C1b narrowed the window; it could not close it.
>
> **Why it got worse this week, and what did NOT cause it:** the window is roughly
> (main-queue latency)/250ms per drop. The drift-measurement work added a 20Hz `_probeTick`
> sampler and a 200ms `driftTick`, which lengthened main-queue latency and widened an
> always-present window. The instrumentation did not introduce the defect (reports date to
> 2026-07); it made an existing one pay out. The `recreate` rung is NOT implicated —
> `window._mmLatchFix` is `false`, so `makeVideoBuffer.destroy()` never runs on the fleet.
> The churn that exercises the race is the ordinary double-buffer: `warmNext`/`flipTo` reload
> a `<video>` at every item boundary, and each reload is a `dropEngine`.
>
> **The fix — retire, never free** (`mmretire.h`, `mm_engine_retire`). `-removeTimeObserver:`
> cannot recall a block AVFoundation has already `dispatch_async`'d, so the struct must simply
> outlive any possible in-flight tick. `dropEngine`'s main-queue block now: tears down the AV
> objects (reclaimed immediately — that is the expensive part), NULLs `mp`/`backend`/the three
> WebCore callbacks so a late tick has nowhere to go, sets `dead` while the memory is still
> unambiguously ours, and parks the ~96-byte struct for deferred reclaim. Reclaim is gated on
> a 10s quiescence window (40x the 250ms observer interval) measured with `mach_absolute_time`
> (monotonic — a wall-clock jump forward would free early, the one unsafe direction). Doing
> the sever step on the **main queue** serializes it with the observer block instead of racing
> it from the WebThread. On list overflow the struct leaks rather than being freed.
> Bookkeeping is host-unit-tested: `tests/test_mmretire.c` (`make -C tests CC=clang test`).
>
> **Still open — the second, UNPROVEN leg.** Engines are only dropped from `h_load` and
> `h_cancelLoad`. If WebCore destroys a `MediaPlayerPrivateiPhone` by any other route
> (element removed, page navigated) the engine is *orphaned*: still ticking at 4Hz, still
> calling into a freed `MediaPlayer`, still writing the ivar mirror into freed memory. That is
> not a race but a certainty, and it would survive this fix. A log-only probe was added to
> `h_load` (warn when `[gEngines count] > 2`, the double-buffer's expected maximum) to settle
> it. If engines accumulate, the fix is an `MSHookFunction` on the backend destructor
> (`__ZN7WebCore24MediaPlayerPrivateiPhoneD*Ev`) routed to `dropEngine`. Note the probe is not
> proof of absence — a new backend can land on a recycled address and silently displace its
> orphan.

**Files:** `MMTransplantEngine.m:110-125` (block capture), `:83-91` (`mm_engine_teardown`),
`:205-209` (`mm_engine_free`); `Tweak.x:30-34` (`dropEngine`), `:69-82` (`h_load`), `:87`
(`h_cancelLoad`).

**The interleaving (WebThread vs main queue):**

The periodic observer block captures the **raw C pointer** `eng` (`MMTransplantEngine.m:110`,
"capture the C pointer (no retain cycle)"). The block is retained by the observer token
`obs`, which is retained into `e->timeObserver`. Teardown does:

```
mm_engine_teardown (WebThread):
  [PL(e) removeTimeObserver:obs];          // line 85
  release obs; release layer/item/player;  // lines 87-90
mm_engine_free (WebThread):
  free(e);                                  // line 208
```

`-removeTimeObserver:` on iOS does **not** flush or cancel a block that is already
enqueued on (or mid-execution on) the target queue. So this ordering is possible:

1. Main queue: observer block starts, or is already queued for, iteration N.
2. WebThread: a new `<video>` load (or `cancelLoad`) fires `h_load`/`h_cancelLoad` →
   `dropEngine(self)` → `mm_engine_free(e)` → `free(e)`.
3. Main queue: the block dereferences `eng->curTime = …`, `IT(eng)` (`eng->item`),
   `eng->curDuration`, and calls `mm_engine_poll(eng)` which reads `eng->item`,
   `eng->net/ready`, `eng->backend`, and **writes through `eng->backend`** at
   `+0x14/+0x18/+0x24`.

At step 3 `eng` is freed heap. `eng->backend` is now garbage → the ivar-mirror writes
(`*(volatile int*)((char*)e->backend + 0x14) = net`, `MMTransplantEngine.m:71-74`) scribble
into an arbitrary address, and `IT(eng)` sends `valueForKey:`/`status` to a dangling/garbage
`item`. Because the mirror target `backend` is the real `MediaPlayerPrivateiPhone` object
that **the WebThread is actively reading** (WebCore polls `networkState()`/`readyState()`),
a stale/garbage write there corrupts WebCore state that the WebThread then reads → a delayed
fault in CoreFoundation/WebCore **on the WebThread**.

**Why this is the prime suspect for the residual WebThread CF `EXC_BREAKPOINT` (SIGTRAP):**
- It is *timing-dependent and slow* — it only bites when a teardown (reload/cancel) races
  a still-queued observer tick, which is exactly "after minutes of correct playback" when a
  reload or seek-heavy moment lines up with a 250 ms observer tick.
- It corrupts state **read on the WebThread** (the mirror write into `backend`, and the CF
  callbacks `netChanged/readyChanged/timeChanged` fired at `MMTransplantEngine.m:77-80` with
  a possibly-stale `e->mp` MediaPlayer\*), matching the crash's thread + CF locus.
- `free()` of the struct doesn't immediately unmap the page, so the getter/mirror often
  "works" on freed-but-not-reused memory for a while — a *slow* corruption, not an instant
  crash. That is the signature described.

**Note:** even the mitigations already in place do **not** cover this. The vtable getters
were moved to read the stable globals `g_curTime/g_curDuration` (good), but the **observer
block itself still touches the freed engine** (`eng->…`, `IT(eng)`, and `mm_engine_poll`'s
`e->backend` writes + callback fires). The UAF was only closed for the getters, not for the
producer side.

**Contributing:** `removeTimeObserver:` is only called when `e->timeObserver && e->player`
are both set (`MMTransplantEngine.m:84`). Ordering within teardown is fine, but there is no
"drain the main queue / flag the engine dead" step, so an already-scheduled block runs
regardless.

---

## C2 — `gEngines` (NSMutableDictionary) mutated on WebThread with no lock; getters/poll assume "one engine" but two can transiently coexist
**Files:** `Tweak.x:19` (`gEngines`), `:26-34` (`engineFor`/`dropEngine`), `:69-82`
(`h_load`), `:83-96` (all `h_*` action hooks).

`gEngines` is an `NSMutableDictionary` (explicitly "NOT thread-safe" per the brief).
Every action hook calls `engineFor(self)` which does `[gEngines objectForKey:…]` (a read),
and `h_load`/`dropEngine`/`h_cancelLoad` mutate it (`setObject:forKey:`,
`removeObjectForKey:`).

- All of these run on the **WebThread**, so if WebKit guarantees a *single* WebThread they
  are serialized **with respect to each other**. But `%ctor`'s `mm_install` initializes
  `gEngines = [[NSMutableDictionary alloc] init]` on the **main queue** (`Tweak.x:148`,
  `:189-198`). There is no barrier guaranteeing the WebThread sees the fully-constructed
  dictionary before the first hooked call. In practice the 2 s `dispatch_after` + the hooks
  not existing until after `MSHookFunction` makes this benign, but it is unsynchronized.
- More importantly, **`engineFor` is also reachable from a non-WebThread**: `mm_slot_in`
  (`Tweak.x:59-67`) calls `engineFor(self)` and is invoked from `h_play` (`Tweak.x:85`).
  That is WebThread today, so OK — but the comment on `gCurrentEngine` (`Tweak.x:20-23`)
  exists precisely because the getters must *not* touch `gEngines` off the WebThread. The
  invariant "gEngines is WebThread-only" is load-bearing and undocumented at the call sites;
  any future getter/observer that reaches for `engineFor` off the WebThread is an immediate
  heap-corruption bug (NSMutableDictionary rehash under concurrent read).

**Interaction with C1:** `dropEngine` frees `e` and removes it from `gEngines`, but the
still-scheduled observer block holds the raw `eng`. So even the dictionary bookkeeping is
correct while the block is dangling.

**Severity rationale:** today's call graph keeps `gEngines` on one thread, so this is not
independently crashing — but it is a fragile, unenforced invariant sitting directly next to
C1, and a plausible amplifier of the same corruption.

---

# IMPORTANT

## I3 — Torn / stale reads of the 8-byte `double` globals on 32-bit ARM
**Files:** `Tweak.x:24` (`g_curTime`/`g_curDuration`), `:118-119` (getters read them),
`MMTransplantEngine.m:122` (observer writes them).

`g_curTime` / `g_curDuration` are `double` (8 bytes). On armv7 a plain `double` store/load
is **not atomic** — it is two 32-bit accesses (or a non-atomic VFP `vstr`/`vldr`). The
observer writes them on the main queue (`MMTransplantEngine.m:122`); the vtable getters read
them on the WebThread and the QuickTime thread (`Tweak.x:118-119`). A reader can observe a
**torn** value (high word of the new time + low word of the old). The consumer casts to
`float` and hands it to WebCore as `currentTime`/`duration`.

- Not memory-unsafe on its own (a torn double is a wrong number, not a bad pointer), so it
  is unlikely to be the CF SIGTRAP directly.
- But a wildly-wrong `duration`/`currentTime` fed into WebCore's HTMLMediaElement state
  machine (which does range math, fires `timeupdate`, seeks, `ended` detection) *could*
  drive WebCore into an assertion path — WebKit has `ASSERT`/`CRASH()` macros that lower to
  a trap (`EXC_BREAKPOINT`) on invalid media-time invariants. Worth ruling out as a
  secondary SIGTRAP source. The `volatile`-less plain globals also permit the compiler to
  keep them in registers across the getter, though `MSFindSymbol`-installed indirect calls
  limit that.

## I4 — `o_currentTime` / `o_duration` fn-ptrs published without a barrier vs the getters that read them
**Files:** `Tweak.x:45-46` (decls), `:130-145` (`mm_install_vtable_getters` writes them),
`:118-119` (`h_vt_*` read them as the fallback).

`mm_install_vtable_getters` runs inside `h_load` on the WebThread and does:
`o_currentTime = …; mm_vt_set(&vt[i], h_vt_currentTime)` (`Tweak.x:140-141`). The vtable slot
is made live by `mm_vt_set` (an aligned pointer write, atomic for readers — good). **But**
the moment the slot points at `h_vt_currentTime`, a reader on the QuickTime thread can call
it and hit the `gCurrentEngine ? … : o_currentTime(self)` branch (`Tweak.x:118`). If
`gCurrentEngine` is momentarily `NULL` (see I5) the getter falls through to `o_currentTime`
— and `o_currentTime` is a plain (non-atomic-published, non-`volatile`) global written on the
WebThread just before the slot flip. There is no release/acquire fence between the
`o_currentTime =` store and the `mm_vt_set` publish, so a weakly-ordered armv7 core could let
the QuickTime thread see the new vtable slot but a **stale/NULL `o_currentTime`** → call
through a NULL/garbage function pointer → crash. Rare (needs the NULL-`gCurrentEngine`
fallback branch) but real; and the fallback branch is reachable during teardown (I5).

## I5 — `gCurrentEngine` set-vs-null races the getters; NULL window drops readers into the raw-fn fallback
**Files:** `Tweak.x:23` (`gCurrentEngine`), `:32` (`dropEngine` nulls it), `:79` (`h_load`
sets it), `:118-119` (getters branch on it).

`gCurrentEngine` is a plain `MMEngine*` (aligned pointer → individual reads/writes are
atomic on armv7, so no torn pointer — good). But the **sequence** across a reload is:

```
h_load (WebThread):
  dropEngine(self)   -> if (e==gCurrentEngine) gCurrentEngine = NULL;  (line 32)
  ... mm_engine_create, gEngines setObject ...
  gCurrentEngine = eng;                                                 (line 79)
```

Between `dropEngine`'s null and line 79's re-assign, `gCurrentEngine == NULL`. A getter on
the QuickTime thread in that window takes the `o_*(self)` fallback (`Tweak.x:118-119`) — the
**original** `MediaPlayerPrivateiPhone::currentTime/duration`, which do a stret msgSend to
`this+8`. Per REFINDINGS §13 that stret path is exactly what crashed the load. Calling the
original getter mid-reload, while WebCore's controller (`this+8`) may be half-torn-down, is a
plausible fault — and it lands in CF/WebCore on whatever thread polls, including the
WebThread. This is a narrower but real variant of the CF-SIGTRAP hypothesis, and it grows
with the **number of reloads** (each `h_load` reopens the NULL window). Pairs with C1 and I4.

## I6 — `mm_install_vtable_getters` "once-only" but installs against the *first* backend's vtable; correct only if all instances share one vtable
**Files:** `Tweak.x:130-145`.

`static int done` gates the install to once. It walks `*(void***)self` — the vtable of the
**first** backend that reaches `h_load`. All `MediaPlayerPrivateiPhone` instances share the
same C++ vtable, so overwriting the shared slot covers every instance — intended. The risk
is the reverse: because the slot is **process-global shared state**, the getters
`h_vt_currentTime/h_vt_duration` are now invoked for **every** `MediaPlayerPrivateiPhone` in
the process, including ones with no engine. Those correctly fall to `o_*` when
`gCurrentEngine` is set to a *different* backend's engine — i.e., getter for backend B
returns backend A's `g_curTime`. With one wall video this is the intended "one engine"
simplification, but if WebKit ever has a second `<video>` element alive (preload, an ad, a
hidden element), backend B's `currentTime` is silently backend A's time. Not a crash, but a
correctness landmine feeding I3's "wrong time into WebCore" concern. Flagged because the
"one engine" assumption is asserted in comments, never enforced.

## I7 — `AVPlayerLayer` accumulation across reloads (`slotted` only guards the *engine*, not the host)
**Files:** `MMTransplantEngine.m:185-203` (`mm_engine_attach_layer`), `:31` (`slotted`),
`:88` (teardown releases `e->layer`).

`mm_engine_attach_layer` does `[host addSublayer:vlayer]` (`:200`) and sets `e->slotted = 1`
so it runs once **per engine**. But every `h_load` builds a **new** engine (new
`slotted=0`) and a **new** AVPlayerLayer, and `mm_slot_in`→`attach_layer` will
`addSublayer:` the new layer onto the **same host** `FigPluginView.layer`.

On teardown, `mm_engine_teardown` releases `e->layer` (`:88`, `__bridge_transfer`) — but it
**never calls `[vlayer removeFromSuperlayer]`**. The host `FigPluginView` still retains the
old layer as a sublayer. So:

- The `__bridge_transfer` release drops *our* +1, but the superlayer's retain keeps the old
  AVPlayerLayer (and its backing AVPlayer, since `AVPlayerLayer` retains its player) **alive
  and parented** after teardown.
- Each reload leaves the previous AVPlayerLayer stacked in the host. Over many reloads the
  `FigPluginView.layer.sublayers` array grows unboundedly — a **per-reload memory leak** of
  AVPlayerLayer + retained AVPlayer + AVPlayerItem + decode resources.
- Worse for the crash hunt: the **old, orphaned AVPlayerLayer still references its AVPlayer**,
  whose periodic-time-observer block may still be live (see C1) — the stacked layers keep the
  old players from fully deallocating, widening the C1 UAF window and slowly exhausting
  video-decode/CoreMedia handles on a 1st-gen iPad. This is the leading **per-reload
  accumulation** finding.

Also: `attach_layer` hides *all* existing host sublayers (`:200` `sub.hidden = YES`) each
time, but the previously-added (now-stale) mmvideo layers are themselves host sublayers, so a
later reload hides the prior mmvideo layer too — masking the leak visually while it keeps
accumulating.

## I8 — Observer block's `valueForKey:@"duration"` still executes a live AVFoundation/CF read — on the main queue, but against a possibly-torn-down item
**Files:** `MMTransplantEngine.m:117-121`.

The duration cache was moved off the WebThread getter onto the observer block (correct — the
getter now only does an atomic read). But the block still does the live
`[IT(eng) valueForKey:@"duration"]` + `getValue:` — and `IT(eng)` reads `eng->item` on the
freed engine in the C1 window. Even absent C1, if `removeTimeObserver:` has released the
observer but a final block is draining, the `item` may have been `__bridge_transfer`-released
(`:89`) between the block's start and this line — the block holds no strong ref to `item`
itself (it holds `eng`, and `eng->item` is nulled by teardown). So this line can send
`valueForKey:` to a released `AVPlayerItem`. This is the same root cause as C1 but called out
because it is a **live CF/AVFoundation call**, exactly the class of call the design tried to
keep off the danger paths.

---

# MINOR

## M9 — `mmlog` fopen/fclose is balanced but unsynchronized
**Files:** `Tweak.x:15`.

`mmlog` does `fopen("a")` + `fprintf` + `fclose` per call — handles are balanced (no FILE\*
leak). It is only called from install + `mm_install_vtable_getters` (WebThread, once), so
concurrent interleaving is unlikely; if it were ever called from two threads, `fopen`/`fclose`
per call is safe against FD leaks but could interleave partial lines. No handle leak. Minor
only.

## M10 — `mm_install` / hooks installed with no memory barrier vs first WebThread call
**Files:** `Tweak.x:147-180`, `:189-198`.

`mm_install` runs on the main queue at `%ctor`+2 s. It writes `gEngines`, `gCreateCF`, and
all `o_*` originals, then `MSHookFunction` publishes the hooks. `MSHookFunction`'s
code-patch acts as a de-facto barrier (I-cache flush) so the WebThread won't call a hook
before the `o_*` it needs is stored (they're stored *by* `MSHookFunction`). Low risk, noted
for completeness.

## M11 — `desiredRate` / `playing` / `net` / `ready` written on both threads without atomicity
**Files:** `MMTransplantEngine.m:129-154` (`play`/`pause`/`set_rate` on WebThread write
`playing`/`desiredRate`), `:56-81` (`mm_engine_poll` on main queue reads `playing`/
`desiredRate` and writes `net`/`ready`).

`e->playing`, `e->desiredRate`, `e->net`, `e->ready` are plain `int`/`float` (4-byte, aligned
→ individual accesses atomic on armv7, so no torn value). But `mm_engine_poll` (main queue)
**reads** `playing`/`desiredRate` to compute the mirrored `m_rate`, while `h_play`/`h_setRate`
(WebThread) **write** them. No lost-update memory-safety issue (single word), but the
mirrored rate can lag/lead the true state by one tick. Cosmetic; not a crash source. Included
because it is shared cross-thread state per the brief's checklist.

## M12 — `mm_engine_poll` fires WebCore `*Changed` callbacks with `e->mp` that is never revalidated
**Files:** `MMTransplantEngine.m:77-80`, `:43-52` (`mp` captured at create).

`e->mp` (the `MediaPlayer*`) is read from `*(void**)((char*)self+4)` at create and never
re-checked. The callbacks `netChanged/readyChanged/timeChanged` are fired on the main queue
with `e->mp`. If the backend/`MediaPlayer` was torn down (C1) `e->mp` is stale → calling a
WebCore method on a freed `MediaPlayer` → CF/WebCore fault. This is a sub-path of C1 (same
free-vs-use window) but specifically the **callback** arm rather than the ivar-mirror arm,
and equally capable of producing a WebThread-adjacent CF crash since the callbacks mutate
WebCore's media state.

---

# Priority for the residual WebThread CF `EXC_BREAKPOINT` (SIGTRAP)

Investigate in this order:

1. **C1 — observer-block UAF of the freed `MMEngine`** (`MMTransplantEngine.m:110-125` +
   teardown at `:83-91`/`:205-209`). Best fit for a *slow*, reload-triggered corruption of
   state the WebThread reads (the ivar-mirror write into `backend` at `:71-74`, and the
   WebCore callback fires at `:77-80`/M12). The getters were made UAF-safe; the **producer
   block was not**. Instrument: on `mm_engine_free`, poison `e` (e.g. memset to `0xDE`) and
   confirm the observer block or `mm_engine_poll` faults — or gate the block on a
   published-dead flag / drain the main queue before free.

2. **I7 — AVPlayerLayer/AVPlayer accumulation across reloads** (`MMTransplantEngine.m:185-203`,
   missing `removeFromSuperlayer`). Directly explains "accumulate over many `h_load`
   reloads": stacked layers keep old AVPlayers (and their observer blocks) alive, both
   widening the C1 window and leaking decode/CoreMedia handles until something traps. Check
   `FigPluginView.layer.sublayers.count` growth across reloads on-device.

3. **I5 + I4 — `gCurrentEngine` NULL window dropping getters into the original stret
   `o_currentTime/o_duration`** (`Tweak.x:32/79/118-119`). A narrower CF-fault path that also
   scales with reload count and lands in WebCore's getter on whatever thread polls.

4. **I3 — torn 64-bit `double` globals feeding WebCore** (`Tweak.x:24/118-119`,
   `MMTransplantEngine.m:122`). Less likely to be the direct trap, but a bad media-time can
   trip a WebCore `ASSERT`/`CRASH()` which lowers to `EXC_BREAKPOINT` — worth excluding once
   the UAF paths are closed.

The unifying theme: **engine lifetime is owned on the WebThread but the engine is consumed
on the main queue (observer) and QuickTime thread (getters) with no synchronization and no
"dead" flag**, and **teardown never unparents the on-screen AVPlayerLayer**. C1 + I7 together
account for both the slow WebThread CF crash and the per-reload accumulation the audit set
out to find.
