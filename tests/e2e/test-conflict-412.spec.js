import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, createTestPlaylist, deleteScheduleByPlaylist, deletePlaylist, seedSchedule, cleanupE2eOrphans, pickEmptyTrack } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const PLAYLIST = '__e2e_412_' + Date.now();
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);
    const TRACK = await pickEmptyTrack(page);
    await createTestPlaylist(page, PLAYLIST);
    await seedSchedule(page, { playlistName: PLAYLIST, displayID: TRACK, startTime: '09:00', endTime: '10:00' });
    await page.reload(); await waitForHydrated(page);

    const scheduleId = await page.evaluate(
      (pn) => Alpine.store('mm').schedules.find(s => s.playlistName === pn)?.id, PLAYLIST);

    // Out-of-band edit the schedule (bumps _serverVersion).
    // Use the list endpoint + filter because the single-item GET is available
    // only after the server restarts with the PR-4c route; the list always works.
    await page.evaluate(async (sid) => {
      const listBody = await (await fetch('/api/schedules')).json();
      const fresh = (listBody.schedules || []).find(s => s.id === sid);
      await fetch('/api/schedules/' + sid, {
        method: 'PUT', headers: { 'Content-Type': 'application/json', 'If-Match': String(fresh._serverVersion) },
        body: JSON.stringify({ startTime: '11:00', endTime: '12:00' }),
      });
    }, scheduleId);

    // Now the store still holds the old version. Try to update — should 412.
    await page.evaluate(async (sid) => {
      try { await Alpine.store('mm').updateSchedule(sid, { startTime: '14:00', endTime: '15:00' }); }
      catch (_) {}
    }, scheduleId);
    // After refetch, the schedule should be at 11:00 (the OOB edit), NOT 14:00.
    await page.waitForFunction((sid) => {
      const s = Alpine.store('mm').schedules.find(x => x.id === sid);
      return s && s.startTime === '11:00';
    }, scheduleId, { timeout: 5000 });
    // Toast should mention 'another admin'.
    const sawToast = await page.evaluate(
      () => Alpine.store('mm').toasts.some(t => /another admin/.test(t.msg)));
    assert.ok(sawToast, 'expected "another admin" toast after 412');

    await deleteScheduleByPlaylist(page, PLAYLIST);
    await deletePlaylist(page, PLAYLIST);
    return 'pass';
  } finally { await browser.close(); }
}
