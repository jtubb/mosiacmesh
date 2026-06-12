/**
 * Section 4 — the Fleet destination.
 *
 * Drives the real admin page:
 *   1. Desktop: the groups list renders one row per display group; selecting
 *      a group shows the detail cards (Playback / Calibration / Device scripts
 *      / Devices).
 *   1b. Selecting a group that HAS devices renders device rows with Profile +
 *      Group dropdowns (non-mutating — we don't change any assignment).
 *   2. Create a __e2e_ group via "+ New group" -> it appears -> Delete group
 *      -> gone (REST round-trip).
 *   3. Mobile: at phone width the list shows; selecting a group opens the
 *      detail sheet; "‹ Fleet" returns to the list.
 *
 * Owns its own state: a uniquely-named __e2e_fleet group, removed in cleanup.
 */
import { chromium } from 'playwright';
import assert from 'node:assert';

const BASE = process.env.MM_BASE_URL || 'http://localhost:3000';
const ADMIN = () => BASE + '/admin.html?nocache=' + Date.now() + '#fleet';
const GROUP = '__e2e_fleet';

async function waitHydrated(page) {
  await page.waitForFunction(
    () => window.Alpine && Alpine.store('mm') && Alpine.store('mm').hydrated === true,
    null, { timeout: 15_000 });
  await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
}
async function settle(page) {
  await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
}
async function listGroupsRest(page) {
  const r = await page.request.get(BASE + '/api/displays');
  const j = await r.json();
  return (j.displays || []);
}
async function listGroupIds(page) { return (await listGroupsRest(page)).map(g => g.displayID); }
async function delGroup(page, id) {
  await page.request.delete(BASE + '/api/displays/' + encodeURIComponent(id));
}

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on('dialog', (d) => {
    // "+ New group" uses prompt(); Delete uses confirm(). Accept with our name.
    if (d.type() === 'prompt') d.accept(GROUP).catch(() => {});
    else d.accept().catch(() => {});
  });
  try {
    // Up-front cleanup of any orphan.
    await page.goto(BASE + '/admin.html');
    await delGroup(page, GROUP);

    // ---- 1. Desktop: groups list renders ----
    await page.goto(ADMIN());
    await waitHydrated(page);
    await page.waitForFunction(() => Alpine.store('mm').activeTab === 'fleet', null, { timeout: 5_000 });
    await page.waitForFunction(
      () => document.querySelectorAll('[data-route="fleet"] .mm-fleet-group').length > 0,
      null, { timeout: 5_000 });
    const groupRowCount = await page.evaluate(() =>
      document.querySelectorAll('[data-route="fleet"] .mm-fleet-group').length);
    const restGroups = await listGroupsRest(page);
    assert.equal(groupRowCount, restGroups.length,
      `fleet list should have one row per group (${groupRowCount} vs REST ${restGroups.length})`);

    // Select the first group -> detail cards appear.
    await page.evaluate(() => document.querySelector('[data-route="fleet"] .mm-fleet-group').click());
    await page.waitForFunction(
      () => document.querySelector('[data-route="fleet"] .mm-fleet-card') != null, null, { timeout: 5_000 });
    const cardTitles = await page.evaluate(() =>
      Array.from(document.querySelectorAll('[data-route="fleet"] .mm-fleet-card-title')).map(h => h.textContent.replace(/\s*\(.*\)/, '').trim()));
    for (const t of ['Playback', 'Calibration', 'Device scripts', 'Devices']) {
      assert.ok(cardTitles.includes(t), `expected a "${t}" card, got ${JSON.stringify(cardTitles)}`);
    }

    // ---- 1b. A group WITH devices: compact rows (no inline selects) → expand
    //          one to reveal Profile+Move → Select mode reveals the bulk bar.
    //          All non-mutating (no Apply, no device moved). ----
    const withDevices = restGroups.find(g => (g.clientCount || 0) > 0);
    if (withDevices) {
      await page.evaluate((id) => {
        const row = Array.from(document.querySelectorAll('[data-route="fleet"] .mm-fleet-group'))
          .find(li => li.querySelector('.mm-fleet-group-name')?.textContent.trim() === id);
        row.click();
      }, withDevices.displayID);
      await page.waitForFunction(
        () => document.querySelector('[data-route="fleet"] .mm-fleet-device') != null, null, { timeout: 5_000 });
      // Compact by default: rows show a name, and NO selects are rendered yet.
      const compact = await page.evaluate(() => {
        const rows = Array.from(document.querySelectorAll('[data-route="fleet"] .mm-fleet-device'));
        return {
          count: rows.length,
          selects: document.querySelectorAll('[data-route="fleet"] .mm-fleet-device select').length,
          firstName: rows[0]?.querySelector('.mm-fleet-dev-name')?.textContent?.trim() || '',
        };
      });
      assert.ok(compact.count > 0, `group ${withDevices.displayID} should show device rows`);
      assert.ok(compact.firstName.length > 0, 'device row should show a name');
      assert.equal(compact.selects, 0, 'device selects should be hidden until a row is expanded');

      // Expand the first row → its panel reveals exactly Profile + Move-to-group.
      await page.evaluate(() => document.querySelector('[data-route="fleet"] .mm-fleet-dev-row').click());
      await settle(page);
      const expandedSelects = await page.evaluate(() =>
        document.querySelectorAll('[data-route="fleet"] .mm-fleet-dev-panel select').length);
      assert.equal(expandedSelects, 2, 'expanding a device row should reveal Profile + Move-to-group selects');
      // Collapse it again.
      await page.evaluate(() => document.querySelector('[data-route="fleet"] .mm-fleet-dev-row').click());
      await settle(page);

      // Enter Select mode → checkboxes appear; select-all reveals the pinned
      // selection toolbar with all three bulk actions (Move / Profile / Run).
      // We do NOT click any action button (no real device mutated).
      await page.evaluate(() => {
        const btn = document.querySelector('[data-route="fleet"] .mm-fleet-selbtn');
        btn.click();
      });
      await settle(page);
      await page.evaluate(() => {
        const sa = document.querySelector('[data-route="fleet"] .mm-fleet-selall input[type="checkbox"]');
        sa.checked = true; sa.dispatchEvent(new Event('change', { bubbles: true }));
      });
      await settle(page);
      const barCheck = await page.evaluate(() => {
        const bar = document.querySelector('[data-route="fleet"] .mm-fleet-selbar');
        return {
          shown: bar && bar.offsetParent !== null,
          actionGroups: bar ? bar.querySelectorAll('.mm-fleet-selact').length : 0,
          selects: bar ? bar.querySelectorAll('select').length : 0,
          countText: bar ? (bar.querySelector('.mm-fleet-selcount')?.textContent || '') : '',
        };
      });
      assert.ok(barCheck.shown, 'select-all in Select mode should reveal the pinned selection toolbar');
      assert.equal(barCheck.actionGroups, 3, 'selection toolbar should offer Move + Profile + Run actions');
      assert.equal(barCheck.selects, 3, 'each bulk action should have its own picker');
      assert.match(barCheck.countText, /\d+ selected/, 'toolbar should show the selected count');
      // Exit Select mode (Done) — clears the selection, no mutation.
      await page.evaluate(() => {
        const btn = document.querySelector('[data-route="fleet"] .mm-fleet-selbtn');
        if (btn && btn.textContent.trim() === 'Done') btn.click();
      });
      await settle(page);
    }

    // ---- 2. Create a group via the UI -> appears -> delete -> gone ----
    await page.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('[data-route="fleet"] .mm-fleet-list-head button'))
        .find(b => b.textContent.includes('New group'));
      if (!btn) throw new Error('no + New group button');
      btn.click();   // prompt() auto-accepted with GROUP by the dialog handler
    });
    let created = false;
    for (let i = 0; i < 20 && !created; i++) {
      created = (await listGroupIds(page)).includes(GROUP);
      if (!created) await settle(page);
    }
    assert.ok(created, `group ${GROUP} should exist after + New group`);

    // Re-hydrate so the new row is in the list, select it, delete it.
    await page.goto(ADMIN());
    await waitHydrated(page);
    await page.waitForFunction((g) => Array.from(document.querySelectorAll('[data-route="fleet"] .mm-fleet-group-name')).some(s => s.textContent.trim() === g), GROUP, { timeout: 5_000 });
    await page.evaluate((g) => {
      const row = Array.from(document.querySelectorAll('[data-route="fleet"] .mm-fleet-group'))
        .find(li => li.querySelector('.mm-fleet-group-name')?.textContent.trim() === g);
      row.click();
    }, GROUP);
    await page.waitForFunction(
      () => document.querySelector('[data-route="fleet"] .mm-btn-danger') != null, null, { timeout: 5_000 });
    await page.evaluate(() => document.querySelector('[data-route="fleet"] .mm-btn-danger').click()); // confirm() auto-accepted
    let gone = false;
    for (let i = 0; i < 20 && !gone; i++) {
      gone = !(await listGroupIds(page)).includes(GROUP);
      if (!gone) await settle(page);
    }
    assert.ok(gone, `group ${GROUP} should be deleted after Delete group`);

    // ---- 3. Mobile: list -> detail sheet -> back ----
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(ADMIN());
    await waitHydrated(page);
    await page.waitForFunction(() => Alpine.store('mm').isMobile === true, null, { timeout: 5_000 });
    await page.waitForFunction(
      () => document.querySelector('[data-route="fleet"] .mm-fleet-group') != null, null, { timeout: 5_000 });
    await page.evaluate(() => document.querySelector('[data-route="fleet"] .mm-fleet-group').click());
    await page.waitForFunction(
      () => { const back = document.querySelector('[data-route="fleet"] .mm-fleet-back'); return back && back.offsetParent !== null; },
      null, { timeout: 5_000 });
    await page.evaluate(() => document.querySelector('[data-route="fleet"] .mm-fleet-back').click());
    await page.waitForFunction(
      () => Alpine.$data(document.querySelector('[x-data="mmFleet"]')).selectedGroupId === null,
      null, { timeout: 5_000 });

    return 'pass';
  } finally {
    try { await delGroup(page, GROUP); } catch (_) {}
    await browser.close();
  }
}
