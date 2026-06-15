import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, cleanupE2eOrphans } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);

    // Open via toolbar button.
    await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Calibrate'));
      btn.click();
    });
    await page.waitForSelector('.mm-calibration', { timeout: 5000 });

    // Verify the dropdown has at least one group from store.displays.
    const groupCount = await page.evaluate(
      () => document.querySelectorAll('.mm-calibration [data-field="group"] option').length);
    assert.ok(groupCount >= 1, `expected ≥1 group in dropdown, got ${groupCount}`);

    // Verify the three steps + upload input are present.
    const steps = await page.evaluate(
      () => document.querySelectorAll('.mm-calibration .steps li').length);
    assert.equal(steps, 3);
    const hasUpload = await page.evaluate(
      () => !!document.querySelector('.mm-calibration [data-field="photo"]'));
    assert.ok(hasUpload, 'expected file input in modal');

    return 'pass';
  } finally { await browser.close(); }
}
