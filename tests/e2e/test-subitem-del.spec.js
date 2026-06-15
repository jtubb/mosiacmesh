/**
 * PR-4c gap-fix (spec §358): Del on a drilled-in sub-item removes
 * the item from the playlist.
 *
 * Flow: seed a playlist with 3 items + a schedule, drill in, single-
 * click the middle item to select it, press Del, assert the playlist
 * now has 2 items in the expected order.
 */
import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, createTestPlaylist, deleteScheduleByPlaylist, deletePlaylist, seedSchedule, cleanupE2eOrphans, pickEmptyTrack } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const PLAYLIST = '__e2e_subdel_' + Date.now();
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);
    const TRACK = await pickEmptyTrack(page);

    // Seed a playlist with three items so removing the middle one is observable.
    await page.evaluate(async (pn) => {
      await fetch('/api/playlists', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: pn, items: [
          { file: '/media/server/videos/probe_test.mp4', duration: 30 },
          { file: '/media/server/videos/big_buck_bunny_1080p_h264.mov', duration: 596 },
          { file: 'bouncingBalls' },
        ], loop: true }),
      });
    }, PLAYLIST);
    await seedSchedule(page, { playlistName: PLAYLIST, displayID: TRACK, startTime: '09:00', endTime: '12:00' });
    await page.reload(); await waitForHydrated(page);

    const scheduleId = await page.evaluate(
      (pn) => Alpine.store('mm').schedules.find(s => s.playlistName === pn)?.id, PLAYLIST);

    // Drill in.
    await page.evaluate((sid) => {
      const clip = document.querySelector(`.mm-clip[data-schedule-id="${sid}"]`);
      clip.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }));
    }, scheduleId);
    await page.waitForSelector('.mm-drillin-item', { timeout: 5000 });

    // Single-click the middle (index 1) item to select it.
    await page.evaluate(() => {
      const items = document.querySelectorAll('.mm-drillin-item');
      items[1].dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    });
    await page.waitForFunction(
      () => Alpine.store('mm').selectedSubItem?.index === 1, null, { timeout: 5000 });

    // Press Del.
    await page.evaluate(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', {
        bubbles: true, cancelable: true, key: 'Delete',
      }));
    });
    // Wait for the playlist's items to drop to 2.
    await page.waitForFunction(
      (pn) => (Alpine.store('mm').playlists[pn]?.items || []).length === 2,
      PLAYLIST, { timeout: 5000 });

    // Verify the order: items 0 and 2 survive (probe_test + bouncingBalls).
    const afterFiles = await page.evaluate(
      (pn) => (Alpine.store('mm').playlists[pn].items || []).map(it => (typeof it === 'string') ? it : it.file),
      PLAYLIST);
    assert.deepEqual(afterFiles, ['/media/server/videos/probe_test.mp4', 'bouncingBalls'],
      `expected probe_test + bouncingBalls to survive, got ${JSON.stringify(afterFiles)}`);

    // selectedSubItem should be null after removal (the removed-index
    // selection clears so a follow-up Del doesn't try to remove the
    // now-shifted next item).
    const selAfter = await page.evaluate(() => Alpine.store('mm').selectedSubItem);
    assert.equal(selAfter, null, `expected selectedSubItem to clear after removal, got ${JSON.stringify(selAfter)}`);

    await deleteScheduleByPlaylist(page, PLAYLIST);
    await deletePlaylist(page, PLAYLIST);
    return 'pass';
  } finally { await browser.close(); }
}
