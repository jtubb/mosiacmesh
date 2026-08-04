/* js/ratetestflag.js — operator toggle: when true, index.html (the webclip's
 * page) redirects to /ratetest.html so the open-loop rate probe runs INSIDE
 * Web.app, where mmvideo is actually active (a true webclip rate test — unlike
 * uiopen, which lands in Safari). Static file, no server restart to flip: set
 * true, RELOAD the fleet to run; set false, RELOAD to restore the wall.
 * Off by default (identical behavior). */
window._mmRateTest = false;
window._mmRateTestQuery = '?mode=eloop';   /* eloop=explicit-loop fix test; seek=seek-stall; rate/'' =rate sweep */
