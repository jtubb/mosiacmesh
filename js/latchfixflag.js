/* js/latchfixflag.js — A/B toggle for the verr=3 LATCH BREAKER.
 *
 * WHY (root-caused on-wall 2026-09-16): after a fatal MEDIA_ERR_DECODE the
 * <video> element parks at networkState 0 (NETWORK_EMPTY) and NOTHING ever
 * re-attempts the load. The screen stays dark indefinitely -- even once the
 * original cause is gone. Measured: 8 screens stuck at verr=3 / ns=0 / rs=0 /
 * activated=False; repopulating the cache to 100% on all 24 screens did NOT
 * recover a single one of them, because they were already latched.
 *
 * The poll watchdog's escalation ended at 'dead' ("stay black"), which IS the
 * latch. Re-load() in place does not clear it -- that watchdog once fired 941
 * times futilely -- but a FRESH element does: a per-screen reload recovered
 * 8/8 and took fleet verr=3 from 8 to 0.
 *
 * WHAT IT DOES: adds a 'recreate' rung to the ladder, AFTER retry and recache
 * and BEFORE dead. It tears both <video> elements down (mmVideoRecovery +
 * makeVideoBuffer.destroy) and rebuilds them, then re-mounts the current item
 * immediately rather than waiting for the next item boundary.
 *
 * ONLY fires on a screen the old code had already given up on ('dead' = black
 * forever), so it cannot disturb a healthy screen. Bounded by a cooldown
 * (WATCHDOG_RECREATE_COOLDOWN_MS) that survives the per-clip counter reset, so
 * a genuinely unplayable clip cannot produce a recreate storm -- the failure
 * mode that once bled the fleet 24 -> 10 during the cold-load-watchdog arc.
 *
 * NOTE the fresh element is UNBLESSED: iOS-5 user activation does not transfer
 * to a new element, so playback.activated is cleared and the ARM path re-blesses
 * it. Under the mmvideo transplant that autoplays with no tap; on stock Safari
 * it falls back to the existing NEEDS_ARM / tap prompt.
 *
 * ROLLOUT: pilot ONE screen (tools/_reload_one.py <clientKey>), eyeball it, then
 * the rest of the group. Static file, no server restart. Default false ->
 * byte-identical behavior, safe to leave committed.
 */
window._mmLatchFix = false;
