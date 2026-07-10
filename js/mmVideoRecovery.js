// js/mmVideoRecovery.js  — ES5 only. Pure decision for poll-watchdog video recovery.
// index.html's setInterval watchdog calls mmWatchdogAction each tick. See
// docs/superpowers/specs/2026-07-10-mmvideo-watchdog-design.md
(function (root) {
  var mmVideoRecovery = {};

  // state = { shouldPlay, decoding, retries, recaches, maxRetries, maxRecaches }
  // Poll-watchdog escalation for a video that should be playing:
  //   not should-play OR decoding -> 'ok'   (healthy / n/a)
  //   stalled, retries < maxRetries  -> 'retry'    (kick load()+play() in place)
  //   stalled, recaches < maxRecaches -> 'recache' (client self-pull the segment)
  //   otherwise -> 'dead'                    (stay black; NEVER central)
  mmVideoRecovery.mmWatchdogAction = function (state) {
    if (!state || !state.shouldPlay || state.decoding) { return 'ok'; }
    if (state.retries < state.maxRetries) { return 'retry'; }
    if (state.recaches < state.maxRecaches) { return 'recache'; }
    return 'dead';
  };

  // state = { active, isLocal, retries, maxRetries }
  // Arm-phase (pre-playback) <video> error recovery. The poll watchdog above is gated
  // on playback.active, so it does NOT cover ARM; an arm-time error fires the 'error'
  // event and is handled here. Retry the local load a few times; when exhausted, skip
  // (the client's existing NEEDS_ARM / tap-to-start path takes over — NEVER central).
  //   active true                 -> 'skip'  (active playback: the poll watchdog owns it)
  //   not local                   -> 'skip'  (central/other error: not our concern)
  //   local, retries < maxRetries -> 'retry'
  //   otherwise                   -> 'skip'
  mmVideoRecovery.mmArmRetryAction = function (state) {
    if (!state || state.active || !state.isLocal) { return 'skip'; }
    if (state.retries < state.maxRetries) { return 'retry'; }
    return 'skip';
  };

  root.mmVideoRecovery = mmVideoRecovery;
  if (typeof module !== 'undefined' && module.exports) { module.exports = mmVideoRecovery; }
})(typeof window !== 'undefined' ? window : global);
