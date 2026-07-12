# arm-recache poll-until-cached re-arm — design

**Date:** 2026-07-12
**Status:** Approved (design), pending plan
**Files:** `js/mmVideoRecovery.js` (new pure helper `mmRecachePollAction`), `index.html` (arm `error` listener: replace the fixed recache reload with a poll loop; constants), `tests/unit/js/mmvideo-recovery.test.js` (extend). Client-only; deploy = staged reload.

## Problem

The merged arm-phase recache ([[2026-07-10-mmvideo-arm-recache-design]]) re-pulls a genuinely-missing
segment at ARM, then schedules **one** fixed-delay reload (`ARM_RECACHE_RELOAD_MS = 2500`) to
re-attempt the arm. On-wall (2026-07-11 arm-recache sign-off) this **failed to self-heal**:

- The self-pull itself **worked** — the seg re-landed on the device's disk (full bytes) and the
  server's `cachedSegments` re-added it (the [[2026-07-11-cache-record-honest]] `CACHED` path).
- But the single reload fired at 2500 ms, **before** the multi-second WiFi download completed, so it
  still hit a localhost 404. The `ARM_MAX_RECACHES = 1` budget was then spent, `mmArmRetryAction`
  returned `'skip'`, and the arm listener gave up. The seg arrived seconds *later*, but nothing
  re-attempts the arm (the poll watchdog is gated on `playback.active`, false during ARM). Device
  stuck `verr=3, rs=0, active=False` — black.

The spec's explicit non-goal — *"No poll-based reload for the arm recache … rely on the existing
retry loop"* — is the exact assumption that broke: a fixed single-shot timer cannot cover a
variable, multi-second download. And segments are not all small: per-screen warps are usually a few
MB, but observed segs range up to **119 MB** (`seg_3d179251f9e1_4.mp4`), which no fixed timeout could
ever accommodate.

## Goal

Make the arm-phase recache **poll `mmCache.state` until the pull terminates** — re-arm the exact
element on `'cached'`, give up (to the existing NEEDS_ARM / tap fallback) on `'failed'`, keep waiting
while `'pending'` — with **no wall-clock bound**. This is size-agnostic (the backend drives the
download to completion for any file size) and mirrors the poll-until-cached logic the poll watchdog
already uses successfully on this wall (`index.html` `_watchdogTick`, the `_wdRecachePending` branch).

## Non-goals

- **No fixed timeout / size-scaled bound.** `mmCache.state` distinguishes `'pending'` (download in
  progress) from `'cached'`/`'failed'`; the backend's `fetchToCache` resolves to one of the terminal
  states for any size. A hung `'pending'` that never resolves is harmless: the server's
  `PREPARE_TIMEOUT_MS = 45s` GOes the group without that device, and if the seg ever caches the device
  re-arms and joins late. (Chosen explicitly over a generous safety backstop — pure poll-until-terminal,
  matching the watchdog.)
- **No change to the poll watchdog.** It already polls-until-terminal correctly (active playback). This
  design touches only the pre-playback ARM path. (No refactor to share code with it — the watchdog is a
  proven hot path; unifying is scope creep with no functional payoff.)
- **No central fallback.** As today: if the seg never lands, the device stays on NEEDS_ARM/tap — never
  central (central streaming is a fleet-scale black hole, [[wall-desync-is-video-seek]]).
- **No change to `mmArmRetryAction`.** Its `retry → recache → skip` ladder is unchanged; it still
  returns `'recache'`. Only the *wiring* of the `'recache'` branch changes.
- ES5 only (`js/mmVideoRecovery.js` + `index.html` run on iPad-1 / iOS 5.1): no `let`/`const`, arrow
  functions, template literals, `class`, `Promise`, `fetch`. [[legacy-ipad-compat]]

## Design

### New pure helper (`js/mmVideoRecovery.js`)

```js
// cacheState = 'none' | 'pending' | 'cached' | 'failed'  (from mmCache.state)
// Poll decision for a pending arm-phase recache self-pull:
//   'cached' -> 'rearm'  (seg landed; load()+play() the arming element)
//   'failed' -> 'giveup' (pull errored; fall to NEEDS_ARM/tap — NEVER central)
//   else     -> 'wait'   ('pending' download in progress, or 'none'; keep polling)
mmVideoRecovery.mmRecachePollAction = function (cacheState) {
  if (cacheState === 'cached') { return 'rearm'; }
  if (cacheState === 'failed') { return 'giveup'; }
  return 'wait';
};
```

Pure, deterministic, reads only its argument. Node-testable. (`mmArmRetryAction` is unchanged.)

### Arm `error` listener change (`index.html`, the `v.addEventListener('error', ...)` handler)

The `'retry'` and `'skip'` branches are untouched. The `'recache'` branch changes from "self-pull +
one fixed 2500 ms reload" to "self-pull once + start a poll loop":

```
action === 'recache':
  v._armRecacheN = (v._armRecacheN || 0) + 1;
  dbg('arm-recache');
  v._armRecachePending = _mmSelfPull(v, s);   // token, or null if not a parseable 127.0.0.1 seg
  // start the poll via the SAME single _armRetryTimer (self-rescheduling):
  _armPoll():                                  // named/closured poll step
    if (!v._armRecachePending) { return; }     // superseded/cleared -> stop
    var pa = mmVideoRecovery.mmRecachePollAction(window.mmCache ? mmCache.state(v._armRecachePending) : 'none');
    if (pa === 'rearm') {
      v._armRecachePending = null;
      dbg('arm-rearm');
      // iOS-5 needs load()+play() together to (re)start a held/errored element;
      // play() may not return a promise on iOS-5, so guard the .catch (same form
      // the existing retry reload uses):
      try { v.load(); } catch (e2) {}
      try { var p = v.play(); if (p && p['catch']) { p['catch'](function () {}); } } catch (e3) {}
      return;
    }
    if (pa === 'giveup') { v._armRecachePending = null; return; }  // NEEDS_ARM/tap owns it
    // 'wait':
    if (v._armRetryTimer) { clearTimeout(v._armRetryTimer); }
    v._armRetryTimer = setTimeout(_armPoll, ARM_RECACHE_POLL_MS);
  // start the first poll one ARM_RECACHE_POLL_MS tick out (the pull just began, so the
  // seg can't be cached yet — one code path, no special-case immediate check):
  if (v._armRetryTimer) { clearTimeout(v._armRetryTimer); }
  v._armRetryTimer = setTimeout(_armPoll, ARM_RECACHE_POLL_MS);
```

The `'retry'` branch keeps its single fixed `ARM_RETRY_BACKOFF_MS` reload through the same
`_armRetryTimer` slot. Because retry runs first (the ladder exhausts `ARM_MAX_RETRIES` before
`'recache'`), the timer is free when the poll starts.

### Per-element state + resets

New per-element field `v._armRecachePending` (the seg token being polled), alongside the existing
`v._armRetryN` / `v._armRecacheN` / `v._armSrc` / `v._armRetryTimer`.

Reset `v._armRecachePending = null` at BOTH existing reset points, so a stale poll can never re-arm
the wrong clip:
- **`playing` listener** (armed OK): already zeroes `_armRetryN`/`_armRecacheN` + clears
  `_armRetryTimer` — add `_armRecachePending = null`.
- **New-src branch** inside the `error` handler (`isLocal && s !== v._armSrc`): already zeroes
  `_armRetryN`/`_armRecacheN` — add `_armRecachePending = null`.

### Constants (`index.html`)

- **Remove** `var ARM_RECACHE_RELOAD_MS = 2500;` (no more fixed reload).
- **Add** `var ARM_RECACHE_POLL_MS = 500;` (poll cadence — responsive, cheap).
- **Keep** `ARM_MAX_RECACHES = 1` (one pull; the poll then waits for it — no need to re-pull),
  `ARM_MAX_RETRIES = 3`, `ARM_RETRY_BACKOFF_MS = 500`.

## Data flow

arm `<video> error` → `mmArmRetryAction` → `'recache'` → `_mmSelfPull` (records token, backend
`fetchToCache` begins) + start poll. Each `ARM_RECACHE_POLL_MS`, `mmRecachePollAction(mmCache.state(token))`:
`'wait'` (reschedule) while the download runs; on completion `mmCache.state` → `'cached'` → `'rearm'`
(`load()+play()` the exact arming element → `playing` → group READY); on error → `'failed'` →
`'giveup'` (NEEDS_ARM/tap). The single `_armRetryTimer` holds at most one pending poll; it is cleared
before each reschedule and on new-src/`playing`.

## Error handling / edge cases

- **New src / `playing` mid-poll:** `_armRecachePending` reset to null at both points + `_armRetryTimer`
  cleared → the in-flight poll no-ops on its next tick (guard `if (!v._armRecachePending) return;`).
- **Re-arm still fails (cached-but-corrupt seg):** `load()+play()` fires a fresh `error` →
  `mmArmRetryAction` returns `'skip'` (recaches exhausted) → NEEDS_ARM. No infinite loop.
- **Stray `error` while polling:** `mmArmRetryAction` returns `'skip'` (recaches exhausted) → early
  return; the poll (owning `_armRetryTimer`) is undisturbed.
- **`_mmSelfPull` returns null** (non-127.0.0.1 / unparseable src): `_armRecachePending = null`; the
  poll's first tick no-ops → NEEDS_ARM. Same as today for central/none devices — never central.
- **Hung `'pending'` forever:** by design no timeout; harmless (group GOes via `PREPARE_TIMEOUT`; device
  re-arms if it ever caches; poll self-cancels when the clip changes).
- **`mmCache` absent:** `mmCache.state` guarded → treated as `'none'` → `'wait'`; but `_mmSelfPull`
  already returns null when `!window.mmCache`, so no poll starts. Consistent.

## Testing

- **Node `--test`** (`tests/unit/js/mmvideo-recovery.test.js`), new `mmRecachePollAction` cases:
  - `'cached'` → `'rearm'`
  - `'failed'` → `'giveup'`
  - `'pending'` → `'wait'`
  - `'none'` → `'wait'`
  - (defensive) unknown/`undefined` → `'wait'`
  Existing `mmArmRetryAction` + `mmWatchdogAction` tests are unchanged (the helper's contract didn't
  change). Run via `python pytest_runner.py --js`.
- **On-wall re-sign-off** (the decisive test — the exact scenario that failed on 2026-07-11): reload one
  target device onto the new build, SSH-delete its cached seg
  (`rm -f /var/mobile/Media/MosaicMeshCache/seg_<token>_0.mp4`), PLAY the single-video playlist, and
  confirm the CLIENTLOG shows `arm-recache` → (poll) → **`arm-rearm`** and the device **self-heals**
  (re-pulls the seg → arms → plays) with NO manual push. Measure with the correct metric:
  `verr` clear AND `rs>=2` AND `ct` advancing across two snapshots (NOT `elapsed`, which advances on
  black screens — [[wall-verr3-is-mmvideo-not-cache]]). If practical, repeat against a larger seg to
  demonstrate size-agnosticism (the whole point of removing the fixed timeout). Deploy via STAGED
  single/small-batch reloads (never a whole-group reload — [[fleet-ssh-no-burst]]).

## Deploy

Staged reload (single/small-batch, never whole-group). Client JS only; no tweak rebuild, no server
restart. The `MMFORCE_TDBG_TEMP` force in `index.html` is re-applied uncommitted for the sign-off and
reverted after (never committed).
