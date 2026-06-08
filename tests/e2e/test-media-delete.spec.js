/**
 * PR-16: delete a media file from the bin.
 *
 * Two cases:
 *   1. Happy: stage a temp __e2e_*.png on disk, hover its bin row,
 *      click ×, confirm → the file disappears from the bin and from
 *      the server's /api/media listing.
 *   2. 409 + refs: stage a second temp image, create a temp
 *      playlist that references it, click × → toast surfaces the
 *      blocking playlist name; the file stays.
 *
 * Test files are written directly to media/server/images via Node
 * fs (bypassing the /upload/image pipeline, which is exercised by
 * tests/unit/test_api_media.py). This keeps the spec focused on
 * the delete path and avoids upload-multipart flakes.
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
// Minimal valid PNG (1×1 transparent) — enough for the server to list it.
const PNG_BYTES = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=',
  'base64');

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const stamp = Date.now();
  const NAME1 = `__e2e_del_${stamp}_a.png`;
  const NAME2 = `__e2e_del_${stamp}_b.png`;
  const URL1 = `/media/server/images/${NAME1}`;
  const URL2 = `/media/server/images/${NAME2}`;
  const PL = `__e2e_del_pl_${stamp}`;

  // Stage files on disk before the page hydrates so the bin sees them.
  fs.mkdirSync(IMG_DIR, { recursive: true });
  fs.writeFileSync(path.join(IMG_DIR, NAME1), PNG_BYTES);
  fs.writeFileSync(path.join(IMG_DIR, NAME2), PNG_BYTES);

  try {
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);

    // Re-hydrate so the bin sees the new files.
    await page.evaluate(() => Alpine.store('mm').hydrate());
    await page.waitForFunction((u) =>
      (Alpine.store('mm').media.images || []).includes(u), URL1, { timeout: 5000 });

    // --- Case 1: happy delete via click ---
    // Accept the confirm() prompt.
    page.on('dialog', d => d.accept());
    await page.evaluate((u) => {
      const items = Array.from(document.querySelectorAll('.mm-bin-item-deletable'));
      const row = items.find(el => el.textContent.includes(u.split('/').pop()));
      if (!row) throw new Error('row not found');
      row.querySelector('.mm-bin-delete').click();
    }, URL1);

    // Wait for the store to drop the URL.
    await page.waitForFunction((u) =>
      !(Alpine.store('mm').media.images || []).includes(u), URL1, { timeout: 5000 });

    // Server-side confirm: GET /api/media no longer lists it.
    const stillThere = await page.evaluate(async (u) => {
      const j = await (await fetch('/api/media')).json();
      return j.images.includes(u);
    }, URL1);
    assert.equal(stillThere, false, 'expected file to be gone from /api/media after delete');
    // File gone off disk too.
    assert.ok(!fs.existsSync(path.join(IMG_DIR, NAME1)), 'expected file deleted from disk');

    // --- Case 2: 409 + refs ---
    await page.evaluate(async ({ pl, u }) => {
      await fetch('/api/playlists', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: pl, items: [{ file: u, duration: 5 }], loop: true }),
      });
      await Alpine.store('mm').hydrate();
    }, { pl: PL, u: URL2 });

    await page.evaluate((u) => {
      const items = Array.from(document.querySelectorAll('.mm-bin-item-deletable'));
      const row = items.find(el => el.textContent.includes(u.split('/').pop()));
      row.querySelector('.mm-bin-delete').click();
    }, URL2);

    // Wait for the toast that names the blocking playlist.
    await page.waitForFunction((pl) =>
      (Alpine.store('mm').toasts || []).some(t => /used by/i.test(t.msg) && t.msg.includes(pl)),
      PL, { timeout: 5000 });

    // File still present.
    assert.ok(fs.existsSync(path.join(IMG_DIR, NAME2)), 'expected referenced file to remain on disk');
    const stillInStore = await page.evaluate((u) =>
      (Alpine.store('mm').media.images || []).includes(u), URL2);
    assert.ok(stillInStore, 'expected store.media.images to still include the URL after rollback');

    return 'pass';
  } finally {
    // Cleanup: drop the test playlist + any leftover test files.
    try {
      await page.evaluate(async (pl) => {
        await fetch('/api/playlists/' + encodeURIComponent(pl), { method: 'DELETE' });
      }, PL);
    } catch (_) { /* best effort */ }
    try { fs.rmSync(path.join(IMG_DIR, NAME1)); } catch (_) {}
    try { fs.rmSync(path.join(IMG_DIR, NAME2)); } catch (_) {}
    await browser.close();
  }
}
