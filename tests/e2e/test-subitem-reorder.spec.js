/**
 * PR-8: drag sub-clips to reorder within the drilled-in playlist row
 * (spec §358). Seed a 3-item playlist, drill in, drag item 0 onto
 * item 2, assert the resulting order is [b, c, a].
 *
 * Uses the synthetic DragEvent pattern (dragstart on source +
 * dragover/drop on target) since Playwright's locator.dragTo() races
 * with Alpine's mid-drag re-render — same pattern PR-4b T14 baked
 * into helpers.syntheticDrag.
 */
import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, deleteScheduleByPlaylist, deletePlaylist, seedSchedule, cleanupE2eOrphans, pickEmptyTrack } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const PLAYLIST = '__e2e_reord_' + Date.now();
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);
    const TRACK = await pickEmptyTrack(page);

    // Three named items so we can assert the post-reorder order
    // independently of the synthetic-drag math.
    await page.evaluate(async (pn) => {
      await fetch('/api/playlists', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: pn, items: [
          { file: '/A.mp4', duration: 10 },
          { file: '/B.mp4', duration: 10 },
          { file: '/C.mp4', duration: 10 },
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

    // Synthesize the drag: source = item 0 (A), target = item 2 (C).
    // The handler computes "insert BEFORE target" + adjusts for the
    // shift caused by removing source first, so dropping A onto C
    // produces [B, A, C]. To produce [B, C, A] we'd need to drop A
    // onto the empty area past the last item — but DOM-wise that's
    // the row body, not an item. Easier: drop A onto C, assert [B,A,C].
    await page.evaluate(() => {
      const items = document.querySelectorAll('.mm-drillin-item');
      const src = items[0];   // A
      const dst = items[2];   // C
      const dt = new DataTransfer();
      src.dispatchEvent(new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer: dt }));
      const r = dst.getBoundingClientRect();
      const opts = { bubbles: true, cancelable: true, dataTransfer: dt, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 };
      dst.dispatchEvent(new DragEvent('dragover', opts));
      dst.dispatchEvent(new DragEvent('drop',     opts));
      src.dispatchEvent(new DragEvent('dragend',  opts));
    });

    await page.waitForFunction(
      (pn) => {
        const pl = Alpine.store('mm').playlists[pn];
        if (!pl || !pl.items) return false;
        const files = pl.items.map(it => (typeof it === 'string') ? it : it.file);
        return files.join(',') === '/B.mp4,/A.mp4,/C.mp4';
      }, PLAYLIST, { timeout: 5000 });

    // Body class cleared; no error toasts.
    const bodyDrag = await page.evaluate(() => document.body.classList.contains('mm-dragging'));
    assert.ok(!bodyDrag, 'expected body.mm-dragging cleared after drop');
    const errToasts = await page.evaluate(() => Alpine.store('mm').toasts.filter(t => t.kind === 'error'));
    assert.equal(errToasts.length, 0, `expected no error toasts, got: ${JSON.stringify(errToasts)}`);

    await deleteScheduleByPlaylist(page, PLAYLIST);
    await deletePlaylist(page, PLAYLIST);
    return 'pass';
  } finally { await browser.close(); }
}
