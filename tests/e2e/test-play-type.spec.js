/**
 * PT-T8: play-type selector force-a-choice smoke.
 *
 * Asserts that the playlist editor's Save button is gated until every
 * media item has a valid play type chosen:
 *
 *   1. Create __e2e_pt playlist via REST with ONE video item that has
 *      NO playmode — the "unchosen" state.
 *   2. Open the playlist editor for __e2e_pt (Content tab → Playlists
 *      sub-view → click the playlist name), click its item row.
 *   3. Assert: Save is disabled AND the item row carries .mm-ple-warn ⚠.
 *   4. Choose "Mesh" in the play-type <select> (value "SEGMENT").
 *   5. Assert: Save is enabled AND .mm-ple-warn is gone from the row.
 *
 * Owned state: __e2e_pt playlist (REST create + REST delete in cleanup).
 * Calls cleanupE2eOrphans up-front per harness convention.
 */
import { chromium } from 'playwright';
import assert from 'node:assert';

const BASE = process.env.MM_BASE_URL || 'http://localhost:3000';
const ADMIN = () => BASE + '/admin.html?nocache=' + Date.now() + '#content';
const PL = '__e2e_pt';

// Wait until Alpine is up and the mm store has finished its initial hydrate.
async function waitHydrated(page) {
  await page.waitForFunction(
    () => window.Alpine && Alpine.store('mm') && Alpine.store('mm').hydrated === true,
    null, { timeout: 15_000 });
  // Let the post-hydrate reactive re-render commit before measuring.
  await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
}

// Alpine flushes x-for/x-show on a microtask after a reactive change; wait
// two animation frames so the DOM reflects the latest store/component state.
async function settle(page) {
  await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
}

// Best-effort cleanup of any leftover __e2e_* schedules + playlists from prior
// failed runs. Mirrors the pattern in test-render-model.spec.js.
async function cleanupE2eOrphans(page) {
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

async function delPlaylist(page, name) {
  await page.request.delete(BASE + '/api/playlists/' + encodeURIComponent(name));
}

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  try {
    // ---- Up-front cleanup ----
    await page.goto(BASE + '/admin.html');
    await cleanupE2eOrphans(page);
    await delPlaylist(page, PL);

    // ---- 1. Create test playlist via REST: one video item with NO playmode ----
    const createRes = await page.request.post(BASE + '/api/playlists', {
      headers: { 'Content-Type': 'application/json' },
      data: {
        name: PL,
        items: [{ file: '/media/server/videos/__e2e_pt_clip.mp4' }],
        loop: false,
      },
    });
    assert.ok(createRes.ok(), `POST /api/playlists ${PL} -> ${createRes.status()}`);

    // ---- 2. Open the playlist editor via Content tab → Playlists sub-view ----
    await page.goto(ADMIN());
    await waitHydrated(page);
    // Wait for the store to hold the new playlist.
    await page.waitForFunction((n) => !!Alpine.store('mm').playlists[n], PL, { timeout: 8_000 });

    // Switch to the Playlists sub-tab inside the Content section.
    await page.evaluate(() => {
      const sec = document.querySelector('[data-route="content"]');
      const btn = Array.from(sec.querySelectorAll('.mm-content-subtabs button'))
        .find(b => b.textContent.trim() === 'Playlists');
      if (!btn) throw new Error('no Playlists sub-tab button');
      btn.click();
    });
    await settle(page);

    // Wait for our playlist row to appear, then click it to open the editor.
    await page.waitForFunction((n) => {
      const sec = document.querySelector('[data-route="content"]');
      return !!sec && Array.from(sec.querySelectorAll('.mm-playlist-name'))
        .some(b => b.textContent.trim() === n && b.offsetParent !== null);
    }, PL, { timeout: 5_000 });

    await page.evaluate((n) => {
      const sec = document.querySelector('[data-route="content"]');
      const row = Array.from(sec.querySelectorAll('.mm-playlist-name'))
        .find(b => b.textContent.trim() === n);
      if (!row) throw new Error('playlist row not found: ' + n);
      row.click();
    }, PL);

    // The playlist editor modal should open.
    await page.waitForFunction(
      () => document.querySelector('.mm-modal .mm-ple') != null, null, { timeout: 5_000 });

    // The item row should auto-select (it's the only item; selectedIdx = 0 after open).
    // But let's click it explicitly to ensure the settings panel is shown.
    await page.waitForFunction(
      () => document.querySelector('.mm-modal .mm-ple-row') != null, null, { timeout: 3_000 });
    await page.evaluate(() => {
      const row = document.querySelector('.mm-modal .mm-ple-row');
      if (!row) throw new Error('no .mm-ple-row in editor');
      row.click();
    });
    await settle(page);

    // ---- 3. Assert Save disabled + ⚠ warning present ----
    const beforeState = await page.evaluate(() => {
      const save = document.querySelector('.mm-modal .mm-form-actions .btn.btn-primary');
      const warn = document.querySelector('.mm-modal .mm-ple-row .mm-ple-warn');
      return {
        saveDisabled: save ? save.disabled : null,
        saveFound: !!save,
        warnFound: !!warn,
        warnText: warn ? warn.textContent.trim() : null,
      };
    });

    assert.ok(beforeState.saveFound, 'expected a Save button (.btn.btn-primary) inside the modal');
    assert.ok(
      beforeState.saveDisabled,
      `Save must be disabled when the item has no play type; got disabled=${beforeState.saveDisabled}`
    );
    assert.ok(
      beforeState.warnFound,
      'expected a .mm-ple-warn ⚠ marker on the item row when play type is unset'
    );

    // The settings panel should show a play-type <select> for this video item.
    await page.waitForFunction(
      () => document.querySelector('.mm-modal .mm-ple-settings select') != null,
      null, { timeout: 3_000 });

    // ---- 4. Choose "Mesh" (value="SEGMENT") in the play-type <select> ----
    await page.evaluate(() => {
      const sel = document.querySelector('.mm-modal .mm-ple-settings select');
      if (!sel) throw new Error('no play-type <select> in the settings panel');
      sel.value = 'SEGMENT';
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await settle(page);

    // ---- 5. Assert Save enabled + ⚠ gone ----
    const afterState = await page.evaluate(() => {
      const save = document.querySelector('.mm-modal .mm-form-actions .btn.btn-primary');
      const warn = document.querySelector('.mm-modal .mm-ple-row .mm-ple-warn');
      return {
        saveDisabled: save ? save.disabled : null,
        saveFound: !!save,
        warnFound: !!warn,
      };
    });

    assert.ok(afterState.saveFound, 'expected the Save button to still be present after choosing SEGMENT');
    assert.ok(
      !afterState.saveDisabled,
      `Save must be enabled after choosing a play type (SEGMENT); got disabled=${afterState.saveDisabled}`
    );
    assert.ok(
      !afterState.warnFound,
      'the .mm-ple-warn ⚠ marker must be gone after a valid play type is selected'
    );

    return 'pass';
  } finally {
    // ---- Cleanup ----
    try { await delPlaylist(page, PL); } catch (_) {}
    await browser.close();
  }
}
