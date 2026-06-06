import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, createTestPlaylist, deleteScheduleByPlaylist, deletePlaylist, syntheticDrag, cleanupE2eOrphans } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const PLAYLIST = '__e2e_create_' + Date.now();
    await page.goto(TIMELINE());
    await waitForHydrated(page);
    await cleanupE2eOrphans(page);
    await createTestPlaylist(page, PLAYLIST);
    await page.reload();
    await waitForHydrated(page);

    // CSS.escape so a Date.now() timestamp in the name (digits) plays
    // nice with attribute selectors using has-text via JS evaluation.
    const sourceSel = await page.evaluate((pn) => {
      const li = Array.from(document.querySelectorAll('.mm-bin-item'))
        .find(el => el.textContent.trim() === pn);
      if (!li) return null;
      const id = 'mm_e2e_src';
      li.setAttribute('data-mm-e2e', id);
      return `[data-mm-e2e="${id}"]`;
    }, PLAYLIST);
    assert.ok(sourceSel, `playlist bin item for ${PLAYLIST} not found`);

    await syntheticDrag(page, {
      sourceSel,
      targetSel: '.mm-day-grid .mm-track-droparea[data-display-id="Mobile"]',
      targetXFrac: 14 / 24,   // ~14:00
    });

    await page.waitForFunction((pn) => {
      const s = Alpine.store('mm').schedules.find(x => x.playlistName === pn);
      return s && s.startTime;
    }, PLAYLIST, { timeout: 5000 });
    const created = await page.evaluate(
      (pn) => Alpine.store('mm').schedules.find(x => x.playlistName === pn),
      PLAYLIST
    );
    assert.ok(created, 'schedule was not created');
    assert.equal(created.displayID, 'Mobile');
    assert.match(created.startTime, /^14:(00|15|30|45)$/, `expected ~14:xx, got ${created.startTime}`);

    await deleteScheduleByPlaylist(page, PLAYLIST);
    await deletePlaylist(page, PLAYLIST);
    return 'pass';
  } finally {
    await browser.close();
  }
}
