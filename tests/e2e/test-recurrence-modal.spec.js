import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, createTestPlaylist, deleteScheduleByPlaylist, deletePlaylist, seedSchedule, cleanupE2eOrphans } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const PLAYLIST = '__e2e_rec_' + Date.now();
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);
    await createTestPlaylist(page, PLAYLIST);
    await seedSchedule(page, { playlistName: PLAYLIST, displayID: 'Mobile', startTime: '09:00', endTime: '10:00' });
    await page.reload(); await waitForHydrated(page);

    const scheduleId = await page.evaluate(
      (pn) => Alpine.store('mm').schedules.find(s => s.playlistName === pn)?.id, PLAYLIST);
    // Open via synthetic Alt+click (HTML5 click event with altKey).
    await page.evaluate((sid) => {
      const clip = document.querySelector(`.mm-clip[data-schedule-id="${sid}"]`);
      clip.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, altKey: true }));
    }, scheduleId);
    await page.waitForSelector('.mm-modal', { timeout: 5000 });

    // Change freq to WEEKLY, check Mon/Wed/Fri, change interval to 2.
    await page.evaluate(() => {
      const root = document.querySelector('.mm-modal');
      const sel = root.querySelector('[data-field="freq"]');
      sel.value = 'WEEKLY'; sel.dispatchEvent(new Event('change', { bubbles: true }));
      root.querySelector('[data-field="interval"]').value = '2';
      root.querySelectorAll('[data-field="byweekday"] input').forEach(cb => {
        cb.checked = ['0','2','4'].includes(cb.value);
      });
      root.querySelector('[data-action="save"]').click();
    });

    await page.waitForFunction(
      (pn) => {
        const s = Alpine.store('mm').schedules.find(x => x.playlistName === pn);
        return s && s.freq === 'WEEKLY' && s.interval === 2 && (s.byweekday || []).length === 3;
      }, PLAYLIST, { timeout: 5000 });
    await deleteScheduleByPlaylist(page, PLAYLIST);
    await deletePlaylist(page, PLAYLIST);
    return 'pass';
  } finally { await browser.close(); }
}
