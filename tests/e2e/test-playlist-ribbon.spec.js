/**
 * PR-17: ribbon-style playlist editor.
 *
 * The existing test-playlist-editor.spec.js still covers the
 * "open + change a field + save" path (it kept working against
 * the rewritten editor since data-field input names are
 * preserved). This spec covers ribbon-specific behavior:
 *
 *   1. Open the editor against a multi-item playlist; assert each
 *      item renders as a clip with width proportional to duration.
 *   2. Click an item → it becomes selected (outlined) + sidebar
 *      populates with that item's fields.
 *   3. Reorder via synthetic dragstart/dragover/drop on the ribbon
 *      → the items[] array shifts and the saved playlist reflects
 *      the new order.
 *   4. Append from media bin: synthesize a media drag onto the
 *      ribbon → a new item is appended.
 *   5. Remove via the sidebar's Remove button.
 *   6. Loop toggle: change checkbox + Save → playlist.loop flips.
 *
 * Uses a temp __e2e_*.png + a __e2e_ribbon_* playlist that starts
 * with three items so reorder + width math have something to bite
 * on. Cleans up its own state.
 */
import { chromium } from 'playwright';
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { TIMELINE, waitForHydrated, cleanupE2eOrphans } from './helpers.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '..', '..');
const IMG_DIR = path.join(REPO_ROOT, 'media', 'server', 'images');
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=',
  'base64');

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const stamp = Date.now();
  const PL = '__e2e_ribbon_' + stamp;
  const EXTRA = `__e2e_ribbon_${stamp}_extra.png`;
  const EXTRA_URL = `/media/server/images/${EXTRA}`;

  fs.mkdirSync(IMG_DIR, { recursive: true });
  fs.writeFileSync(path.join(IMG_DIR, EXTRA), PNG);

  try {
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);
    // Cleanup leftover __e2e_ribbon playlists.
    await page.evaluate(async () => {
      const j = await (await fetch('/api/playlists')).json();
      for (const p of (j.playlists || [])) {
        if (p.name.startsWith('__e2e_ribbon_')) {
          await fetch('/api/playlists/' + encodeURIComponent(p.name), { method: 'DELETE' });
        }
      }
    });

    // Create the playlist with 3 items of different durations.
    await page.evaluate(async (pn) => {
      await fetch('/api/playlists', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: pn,
          items: [
            { file: '/media/server/videos/probe_test.mp4', duration: 10 },
            { file: '/media/server/videos/probe_test.mp4', duration: 30 },
            { file: '/media/server/videos/probe_test.mp4', duration: 5 },
          ],
          loop: false,
        }),
      });
      await Alpine.store('mm').hydrate();
    }, PL);

    // Open the editor by invoking the exported function directly —
    // the right-click route through the timeline context-menu is
    // covered by test-playlist-editor.spec.js.
    await page.evaluate(async (pn) => {
      const mod = await import('/js/timeline/modals/playlist-editor.js');
      mod.openPlaylistEditor(Alpine.store('mm'), pn, 0);
    }, PL);
    await page.waitForSelector('.mm-modal .mm-plr-ribbon', { timeout: 5000 });

    // --- Case 1: clips render with widths proportional to duration ---
    const widths = await page.evaluate(() => {
      const clips = Array.from(document.querySelectorAll('.mm-plr-clip'));
      return clips.map(c => Math.round(c.getBoundingClientRect().width));
    });
    assert.equal(widths.length, 3, 'expected 3 clips');
    // 30s clip should be widest, 5s narrowest. (MIN_CLIP_PX clamps tiny clips, but 5s * 6px/s = 30 < 60 → snaps to MIN_CLIP_PX, OK.)
    assert.ok(widths[1] > widths[0], `expected 30s clip (${widths[1]}) wider than 10s clip (${widths[0]})`);
    assert.ok(widths[1] > widths[2], `expected 30s clip (${widths[1]}) wider than 5s clip (${widths[2]})`);

    // --- Case 2: initial selection populates sidebar ---
    const initialSel = await page.evaluate(() => ({
      selectedIndex: Array.from(document.querySelectorAll('.mm-plr-clip'))
        .findIndex(c => c.classList.contains('mm-plr-clip-selected')),
      duration: document.querySelector('[data-field="duration"]').value,
    }));
    assert.equal(initialSel.selectedIndex, 0, 'expected first clip selected on open');
    assert.equal(initialSel.duration, '10', 'expected sidebar duration field to show 10');

    // Click the 2nd clip → select it; sidebar updates.
    await page.evaluate(() => {
      document.querySelectorAll('.mm-plr-clip')[1].dispatchEvent(
        new MouseEvent('click', { bubbles: true, cancelable: true }));
    });
    await page.waitForFunction(() =>
      document.querySelector('[data-field="duration"]').value === '30', null, { timeout: 5000 });

    // --- Case 3: reorder via drag — move item 0 (10s) to after item 2 (5s) ---
    // Drop X past the right edge of the last clip → drop index = 3.
    await page.evaluate(() => {
      const ribbon = document.querySelector('.mm-plr-ribbon');
      const clips = Array.from(ribbon.querySelectorAll('.mm-plr-clip'));
      const source = clips[0];
      const lastRect = clips[clips.length - 1].getBoundingClientRect();
      const dt = new DataTransfer();
      source.dispatchEvent(new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer: dt }));
      ribbon.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt, clientX: lastRect.right + 20, clientY: lastRect.top + 5 }));
      ribbon.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt, clientX: lastRect.right + 20, clientY: lastRect.top + 5 }));
      source.dispatchEvent(new DragEvent('dragend', { bubbles: true, cancelable: true, dataTransfer: dt }));
    });
    // After reorder, durations should be [30, 5, 10].
    const afterReorder = await page.evaluate(() =>
      Array.from(document.querySelectorAll('.mm-plr-clip-dur')).map(d => d.textContent));
    assert.match(afterReorder[0], /^30/);
    assert.match(afterReorder[1], /^5/);
    assert.match(afterReorder[2], /^10/);

    // --- Case 4: append from media bin via synthetic drag ---
    // Re-hydrate so the bin sees the extra.png we wrote on disk.
    await page.evaluate(() => Alpine.store('mm').hydrate());
    await page.waitForFunction((u) =>
      (Alpine.store('mm').media.images || []).includes(u), EXTRA_URL, { timeout: 5000 });

    // Use the dragstate.setDrag API the way the media bin would.
    await page.evaluate(async (u) => {
      const ds = await import('/js/timeline/drag/dragstate.js');
      ds.setDrag({ kind: 'media', file: u, duration: null });
      // Synthesize dragover + drop on the ribbon.
      const ribbon = document.querySelector('.mm-plr-ribbon');
      const r = ribbon.getBoundingClientRect();
      const dt = new DataTransfer();
      ribbon.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt, clientX: r.right - 10, clientY: r.top + 20 }));
      ribbon.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt, clientX: r.right - 10, clientY: r.top + 20 }));
    }, EXTRA_URL);
    // 4 clips now.
    await page.waitForFunction(() =>
      document.querySelectorAll('.mm-plr-clip').length === 4, null, { timeout: 5000 });
    // Selected = last (the newly added one); sidebar file should reflect it.
    const selectedFile = await page.evaluate(() => document.querySelector('[data-field="file"]').value);
    assert.equal(selectedFile, EXTRA_URL);

    // --- Case 5: remove via sidebar button ---
    const before5 = await page.evaluate(() => document.querySelectorAll('.mm-plr-clip').length);
    await page.evaluate(() => document.querySelector('[data-action="remove"]').click());
    await page.waitForFunction((expected) =>
      document.querySelectorAll('.mm-plr-clip').length === expected,
      before5 - 1, { timeout: 5000 });

    // --- Case 6: Loop toggle + Save ---
    await page.evaluate(() => {
      const cb = document.querySelector('.mm-plr-loop');
      cb.checked = true;
      cb.dispatchEvent(new Event('change', { bubbles: true }));
      document.querySelector('[data-action="save"]').click();
    });
    await page.waitForFunction((pn) => {
      const pl = Alpine.store('mm').playlists[pn];
      return pl && pl.loop === true && Array.isArray(pl.items) && pl.items.length === 3;
    }, PL, { timeout: 5000 });

    // The saved items should be in the post-reorder order: [30s, 5s, 10s].
    const saved = await page.evaluate((pn) =>
      Alpine.store('mm').playlists[pn].items.map(i => i.duration), PL);
    assert.deepEqual(saved, [30, 5, 10]);

    return 'pass';
  } finally {
    try {
      await page.evaluate(async (pn) => {
        await fetch('/api/playlists/' + encodeURIComponent(pn), { method: 'DELETE' });
      }, PL);
    } catch (_) { /* best effort */ }
    try { fs.rmSync(path.join(IMG_DIR, EXTRA)); } catch (_) {}
    await browser.close();
  }
}
