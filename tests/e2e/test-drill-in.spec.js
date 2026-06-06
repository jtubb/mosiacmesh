import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, createTestPlaylist, deleteScheduleByPlaylist, deletePlaylist, seedSchedule, cleanupE2eOrphans } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const PLAYLIST = '__e2e_drill_' + Date.now();
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);
    await createTestPlaylist(page, PLAYLIST);
    await seedSchedule(page, { playlistName: PLAYLIST, displayID: 'Mobile', startTime: '09:00', endTime: '12:00' });
    await page.reload(); await waitForHydrated(page);

    const scheduleId = await page.evaluate(
      (pn) => Alpine.store('mm').schedules.find(s => s.playlistName === pn)?.id,
      PLAYLIST
    );
    await page.waitForSelector(`.mm-clip[data-schedule-id="${scheduleId}"]`, { timeout: 5000 });
    // Synthetic dblclick: Playwright's clip.dblclick() uses real mouse
    // events that the draggable=true clip interprets as a drag-start
    // attempt, so the dblclick is dropped. dispatching dblclick directly
    // bypasses the drag detector.
    await page.evaluate((sid) => {
      const clip = document.querySelector(`.mm-clip[data-schedule-id="${sid}"]`);
      clip.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }));
    }, scheduleId);

    const sub = page.locator('.mm-drillin-row .mm-drillin-item').first();
    await sub.waitFor({ timeout: 5000 });
    const itemText = await sub.innerText();
    assert.match(itemText, /probe_test/, `expected drilled item to mention probe_test, got "${itemText}"`);

    await deleteScheduleByPlaylist(page, PLAYLIST);
    await deletePlaylist(page, PLAYLIST);
    return 'pass';
  } finally { await browser.close(); }
}
