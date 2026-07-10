# mmvideo playback watchdog: never-downgrade + recache + silent-stall recovery — design

**Date:** 2026-07-10
**Status:** Approved (design), pending plan
**Files:** `js/mmVideoRecovery.js` (extend: add `mmWatchdogAction`), `index.html` (poll watchdog wiring; remove the error-driven retry/downgrade), `tests/unit/js/mmvideo-recovery.test.js` (extend). Client-only; deploy = STAGED reload.

## Problem

On a cached-video group PLAY, ~half the wall (12 of 24) is black. Physical count
(ground truth) — not the earlier `elapsed`-based metric, which measured playback-clock
position and advances even on a black screen. The correct signal is whether the
`<video>` is actually decoding: `verr` clear AND `rs>=2` AND `ct` (currentTime)
advancing.

Measured true state + the decisive correlation:

- **12 PLAYING** — ALL `cacheMode:lighttpd-localhost` (cached), `rs=4`, `ct` advancing.
- **12 BLACK** — 10 `cacheMode:none` (central) + 2 cached; the black ones sit at
  `rs=0, ct=0, verr=None` (silent stall, **no error event**).

Root causes (established across the investigation — see memory
`wall-verr3-is-mmvideo-not-cache`):

1. **Central streaming is a black hole at fleet scale.** Every `none` device is stuck
   at `rs=0` — ~10 iPad-1s pulling video from the central server at once saturate the
   WiFi/serve path (`full-video-wifi-bound`), so none load. The **cached path works**
   (12/12 cached play).
2. **The downgrade-to-`none` is counterproductive.** The shipped `error` listener sends
   `ANNOUNCE_CACHE_MODE:none` on a cached-video error, moving a device from
   "cached, occasionally flaky (usually plays)" to "central, guaranteed black." Worse:
   `cacheMode:none` routes the device to the **central** URL even though its **local
   segment files are still present** (`cachedSegments` intact, files on disk) — it
   abandons a working local copy for a broken stream.
3. **No recache on downgrade.** `cacheMode:none` is *defined* as "not cache-capable";
   all PRECACHE/reconcile is gated on `_client_is_push_eligible` (render.py:634), which
   excludes `none`. So a downgraded device is removed from the caching system — nothing
   re-pulls to it — and only a page-reload REGISTER (`apply_cache_capability`) can
   re-upgrade it.
4. **The silent stall has no error event.** The dominant black mode is `rs=0` with
   `verr=None` — the shipped retry-on-`error` fix never fires for it.

## Goal

Keep every capable device on its (working) local cache and recover playback failures
**in place**, never falling to central. Detect both failure modes (silent `rs=0` stall
and `verr=3`) with one poll-based watchdog, and escalate retry → recache → black.

## Non-goals

- **No central fallback / no downgrade-to-`none`.** A device that can't recover stays
  BLACK (visible breakage — the operator's desired signal), never central.
- **No server change.** Client-only (`index.html` + `js/mmVideoRecovery.js`), deployed
  by reload. (A server-side rejection of `none` downgrades is a possible future
  defense-in-depth; out of scope here.)
- **No manual re-probe of eroded devices.** They auto-recover: on the deploy reload the
  device RE-REGISTERs and `apply_cache_capability` re-upgrades `none`->`lighttpd-localhost`.
- ES5 only (`js/mmVideoRecovery.js` + `index.html` run on iPad-1 / iOS 5.1): no
  `let`/`const`, arrow functions, template literals, `class`, `Promise`, `fetch`.

## Design

### Architecture

A single **poll-based watchdog** in `index.html` (a `setInterval`) is the detector. It
**replaces** the shipped `error`-event-driven retry+downgrade (the `error` listener is
removed; the watchdog polls `verr` too, so it covers errors AND the silent stall). Pure
decision logic extends `js/mmVideoRecovery.js`; the stateful wiring (ct tracking, grace
timing, counters, `load()`/self-pull) stays in `index.html` — the `GoTime._nextSyncDelay`
split.

### Watchdog tick (every `WATCHDOG_INTERVAL_MS`)

Against the active video element `v`:

1. **Should-play gate:** the group is playing, the current item is a video, and the clip
   has been current for more than `WATCHDOG_START_GRACE_MS` (a freshly-loaded clip needs
   time; don't judge it instantly). If not should-play -> reset stall state (counters,
   level, `_mmRecachePending`) and return.
2. **Recache-in-progress check:** if `_mmRecachePending` is set:
   - `mmCache.state(pendingToken) === 'cached'` -> clear pending; `v.load()`+`v.play()`
     (reload with the fresh file); reset the grace clock so the reloaded clip gets a
     fair window. Return.
   - `mmCache.state(pendingToken) === 'failed'` -> clear pending (fall through; the
     ladder will reach `dead` since recaches are exhausted).
   - otherwise (still pulling) -> return (wait).
3. **Decoding?** `decoding = (verr in {null,0}) AND rs>=2 AND (ct - lastCt) > WATCHDOG_CT_MIN_ADVANCE_MS`.
   Store `lastCt = ct`.
4. **Healthy** (`decoding`) -> reset counters + level -> return.
5. **Stalled** -> `action = mmWatchdogAction({shouldPlay:true, decoding:false, retries, recaches, maxRetries, maxRecaches})`:
   - `'retry'` -> `retries++`; `dbg('wd-retry')`; `v.load()`; `v.play()` (catch-guarded).
   - `'recache'` -> `recaches++`; `dbg('wd-recache')`; fire the self-pull (below); set
     `_mmRecachePending = token`.
   - `'dead'` -> `dbg('wd-dead')` once; stop escalating (stay black). State resets on the
     next clip (new src) so a looping playlist re-attempts each loop.

### Pure decision helper (extends `js/mmVideoRecovery.js`)

```
mmWatchdogAction({ shouldPlay, decoding, retries, recaches, maxRetries, maxRecaches })
  -> 'ok' | 'retry' | 'recache' | 'dead'
```

- `!shouldPlay || decoding` -> `'ok'`
- stalled and `retries < maxRetries`   -> `'retry'`
- stalled and `recaches < maxRecaches` -> `'recache'`
- otherwise -> `'dead'`

Pure, deterministic, no DOM/timers/globals. (The existing `mmVideoErrorAction` may be
removed with the error listener, or left dead; the plan decides.)

### Recache self-pull (client-only)

From the stalled element's src `http://127.0.0.1:8080/seg_<key>.mp4`:
- `token = 'seg_<key>'` (basename minus `.mp4`).
- `centralUrl = 'http://' + window.location.host + '/media/' + udid + '/videos/seg_<key>.mp4'`
  (`udid` already exists client-side; it builds the aruco URL at index.html:1578).
- `mmCache.handlePrecache({ group: (window._mmDisplayID || 'self'), url: centralUrl, token: token })`
  — the mmvideo backend re-fetches central and overwrites the local file; the reload
  happens in the watchdog's recache-in-progress check (step 2) once `mmCache.state` is
  `cached`.
Guard: only attempt when the src is a `127.0.0.1` cached URL and `mmCache` + `udid` exist;
otherwise skip recache (ladder goes straight to `dead`).

### Never-downgrade + eroded-device recovery

- Delete the `error`-listener body that sends `ANNOUNCE_CACHE_MODE:none` (+ its
  `cache-local-fail` dbg). Remove the whole `error` listener (subsumed by the watchdog).
- No client code path sends `mode:"none"` on a playback failure anymore.
- The 12 devices currently on `none` auto-recover on the deploy reload: RE-REGISTER ->
  `apply_cache_capability` upgrades `none`->`lighttpd-localhost` (they report cacheCapable),
  re-entering the cache path and using their existing local files.

### Parameters (tunable consts in `index.html`)

- `WATCHDOG_INTERVAL_MS = 2000`
- `WATCHDOG_START_GRACE_MS = 3000`
- `WATCHDOG_MAX_RETRIES = 2`
- `WATCHDOG_MAX_RECACHES = 1`
- `WATCHDOG_CT_MIN_ADVANCE_MS = 100`

## Data flow

`setInterval tick` -> should-play gate -> recache-pending check -> decoding test ->
`mmWatchdogAction` -> `retry` (`load()`+`play()`) | `recache` (self-pull -> pending ->
later reload) | `dead` (stay black) | `ok` (reset). A new clip (src change) resets all
per-element watchdog state so loops re-attempt.

## Error handling / edge cases

- **Start grace** prevents false stalls during normal clip load/seek.
- **New clip resets state** (`retries`/`recaches`/level/`lastCt`/`_mmRecachePending`) —
  keyed on the element's current src, like the shipped `_mmRetrySrc` reset.
- **Recache herd:** recache fires only after retries are exhausted (rare) and once per
  clip per element (`maxRecaches=1`); it pulls a single segment, not a stream. Bounded.
- **`mmCache`/`udid` absent** (non-cache client) -> skip recache; the ladder reaches
  `dead` (stays black rather than falling to central).
- **Non-`127.0.0.1` src** (a central/none device before it re-upgrades) -> the watchdog
  still retries `load()`+`play()` (harmless kick) but recache is skipped (no local URL to
  rebuild); such a device recovers when it re-registers to `lighttpd-localhost` on the
  reload.
- **Watchdog runs once** — guard against double `setInterval` on reconnect.

## Testing

- **Node `--test`** (`tests/unit/js/mmvideo-recovery.test.js`), pure `mmWatchdogAction`:
  - `shouldPlay:false` -> `'ok'`
  - `decoding:true` -> `'ok'`
  - stalled, `retries=0<2` -> `'retry'`; `retries=1` (max-1) -> `'retry'`
  - stalled, `retries=2` (==max), `recaches=0<1` -> `'recache'`
  - stalled, `retries=2`, `recaches=1` (==max) -> `'dead'`
  Run via `python pytest_runner.py --js`.
- **On-wall sign-off** (manual; the fleet is the test) using the CORRECT metric
  (`rs>=2` AND `ct` advancing AND `verr` clear — NOT `elapsed`), deployed via STAGED
  single/small-batch reloads (never a group reload — it drops the fleet ~7 min).
  Verify: (1) black count -> ~0; (2) the `none` devices re-upgrade to
  `lighttpd-localhost` on reload; (3) `wd-retry`/`wd-recache` emits appear and stalled
  clips recover; (4) NO device on `cacheMode:none` after a heavy PLAY (erosion stopped).

## Deploy

STAGED reload (single/small-batch, never whole-group). Client JS only; no tweak rebuild,
no server restart. The `MMFORCE_TDBG_TEMP` force is re-applied uncommitted for the
sign-off (as before) and reverted after.
