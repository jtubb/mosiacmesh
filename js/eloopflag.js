/* js/eloopflag.js — A/B toggle for the explicit-loop restart (single-item playlists).
 * false (default): native DOM <video loop> wraps the video — but intermittently STALLS
 *   at the loop boundary (~20% measured via ratetest.html). Byte-identical to legacy.
 * true: loop=false + an 'ended' handler that seeks to the clock-correct position and
 *   calls play() (the play() the old loop=false path was missing) — 0% stall measured.
 * Static file, no server restart to flip; set true + RELOAD the fleet to enable. */
window._mmExplicitLoop = true;
