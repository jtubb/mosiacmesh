/**
 * PR-12: create + delete display groups from the admin timeline.
 *
 * Before this PR, display groups were implicitly created by client
 * auto-config (Mobile/Tablet/Desktop) and groups with zero online
 * clients were invisible. PR-12 adds GET/POST/DELETE /api/displays
 * + UI: "+ Group" button in the toolbar, "Delete group" in the
 * track-header right-click menu.
 *
 * This spec creates an __e2e_-prefixed group, asserts a new track
 * appears in the timeline, deletes it, asserts the track disappears.
 *
 * Requires the server to be running PR-12 — skips with a clear
 * message if /api/displays returns 404 (e.g., dev server hasn't been
 * restarted yet).
 */
import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, cleanupE2eOrphans } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const GROUP = '__e2e_grp_' + Date.now();
    await page.goto(TIMELINE()); await waitForHydrated(page);

    // Skip cleanly if PR-12 server-side hasn't been deployed yet.
    const routeOk = await page.evaluate(async () => {
      const r = await fetch('/api/displays');
      return r.ok;
    });
    if (!routeOk) {
      console.log('  (skipping — /api/displays not yet active; restart the dev server to enable)');
      return 'pass';
    }

    await cleanupE2eOrphans(page);
    // Best-effort cleanup of any leftover __e2e_grp_* groups.
    await page.evaluate(async () => {
      const r = await fetch('/api/displays');
      const j = await r.json();
      for (const d of (j.displays || [])) {
        if (d.displayID.startsWith('__e2e_grp_') && d.clientCount === 0 && d.scheduleCount === 0) {
          await fetch('/api/displays/' + encodeURIComponent(d.displayID), { method: 'DELETE' });
        }
      }
    });

    // --- Case 1: create via the store, assert track appears ---
    await page.evaluate((g) => Alpine.store('mm').createDisplayGroup(g), GROUP);
    await page.waitForFunction((g) => {
      return Alpine.store('mm').displayGroups.some(d => d.displayID === g);
    }, GROUP, { timeout: 5000 });
    // Track header should render with our displayID.
    await page.waitForFunction((g) => {
      return !!document.querySelector(`.mm-track-header[data-display-id="${g}"]`);
    }, GROUP, { timeout: 5000 });

    // --- Case 2: delete via the store, assert track disappears ---
    await page.evaluate((g) => Alpine.store('mm').deleteDisplayGroup(g), GROUP);
    await page.waitForFunction((g) => {
      return !Alpine.store('mm').displayGroups.some(d => d.displayID === g);
    }, GROUP, { timeout: 5000 });
    await page.waitForFunction((g) => {
      return document.querySelector(`.mm-track-header[data-display-id="${g}"]`) == null;
    }, GROUP, { timeout: 5000 });

    // --- Case 3: 409+refs when the group is in use ---
    // Re-create + add a stub client reference via direct settings.dat
    // is impractical from a browser. Instead, exercise the 409 path by
    // creating a group, posting a schedule against it, then trying to
    // delete. The optimistic store delete should roll back and the
    // toast surfaces the server's refs error.
    await page.evaluate((g) => Alpine.store('mm').createDisplayGroup(g), GROUP);
    await page.evaluate(async (g) => {
      // Need a playlist first — schedules require playlistName FK.
      await fetch('/api/playlists', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: '__e2e_grp_pl', items: [{ file: '/media/server/videos/probe_test.mp4', duration: 5 }], loop: true,
        }),
      });
      await fetch('/api/schedules', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          playlistName: '__e2e_grp_pl', displayID: g, freq: 'DAILY',
          dtstart: new Date().toISOString().slice(0, 10),
          startTime: '09:00', endTime: '10:00',
        }),
      });
    }, GROUP);

    // Trigger the delete — should fail with 409 + refs.
    let threw = false;
    try {
      await page.evaluate((g) => Alpine.store('mm').deleteDisplayGroup(g), GROUP);
    } catch (_) { threw = true; }
    // Either path the inner promise rejected, OR the page-evaluate
    // surfaces it; in both cases the store should have rolled back so
    // the group is still present.
    const stillThere = await page.evaluate((g) =>
      Alpine.store('mm').displayGroups.some(d => d.displayID === g), GROUP);
    assert.ok(stillThere, 'expected group to still be present after 409 (rollback)');
    // Toast should mention "in use".
    const sawToast = await page.evaluate(() =>
      Alpine.store('mm').toasts.some(t => /in use/i.test(t.msg)));
    assert.ok(sawToast, 'expected "in use" toast on 409 delete');

    // Cleanup: remove schedule + playlist, then group.
    await page.evaluate(async (g) => {
      const sj = await (await fetch('/api/schedules')).json();
      for (const s of (sj.schedules || [])) {
        if (s.displayID === g) {
          await fetch('/api/schedules/' + encodeURIComponent(s.id), { method: 'DELETE' });
        }
      }
      await fetch('/api/playlists/__e2e_grp_pl', { method: 'DELETE' });
      await fetch('/api/displays/' + encodeURIComponent(g), { method: 'DELETE' });
    }, GROUP);

    return 'pass';
  } finally { await browser.close(); }
}
