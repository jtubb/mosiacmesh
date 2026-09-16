/* js/driftprobeflag.js — A/B toggle for the drift MEASUREMENT probe
 * (audit 2026-09-15 section 0.3). Measurement only: it never steers anything.
 *
 * WHY: the wall's steady-state drift error sits at -77..-206ms, always behind,
 * and two mechanisms predict that same number while needing OPPOSITE fixes --
 * P-droop against the measured ~2.5% decode deficit (deficit/GAIN = 125ms), or a
 * 250ms zero-order-hold currentTime whose mean staleness is also 125ms. An
 * integral cures the first and is powerless against the second, which is a fair
 * description of why PI and the feedforward both failed on-wall.
 *
 * WHAT IT DOES: oversamples v.currentTime at 20Hz during normal playback and
 * reports the error read AT staircase step edges (staleness ~0, so this is where
 * the video REALLY is) against the error read at arbitrary moments (what the
 * 500ms drift loop acts on). Their difference is the sensor's staleness, and the
 * measured step period confirms or kills the zero-order-hold premise outright.
 *
 * READING THE RESULT (per screen, in the CLIENTLOG 'driftprobe' line):
 *   pV=BIAS   pTrue~0    pLoop~-125  -> the video is fine; the controller is
 *                                       chasing a phantom. Fix the SENSOR; do
 *                                       NOT retry PI first.
 *   pV=DROOP  pTrue~-125 pLoop~-125  -> the video really is behind. Retry PI now
 *                                       that Lever A removed the spurious seeks.
 *   pV=MIXED                         -> both, in the proportion pStale shows.
 *   pStep~250                        -> the zero-order hold is confirmed.
 *
 * NEEDS BOTH FLAGS: reporting goes through the CLIENTLOG dbg() channel, so
 * tdbgflag.js must also be true or nothing is emitted (same A/B mechanics as the
 * beat work). Static file, no server restart: set true + RELOAD the fleet.
 * Default false -> byte-identical behavior, safe to leave committed.
 */
window._mmDriftProbe = false;
