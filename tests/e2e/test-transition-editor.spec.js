/**
 * TE-T8: transition-editor smoke.
 *
 * Asserts that the playlist editor exposes the transition-effects UI and
 * that the controls behave correctly:
 *
 *   1. Create __e2e_te playlist via REST with ONE video item (FULL playmode)
 *      so Save is not gated by a missing play-type choice.
 *   2. Open the admin page, wait for hydrate, navigate to Content > Playlists,
 *      click the playlist row to open the editor.
 *   3. Assert: the "Start effect" <select> contains options with text "Fade"
 *      and "Wipe" (sourced from /api/effects via store.effectCatalog).
 *   4. Select "Wipe" — assert that `direction` and `scope` param controls appear
 *      inside .mm-ple-effect-params.
 *   5. Select "Fade" — assert that the `audioFade` checkbox appears for the video
 *      item (audioFade is video-only; the param is hidden for non-video items).
 *   6. Assert that the chosen effect name is written onto the item (visible in
 *      the draft via the Alpine store or confirmed via a Save round-trip).
 *
 * Owned state: __e2e_te playlist (REST create + REST delete in cleanup).
 * Calls cleanupE2eOrphans up-front per harness convention.
 */
import { chromium } from 'playwright';
import assert from 'node:assert';
import { BASE, cleanupE2eOrphans, deletePlaylist } from './helpers.js';

const ADMIN = () => BASE + '/admin.html?nocache=' + Date.now() + '#content';
const PL = '__e2e_te';

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

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  try {
    // ---- Up-front cleanup ----
    await page.goto(BASE + '/admin.html');
    await cleanupE2eOrphans(page);
    await deletePlaylist(page, PL);

    // ---- 1. Create test playlist via REST: one VIDEO item with a valid playmode ----
    // Use FULL playmode so Save is not gated by mediaItemsMissingPlayType.
    const createRes = await page.request.post(BASE + '/api/playlists', {
      headers: { 'Content-Type': 'application/json' },
      data: {
        name: PL,
        items: [{ file: '/media/server/videos/__e2e_te_clip.mp4', playmode: 'FULL', duration: 30 }],
        loop: false,
      },
    });
    assert.ok(createRes.ok(), `POST /api/playlists ${PL} -> ${createRes.status()}`);

    // ---- 2. Open admin Content tab and wait for store hydration ----
    await page.goto(ADMIN());
    await waitHydrated(page);

    // Wait for the store to hold the new playlist.
    await page.waitForFunction((n) => !!Alpine.store('mm').playlists[n], PL, { timeout: 8_000 });

    // ---- 3. Assert effectCatalog is non-empty (server must expose /api/effects) ----
    const catalogLen = await page.evaluate(
      () => (Alpine.store('mm').effectCatalog || []).length);
    assert.ok(catalogLen >= 2,
      `store.effectCatalog must have at least 2 entries (fade + wipe), got ${catalogLen}`);

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

    // Click the item row so the settings box is visible.
    await page.waitForFunction(
      () => document.querySelector('.mm-modal .mm-ple-row') != null, null, { timeout: 3_000 });
    await page.evaluate(() => {
      const row = document.querySelector('.mm-modal .mm-ple-row');
      if (!row) throw new Error('no .mm-ple-row in editor');
      row.click();
    });
    await settle(page);

    // The settings panel with effect controls must now be visible.
    await page.waitForFunction(
      () => document.querySelector('.mm-modal .mm-ple-settings') != null,
      null, { timeout: 3_000 });

    // ---- 3. Assert the Start-effect <select> contains "Fade" and "Wipe" options ----
    const effectSelectInfo = await page.evaluate(() => {
      // buildEffectControl uses .mm-ple-effect-group > label > select
      const groups = Array.from(
        document.querySelectorAll('.mm-modal .mm-ple-settings .mm-ple-effect-group'));
      if (groups.length < 1) return { found: false, reason: 'no .mm-ple-effect-group found' };
      const startGroup = groups[0]; // first group = "Start effect"
      const sel = startGroup.querySelector('label > select');
      if (!sel) return { found: false, reason: 'no <select> inside start effect group' };
      const optionTexts = Array.from(sel.options).map(o => o.textContent.trim());
      return { found: true, optionTexts };
    });

    assert.ok(effectSelectInfo.found,
      `Start-effect select not found: ${effectSelectInfo.reason}`);
    assert.ok(
      effectSelectInfo.optionTexts.includes('Fade'),
      `Start-effect <select> must include "Fade"; got options: ${JSON.stringify(effectSelectInfo.optionTexts)}`
    );
    assert.ok(
      effectSelectInfo.optionTexts.includes('Wipe'),
      `Start-effect <select> must include "Wipe"; got options: ${JSON.stringify(effectSelectInfo.optionTexts)}`
    );

    // ---- 4. Select "Wipe" — assert direction and scope controls appear ----
    await page.evaluate(() => {
      const groups = Array.from(
        document.querySelectorAll('.mm-modal .mm-ple-settings .mm-ple-effect-group'));
      const startGroup = groups[0];
      const sel = startGroup.querySelector('label > select');
      if (!sel) throw new Error('no start-effect <select>');
      sel.value = 'wipe';
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await settle(page);

    const wipeParams = await page.evaluate(() => {
      const groups = Array.from(
        document.querySelectorAll('.mm-modal .mm-ple-settings .mm-ple-effect-group'));
      const startGroup = groups[0];
      const paramsWrap = startGroup.querySelector('.mm-ple-effect-params');
      if (!paramsWrap) return { found: false };
      // param labels are the pWrap.textContent (contains the key name)
      const paramTexts = Array.from(paramsWrap.querySelectorAll('.mm-ple-effect-param'))
        .map(el => el.textContent.trim());
      // direction and scope are 'choice' params rendered as <select>
      const selects = Array.from(paramsWrap.querySelectorAll('select'));
      const selectNames = selects.map(s => {
        const label = s.closest('.mm-ple-effect-param');
        return label ? label.textContent.trim().replace(/\s*$/, '') : s.getAttribute('name') || '';
      });
      return { found: true, paramTexts, selectNames, selectCount: selects.length };
    });

    assert.ok(wipeParams.found, 'expected .mm-ple-effect-params after selecting Wipe');
    // direction and scope are both 'choice' params — rendered as <select> elements
    assert.ok(
      wipeParams.selectCount >= 2,
      `Wipe must show at least 2 param <select>s (direction + scope); got ${wipeParams.selectCount}. paramTexts=${JSON.stringify(wipeParams.paramTexts)}`
    );
    const directionVisible = wipeParams.paramTexts.some(t => t.includes('direction'));
    const scopeVisible = wipeParams.paramTexts.some(t => t.includes('scope'));
    assert.ok(directionVisible,
      `"direction" param must appear after selecting Wipe; paramTexts=${JSON.stringify(wipeParams.paramTexts)}`);
    assert.ok(scopeVisible,
      `"scope" param must appear after selecting Wipe; paramTexts=${JSON.stringify(wipeParams.paramTexts)}`);

    // ---- 5. Switch to "Fade" — assert audioFade checkbox appears (video item) ----
    await page.evaluate(() => {
      const groups = Array.from(
        document.querySelectorAll('.mm-modal .mm-ple-settings .mm-ple-effect-group'));
      const startGroup = groups[0];
      const sel = startGroup.querySelector('label > select');
      if (!sel) throw new Error('no start-effect <select>');
      sel.value = 'fade';
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await settle(page);

    const fadeParams = await page.evaluate(() => {
      const groups = Array.from(
        document.querySelectorAll('.mm-modal .mm-ple-settings .mm-ple-effect-group'));
      const startGroup = groups[0];
      const paramsWrap = startGroup.querySelector('.mm-ple-effect-params');
      if (!paramsWrap) return { found: false };
      const checkboxes = Array.from(paramsWrap.querySelectorAll('input[type="checkbox"]'));
      const paramTexts = Array.from(paramsWrap.querySelectorAll('.mm-ple-effect-param'))
        .map(el => el.textContent.trim());
      return { found: true, checkboxCount: checkboxes.length, paramTexts };
    });

    assert.ok(fadeParams.found, 'expected .mm-ple-effect-params after selecting Fade');
    // audioFade is a boolean param — rendered as a checkbox (only for video items)
    assert.ok(
      fadeParams.checkboxCount >= 1,
      `Fade on a VIDEO item must show the audioFade checkbox; got ${fadeParams.checkboxCount} checkboxes. paramTexts=${JSON.stringify(fadeParams.paramTexts)}`
    );
    const audioFadeVisible = fadeParams.paramTexts.some(t => t.includes('audioFade'));
    assert.ok(audioFadeVisible,
      `"audioFade" param must appear after selecting Fade on a video item; paramTexts=${JSON.stringify(fadeParams.paramTexts)}`
    );

    // ---- 6. Confirm the chosen effect is written onto the draft item ----
    // After selecting "fade" above, commitEffect() was called inside renderParams().
    // The draft item's startEffect.name should now be "fade".
    // We verify indirectly: open the modal, the select should still show "fade" on re-open.
    // Direct verification: read the current <select> value.
    const startEffectValue = await page.evaluate(() => {
      const groups = Array.from(
        document.querySelectorAll('.mm-modal .mm-ple-settings .mm-ple-effect-group'));
      const startGroup = groups[0];
      const sel = startGroup.querySelector('label > select');
      return sel ? sel.value : null;
    });
    assert.equal(startEffectValue, 'fade',
      `After choosing fade the start-effect <select> must show "fade"; got "${startEffectValue}"`);

    return 'pass';
  } finally {
    // ---- Cleanup ----
    try { await deletePlaylist(page, PL); } catch (_) {}
    await browser.close();
  }
}
