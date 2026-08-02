/* js/beatflag.js — A/B toggle for the master-clock TIMEBEAT (see
 * docs/superpowers/specs/2026-08-01-master-clock-beat-design.md).
 * false: pure /time round-trip clock (legacy, byte-identical). true: phase-lock
 * to the server broadcast beat. Set true, save, RELOAD the fleet to enable.
 * The server emits TIMEBEAT unconditionally; only flagged clients consume it. */
window._mmClockBeat = false;
