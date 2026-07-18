/* MosaicMesh ?tdbg force-toggle (operator diagnostic) -- ES5, iPad-1 safe.
 *
 * WHAT: when true, every display client behaves as if opened with ?tdbg, i.e.
 * index.html sets window._mmDbg = true and streams per-screen clock telemetry
 * (off / prec / accAge / phStd / phMean / synced / settled / cready / skew) to
 * the server CLIENTLOG. Lets an operator watch oscillator-skew convergence
 * fleet-wide WITHOUT putting ?tdbg in each webclip's URL and WITHOUT SSH.
 *
 * HOW: this file is served from disk by the /js/ static handler (mtime-keyed
 * cache), so flipping the flag below + a fleet RELOAD toggles telemetry live
 * with NO server restart. index.html loads it before the ?tdbg gate runs.
 *
 * TOGGLE: set to true, save, RELOAD the fleet -> telemetry on. Set back to
 * false + RELOAD -> telemetry off. Default is false (byte-identical behavior;
 * safe to leave committed).
 */
window._mmForceTdbg = false;
