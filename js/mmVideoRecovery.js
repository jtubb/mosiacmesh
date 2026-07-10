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

  root.mmVideoRecovery = mmVideoRecovery;
  if (typeof module !== 'undefined' && module.exports) { module.exports = mmVideoRecovery; }
})(typeof window !== 'undefined' ? window : global);
