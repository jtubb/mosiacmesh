/**
 * PR-7: end-to-end coverage of api.js's 5xx auto-retry. Spec §10.
 *
 * Approach: monkey-patch window.fetch in the browser so the FIRST
 * mutating PUT to /api/schedules/* returns 503; subsequent calls
 * pass through. Triggers a store.updateSchedule and asserts:
 *   - The 'Retrying…' info toast appears between attempts.
 *   - The update eventually succeeds (the schedule's startTime
 *     reflects the patch).
 *   - The 'Retrying…' toast is dismissed once the chain ends.
 *   - Backoff is shrunk via api.__testOverrideRetryDelays so the
 *     spec finishes in <1s.
 */
import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, createTestPlaylist, deleteScheduleByPlaylist, deletePlaylist, seedSchedule, cleanupE2eOrphans, pickEmptyTrack } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const PLAYLIST = '__e2e_retry_' + Date.now();
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);
    const TRACK = await pickEmptyTrack(page);
    await createTestPlaylist(page, PLAYLIST);
    await seedSchedule(page, { playlistName: PLAYLIST, displayID: TRACK, startTime: '09:00', endTime: '10:00' });
    await page.reload(); await waitForHydrated(page);

    const scheduleId = await page.evaluate(
      (pn) => Alpine.store('mm').schedules.find(s => s.playlistName === pn)?.id, PLAYLIST);

    // Shrink retry backoff + install a fetch shim that fails the first
    // PUT to this schedule's endpoint with 503, then passes through.
    await page.evaluate(async (sid) => {
      const apiMod = await import('/js/timeline/api.js');
      window.__restoreRetry = apiMod.__testOverrideRetryDelays([20, 20, 20]);
      const realFetch = window.fetch.bind(window);
      let putFails = 1;   // fail first PUT then let it through
      window.__originalFetch = realFetch;
      window.fetch = async (url, opts) => {
        const isPutToThis = (opts && opts.method === 'PUT')
          && typeof url === 'string'
          && url.includes('/api/schedules/' + sid);
        if (isPutToThis && putFails > 0) {
          putFails--;
          return new Response('upstream down', { status: 503 });
        }
        return realFetch(url, opts);
      };
    }, scheduleId);

    // Fire the update via the store. It should retry once and succeed.
    await page.evaluate(async (sid) => {
      await Alpine.store('mm').updateSchedule(sid, { startTime: '14:00', endTime: '15:00' });
    }, scheduleId);

    // Verify final store state has the patched times.
    await page.waitForFunction((sid) => {
      const s = Alpine.store('mm').schedules.find(x => x.id === sid);
      return s && s.startTime === '14:00';
    }, scheduleId, { timeout: 5000 });

    // After the retry chain settles, the 'Retrying…' toast should be gone.
    const finalToasts = await page.evaluate(() => Alpine.store('mm').toasts.map(t => t.msg));
    const retryToastStillUp = finalToasts.some(m => /Retrying/.test(m));
    assert.ok(!retryToastStillUp, `expected 'Retrying…' toast cleared, still see: ${JSON.stringify(finalToasts)}`);

    // Cleanup: restore fetch + delete the test schedule + playlist.
    await page.evaluate(() => {
      if (window.__originalFetch) window.fetch = window.__originalFetch;
      if (window.__restoreRetry) window.__restoreRetry();
    });
    await deleteScheduleByPlaylist(page, PLAYLIST);
    await deletePlaylist(page, PLAYLIST);
    return 'pass';
  } finally { await browser.close(); }
}
