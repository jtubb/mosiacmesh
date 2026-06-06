/**
 * Shared helpers for the Playwright e2e specs in this directory.
 *
 * Each spec is responsible for creating + cleaning up its own state
 * (uniquely-named test playlist + at most one test schedule) so the
 * shared dev server's settings.dat stays clean between runs.
 *
 * Drag-and-drop note: Playwright's `locator.dragTo()` uses real mouse
 * events (mousedown/move/up) which Chromium translates to HTML5 drag
 * IF the source has `draggable="true"`. In practice the timing is
 * flaky against our reactive renderer (the element under the cursor
 * shifts as Alpine re-renders mid-drag). We dispatch synthetic
 * `DragEvent`s via page.evaluate instead — same approach used by the
 * MCP-driven verification during PR-4b development.
 */
export const BASE = process.env.MM_BASE_URL || 'http://localhost:3000';
export const TIMELINE = () => BASE + '/admin?nocache=' + Date.now() + '#timeline';

export async function waitForHydrated(page) {
  await page.waitForFunction(
    () => window.Alpine && Alpine.store('mm') && Alpine.store('mm').hydrated,
    null,
    { timeout: 10_000 }
  );
  await page.evaluate(() => {
    const btn = document.querySelector('button[data-nav="timeline"]');
    if (btn) btn.click();
  });
  await page.waitForFunction(() => document.querySelector('.mm-day-grid') != null, null, { timeout: 5_000 });
}

export async function deleteScheduleByPlaylist(page, playlistName) {
  await page.evaluate(async (pn) => {
    const r = await fetch('/api/schedules');
    const j = await r.json();
    for (const s of (j.schedules || [])) {
      if (s.playlistName === pn) {
        await fetch('/api/schedules/' + encodeURIComponent(s.id), { method: 'DELETE' });
      }
    }
  }, playlistName);
}

export async function createTestPlaylist(page, name) {
  await page.evaluate(async (n) => {
    await fetch('/api/playlists', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: n,
        items: [{ file: '/media/server/videos/probe_test.mp4', duration: 30 }],
        loop: true,
      }),
    });
  }, name);
}

export async function deletePlaylist(page, name) {
  await page.evaluate(async (n) => {
    await fetch('/api/playlists/' + encodeURIComponent(n), { method: 'DELETE' });
  }, name);
}

/**
 * Best-effort cleanup of any leftover __e2e_* schedules + playlists from
 * prior failed runs. Run from each spec BEFORE creating new state, so a
 * crashed earlier spec doesn't contaminate the current one (an orphan
 * clip on a 'pristine' track changes the drag math and the assertion
 * fails for the wrong reason).
 */
export async function cleanupE2eOrphans(page) {
  await page.evaluate(async () => {
    const sr = await fetch('/api/schedules'); const sj = await sr.json();
    for (const s of (sj.schedules || [])) {
      if ((s.playlistName || '').startsWith('__e2e_')) {
        await fetch('/api/schedules/' + encodeURIComponent(s.id), { method: 'DELETE' });
      }
    }
    const pr = await fetch('/api/playlists'); const pj = await pr.json();
    const playlists = pj.playlists || {};
    const names = Array.isArray(playlists) ? playlists.map(p => p.name) : Object.keys(playlists);
    for (const name of names) {
      if (name.startsWith('__e2e_')) {
        await fetch('/api/playlists/' + encodeURIComponent(name), { method: 'DELETE' });
      }
    }
  });
}

export async function seedSchedule(page, { playlistName, displayID, startTime, endTime }) {
  return await page.evaluate(async ({ pn, did, st, et }) => {
    const today = new Date().toISOString().slice(0, 10);
    const r = await fetch('/api/schedules', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ playlistName: pn, displayID: did, freq: 'DAILY',
        dtstart: today, startTime: st, endTime: et }),
    });
    return await r.json();
  }, { pn: playlistName, did: displayID, st: startTime, et: endTime });
}

/**
 * Synthetic HTML5 drag dispatch — fires dragstart on `sourceSel`, then
 * dragover/drop on `targetSel` at (clientX, clientY) inside the
 * target's rect. Returns once the chain has been dispatched.
 *
 * Why not page.dragTo: Playwright's drag emulation uses real mouse
 * events that Chromium translates to HTML5 drag if the source is
 * draggable=true. The translation timing fights Alpine's mid-drag
 * re-render and intermittently fails to deliver `drop` to our
 * .mm-track-droparea. Synthetic dispatch is deterministic.
 */
export async function syntheticDrag(page, { sourceSel, targetSel, targetXFrac = 0.5, targetYFrac = 0.5 }) {
  await page.evaluate(async ({ src, tgt, xf, yf }) => {
    const source = document.querySelector(src);
    const target = document.querySelector(tgt);
    if (!source || !target) throw new Error(`syntheticDrag: missing ${!source ? 'source' : 'target'}`);
    const sr = source.getBoundingClientRect();
    const tr = target.getBoundingClientRect();
    // dragstart at source's LEFT edge so clip-move's `offsetXInClip`
    // ≈ 0 — then drop X maps directly to start time without an offset
    // correction. (Real users grab somewhere in the middle and the
    // offset matters; for an automated test, no-offset is cleaner.)
    const sx = sr.left + 2;
    const sy = sr.top + sr.height / 2;
    const tx = tr.left + tr.width * xf;
    const ty = tr.top + tr.height * yf;
    const dt = new DataTransfer();
    source.dispatchEvent(new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer: dt, clientX: sx, clientY: sy }));
    target.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt, clientX: tx, clientY: ty }));
    target.dispatchEvent(new DragEvent('drop',     { bubbles: true, cancelable: true, dataTransfer: dt, clientX: tx, clientY: ty }));
    source.dispatchEvent(new DragEvent('dragend',  { bubbles: true, cancelable: true, dataTransfer: dt, clientX: tx, clientY: ty }));
  }, { src: sourceSel, tgt: targetSel, xf: targetXFrac, yf: targetYFrac });
}
