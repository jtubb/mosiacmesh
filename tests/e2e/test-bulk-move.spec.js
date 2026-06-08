/**
 * PR-15: bulk select + apply in the track-header popover.
 *
 * Tick the select-all checkbox → footer shows "N selected" + a
 * target dropdown + Apply button → Apply fires
 * bulkAssignDevicesToDisplay → assert the captured payload.
 *
 * Uses temp __e2e_grp_* groups + synthetic clients injected into
 * store.displays so the spec doesn't touch real device records.
 * Server-side path is covered by tests/unit/test_api_endpoints.py::TestBulkAssign.
 *
 * (A drag-and-drop variant was prototyped during PR-15 but removed
 * — see the rationale in track-header-popover.js's module comment.)
 */
import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, cleanupE2eOrphans } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const SRC = '__e2e_grp_src_' + Date.now();
    const DST = '__e2e_grp_dst_' + Date.now();

    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);

    // Cleanup leftover __e2e_grp_* with zero refs.
    await page.evaluate(async () => {
      const j = await (await fetch('/api/displays')).json();
      for (const d of (j.displays || [])) {
        if (d.displayID.startsWith('__e2e_grp_') && d.clientCount === 0 && d.scheduleCount === 0) {
          await fetch('/api/displays/' + encodeURIComponent(d.displayID), { method: 'DELETE' });
        }
      }
    });

    await page.evaluate(async ({ src, dst }) => {
      await fetch('/api/displays', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ displayID: src }) });
      await fetch('/api/displays', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ displayID: dst }) });
    }, { src: SRC, dst: DST });
    await page.evaluate(() => Alpine.store('mm').hydrate());
    await page.waitForFunction((src) => Alpine.store('mm').displayGroups.some(g => g.displayID === src), SRC, { timeout: 5000 });

    // Inject 3 synthetic clients into store.displays + the source group.
    const FAKES = ['__e2e_dev_a', '__e2e_dev_b', '__e2e_dev_c'];
    await page.evaluate(({ src, fakes }) => {
      const store = Alpine.store('mm');
      for (const k of fakes) {
        store.displays.push({ clientKey: k, displayID: src, isOnline: true, friendlyName: 'FakeDev_' + k.slice(-1), deviceType: 'tablet' });
      }
      const srcGroup = store.displayGroups.find(g => g.displayID === src);
      if (srcGroup) {
        srcGroup.clients = [...fakes];
        srcGroup.clientCount = fakes.length;
        srcGroup.onlineCount = fakes.length;
      }
    }, { src: SRC, fakes: FAKES });

    // Click the source track header.
    await page.evaluate((src) => {
      const header = document.querySelector(`.mm-track-header[data-display-id="${src}"]`);
      header.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    }, SRC);
    await page.waitForFunction(
      () => document.getElementById('mmTrackHeaderPopover').style.display === 'block', null, { timeout: 5000 });

    // Stub api.bulkAssignDevicesToDisplay so the test doesn't write
    // fake clients to settings.dat. Capture the call to assert payload.
    const result = await page.evaluate(async ({ dst, fakes }) => {
      const apiMod = await import('/js/timeline/api.js');
      const captured = [];
      const orig = apiMod.api.bulkAssignDevicesToDisplay;
      apiMod.api.bulkAssignDevicesToDisplay = async (keys, did) => {
        captured.push({ keys: [...keys], did });
        return { success: true, displayID: did, moved: keys, missing: [], movedCount: keys.length };
      };
      try {
        // Tick "Select all".
        const sa = document.querySelector('.mm-thp-select-all');
        sa.checked = true;
        sa.dispatchEvent(new Event('change', { bubbles: true }));
        const bar = document.querySelector('.mm-thp-bulkbar');
        const visible = bar && bar.style.display === 'flex';
        // Pick destination + Apply.
        const sel = document.querySelector('.mm-thp-bulk-target');
        sel.value = dst;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        const btn = document.querySelector('.mm-thp-bulkbar .btn-primary');
        const wasDisabled = btn.disabled;
        btn.click();
        await new Promise(r => setTimeout(r, 100));
        return { captured, barVisible: visible, applyWasEnabled: !wasDisabled };
      } finally {
        apiMod.api.bulkAssignDevicesToDisplay = orig;
      }
    }, { dst: DST, fakes: FAKES });

    assert.ok(result.barVisible, 'expected the bulk-bar to be visible after select-all');
    assert.ok(result.applyWasEnabled, 'expected Apply to be enabled with selection + target');
    assert.equal(result.captured.length, 1, 'expected exactly one bulk-assign API call');
    assert.deepEqual([...result.captured[0].keys].sort(), [...FAKES].sort());
    assert.equal(result.captured[0].did, DST);

    // Verify store optimistic state: all fakes now in DST, none in SRC.
    const afterBulk = await page.evaluate(({ src, dst, fakes }) => {
      const store = Alpine.store('mm');
      return {
        srcCount: store.displayGroups.find(g => g.displayID === src)?.clientCount,
        dstCount: store.displayGroups.find(g => g.displayID === dst)?.clientCount,
        clientDisplayIDs: fakes.map(k => store.displays.find(d => d.clientKey === k)?.displayID),
      };
    }, { src: SRC, dst: DST, fakes: FAKES });
    assert.equal(afterBulk.srcCount, 0, 'src should be empty after bulk move');
    assert.equal(afterBulk.dstCount, FAKES.length, 'dst should have all 3 fakes');
    assert.deepEqual(afterBulk.clientDisplayIDs, [DST, DST, DST]);

    // Cleanup: remove synthetic clients + temp groups.
    await page.evaluate(async ({ src, dst, fakes }) => {
      const store = Alpine.store('mm');
      store.displays = store.displays.filter(d => !fakes.includes(d.clientKey));
      await fetch('/api/displays/' + encodeURIComponent(src), { method: 'DELETE' });
      await fetch('/api/displays/' + encodeURIComponent(dst), { method: 'DELETE' });
    }, { src: SRC, dst: DST, fakes: FAKES });

    return 'pass';
  } finally { await browser.close(); }
}
