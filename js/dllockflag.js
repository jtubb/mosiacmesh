/* js/dllockflag.js — A/B toggle for the one-way downlink clock lock (v4b).
 * Only meaningful when _mmClockBeat is also on: switches the beat steer's
 * target from the median (_robustTarget) to the windowed-max (_downlinkTarget).
 * Default false -> GoTime behaves byte-identically to today. */
window._mmDownlinkLock = false;
