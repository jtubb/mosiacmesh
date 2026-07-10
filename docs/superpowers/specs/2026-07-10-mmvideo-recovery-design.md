# mmvideo verr=3 recovery + false-downgrade fix — design

**Date:** 2026-07-10
**Status:** Approved (design), pending plan
**Files:** `js/mmVideoRecovery.js` (new, pure ES5 module), `index.html` (error-listener wiring), `tests/unit/js/mmvideo-recovery.test.js` (new). Client-only; deploy = fleet reload.

## Problem

An on-wall PLAY of a cached video playlist produced `verr=3` (`MEDIA_ERR_DECODE`) on
roughly half the iPad-1 screens ("half the webclips crashed"). A systematic
investigation exonerated the cache end-to-end and localized two client-side defects:

- **Root cause (established):** the cache files are present + byte-exact, the on-device
  lighttpd serves them `200` with ranges, the client uses the correct
  `http://127.0.0.1:8080/seg_<key>.mp4` URL (`fsrc == csrc`), the segments are
  faststart, and there is no central fallback traffic. Yet the mmvideo native player
  intermittently fails to start on a *healthy* cached segment (`rs=0, verr=3`),
  **variably** across runs. It is a transient mmvideo cold-start flake, not a cache,
  URL, encoding, or network problem. (See memory `wall-verr3-is-mmvideo-not-cache`.)

- **Defect #1 — false cache-downgrade:** the existing `<video>` `error` listener in
  `index.html` (on the pooled elements) sends `ANNOUNCE_CACHE_MODE {mode:"none"}` on the
  *first* error whose src is `127.0.0.1` — treating the `<video>` error as an
  authoritative cache-capability signal. Because mmvideo emits spurious `verr=3` on
  proven-good cached files, a single transient flake **permanently downgrades a healthy
  device off caching** (42 such downgrades were observed in one test PLAY). With the
  cache reconcile OFF, the downgraded device does not re-upgrade until the next SSH
  re-probe on REGISTER. The cache population erodes run-over-run.

- **Defect #2 — no video-start recovery:** a `verr=3` at start goes to black with no
  retry, so a recoverable transient flake becomes a permanently blank panel for the clip.

## Goal

On a cached-video `verr=3`, **retry the local load first** (recover the flake), and
**only downgrade after repeated local failure** (stop the false downgrade). Fix both
defects with one retry-then-downgrade flow.

## Non-goals

- **No central fallback for the failed clip.** After retries are exhausted the device
  downgrades and the current clip stays black until the next central-served cycle
  (operator choice — avoids a client-side URL rewrite).
- **No re-upgrade path.** Preventing the false downgrade is the fix; genuinely-lost
  devices still recover via the existing SSH re-probe on REGISTER. (YAGNI.)
- **No polling watchdog.** The `<video>` `error` event *does* fire on mmvideo (confirmed:
  42 `cache-local-fail` emits in one run), so recovery is event-driven. A timer-based
  "stuck with no error" watchdog is out of scope.
- **No server change.** The server already handles `ANNOUNCE_CACHE_MODE`; this is
  client-only, deployed by fleet reload.
- ES5 only (`js/mmVideoRecovery.js` + the `index.html` inline script run on iPad-1 /
  iOS 5.1): no `let`/`const`, arrow functions, template literals, `class`, `Promise`,
  `fetch`. jQuery 1.x / SockJS stay.

## Design

### Architecture

Split pure decision logic from side-effectful wiring (the `GoTime._nextSyncDelay`
pattern):

- **`js/mmVideoRecovery.js`** — a small pure ES5 module, included via `<script>` in
  `index.html` alongside `js/mmCache.js`. Exports one pure function; no DOM, no timers,
  no sockets; node-`require`-able for unit tests.
- **`index.html`** — the pooled-element `error` listener (currently ~line 210, inside
  `mkVideo` in `getPersistentVideo`) holds the stateful wiring: per-element retry
  counters, the backoff `setTimeout`, the local re-load (`v.load()` + `v.play()`), the
  `sock.send(ANNOUNCE_CACHE_MODE)` downgrade, and the `dbg` diagnostics. It calls the
  pure helper to decide what to do.

### Pure decision helper

```
mmVideoErrorAction({ isLocal, retries, maxRetries }) -> 'ignore' | 'retry' | 'downgrade'
```

- `isLocal` false  -> `'ignore'`  (non-`127.0.0.1` errors are not this fix's concern)
- `isLocal` true and `retries < maxRetries` -> `'retry'`
- `isLocal` true and `retries >= maxRetries` -> `'downgrade'`

Pure, deterministic, no side effects. `isLocal` is computed by the caller from the
element's `currentSrc`/`src` (`indexOf('127.0.0.1') !== -1`).

### Retry-then-downgrade flow (in the `error` listener)

Per pooled element, track `v._mmRetryN` (count) and `v._mmRetrySrc` (the src it counts
for). On the `error` event:

1. Compute `isLocal` from `v.currentSrc || v.src`.
2. If the current src differs from `v._mmRetrySrc`, reset `v._mmRetryN = 0` and set
   `v._mmRetrySrc` to it (a new clip starts a fresh retry budget).
3. `action = mmVideoErrorAction({ isLocal, retries: v._mmRetryN, maxRetries: MM_VIDEO_MAX_RETRIES })`.
4. `'ignore'` -> return (existing non-cache behavior; nothing sent).
5. `'retry'` -> `v._mmRetryN++`; `dbg('cache-local-retry')`; arm a single
   `setTimeout(reload, MM_VIDEO_RETRY_BACKOFF_MS)` where `reload` re-issues
   `try{v.load();}catch(e){}` then `try{ var p=v.play(); if(p&&p['catch']) p['catch'](function(){}); }catch(e){}`
   (iOS-5 needs load()+play() together to restart a held element). Store the timer id on
   the element so it can be cancelled.
6. `'downgrade'` -> `dbg('cache-local-fail')`; send
   `generateMessage("SRV","ANNOUNCE_CACHE_MODE",{mode:"none"})` **once** (guard with
   `v._mmDowngraded` so a burst of errors doesn't spam it); stop retrying.

Reset hooks:
- On the element's `playing` event (already wired at ~line 209): also `v._mmRetryN = 0`
  and clear any pending retry timer + `v._mmDowngraded` — a recovered clip forgets its
  failures.
- When a new src is set (step 2 detects `src !== v._mmRetrySrc`): reset the counter and
  cancel any pending retry timer so a stale retry can't fire against the new clip.

### Parameters (tunable consts in `index.html`)

- `MM_VIDEO_MAX_RETRIES = 3`
- `MM_VIDEO_RETRY_BACKOFF_MS = 500`

### Data flow

`<video> error` → compute `isLocal` + reset-on-new-src → `mmVideoErrorAction(...)` →
`retry` (backoff → `load()`+`play()`, loops until success or budget) or `downgrade`
(one `ANNOUNCE_CACHE_MODE:none`) or `ignore`. A successful `playing` resets the budget.

## Error handling / edge cases

- **Src changes mid-retry (new clip):** step 2 resets the counter; the pending retry
  timer is cancelled so it can't re-load the wrong clip.
- **Downgrade idempotency:** `v._mmDowngraded` guard sends `ANNOUNCE_CACHE_MODE:none`
  at most once per element until a `playing` clears it; after downgrade `cacheMode` is
  `none`, so the next src is central (non-`127.0.0.1`) → future errors `ignore` →
  the retry path quiesces naturally.
- **Retry re-issues against the current element/src only** (the listener closes over its
  own `v`; the pool has 2 elements, each with its own counter).
- **Non-local errors** (central stream, other) → `ignore`, unchanged behavior.
- **`sock` unavailable** when downgrading → guard with `typeof sock !== 'undefined' && sock !== null`
  (as the current code does); skip the send, still stop retrying.

## Testing

- **Node `--test`** (`tests/unit/js/mmvideo-recovery.test.js`) over the pure helper:
  - non-local → `'ignore'` (regardless of retries)
  - local, `retries = 0 < 3` → `'retry'`
  - local, `retries = 2 == max-1` → `'retry'`
  - local, `retries = 3 == max` → `'downgrade'`
  - local, `retries > max` → `'downgrade'`
  Run via `python pytest_runner.py --js` (or `node --test tests/unit/js/mmvideo-recovery.test.js`).
- **On-wall sign-off** (manual; the fleet is the test — cannot be unit-tested):
  RELOAD the group, PLAY a cached video playlist, and confirm via `?tdbg`:
  (1) `cache-local-retry` emits appear and clips **recover** (play) instead of going
  black; (2) a healthy device that flakes once is **not** downgraded (no
  `cache-local-fail` / no `ANNOUNCE_CACHE_MODE:none` unless a segment is genuinely
  unusable across all 3 retries); (3) materially fewer black panels than the
  pre-fix baseline.

## Deploy

Fleet **reload** (JS is server-served; no tweak rebuild, no server restart). The debug
instrumentation currently uncommitted in `index.html` (tdbg-force + `fsrc/csrc/warm/lmd/
spath/epath/ecode`) is separate; the implementation will keep only what the fix needs.
