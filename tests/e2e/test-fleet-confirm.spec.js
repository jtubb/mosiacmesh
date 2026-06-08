/**
 * PR-4c gap-fix (spec §363): fleet-wide actions (Login/Start/Stop/
 * Reboot/Test) affecting more than 3 devices show a confirm modal
 * before firing. ≤3 devices: fires immediately, no prompt.
 *
 * This spec mocks window.sock so the test never actually broadcasts
 * a RUN_SCRIPT frame to real iPads. It asserts:
 *   - >3 devices: modal opens with the action verb + device count
 *   - Cancel: no frame sent
 *   - Confirm: exactly one RUN_SCRIPT frame with {all: true, script: 'login'}
 *   - ≤3 devices: no modal opens, frame sent immediately
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

    // Verify the fleet has >3 devices so the confirm path is exercised.
    const fleetSize = await page.evaluate(() => Alpine.store('mm').displays.length);
    assert.ok(fleetSize > 3, `expected fleet > 3 for this test, got ${fleetSize}`);

    // Mock window.sock so we capture frames instead of broadcasting.
    await page.evaluate(() => {
      window.__capturedFrames = [];
      window.sock = {
        send: (frame) => { window.__capturedFrames.push(frame); },
      };
      // generateMessage already exists; if not, stub it.
      if (typeof window.generateMessage !== 'function') {
        window.generateMessage = (dest, req, payload) => JSON.stringify({ DEST: dest, REQUEST: req, PAYLOAD: payload });
      }
    });

    // --- Case 1: Cancel ---
    // Click the 🔓 Login fleet button. textContent is just the emoji,
    // so look up by title.
    await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('button[title*="Wake + unlock"]'))[0];
      btn.click();
    });
    await page.waitForSelector('.mm-fleet-confirm', { timeout: 5000 });

    // Modal title contains 'Fleet action: Login'; summary text mentions the count + 'login'.
    const modalInfo = await page.evaluate(() => {
      const dialog = document.querySelector('.mm-modal');
      return {
        title: dialog.querySelector('h2')?.textContent || '',
        summary: dialog.querySelector('.mm-fleet-confirm-summary')?.textContent || '',
        cancelLabel: dialog.querySelector('.mm-form-actions button.btn-ghost')?.textContent || '',
        confirmLabel: dialog.querySelector('.mm-form-actions button.btn-primary')?.textContent || '',
      };
    });
    assert.match(modalInfo.title, /Login/, `expected title to mention Login, got "${modalInfo.title}"`);
    assert.match(modalInfo.summary, new RegExp(`${fleetSize} device`), `expected summary to mention ${fleetSize} devices, got "${modalInfo.summary}"`);
    assert.match(modalInfo.summary, /login/, 'expected summary to mention the action verb');

    // Click Cancel.
    await page.evaluate(() => {
      document.querySelector('.mm-modal .mm-form-actions button.btn-ghost').click();
    });
    await page.waitForFunction(() => !document.querySelector('.mm-modal'), null, { timeout: 5000 });
    let captured = await page.evaluate(() => window.__capturedFrames);
    assert.equal(captured.length, 0, `expected no frames after Cancel, got ${captured.length}`);

    // --- Case 2: Confirm ---
    await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('button[title*="Wake + unlock"]'))[0];
      btn.click();
    });
    await page.waitForSelector('.mm-fleet-confirm', { timeout: 5000 });
    await page.evaluate(() => {
      document.querySelector('.mm-modal .mm-form-actions button.btn-primary').click();
    });
    await page.waitForFunction(() => !document.querySelector('.mm-modal'), null, { timeout: 5000 });
    captured = await page.evaluate(() => window.__capturedFrames);
    assert.equal(captured.length, 1, `expected 1 RUN_SCRIPT frame after Confirm, got ${captured.length}`);
    const parsed = JSON.parse(captured[0]);
    assert.equal(parsed.REQUEST, 'RUN_SCRIPT');
    assert.deepEqual(parsed.PAYLOAD, { all: true, script: 'login' });

    // --- Case 3: ≤3 devices fires immediately, no modal ---
    // Single evaluate so reset + click + frame-check is atomic w.r.t.
    // page state. The "click" path is synchronous (sendFrame runs in
    // the same tick as the click), so we can read captured frames
    // immediately after.
    const case3 = await page.evaluate(() => {
      Alpine.store('mm').displays = Alpine.store('mm').displays.slice(0, 2);
      window.__capturedFrames = [];
      const btn = Array.from(document.querySelectorAll('button[title*="Wake + unlock"]'))[0];
      btn.click();
      return {
        capturedCount: window.__capturedFrames.length,
        modalOpen: !!document.querySelector('.mm-fleet-confirm'),
        capturedPayload: window.__capturedFrames[0] ? JSON.parse(window.__capturedFrames[0]).PAYLOAD : null,
      };
    });
    assert.ok(!case3.modalOpen, 'expected no confirm modal for ≤3 devices');
    assert.equal(case3.capturedCount, 1, `expected 1 RUN_SCRIPT frame for ≤3 devices (no prompt), got ${case3.capturedCount}`);
    assert.deepEqual(case3.capturedPayload, { all: true, script: 'login' });

    return 'pass';
  } finally { await browser.close(); }
}
