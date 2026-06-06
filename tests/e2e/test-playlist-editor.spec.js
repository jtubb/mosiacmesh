import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, createTestPlaylist, deleteScheduleByPlaylist, deletePlaylist, seedSchedule, cleanupE2eOrphans } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const PLAYLIST = '__e2e_ple_' + Date.now();
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);
    await createTestPlaylist(page, PLAYLIST);
    await seedSchedule(page, { playlistName: PLAYLIST, displayID: 'Mobile', startTime: '09:00', endTime: '12:00' });
    await page.reload(); await waitForHydrated(page);

    const scheduleId = await page.evaluate(
      (pn) => Alpine.store('mm').schedules.find(s => s.playlistName === pn)?.id, PLAYLIST);
    // Drill in.
    await page.evaluate((sid) => {
      const clip = document.querySelector(`.mm-clip[data-schedule-id="${sid}"]`);
      clip.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }));
    }, scheduleId);
    await page.waitForSelector('.mm-drillin-item', { timeout: 5000 });

    // Click the first item -> editor opens.
    await page.evaluate(() => {
      const it = document.querySelector('.mm-drillin-item');
      it.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    });
    await page.waitForSelector('.mm-modal', { timeout: 5000 });

    // Set backgroundColor + Save.
    await page.evaluate(() => {
      const root = document.querySelector('.mm-modal');
      root.querySelector('[data-field="backgroundColor"]').value = '#123456';
      root.querySelector('[data-action="save"]').click();
    });
    await page.waitForFunction(
      (pn) => {
        const pl = Alpine.store('mm').playlists[pn];
        return pl && pl.items && pl.items[0] && pl.items[0].backgroundColor === '#123456';
      }, PLAYLIST, { timeout: 5000 });

    await deleteScheduleByPlaylist(page, PLAYLIST);
    await deletePlaylist(page, PLAYLIST);
    return 'pass';
  } finally { await browser.close(); }
}
