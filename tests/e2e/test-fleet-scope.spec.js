/**
 * PR-13: fleet-action scope. Two surfaces:
 *   1. Toolbar dropdown ("Fleet:" scope selector) — picking a
 *      displayID scopes the next click on a fleet button.
 *   2. Track-header right-click — Login/Start/Stop/Reboot/Test items
 *      fire scoped to that track's displayID (no toolbar interaction
 *      required).
 *
 * Mocks window.sock so the test doesn't actually run any device
 * scripts. Asserts the captured RUN_SCRIPT frames carry the right
 * payload shape: {all:true,script} for "All devices", {displayID,
 * script} for scoped.
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

    // Mock sock so frames are captured instead of broadcast.
    await page.evaluate(() => {
      window.__sent = [];
      window.sock = { send: (frame) => { window.__sent.push(JSON.parse(frame)); } };
      if (typeof window.generateMessage !== 'function') {
        window.generateMessage = (dest, req, payload) =>
          JSON.stringify({ DEST: dest, REQUEST: req, PAYLOAD: payload });
      }
    });

    // Pick a non-empty group. Prefer one with ≤3 clients so the path
    // skips the confirm modal (simpler assertions); otherwise take
    // whichever non-empty group exists and traverse the confirm. We
    // require at least 1 client because fireFleetAction early-returns
    // with a warn-toast on 0 targets — no RUN_SCRIPT frame ever fires.
    const scoped = await page.evaluate(() => {
      const groups = Alpine.store('mm').displayGroups || [];
      const nonEmpty = groups.filter(g => g.clientCount > 0);
      if (nonEmpty.length === 0) return null;
      const small = nonEmpty.find(g => g.clientCount <= 3);
      const pick = small ?? nonEmpty[0];
      return { displayID: pick.displayID, clientCount: pick.clientCount, needsConfirm: pick.clientCount > 3 };
    });
    assert.ok(scoped, 'expected at least one non-empty display group on the server');

    // --- Case 1: toolbar dropdown → click Login → {displayID, script:"login"} ---
    await page.evaluate((did) => {
      const sel = document.querySelector('.mm-fleet-scope');
      sel.value = did;
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    }, scoped.displayID);

    await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('.mm-toolbar .btn'))
        .find(b => b.title && /Wake \+ unlock/i.test(b.title));
      btn.click();
    });

    if (scoped.needsConfirm) {
      // Confirm modal opened; click "Login X devices".
      await page.waitForSelector('.mm-fleet-confirm', { timeout: 5000 });
      await page.evaluate(() => {
        const btns = Array.from(document.querySelectorAll('.mm-fleet-confirm .btn-primary'));
        btns[btns.length - 1].click();
      });
    }

    await page.waitForFunction(() => (window.__sent || []).length >= 1, null, { timeout: 5000 });
    const firstFrame = await page.evaluate(() => window.__sent[0]);
    assert.equal(firstFrame.REQUEST, 'RUN_SCRIPT');
    assert.equal(firstFrame.PAYLOAD.displayID, scoped.displayID,
      `expected displayID=${scoped.displayID}, got payload=${JSON.stringify(firstFrame.PAYLOAD)}`);
    assert.equal(firstFrame.PAYLOAD.script, 'login');
    assert.ok(!('all' in firstFrame.PAYLOAD), 'scoped frame should NOT carry the {all:true} sentinel');

    // --- Case 2: reset scope to "All", click Start → {all:true, script:"start"} ---
    await page.evaluate(() => {
      window.__sent = [];
      const sel = document.querySelector('.mm-fleet-scope');
      sel.value = '';
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('.mm-toolbar .btn'))
        .find(b => b.title && /Open the display page/i.test(b.title));
      btn.click();
    });
    // ALL devices likely exceeds the 3-device threshold → confirm modal.
    const allCount = await page.evaluate(() => (Alpine.store('mm').displays || []).length);
    if (allCount > 3) {
      await page.waitForSelector('.mm-fleet-confirm', { timeout: 5000 });
      await page.evaluate(() => {
        const btns = Array.from(document.querySelectorAll('.mm-fleet-confirm .btn-primary'));
        btns[btns.length - 1].click();
      });
    }
    await page.waitForFunction(() => (window.__sent || []).length >= 1, null, { timeout: 5000 });
    const secondFrame = await page.evaluate(() => window.__sent[0]);
    assert.equal(secondFrame.PAYLOAD.all, true);
    assert.equal(secondFrame.PAYLOAD.script, 'start');
    assert.ok(!('displayID' in secondFrame.PAYLOAD), 'all-devices frame should NOT carry displayID');

    // --- Case 3: track-header right-click → "Stop" item → scoped frame ---
    await page.evaluate(() => { window.__sent = []; });
    await page.evaluate((did) => {
      const header = document.querySelector(`.mm-track-header[data-display-id="${did}"]`);
      const r = header.getBoundingClientRect();
      header.dispatchEvent(new MouseEvent('contextmenu', {
        bubbles: true, cancelable: true, clientX: r.left + 5, clientY: r.top + 5,
      }));
    }, scoped.displayID);
    await page.waitForFunction(() => {
      const m = document.getElementById('mmContextMenu');
      return m && m.style.display === 'block' && m.querySelectorAll('li').length > 0;
    }, null, { timeout: 5000 });

    // Verify the action items are present in the expected order.
    const labels = await page.evaluate(() =>
      Array.from(document.querySelectorAll('#mmContextMenu li:not(.mm-context-divider)'))
        .map(li => li.textContent.trim()));
    assert.deepEqual(labels.slice(0, 5), ['Login', 'Start', 'Stop', 'Reboot', 'Test']);

    // Click "Stop".
    await page.evaluate(() => {
      const li = Array.from(document.querySelectorAll('#mmContextMenu li'))
        .find(x => x.textContent.trim() === 'Stop');
      li.click();
    });
    if (scoped.needsConfirm) {
      await page.waitForSelector('.mm-fleet-confirm', { timeout: 5000 });
      await page.evaluate(() => {
        const btns = Array.from(document.querySelectorAll('.mm-fleet-confirm .btn-primary'));
        btns[btns.length - 1].click();
      });
    }
    await page.waitForFunction(() => (window.__sent || []).length >= 1, null, { timeout: 5000 });
    const thirdFrame = await page.evaluate(() => window.__sent[0]);
    assert.equal(thirdFrame.PAYLOAD.displayID, scoped.displayID);
    assert.equal(thirdFrame.PAYLOAD.script, 'stop');

    return 'pass';
  } finally { await browser.close(); }
}
