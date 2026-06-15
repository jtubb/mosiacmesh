/**
 * PR-18: in-modal media picker + video-duration cap.
 *
 *   1. Picker: open the editor against an empty-ish playlist;
 *      assert the picker lists media; click the "+" on a row
 *      → the item is appended and selected. Search filters the
 *      list.
 *   2. Cap: pick a video with a probed natural length (probe_test.mp4
 *      is 30s on the dev server's fixture). Set the sidebar
 *      duration field to 99 → on blur, the field snaps to 30.
 *      Save → the persisted item.duration is 30, not 99.
 *
 * Same picker is also used by the drag-from-bin path (test-playlist-
 * ribbon covers that). This spec exercises the picker UI directly.
 */
import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, cleanupE2eOrphans } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const stamp = Date.now();
  const PL = '__e2e_add_cap_' + stamp;
  const VIDEO = '/media/server/videos/probe_test.mp4';
  const VIDEO_LEN = 30;   // matches the dev server's probe; if probe changes the test fails loudly

  try {
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);

    // Confirm the dev server's probe of the test video is the
    // expected length — if not, fail the spec with a useful message
    // rather than silently testing the wrong assumption.
    const probed = await page.evaluate((u) =>
      Alpine.store('mm').media.videoDurations?.[u] ?? null, VIDEO);
    assert.equal(probed, VIDEO_LEN,
      `expected ${VIDEO} probed length === ${VIDEO_LEN}s, got ${probed} (server fixture changed?)`);

    // Create the playlist with one image item so the editor has
    // something to open against.
    await page.evaluate(async (pn) => {
      await fetch('/api/playlists', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: pn, loop: false,
          items: [{ file: '/media/server/images/probe_test.png', duration: 5 }],
        }),
      });
      await Alpine.store('mm').hydrate();
    }, PL);

    await page.evaluate(async (pn) => {
      const mod = await import('/js/timeline/modals/playlist-editor.js');
      mod.openPlaylistEditor(Alpine.store('mm'), pn, 0);
    }, PL);
    await page.waitForSelector('.mm-modal .mm-plr-ribbon', { timeout: 5000 });

    // --- Case 1a: picker populated ---
    const pickerCount = await page.evaluate(() =>
      document.querySelectorAll('.mm-plr-picker-row').length);
    assert.ok(pickerCount > 0, `expected at least one picker row, got ${pickerCount}`);

    // --- Case 1b: search filters ---
    await page.evaluate(() => {
      const s = document.querySelector('.mm-plr-picker-search');
      s.value = 'probe_test';
      s.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await page.waitForFunction(() => {
      const rows = Array.from(document.querySelectorAll('.mm-plr-picker-row'));
      return rows.length > 0 && rows.every(r => /probe_test/i.test(r.textContent));
    }, null, { timeout: 5000 });

    // --- Case 1c: click "+" on the video row → item appended ---
    const ribbonBefore = await page.evaluate(() =>
      document.querySelectorAll('.mm-plr-clip').length);
    await page.evaluate((u) => {
      const row = Array.from(document.querySelectorAll('.mm-plr-picker-row'))
        .find(r => r.dataset.url === u);
      row.querySelector('.mm-plr-picker-add').click();
    }, VIDEO);
    await page.waitForFunction((expected) =>
      document.querySelectorAll('.mm-plr-clip').length === expected,
      ribbonBefore + 1, { timeout: 5000 });
    // The new item should be selected; sidebar file should match the video.
    const selFile = await page.evaluate(() =>
      document.querySelector('[data-field="file"]').value);
    assert.equal(selFile, VIDEO);
    // Newly-added video item should have duration = probed length.
    const initialDur = await page.evaluate(() =>
      document.querySelector('[data-field="duration"]').value);
    assert.equal(initialDur, String(VIDEO_LEN), `expected new video item duration to default to probed length ${VIDEO_LEN}`);

    // --- Case 2a: sidebar shows max + hint for the selected video ---
    const durFieldState = await page.evaluate(() => {
      const f = document.querySelector('[data-field="duration"]');
      return { max: f.getAttribute('max'), hint: document.querySelector('[data-field="duration-hint"]').textContent };
    });
    assert.equal(durFieldState.max, String(VIDEO_LEN));
    assert.match(durFieldState.hint, /max 30s/);

    // --- Case 2b: type 99 → input handler clamps to 30 in the draft ---
    await page.evaluate(() => {
      const f = document.querySelector('[data-field="duration"]');
      f.value = '99';
      f.dispatchEvent(new Event('input', { bubbles: true }));
      f.dispatchEvent(new Event('blur', { bubbles: true }));
    });
    // After blur, field should snap to 30.
    const fieldAfterBlur = await page.evaluate(() =>
      document.querySelector('[data-field="duration"]').value);
    assert.equal(fieldAfterBlur, String(VIDEO_LEN), 'expected field to snap to video length after blur');

    // --- Case 2c: Save → persisted item.duration === 30 ---
    await page.evaluate(() =>
      document.querySelector('[data-action="save"]').click());
    await page.waitForFunction(({ pn, u }) => {
      const pl = Alpine.store('mm').playlists[pn];
      return pl && pl.items.some(i => i.file === u);
    }, { pn: PL, u: VIDEO }, { timeout: 5000 });
    const persisted = await page.evaluate(({ pn, u }) => {
      const pl = Alpine.store('mm').playlists[pn];
      return pl.items.find(i => i.file === u);
    }, { pn: PL, u: VIDEO });
    assert.equal(persisted.duration, VIDEO_LEN,
      `expected persisted duration ${VIDEO_LEN}, got ${persisted.duration}`);

    return 'pass';
  } finally {
    try {
      await page.evaluate(async (pn) => {
        await fetch('/api/playlists/' + encodeURIComponent(pn), { method: 'DELETE' });
      }, PL);
    } catch (_) {}
    await browser.close();
  }
}
