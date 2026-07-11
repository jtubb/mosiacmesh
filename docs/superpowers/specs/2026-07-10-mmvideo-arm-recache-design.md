# mmvideo arm-phase recache — self-heal a missing segment at ARM — design

**Date:** 2026-07-10
**Status:** Approved (design), pending plan
**Files:** `js/mmVideoRecovery.js` (extend `mmArmRetryAction`), `index.html` (shared `_mmSelfPull`, extend the arm listener), `tests/unit/js/mmvideo-recovery.test.js` (extend). Client-only; deploy = staged reload.

## Problem

The merged arm-phase recovery (`feature/mmvideo-watchdog`, `36e9cfa`) retries `load()`+`play()`
on an arm-time cached-video `error`. That recovers a *transient* mmvideo cold-start flake, but
it CANNOT fix a **genuinely missing segment**: retrying `load()` on a 404 stays a 404.

On-wall (2026-07-10) the whole 24-screen wall was black because every device was **missing
`seg_0`** locally (evicted by the historical [[mmcache-supersede-evicts-siblings]] bug, never
re-pulled, while the server's `cachedSegments` record wrongly said "cached"). At PLAY the
client requested the localhost URL for `seg_0` -> **404** -> `verr` -> couldn't arm the first
clip -> `NEEDS_ARM` -> black. The only fix was a **manual SSH push** of `seg_0` to all 23
devices (`cat >` over ssh; scp is absent on iOS-5). After the push the arm succeeded and the
wall played 23/23.

The manual push is not durable. A missing segment at arm should **self-heal**.

## Goal

Extend the arm-phase recovery from retry-only to a **retry -> recache -> skip** ladder
(mirroring the poll watchdog): when an arm-time cached-video error persists past the retry
budget, **re-pull the missing segment** (client self-pull), so the next `load()` finds it and
arms — no manual push. Never falls to central.

## Non-goals

- **No fix to the stale `cachedSegments` server record** (a separate follow-up). This design
  makes the CLIENT self-heal regardless of the server's belief.
- **No central fallback.** If recache doesn't land in time, the device stays on the existing
  `NEEDS_ARM`/tap path (black until armed) — never central.
- **No poll-based reload for the arm recache.** The arm listener is event-driven; its existing
  `load()`+`play()` retry loop re-attempts and arms once the pull lands (unlike the watchdog,
  which polls `mmCache.state` because it isn't in a retry loop). If the pull is slow, the
  device falls to `NEEDS_ARM` — no worse than today.
- ES5 only (`js/mmVideoRecovery.js` + `index.html` run on iPad-1 / iOS 5.1): no `let`/`const`,
  arrow functions, template literals, `class`, `Promise`, `fetch`.

## Design

### Extend the pure helper

```
mmArmRetryAction({ active, isLocal, retries, recaches, maxRetries, maxRecaches })
  -> 'skip' | 'retry' | 'recache'
```

- `!state || active || !isLocal` -> `'skip'`  (active playback: the poll watchdog owns it; non-cached: not our concern)
- `retries < maxRetries`   -> `'retry'`
- `recaches < maxRecaches`  -> `'recache'`
- otherwise -> `'skip'`

Pure, deterministic, reads only `state`. (Extends the shipped 4-arg version by adding
`recaches`/`maxRecaches` + the `'recache'` verdict; the order is retry-budget first, then
recache-budget — same ladder as `mmWatchdogAction`.)

### Shared self-pull helper (`index.html`)

Extract the recache self-pull (currently inline in the watchdog's `_wdRecache`) so the
watchdog and the arm listener share ONE implementation:

```
_mmSelfPull(v, src) -> token | null
  if (!window.mmCache || src.indexOf('127.0.0.1') === -1) { return null; }
  var m = src.match(/seg_[a-f0-9]+_\d+/);
  if (!m) { return null; }
  var token = m[0];
  var central = 'http://' + window.location.host + '/media/' + udid + '/videos/' + token + '.mp4';
  mmCache.handlePrecache({ group: (window._mmDisplayID || 'self'), url: central, token: token });
  return token;
```

- The watchdog's `_wdRecache` becomes: `var t = _mmSelfPull(v, src); if (t) { _wdRecachePending = t; }`
  (keeps its poll-reload path unchanged).
- The arm listener calls `_mmSelfPull(v, src)` and relies on its retry loop to reload.

### Extend the arm `error` listener (`index.html`)

Per-element arm state gains `_armRecacheN` (alongside `_armRetryN` / `_armSrc` /
`_armRetryTimer`); all reset together on a new src and on `playing`. On the `error` event:

1. Compute `isLocal` from the src; on a new src (`s !== v._armSrc`) reset `_armRetryN=0`,
   `_armRecacheN=0`, `v._armSrc=s`.
2. `action = mmArmRetryAction({ active: playback.active, isLocal: isLocal,
   retries: v._armRetryN || 0, recaches: v._armRecacheN || 0,
   maxRetries: ARM_MAX_RETRIES, maxRecaches: ARM_MAX_RECACHES })`.
3. `'skip'` -> return (NEEDS_ARM/tap takes over).
4. `'retry'` -> `v._armRetryN++`; `dbg('arm-retry')`; clear+arm
   `setTimeout(load()+play(), ARM_RETRY_BACKOFF_MS)`.
5. `'recache'` -> `v._armRecacheN++`; `dbg('arm-recache')`; `_mmSelfPull(v, s)`; clear+arm
   `setTimeout(load()+play(), ARM_RECACHE_RELOAD_MS)` (longer settle so the pull lands before
   the re-attempt).

The `playing` listener additionally zeroes `_armRecacheN` (already zeroes `_armRetryN`).

### Parameters (tunable consts in `index.html`)

- `ARM_MAX_RETRIES = 3` (existing)
- `ARM_MAX_RECACHES = 1` (new)
- `ARM_RETRY_BACKOFF_MS = 500` (existing)
- `ARM_RECACHE_RELOAD_MS = 2500` (new)

## Data flow

arm `<video> error` -> compute isLocal + reset-on-new-src -> `mmArmRetryAction` -> `retry`
(load+play, 500ms) | `recache` (`_mmSelfPull` + load+play after 2500ms) | `skip` (NEEDS_ARM).
The single retry timer is cleared before each re-arm, so at most one is live per element.

## Error handling / edge cases

- **Single timer:** the existing `_armRetryTimer` is cleared before each new `setTimeout`, so
  retry and recache re-arms never stack.
- **Recache pull fails / slow:** if the pull doesn't land within `ARM_RECACHE_RELOAD_MS`, the
  re-attempt errors again; `mmArmRetryAction` returns `'skip'` (recaches exhausted) -> the
  device stays `NEEDS_ARM` (existing behavior, never central).
- **Non-`127.0.0.1` src** (central/none device) -> `mmArmRetryAction` returns `'skip'`; and
  `_mmSelfPull` returns null (no local URL to rebuild). No recache; no central.
- **New clip during arm:** `_armRetryN`/`_armRecacheN`/`_armSrc` reset on src change, so a new
  clip gets a fresh budget.
- **`_mmSelfPull` shared with the watchdog:** returns the token; the watchdog sets its
  `_wdRecachePending` from it (poll-reload); the arm listener ignores the return.

## Testing

- **Node `--test`** (`tests/unit/js/mmvideo-recovery.test.js`), extended `mmArmRetryAction`:
  - `active:true` -> `'skip'`
  - `isLocal:false` -> `'skip'`
  - not-active, local, `retries=0<3` -> `'retry'`; `retries=2` (max-1) -> `'retry'`
  - not-active, local, `retries=3` (==max), `recaches=0<1` -> `'recache'`
  - not-active, local, `retries=3`, `recaches=1` (==max) -> `'skip'`
  - `null` -> `'skip'`
  Run via `python pytest_runner.py --js`.
- **On-wall sign-off** (the decisive test, against the exact hand-fixed scenario): delete
  `seg_0` from one device's `MosaicMeshCache` (or use a device known to lack it), PLAY, and
  confirm `arm-recache` fires and the device **self-heals** (re-pulls `seg_0` -> arms ->
  plays) with NO manual push. Deploy via STAGED single/small-batch reloads (never a whole-group
  reload). Measure with the `rs>=2` AND `ct` advancing AND `verr` clear metric (NOT `elapsed`).

## Deploy

Staged reload (single/small-batch, never whole-group). Client JS only; no tweak rebuild, no
server restart. The `MMFORCE_TDBG_TEMP` force is re-applied uncommitted for the sign-off and
reverted after.
