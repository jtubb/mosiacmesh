// js/mmVideoRecovery.js  — ES5 only. Pure decision for cached-video <video> error recovery.
// On a pooled <video> 'error', index.html asks this what to do. See
// docs/superpowers/specs/2026-07-10-mmvideo-recovery-design.md
(function (root) {
  var mmVideoRecovery = {};

  // state = { isLocal: bool, retries: int, maxRetries: int }
  //   isLocal false            -> 'ignore'    (non-127.0.0.1 error; not our concern)
  //   isLocal true, retries<max -> 'retry'    (reload the local src)
  //   isLocal true, retries>=max -> 'downgrade' (budget exhausted -> ANNOUNCE_CACHE_MODE none)
  mmVideoRecovery.mmVideoErrorAction = function (state) {
    if (!state || !state.isLocal) { return 'ignore'; }
    if (state.retries < state.maxRetries) { return 'retry'; }
    return 'downgrade';
  };

  root.mmVideoRecovery = mmVideoRecovery;
  if (typeof module !== 'undefined' && module.exports) { module.exports = mmVideoRecovery; }
})(typeof window !== 'undefined' ? window : global);
