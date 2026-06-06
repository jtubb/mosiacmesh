import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, createTestPlaylist, deleteScheduleByPlaylist, deletePlaylist, seedSchedule, syntheticDrag, cleanupE2eOrphans } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const PLAYLIST = '__e2e_move_' + Date.now();
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);
    await createTestPlaylist(page, PLAYLIST);
    await seedSchedule(page, { playlistName: PLAYLIST, displayID: 'Mobile', startTime: '09:00', endTime: '10:00' });
    await page.reload(); await waitForHydrated(page);

    const scheduleId = await page.evaluate(
      (pn) => Alpine.store('mm').schedules.find(s => s.playlistName === pn)?.id,
      PLAYLIST
    );
    assert.ok(scheduleId, 'seeded schedule missing');

    await page.waitForSelector(`.mm-clip[data-schedule-id="${scheduleId}"]`, { timeout: 5000 });
    await syntheticDrag(page, {
      sourceSel: `.mm-clip[data-schedule-id="${scheduleId}"]`,
      targetSel: '.mm-day-grid .mm-track-droparea[data-display-id="Mobile"]',
      targetXFrac: 14 / 24,
    });

    await page.waitForFunction((pn) => {
      const s = Alpine.store('mm').schedules.find(x => x.playlistName === pn);
      return s && s.startTime !== '09:00';
    }, PLAYLIST, { timeout: 5000 });
    const moved = await page.evaluate(
      (pn) => Alpine.store('mm').schedules.find(x => x.playlistName === pn),
      PLAYLIST
    );
    assert.notEqual(moved.startTime, '09:00');
    assert.match(moved.startTime, /^1[34]:(00|15|30|45)$/, `expected ~13-14:xx, got ${moved.startTime}`);

    await deleteScheduleByPlaylist(page, PLAYLIST);
    await deletePlaylist(page, PLAYLIST);
    return 'pass';
  } finally { await browser.close(); }
}
