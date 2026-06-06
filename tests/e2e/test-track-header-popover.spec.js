/**
 * PR-4c gap-fix (spec §362): click a track header → popover lists the
 * clients in that group with per-client profileName dropdowns. Changing
 * a dropdown fires store.assignProfileToClient.
 *
 * This spec asserts the wiring: open the popover, find a client, change
 * its profile, verify the store + server agree. Reverts the change so
 * the baseline state isn't disturbed.
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

    // Pick the first track header that has at least one client.
    const target = await page.evaluate(() => {
      const store = Alpine.store('mm');
      const headers = Array.from(document.querySelectorAll('.mm-track-header'));
      for (const h of headers) {
        const id = h.dataset.displayId;
        const clients = store.displays.filter(d => d.displayID === id);
        if (clients.length > 0) {
          return { displayID: id, clientKey: clients[0].clientKey, originalProfile: clients[0].profileName || '' };
        }
      }
      return null;
    });
    assert.ok(target, 'expected at least one track header with clients in the fleet');

    // Click the track header.
    await page.evaluate((displayID) => {
      const header = Array.from(document.querySelectorAll('.mm-track-header'))
        .find(h => h.dataset.displayId === displayID);
      header.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    }, target.displayID);
    await page.waitForFunction(
      () => document.getElementById('mmTrackHeaderPopover').style.display === 'block',
      null, { timeout: 5000 });

    // Verify the popover lists at least one client + has a profile select for it.
    const popoverState = await page.evaluate((clientKey) => {
      const pop = document.getElementById('mmTrackHeaderPopover');
      const sel = pop.querySelector(`select[data-client-key="${clientKey}"]`);
      if (!sel) return { error: 'no select for client', popText: pop.textContent.slice(0, 200) };
      return {
        optionValues: Array.from(sel.options).map(o => o.value),
        currentValue: sel.value,
        headerText: pop.querySelector('.mm-thp-header strong')?.textContent || '',
      };
    }, target.clientKey);
    assert.ok(popoverState.optionValues, popoverState.error);
    assert.ok(popoverState.optionValues.includes(''), 'expected (auto-match) sentinel option');
    assert.ok(popoverState.optionValues.includes('ipad1-ios5'), 'expected ipad1-ios5 profile in dropdown');
    assert.match(popoverState.headerText, /Profile overrides for/, 'expected popover header label');

    // Change to ipad1-ios5 and assert the store updates + server agrees.
    await page.evaluate((clientKey) => {
      const sel = document.querySelector(`#mmTrackHeaderPopover select[data-client-key="${clientKey}"]`);
      sel.value = 'ipad1-ios5';
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    }, target.clientKey);
    await page.waitForFunction(
      (clientKey) => {
        const c = Alpine.store('mm').displays.find(d => d.clientKey === clientKey);
        return c && c.profileName === 'ipad1-ios5';
      }, target.clientKey, { timeout: 5000 });

    // Revert: set back to whatever was originally there.
    await page.evaluate(async ({ clientKey, original }) => {
      await Alpine.store('mm').assignProfileToClient(clientKey, original);
    }, { clientKey: target.clientKey, original: target.originalProfile });
    await page.waitForFunction(
      ({ clientKey, original }) => {
        const c = Alpine.store('mm').displays.find(d => d.clientKey === clientKey);
        return c && (c.profileName || '') === (original || '');
      }, { clientKey: target.clientKey, original: target.originalProfile }, { timeout: 5000 });

    // Esc dismisses the popover.
    await page.evaluate(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    await page.waitForFunction(
      () => document.getElementById('mmTrackHeaderPopover').style.display === 'none',
      null, { timeout: 5000 });

    return 'pass';
  } finally { await browser.close(); }
}
