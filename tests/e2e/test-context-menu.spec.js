import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, createTestPlaylist, deletePlaylist, seedSchedule, cleanupE2eOrphans } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const PLAYLIST = '__e2e_ctx_' + Date.now();
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);
    await createTestPlaylist(page, PLAYLIST);
    await seedSchedule(page, { playlistName: PLAYLIST, displayID: 'Mobile', startTime: '15:00', endTime: '16:00' });
    await page.reload(); await waitForHydrated(page);

    const scheduleId = await page.evaluate(
      (pn) => Alpine.store('mm').schedules.find(s => s.playlistName === pn)?.id, PLAYLIST);
    // Synthetic contextmenu event on the clip.
    await page.evaluate((sid) => {
      const clip = document.querySelector(`.mm-clip[data-schedule-id="${sid}"]`);
      const r = clip.getBoundingClientRect();
      clip.dispatchEvent(new MouseEvent('contextmenu', {
        bubbles: true, cancelable: true, clientX: r.left + 5, clientY: r.top + 5,
      }));
    }, scheduleId);
    await page.waitForSelector('.mm-context-menu li:not(.mm-context-divider)', { timeout: 5000 });

    // Find + click the Delete item.
    await page.evaluate(() => {
      const items = Array.from(document.querySelectorAll('.mm-context-menu li'));
      const del = items.find(li => li.textContent.trim() === 'Delete');
      del.click();
    });
    await page.waitForFunction(
      (pn) => !Alpine.store('mm').schedules.find(x => x.playlistName === pn),
      PLAYLIST, { timeout: 5000 });
    await deletePlaylist(page, PLAYLIST);
    return 'pass';
  } finally { await browser.close(); }
}
