/**
 * PR-9: right-click on a track header → 'Reload group' context menu
 * item → sends RELOAD via SockJS scoped to that displayID. Spec §361.
 *
 * Mocks window.sock to capture frames so the spec never actually
 * reloads the real iPads in the fleet. Asserts:
 *   - Right-click opens the context menu with one item: 'Reload group'.
 *   - Clicking it sends a RELOAD frame with the correct displayID.
 *   - Click outside dismisses the menu.
 */
import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, cleanupE2eOrphans } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);

    // Mock window.sock so the test doesn't broadcast to real displays.
    await page.evaluate(() => {
      window.__captured = [];
      window.sock = { send: (frame) => { window.__captured.push(frame); } };
      if (typeof window.generateMessage !== 'function') {
        window.generateMessage = (dest, req, payload) => JSON.stringify({ DEST: dest, REQUEST: req, PAYLOAD: payload });
      }
    });

    // Pick the first track header with a displayID.
    const target = await page.evaluate(() => {
      const h = document.querySelector('.mm-track-header[data-display-id]');
      return h ? h.dataset.displayId : null;
    });
    assert.ok(target, 'expected at least one track header in the timeline');

    // Right-click it.
    await page.evaluate((displayID) => {
      const header = Array.from(document.querySelectorAll('.mm-track-header'))
        .find(h => h.dataset.displayId === displayID);
      const r = header.getBoundingClientRect();
      header.dispatchEvent(new MouseEvent('contextmenu', {
        bubbles: true, cancelable: true, clientX: r.left + 5, clientY: r.top + 5,
      }));
    }, target);
    await page.waitForFunction(
      () => {
        const m = document.getElementById('mmContextMenu');
        return m && m.style.display === 'block' && m.querySelector('li');
      }, null, { timeout: 5000 });

    // Menu items: 5 fleet actions (PR-13) + divider + Reload group
    // (PR-9) + Delete group (PR-12). Asserts all are present in the
    // expected order so future additions surface as a fail-noisy diff.
    // .mm-context-divider is a visual separator (no action), filtered
    // out so the label list compares cleanly.
    const itemsAfterOpen = await page.evaluate(() =>
      Array.from(document.querySelectorAll('#mmContextMenu li:not(.mm-context-divider)'))
        .map(li => li.textContent.trim()));
    assert.deepEqual(itemsAfterOpen, [
      'Login', 'Start', 'Stop', 'Reboot', 'Test',
      'Reload group', 'Delete group',
    ]);

    // Click "Reload group" (the first li). Don't grab li:first-child
    // because the menu order is asserted above; rely on textContent.
    await page.evaluate(() => {
      const li = Array.from(document.querySelectorAll('#mmContextMenu li'))
        .find(x => x.textContent.trim() === 'Reload group');
      li.click();
    });

    // Verify SockJS frame was sent.
    const sentFrames = await page.evaluate(() => window.__captured);
    assert.equal(sentFrames.length, 1, `expected 1 RELOAD frame, got ${sentFrames.length}`);
    const parsed = JSON.parse(sentFrames[0]);
    assert.equal(parsed.REQUEST, 'RELOAD');
    assert.deepEqual(parsed.PAYLOAD, { displayID: target });

    // Menu should be closed after click.
    const menuOpenAfter = await page.evaluate(() =>
      document.getElementById('mmContextMenu').style.display === 'block');
    assert.ok(!menuOpenAfter, 'expected menu to close after picking an item');

    // Outside-click dismissal: open again + click body.
    await page.evaluate((displayID) => {
      const header = Array.from(document.querySelectorAll('.mm-track-header'))
        .find(h => h.dataset.displayId === displayID);
      const r = header.getBoundingClientRect();
      header.dispatchEvent(new MouseEvent('contextmenu', {
        bubbles: true, cancelable: true, clientX: r.left + 5, clientY: r.top + 5,
      }));
    }, target);
    await page.waitForFunction(
      () => document.getElementById('mmContextMenu').style.display === 'block',
      null, { timeout: 5000 });
    await page.evaluate(() => {
      document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, clientX: 5, clientY: 5 }));
    });
    const menuOpenAfterOutsideClick = await page.evaluate(() =>
      document.getElementById('mmContextMenu').style.display === 'block');
    assert.ok(!menuOpenAfterOutsideClick, 'expected menu to close on outside click');

    return 'pass';
  } finally { await browser.close(); }
}
