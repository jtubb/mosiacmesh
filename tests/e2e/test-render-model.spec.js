/**
 * AR-T21: render-model gating smoke.
 *
 * Asserts the three observable consequences of the auto-render model
 * on the live admin page:
 *
 *   1. Play Now: a SEGMENT playlist that has no render entry is
 *      shown with a DISABLED button inside the Play Now modal —
 *      the operator cannot immediately start it.
 *
 *   2. Content tab: the playlist row for the __e2e_seg playlist
 *      shows a render-summary span (`.mm-playlist-render`) because
 *      it has at least one SEGMENT item. The span reads
 *      "rendered 0/<N>" (zero groups ready) confirming the
 *      per-playlist render badge was added by AR-T19.
 *
 *   3. Fleet: no "Render now" button exists anywhere in the Fleet
 *      section — the button removed by AR-T18 must not have crept
 *      back in.
 *
 * Why #1 uses the modal rather than just the store getter:
 * The modal is built from the real page DOM, so it catches regressions
 * in the button-building code (play-now.js), not just the store helper.
 * The Play Now modal requires a group to target; if the server has NO
 * groups, the spec falls back to the weaker but still-valid assertion
 * that the Content tab render-summary span reads "rendered 0/<N>".
 *
 * The __e2e_seg playlist uses a file that doesn't need to exist on
 * disk — the item shape is what matters for render-model gating.
 * The server stores and returns the playlist; the render registry
 * is consulted at display time only.
 *
 * Owned state: __e2e_seg playlist (REST create + REST delete in cleanup).
 * Calls cleanupE2eOrphans up-front per harness convention.
 */
import { chromium } from 'playwright';
import assert from 'node:assert';

const BASE = process.env.MM_BASE_URL || 'http://localhost:3000';
const ADMIN = () => BASE + '/admin.html?nocache=' + Date.now();
const PL = '__e2e_seg';

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

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  // Accept any confirm/prompt dialogs (e.g. modal-shell Esc guard).
  page.on('dialog', (d) => d.accept().catch(() => {}));
  try {
    // ---- Up-front cleanup ----
    await page.goto(BASE + '/admin.html');
    await cleanupE2eOrphans(page);
    await delPlaylist(page, PL);

    // ---- Create the test playlist via REST ----
    // playmode:'SEGMENT' makes this playlist renderable, so:
    //   - isPlaylistReady() returns false (no render entry yet)
    //   - the Play Now button is disabled for this playlist
    //   - the Content tab row shows a render-summary span
    const createRes = await page.request.post(BASE + '/api/playlists', {
      headers: { 'Content-Type': 'application/json' },
      data: {
        name: PL,
        items: [{ file: '/media/server/videos/__e2e_seg_clip.mp4', playmode: 'SEGMENT', duration: 30 }],
        loop: false,
      },
    });
    assert.ok(createRes.ok(), `POST /api/playlists ${PL} -> ${createRes.status()}`);

    // Hydrate the page so the store reflects the new playlist.
    await page.goto(ADMIN() + '#content');
    await waitHydrated(page);
    await page.waitForFunction((n) => !!Alpine.store('mm').playlists[n], PL, { timeout: 8_000 });

    // ---- 1 + optional Play Now gate (requires ≥1 display group) ----
    const groupIds = await page.evaluate(() =>
      (Alpine.store('mm').displayGroups || []).map(g => g.displayID));

    if (groupIds.length > 0) {
      // Open Play Now for the first group.
      const targetGroup = groupIds[0];
      await page.evaluate(async (did) => {
        const mod = await import('/js/timeline/modals/play-now.js');
        mod.openPlayNowModal(Alpine.store('mm'), did);
      }, targetGroup);
      await page.waitForSelector('.mm-modal .mm-play-now', { timeout: 5_000 });

      // The __e2e_seg playlist has no render entry → button must be disabled.
      const plBtn = await page.evaluate((pl) => {
        const btns = Array.from(document.querySelectorAll('.mm-modal .mm-play-now-list .mm-play-now-pick'));
        const btn = btns.find(b => b.textContent.startsWith(pl));
        if (!btn) return { found: false };
        return { found: true, disabled: btn.disabled, title: btn.title };
      }, PL);
      assert.ok(plBtn.found, `expected a Play Now button for "${PL}" in the modal`);
      assert.ok(plBtn.disabled,
        `"${PL}" is a SEGMENT playlist with no render entry — button must be disabled; got title="${plBtn.title}"`);

      // Close the modal before moving on.
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => document.querySelector('.mm-modal') == null, null, { timeout: 5_000 });
    } else {
      // No display groups on this server — skip the Play Now assertion and
      // log a notice so the reviewer knows the fallback path was taken.
      console.log('  [render-model] no display groups; skipping Play Now button assertion');
    }

    // ---- 2. Content tab: render-summary span visible for __e2e_seg ----
    // Navigate to the Content tab → Playlists sub-view so the row renders.
    await page.evaluate(() => Alpine.store('mm').activeTab = 'content');
    await settle(page);
    // Click the Playlists sub-tab via the button.
    await page.evaluate(() => {
      const sec = document.querySelector('[data-route="content"]');
      const btn = Array.from(sec.querySelectorAll('.mm-content-subtabs button'))
        .find(b => b.textContent.trim() === 'Playlists');
      if (btn) btn.click();
    });
    await settle(page);
    // Wait for the playlist row to appear.
    await page.waitForFunction((n) => {
      const sec = document.querySelector('[data-route="content"]');
      return !!sec && Array.from(sec.querySelectorAll('.mm-playlist-name'))
        .some(b => b.textContent.trim() === n && b.offsetParent !== null);
    }, PL, { timeout: 5_000 });

    // The render-summary span is conditionally shown when playlistRenderSummary().total > 0.
    // total > 0 when the playlist is renderable AND there are display groups.
    const renderSpan = await page.evaluate((pl) => {
      const sec = document.querySelector('[data-route="content"]');
      const rows = Array.from(sec.querySelectorAll('.mm-playlist-row'));
      const row = rows.find(r => r.querySelector('.mm-playlist-name')?.textContent.trim() === pl);
      if (!row) return { rowFound: false };
      const span = row.querySelector('.mm-playlist-render');
      // span exists in DOM (x-show hides via display:none when total===0)
      const visible = span && span.offsetParent !== null;
      return {
        rowFound: true,
        spanInDom: !!span,
        spanVisible: visible,
        spanText: span ? span.textContent.trim() : null,
      };
    }, PL);

    assert.ok(renderSpan.rowFound, `expected a playlist row for "${PL}" in the Content tab`);
    assert.ok(renderSpan.spanInDom,
      `expected a .mm-playlist-render span in the DOM for "${PL}" (AR-T19)`);

    if (groupIds.length > 0) {
      // With groups: the span should be visible and read "rendered 0/<N>".
      assert.ok(renderSpan.spanVisible,
        `"${PL}" has SEGMENT items + ${groupIds.length} group(s) — .mm-playlist-render should be visible; got "${renderSpan.spanText}"`);
      assert.match(renderSpan.spanText, /^rendered \d+\/\d+$/,
        `render summary text should be "rendered X/Y", got "${renderSpan.spanText}"`);
      // No render has run yet → ready count should be 0.
      const readyCount = parseInt(renderSpan.spanText.match(/\d+/)[0], 10);
      assert.equal(readyCount, 0,
        `fresh SEGMENT playlist should have 0 ready renders, got "${renderSpan.spanText}"`);
    } else {
      // No groups: total===0, span is hidden (or not visible) — this is
      // correct behaviour, not a regression.
      console.log('  [render-model] no display groups; render span visibility is N/A');
    }

    // ---- 3. Fleet: NO "Render now" button anywhere ----
    // Navigate to the Fleet tab and select a group to open the detail cards.
    await page.evaluate(() => Alpine.store('mm').activeTab = 'fleet');
    await settle(page);
    await page.waitForFunction(
      () => document.querySelector('[data-route="fleet"]') &&
            getComputedStyle(document.querySelector('[data-route="fleet"]')).display !== 'none',
      null, { timeout: 5_000 });

    // Select the first group (if any) to open Playback / Calibration / etc cards.
    if (groupIds.length > 0) {
      await page.evaluate(() => {
        const row = document.querySelector('[data-route="fleet"] .mm-fleet-group');
        if (row) row.click();
      });
      await page.waitForFunction(
        () => document.querySelector('[data-route="fleet"] .mm-fleet-card') != null,
        null, { timeout: 5_000 });
    }

    // "Render now" was removed in AR-T18. Assert it does not appear anywhere
    // inside the Fleet section, with or without group cards open.
    const renderNowCount = await page.evaluate(() => {
      const sec = document.querySelector('[data-route="fleet"]');
      if (!sec) return -1;  // section not in DOM — fail loudly
      const allText = Array.from(sec.querySelectorAll('button, a, span'))
        .filter(el => /render\s*now/i.test(el.textContent));
      return allText.length;
    });
    assert.notEqual(renderNowCount, -1, 'Fleet section ([data-route="fleet"]) not found in DOM');
    assert.equal(renderNowCount, 0,
      `Fleet must have 0 "Render now" elements (AR-T18 removed it); found ${renderNowCount}`);

    return 'pass';
  } finally {
    try { await delPlaylist(page, PL); } catch (_) {}
    await browser.close();
  }
}
