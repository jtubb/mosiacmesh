import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, cleanupE2eOrphans } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);

    // Open Profiles modal via toolbar button.
    await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Profiles'));
      btn.click();
    });
    await page.waitForSelector('.mm-profile-editor', { timeout: 5000 });

    // The default ipad1-ios5 profile should be in the list.
    const has = await page.evaluate(
      () => !!Array.from(document.querySelectorAll('.mm-pe-profiles li')).find(li => li.textContent.includes('iPad 1')));
    assert.ok(has, 'expected ipad1-ios5 (label "iPad 1 — iOS 5.1.1") in profile list');

    // Select it -> form populates -> change label -> save.
    await page.evaluate(() => {
      const li = Array.from(document.querySelectorAll('.mm-pe-profiles li')).find(l => l.textContent.includes('iPad 1'));
      li.click();
    });
    await page.waitForSelector('[data-field="label"]', { timeout: 5000 });
    const NEW_LABEL = '__e2e_label_' + Date.now();
    await page.evaluate((lbl) => {
      const root = document.querySelector('.mm-profile-editor');
      const inp = root.querySelector('[data-field="label"]');
      inp.value = lbl;
      inp.dispatchEvent(new Event('input', { bubbles: true }));
      root.querySelector('[data-action="save-form"]').click();
    }, NEW_LABEL);

    await page.waitForFunction(
      (lbl) => Alpine.store('mm').profiles['ipad1-ios5']?.label === lbl,
      NEW_LABEL, { timeout: 5000 });

    // Revert so we don't leave a noisy label on the server.
    await page.evaluate(async () => {
      const p = Alpine.store('mm').profiles['ipad1-ios5'];
      await Alpine.store('mm').updateProfile('ipad1-ios5', { ...p, label: 'iPad 1 — iOS 5.1.1' });
    });
    return 'pass';
  } finally { await browser.close(); }
}
