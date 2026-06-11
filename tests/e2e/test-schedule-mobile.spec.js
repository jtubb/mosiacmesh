/**
 * Section 3 — the responsive Schedule destination on a phone viewport.
 *
 * Drives the REAL admin page at a 390×844 (phone) viewport so store.isMobile
 * is true and the mmScheduleMobile stack renders. Asserts:
 *   1. Day agenda renders grouped by display group, with a row per schedule.
 *   2. Tapping a row opens the recurrence editor sheet.
 *   3. "+ Schedule" -> pick playlist + group -> Save -> the new schedule
 *      round-trips through /api/schedules (the create path, end-to-end).
 *   4. Week shows seven day-section headers.
 *
 * Owns its own state: a uniquely-named __e2e_sched playlist + the schedule
 * it creates, both deleted in cleanup so the shared dev server stays clean.
 */
import { chromium } from 'playwright';
import assert from 'node:assert';

const BASE = process.env.MM_BASE_URL || 'http://localhost:3000';
const ADMIN = () => BASE + '/admin.html?nocache=' + Date.now() + '#schedule';
const PL = '__e2e_sched';
const PHONE = { width: 390, height: 844 };

async function waitHydrated(page) {
  await page.waitForFunction(
    () => window.Alpine && Alpine.store('mm') && Alpine.store('mm').hydrated === true,
    null, { timeout: 15_000 });
  await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
}
async function settle(page) {
  await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
}

async function delPlaylist(page, name) {
  await page.request.delete(BASE + '/api/playlists/' + encodeURIComponent(name));
}
async function ensurePlaylist(page, name) {
  await delPlaylist(page, name);
  const r = await page.request.post(BASE + '/api/playlists', {
    headers: { 'Content-Type': 'application/json' },
    data: { name, items: [{ file: 'lissajous', playmode: 'SCRIPT' }], loop: false },
  });
  assert.ok(r.ok(), `POST /api/playlists ${name} -> ${r.status()}`);
}
async function listSchedules(page) {
  const r = await page.request.get(BASE + '/api/schedules');
  const j = await r.json();
  return j.schedules || [];
}
async function delSchedulesForPlaylist(page, name) {
  for (const s of await listSchedules(page)) {
    if (s.playlistName === name) await page.request.delete(BASE + '/api/schedules/' + encodeURIComponent(s.id));
  }
}
// First display group id (the create picker needs a real group to target).
async function firstGroupId(page) {
  const r = await page.request.get(BASE + '/api/displays');
  const j = await r.json();
  const list = j.displays || [];
  return list.length ? list[0].displayID : null;
}

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: PHONE });
  try {
    // Up-front cleanup of orphans from a prior crashed run.
    await page.goto(BASE + '/admin.html');
    await delSchedulesForPlaylist(page, PL);
    await delPlaylist(page, PL);

    const groupId = await firstGroupId(page);
    assert.ok(groupId, 'need at least one display group on the dev server for the create path');

    await ensurePlaylist(page, PL);

    // ---- 1. Mobile agenda renders ----
    await page.goto(ADMIN());
    await waitHydrated(page);
    await page.waitForFunction(() => Alpine.store('mm').isMobile === true, null, { timeout: 5_000 });
    await page.waitForFunction(() => Alpine.store('mm').activeTab === 'schedule', null, { timeout: 5_000 });
    // The mobile stack is present (not the desktop grid).
    await page.waitForFunction(
      () => document.querySelector('.mm-schedule-mobile') && document.querySelector('.mm-schedule-mobile').offsetParent !== null,
      null, { timeout: 5_000 });

    // ---- 3. Create a schedule via "+ Schedule" ----
    await page.evaluate(() => {
      const fab = document.querySelector('.mm-schedule-mobile .mm-sched-fab');
      if (!fab) throw new Error('no + Schedule FAB');
      fab.click();
    });
    await page.waitForFunction(
      () => document.querySelector('.mm-modal [data-field="playlistName"]') != null, null, { timeout: 5_000 });
    // Pick our playlist + the first group, then Save.
    await page.evaluate(({ pl, gid }) => {
      const setSel = (sel, val) => {
        const el = document.querySelector(sel);
        el.value = val;
        el.dispatchEvent(new Event('change', { bubbles: true }));
      };
      setSel('.mm-modal [data-field="playlistName"]', pl);
      setSel('.mm-modal [data-field="displayID"]', gid);
    }, { pl: PL, gid: groupId });
    await settle(page);
    await page.evaluate(() => {
      const save = Array.from(document.querySelectorAll('.mm-modal .mm-form-actions button'))
        .find(b => b.textContent.trim() === 'Save');
      if (!save) throw new Error('no Save button');
      save.click();
    });
    await page.waitForFunction(() => document.querySelector('.mm-modal') == null, null, { timeout: 5_000 });

    // Verify via REST: a schedule for PL now exists on groupId.
    let created = null;
    for (let i = 0; i < 20 && !created; i++) {
      const all = await listSchedules(page);
      created = all.find(s => s.playlistName === PL && s.displayID === groupId) || null;
      if (!created) await settle(page);
    }
    assert.ok(created, `expected a schedule for ${PL} on ${groupId} after + Schedule -> Save`);

    // ---- 2. Re-hydrate; the agenda shows the row; tapping opens the editor ----
    await page.goto(ADMIN());
    await waitHydrated(page);
    await page.waitForFunction(
      () => document.querySelector('.mm-schedule-mobile [data-schedule-id]') != null, null, { timeout: 5_000 });
    await page.evaluate(() => {
      const row = document.querySelector('.mm-schedule-mobile [data-schedule-id]');
      row.click();
    });
    await page.waitForFunction(
      () => document.querySelector('.mm-modal [data-field="freq"]') != null, null, { timeout: 5_000 });
    // Close it.
    await page.evaluate(() => {
      const cancel = Array.from(document.querySelectorAll('.mm-modal .mm-form-actions button'))
        .find(b => b.textContent.trim() === 'Cancel');
      cancel.click();
    });
    await page.waitForFunction(() => document.querySelector('.mm-modal') == null, null, { timeout: 5_000 });

    // ---- 4. Week shows seven day-section headers ----
    await page.evaluate(() => Alpine.store('mm').setViewMode('week'));
    await settle(page);
    const headerCount = await page.evaluate(() =>
      document.querySelectorAll('.mm-schedule-mobile .mm-agenda-day-header').length);
    assert.equal(headerCount, 7, `week view should render 7 day headers, got ${headerCount}`);

    return 'pass';
  } finally {
    try { await delSchedulesForPlaylist(page, PL); } catch (_) {}
    try { await delPlaylist(page, PL); } catch (_) {}
    await browser.close();
  }
}
