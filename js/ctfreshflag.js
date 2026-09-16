/* js/ctfreshflag.js — A/B toggle for the currentTime STALENESS CORRECTOR
 * (audit 0.4 lever 1). Unlike the drift probe, this one CHANGES CONTROL:
 * it feeds the drift steer a corrected position instead of the raw read.
 *
 * WHY (measured on-wall 2026-09-16, 18/18 screens, verdict BIAS):
 *   median step period 256ms  -> currentTime is a zero-order hold
 *   median staleness   142ms  -> what the sensor under-reports by
 *   median loop err    -96ms  -> the phantom lag the controllers act on
 *   median true err    +40ms  -> the videos are actually AHEAD, not behind
 * Each screen's observer phase and main-queue delay differ, so its staleness
 * differs (45-245ms across the fleet) -- and THAT difference is cross-screen
 * desync. The controllers chase a lag that is not there and over-speed past it.
 *
 * WHAT IT DOES: oversamples currentTime at 20Hz and feeds the steer
 * max(ct_i - t_i) over a ~900ms window instead of the raw value. Staleness only
 * ever makes a reading too SMALL, so the maximum picks the freshest sample and
 * recovers the true position. The leftover residual is bounded by the SAMPLING
 * interval (~15-25ms) rather than the observer period, and it is common-mode
 * across screens, so it does not desync anything.
 *
 * EXPECTED EFFECT: loop err moves from ~-96ms toward 0; true err moves from
 * ~+40ms toward 0; the cross-screen spread of true err tightens from ~86ms.
 * Verify with python tools/driftprobe.py --group 'OEB Sign 1' -- keep
 * js/driftprobeflag.js and js/tdbgflag.js on so the probe can measure the result.
 * The probe deliberately keeps reading the RAW sensor, so pStale should be
 * UNCHANGED (~142ms): it measures the sensor, not the correction.
 *
 * Static file, no server restart: set true + RELOAD the fleet. Default false ->
 * byte-identical behavior, safe to leave committed.
 */
window._mmCtFresh = false;
