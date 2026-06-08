/**
 * PR-11: right-edge resize on freshly-placed (narrow) clips.
 *
 * Before this PR, the 6px resize handle was easy to miss on a 1-hour
 * clip (~50px wide at 1600px viewport). A click on the clip body
 * within the right-edge proximity triggered an HTML5 clip-move drag
 * that shifted startTime+endTime together rather than extending the
 * end. This spec asserts:
 *
 *   1. A pointerdown EXACTLY on the right-edge resize handle moves
 *      endTime out (resize succeeds — the canonical happy path).
 *   2. A dragstart 8px inside the right edge — within EDGE_HIT_PX of
 *      12 but outside the 10px handle — is preventDefault'd by the
 *      clip-move guard, so startTime stays put (no shifted clip).
 *
 * Uses synthetic pointer events + a real DragEvent on the clip body
 * so the dispatch is deterministic against Alpine re-renders.
 */
import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, createTestPlaylist, deleteScheduleByPlaylist, deletePlaylist, seedSchedule, cleanupE2eOrphans } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const PLAYLIST = '__e2e_rresize_' + Date.now();
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);
    // Pick any track — we target the clip by data-schedule-id, so
    // co-tenant clips on the same track don't interfere with the
    // synthetic pointer dispatch.
    const TRACK = await page.evaluate(() => {
      const ids = [...new Set(Alpine.store('mm').displays.map(d => d.displayID).filter(Boolean))];
      return ids[0];
    });
    if (!TRACK) throw new Error('no displays in store — dev server has no tracks');
    await createTestPlaylist(page, PLAYLIST);
    await seedSchedule(page, { playlistName: PLAYLIST, displayID: TRACK, startTime: '09:00', endTime: '10:00' });
    await page.reload(); await waitForHydrated(page);

    const scheduleId = await page.evaluate(
      (pn) => Alpine.store('mm').schedules.find(s => s.playlistName === pn)?.id, PLAYLIST);
    assert.ok(scheduleId, 'expected schedule to be created');

    // --- Case 1: resize via the handle moves endTime ---
    // Synthesize pointerdown/move/up on the right resize handle.
    await page.evaluate((sid) => {
      const clip = document.querySelector(`.mm-clip[data-schedule-id="${sid}"]`);
      const handle = clip.querySelector('.mm-clip-resize-handle[data-edge="right"]');
      const cRect = clip.getBoundingClientRect();
      const hRect = handle.getBoundingClientRect();
      const grid = clip.closest('.mm-day-grid');
      const gRect = grid.getBoundingClientRect();
      // Target: drag to ~12:00 (3 hours past 09:00 start).
      // grid usable area starts at LABEL_COL_PX (110) from grid.left.
      const usableLeft = gRect.left + 110;
      const usableWidth = gRect.width - 110;
      const targetX = usableLeft + (12 / 24) * usableWidth;
      const targetY = cRect.top + cRect.height / 2;
      handle.dispatchEvent(new PointerEvent('pointerdown', {
        bubbles: true, cancelable: true, pointerId: 1, pointerType: 'mouse',
        clientX: hRect.left + 5, clientY: hRect.top + hRect.height / 2,
      }));
      document.dispatchEvent(new PointerEvent('pointermove', {
        bubbles: true, cancelable: true, pointerId: 1, pointerType: 'mouse',
        clientX: targetX, clientY: targetY,
      }));
      document.dispatchEvent(new PointerEvent('pointerup', {
        bubbles: true, cancelable: true, pointerId: 1, pointerType: 'mouse',
        clientX: targetX, clientY: targetY,
      }));
    }, scheduleId);

    // After PUT lands, store should hold the new endTime (snapped to 15m).
    await page.waitForFunction((sid) => {
      const s = Alpine.store('mm').schedules.find(x => x.id === sid);
      return s && s.startTime === '09:00' && s.endTime === '12:00';
    }, scheduleId, { timeout: 5000 });

    // --- Case 2: dragstart 8px from the right edge of the clip is guarded ---
    // Reset to 09:00–10:00 so the second sub-case starts from known state.
    await page.evaluate(async (sid) => {
      const list = await (await fetch('/api/schedules')).json();
      const cur = (list.schedules || []).find(s => s.id === sid);
      await fetch('/api/schedules/' + sid, {
        method: 'PUT', headers: { 'Content-Type': 'application/json', 'If-Match': String(cur._serverVersion) },
        body: JSON.stringify({ startTime: '09:00', endTime: '10:00' }),
      });
    }, scheduleId);
    await page.reload(); await waitForHydrated(page);

    // Dispatch a real dragstart 8px inside the right edge of the clip.
    // The guard should cancel it (preventDefault). We assert the event
    // ends up defaultPrevented, AND that no clip-move side-effect lands.
    const wasPrevented = await page.evaluate((sid) => {
      const clip = document.querySelector(`.mm-clip[data-schedule-id="${sid}"]`);
      const r = clip.getBoundingClientRect();
      const dt = new DataTransfer();
      const ev = new DragEvent('dragstart', {
        bubbles: true, cancelable: true, dataTransfer: dt,
        clientX: r.right - 8, clientY: r.top + r.height / 2,
      });
      clip.dispatchEvent(ev);
      return ev.defaultPrevented;
    }, scheduleId);
    assert.ok(wasPrevented, 'expected dragstart 8px from right edge to be preventDefault\'d by the clip-move guard');

    // Confirm no time shift happened (would have if the guard missed).
    const afterShift = await page.evaluate((sid) => {
      const s = Alpine.store('mm').schedules.find(x => x.id === sid);
      return { startTime: s.startTime, endTime: s.endTime };
    }, scheduleId);
    assert.equal(afterShift.startTime, '09:00', 'startTime should not have shifted');
    assert.equal(afterShift.endTime, '10:00', 'endTime should not have shifted');

    await deleteScheduleByPlaylist(page, PLAYLIST);
    await deletePlaylist(page, PLAYLIST);
    return 'pass';
  } finally { await browser.close(); }
}
