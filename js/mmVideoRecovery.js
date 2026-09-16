// js/mmVideoRecovery.js  — ES5 only. Pure decision for poll-watchdog video recovery.
// index.html's setInterval watchdog calls mmWatchdogAction each tick. See
// docs/superpowers/specs/2026-07-10-mmvideo-watchdog-design.md
(function (root) {
  var mmVideoRecovery = {};

  // state = { shouldPlay, decoding, retries, recaches, recreates,
  //           maxRetries, maxRecaches, maxRecreates }
  // Poll-watchdog escalation for a video that should be playing:
  //   not should-play OR decoding -> 'ok'   (healthy / n/a)
  //   stalled, retries < maxRetries    -> 'retry'    (kick load()+play() in place)
  //   stalled, recaches < maxRecaches  -> 'recache'  (client self-pull the segment)
  //   stalled, recreates < maxRecreates-> 'recreate' (rebuild the <video> element)
  //   otherwise -> 'dead'                    (stay black; NEVER central)
  //
  // Why 'recreate' exists (root-caused on-wall 2026-09-16): after a fatal
  // MEDIA_ERR_DECODE the element parks at networkState 0 (NETWORK_EMPTY) and
  // nothing ever re-attempts the load, so the screen stays dark indefinitely
  // even once the original cause is gone. Ending the ladder at 'dead' WAS that
  // latch. Re-load() on the PERSISTENT element does not clear it (this watchdog
  // once fired 941 times futilely); a FRESH element does -- 8/8 stuck screens
  // recovered and fleet verr=3 went 8 -> 0. It sits AFTER the cheaper steps
  // because rebuilding the element is the most disruptive repair, and it is
  // budgeted so a genuinely dead clip still terminates at 'dead'.
  //
  // Back-compat: callers that pass no recreate budget keep the old ladder --
  // never hand a caller an action it has no code to perform.
  mmVideoRecovery.mmWatchdogAction = function (state) {
    if (!state || !state.shouldPlay || state.decoding) { return 'ok'; }
    if (state.retries < state.maxRetries) { return 'retry'; }
    if (state.recaches < state.maxRecaches) { return 'recache'; }
    if ((state.recreates || 0) < (state.maxRecreates || 0)) { return 'recreate'; }
    return 'dead';
  };

  // state = { active, isLocal, retries, recaches, maxRetries, maxRecaches }
  // Arm-phase (pre-playback) <video> error recovery. The poll watchdog above is gated
  // on playback.active, so it does NOT cover ARM; an arm-time error fires the 'error'
  // event and is handled here. Retry the local load a few times; when retries exhausted,
  // try self-pull (recache); when both exhausted, skip (the client's existing NEEDS_ARM /
  // tap-to-start path takes over — NEVER central).
  //   active true                   -> 'skip'  (active playback: the poll watchdog owns it)
  //   not local                     -> 'skip'  (central/other error: not our concern)
  //   local, retries < maxRetries   -> 'retry'
  //   local, recaches < maxRecaches -> 'recache'
  //   otherwise                     -> 'skip'
  mmVideoRecovery.mmArmRetryAction = function (state) {
    if (!state || state.active || !state.isLocal) { return 'skip'; }
    if (state.retries < state.maxRetries) { return 'retry'; }
    if (state.recaches < state.maxRecaches) { return 'recache'; }
    return 'skip';
  };

  // cacheState = 'none' | 'pending' | 'cached' | 'failed'  (from mmCache.state)
  // Poll decision for a pending arm-phase recache self-pull:
  //   'cached' -> 'rearm'  (seg landed; the arm listener does load()+play())
  //   'failed' -> 'giveup' (pull errored; fall to NEEDS_ARM/tap — NEVER central)
  //   else     -> 'wait'   ('pending' download in progress, or 'none'; keep polling — no time bound)
  mmVideoRecovery.mmRecachePollAction = function (cacheState) {
    if (cacheState === 'cached') { return 'rearm'; }
    if (cacheState === 'failed') { return 'giveup'; }
    return 'wait';
  };

  root.mmVideoRecovery = mmVideoRecovery;
  if (typeof module !== 'undefined' && module.exports) { module.exports = mmVideoRecovery; }
})(typeof window !== 'undefined' ? window : global);
