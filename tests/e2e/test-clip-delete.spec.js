import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, createTestPlaylist, deletePlaylist, seedSchedule, cleanupE2eOrphans, pickEmptyTrack } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const PLAYLIST = '__e2e_delete_' + Date.now();
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);
    const TRACK = await pickEmptyTrack(page);
    await createTestPlaylist(page, PLAYLIST);
    await seedSchedule(page, { playlistName: PLAYLIST, displayID: TRACK, startTime: '15:00', endTime: '16:00' });
    await page.reload(); await waitForHydrated(page);

    const scheduleId = await page.evaluate(
      (pn) => Alpine.store('mm').schedules.find(s => s.playlistName === pn)?.id,
      PLAYLIST
    );
    const clip = page.locator(`.mm-clip[data-schedule-id="${scheduleId}"]`);
    await clip.waitFor({ timeout: 5000 });
    await clip.click();
    // Single-clip delete: no confirm prompt
    await page.keyboard.press('Delete');
    await page.waitForFunction(
      (pn) => !Alpine.store('mm').schedules.find(x => x.playlistName === pn),
      PLAYLIST, { timeout: 5000 }
    );
    await deletePlaylist(page, PLAYLIST);
    return 'pass';
  } finally { await browser.close(); }
}
