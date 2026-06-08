/**
 * PR-15: bulk select + drag in the track-header popover.
 *
 * Two surfaces, one spec — both go through the same store +
 * popover code path and either could regress if the other does.
 *
 *   1. Bulk select bar: tick the select-all checkbox → footer
 *      shows "N selected" + a target dropdown + Apply button →
 *      Apply fires bulkAssignDevicesToDisplay → assert server side
 *      that all clients moved.
 *   2. Drag-and-drop a row to another track header → assert the
 *      single-device move landed via assignDeviceToDisplay.
 *
 * Uses temp __e2e_grp_* groups + synthetic clients injected into
 * settings.dat via the bulk-create REST + a server-side fixture
 * (we POST a few fake clients via the existing test register path).
 * Actually — to avoid bloating settings.dat with leftover synthetic
 * clients, this spec mocks the API calls and exercises the
 * store + UI logic only. The server-side path is covered by
 * tests/unit/test_api_endpoints.py::TestBulkAssign.
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

    // Cleanup any leftover __e2e_grp_* with zero refs.
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
    // This avoids polluting the server's real client list.
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

    // --- Case 1: bulk select + apply ---
    // Re-render the timeline so the new track-header is in the DOM.
    await page.evaluate(() => Alpine.store('mm').$nextRender?.());
    // Click the source track header.
    await page.evaluate((src) => {
      const header = document.querySelector(`.mm-track-header[data-display-id="${src}"]`);
      header.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    }, SRC);
    await page.waitForFunction(
      () => document.getElementById('mmTrackHeaderPopover').style.display === 'block', null, { timeout: 5000 });

    // Stub api.bulkAssignDevicesToDisplay so we don't write fake
    // clients to settings.dat. Capture the call to assert payload.
    const result1 = await page.evaluate(async ({ dst, fakes }) => {
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
        // Bulk-bar should be visible now.
        const bar = document.querySelector('.mm-thp-bulkbar');
        const visible = bar && bar.style.display === 'flex';
        // Pick the destination in the bulk target select.
        const sel = document.querySelector('.mm-thp-bulk-target');
        sel.value = dst;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        // Apply.
        const btn = document.querySelector('.mm-thp-bulkbar .btn-primary');
        const wasDisabled = btn.disabled;
        btn.click();
        // Wait for the async to settle.
        await new Promise(r => setTimeout(r, 100));
        return { captured, barVisible: visible, applyWasEnabled: !wasDisabled };
      } finally {
        apiMod.api.bulkAssignDevicesToDisplay = orig;
      }
    }, { dst: DST, fakes: FAKES });

    assert.ok(result1.barVisible, 'expected the bulk-bar to be visible after select-all');
    assert.ok(result1.applyWasEnabled, 'expected Apply to be enabled with selection + target');
    assert.equal(result1.captured.length, 1, 'expected exactly one bulk-assign API call');
    assert.deepEqual([...result1.captured[0].keys].sort(), [...FAKES].sort());
    assert.equal(result1.captured[0].did, DST);

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

    // --- Case 2: drag a row from popover to another track header ---
    // Reset the fakes back to SRC and put one client in SRC again.
    await page.evaluate(({ src, dst, fakes }) => {
      const store = Alpine.store('mm');
      // Move all back to SRC for the drag case.
      for (const k of fakes) {
        const c = store.displays.find(d => d.clientKey === k);
        if (c) c.displayID = src;
      }
      const srcGroup = store.displayGroups.find(g => g.displayID === src);
      const dstGroup = store.displayGroups.find(g => g.displayID === dst);
      if (srcGroup) { srcGroup.clients = [...fakes]; srcGroup.clientCount = fakes.length; srcGroup.onlineCount = fakes.length; }
      if (dstGroup) { dstGroup.clients = []; dstGroup.clientCount = 0; dstGroup.onlineCount = 0; }
    }, { src: SRC, dst: DST, fakes: FAKES });
    // Re-open the popover.
    await page.evaluate((src) => {
      const header = document.querySelector(`.mm-track-header[data-display-id="${src}"]`);
      header.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    }, SRC);
    await page.waitForFunction(() => document.getElementById('mmTrackHeaderPopover').style.display === 'block', null, { timeout: 5000 });

    // Stub the single-device API.
    const result2 = await page.evaluate(async ({ src, dst, fake }) => {
      const apiMod = await import('/js/timeline/api.js');
      const captured = [];
      const orig = apiMod.api.assignDeviceToDisplay;
      apiMod.api.assignDeviceToDisplay = async (ck, did) => { captured.push({ ck, did }); return { success: true }; };
      try {
        // Synthesize a dragstart on the first row, then dragover + drop on dst track-header.
        const row = document.querySelector(`#mmTrackHeaderPopover li[data-client-key="${fake}"]`);
        const target = document.querySelector(`.mm-track-header[data-display-id="${dst}"]`);
        const dt = new DataTransfer();
        const sRect = row.getBoundingClientRect();
        const tRect = target.getBoundingClientRect();
        row.dispatchEvent(new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer: dt, clientX: sRect.left + 5, clientY: sRect.top + 5 }));
        target.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt, clientX: tRect.left + 5, clientY: tRect.top + 5 }));
        target.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt, clientX: tRect.left + 5, clientY: tRect.top + 5 }));
        row.dispatchEvent(new DragEvent('dragend', { bubbles: true, cancelable: true, dataTransfer: dt }));
        await new Promise(r => setTimeout(r, 100));
        return { captured };
      } finally {
        apiMod.api.assignDeviceToDisplay = orig;
      }
    }, { src: SRC, dst: DST, fake: FAKES[0] });

    assert.equal(result2.captured.length, 1, 'expected exactly one single-device move via drag');
    assert.deepEqual(result2.captured[0], { ck: FAKES[0], did: DST });

    // Cleanup test groups.
    await page.evaluate(async ({ src, dst, fakes }) => {
      const store = Alpine.store('mm');
      // Local cleanup: remove synthetic clients.
      store.displays = store.displays.filter(d => !fakes.includes(d.clientKey));
      // Server cleanup: remove the test groups (now empty).
      await fetch('/api/displays/' + encodeURIComponent(src), { method: 'DELETE' });
      await fetch('/api/displays/' + encodeURIComponent(dst), { method: 'DELETE' });
    }, { src: SRC, dst: DST, fakes: FAKES });

    return 'pass';
  } finally { await browser.close(); }
}
