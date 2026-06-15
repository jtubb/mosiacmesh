/**
 * Section 1 (Admin Overhaul) — four-destination shell + Now landing.
 *
 * Tasks 9–14 replaced the old jQuery adminRoute() console with an
 * Alpine-driven shell: a Now/Content/Schedule/Fleet nav bound to
 * $store.mm.activeTab, sections toggled via x-show, hash routing
 * (#now/#content/#schedule/#fleet) kept in sync by
 * js/timeline/shell/router.js, a responsive nav (left rail on desktop /
 * fixed bottom bar ≤760px), modals that dock to a bottom sheet ≤760px,
 * and a Now landing rendering one .now-card per display group from
 * GET /api/playback.
 *
 * This spec drives the REAL page against the running dev server and
 * asserts the behavior that no Node --test unit can see: that the nav
 * actually flips sections + hash + aria, that deep-linking lands on the
 * right tab, that the responsive breakpoint reflows the nav and the
 * modal shell, and that a live PLAYBACK_CHANGED frame injected through
 * the page's SockJS hook updates the matching Now card.
 *
 * Self-contained: no server fixtures created/cleaned — the PLAYBACK
 * update is an injected frame targeting an EXISTING group's displayID
 * read back from the rendered cards. Resilient: waitForFunction with
 * timeouts, never fixed sleeps.
 */
import { chromium } from 'playwright';
import assert from 'node:assert';

const BASE = process.env.MM_BASE_URL || 'http://localhost:3000';
const ADMIN = () => BASE + '/admin.html?nocache=' + Date.now();

// Wait until Alpine is up and the mm store has finished its initial
// hydrate (the five-GET Promise.all + /api/playback). The shell renders
// before hydrate resolves, so gating on hydrated avoids racing the
// post-hydrate re-render.
async function waitHydrated(page) {
  await page.waitForFunction(
    () => window.Alpine && Alpine.store('mm') && Alpine.store('mm').hydrated === true,
    null, { timeout: 15_000 });
  // Let the post-hydrate reactive re-render commit before measuring.
  await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
}

// A section is "shown" when x-show hasn't set display:none on it.
async function sectionVisible(page, route) {
  return await page.evaluate((r) => {
    const el = document.querySelector(`[data-route="${r}"]`);
    if (!el) return false;
    return getComputedStyle(el).display !== 'none';
  }, route);
}

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  try {
    // ---- 1. Hydrate + nav exists ----
    await page.goto(ADMIN());
    await waitHydrated(page);

    const navLabels = await page.evaluate(() =>
      Array.from(document.querySelectorAll('.sidebar .navitem'))
        .map(b => b.textContent.replace(/\s+/g, ' ').trim()));
    // Each label carries the icon glyph + the word; assert the word is present.
    for (const word of ['Now', 'Content', 'Schedule', 'Fleet']) {
      assert.ok(navLabels.some(l => l.includes(word)),
        `nav missing "${word}" button; got ${JSON.stringify(navLabels)}`);
    }

    // ---- 2. Tab switching + hash + aria-current ----
    const tabs = ['now', 'content', 'schedule', 'fleet'];
    for (const tab of tabs) {
      // Click the matching nav button by its label word.
      const word = tab[0].toUpperCase() + tab.slice(1);
      await page.evaluate((w) => {
        const btn = Array.from(document.querySelectorAll('.sidebar .navitem'))
          .find(b => b.textContent.includes(w));
        if (!btn) throw new Error('no navitem for ' + w);
        btn.click();
      }, word);

      // Hash updated.
      await page.waitForFunction((t) => location.hash === '#' + t, tab, { timeout: 4_000 });
      assert.equal(await page.evaluate(() => location.hash), '#' + tab,
        `hash should be #${tab}`);

      // activeTab reflects it; the section is visible, others hidden.
      // x-show toggles display on the next Alpine tick, so poll the DOM
      // rather than asserting synchronously after the click.
      await page.waitForFunction((t) => Alpine.store('mm').activeTab === t, tab, { timeout: 4_000 });
      await page.waitForFunction((t) => {
        const shown = (r) => {
          const el = document.querySelector(`[data-route="${r}"]`);
          return el && getComputedStyle(el).display !== 'none';
        };
        const all = ['now', 'content', 'schedule', 'fleet'];
        return shown(t) && all.filter(x => x !== t).every(x => !shown(x));
      }, tab, { timeout: 4_000 });
      assert.ok(await sectionVisible(page, tab), `section ${tab} should be visible`);
      for (const other of tabs.filter(t => t !== tab)) {
        assert.ok(!(await sectionVisible(page, other)),
          `section ${other} should be hidden while ${tab} active`);
      }

      // aria-current="page" + class "on" on the active nav button only.
      const ariaState = await page.evaluate((w) => {
        const btns = Array.from(document.querySelectorAll('.sidebar .navitem'));
        const active = btns.find(b => b.textContent.includes(w));
        return {
          activeCurrent: active.getAttribute('aria-current'),
          activeOn: active.classList.contains('on'),
          othersCurrent: btns.filter(b => !b.textContent.includes(w))
            .map(b => b.getAttribute('aria-current')),
        };
      }, word);
      assert.equal(ariaState.activeCurrent, 'page', `${tab} nav should be aria-current=page`);
      assert.ok(ariaState.activeOn, `${tab} nav should carry class "on"`);
      assert.ok(ariaState.othersCurrent.every(v => v !== 'page'),
        'only the active nav should be aria-current=page');
    }

    // ---- 3. Deep-link to #schedule renders the timeline grid ----
    await page.goto(BASE + '/admin.html#schedule');
    await waitHydrated(page);
    await page.waitForFunction(() => Alpine.store('mm').activeTab === 'schedule', null, { timeout: 5_000 });
    assert.ok(await sectionVisible(page, 'schedule'), 'deep-link: schedule section visible');
    assert.ok(!(await sectionVisible(page, 'now')), 'deep-link: now section hidden');
    // The day-grid (and at least one track header) renders inside it.
    await page.waitForFunction(
      () => document.querySelector('[data-route="schedule"] .mm-day-grid') != null,
      null, { timeout: 5_000 });
    const gridOk = await page.evaluate(() => {
      const sec = document.querySelector('[data-route="schedule"]');
      return !!sec.querySelector('.mm-day-grid')
        && sec.querySelectorAll('.mm-track-header').length > 0;
    });
    assert.ok(gridOk, 'deep-link: timeline grid + track header rendered inside schedule section');

    // ---- 4. Responsive nav: desktop rail vs mobile bottom bar ----
    await page.setViewportSize({ width: 1000, height: 900 });
    await page.evaluate(() => new Promise(r => requestAnimationFrame(r)));
    const desktopNav = await page.evaluate(() => {
      const s = document.getElementById('sidebar');
      const cs = getComputedStyle(s);
      return { position: cs.position, flexDirection: cs.flexDirection };
    });
    assert.notEqual(desktopNav.position, 'fixed',
      `desktop sidebar should be a static/relative rail, got position=${desktopNav.position}`);

    await page.setViewportSize({ width: 380, height: 800 });
    await page.evaluate(() => new Promise(r => requestAnimationFrame(r)));
    const mobileNav = await page.evaluate(() => {
      const s = document.getElementById('sidebar');
      const cs = getComputedStyle(s);
      return { position: cs.position, bottom: cs.bottom, flexDirection: cs.flexDirection };
    });
    assert.equal(mobileNav.position, 'fixed',
      `mobile sidebar should be fixed bottom bar, got position=${mobileNav.position}`);
    assert.equal(mobileNav.bottom, '0px',
      `mobile sidebar should dock to bottom (bottom:0), got ${mobileNav.bottom}`);
    assert.equal(mobileNav.flexDirection, 'row',
      `mobile bottom bar should lay nav items in a row, got ${mobileNav.flexDirection}`);

    // ---- 5. Modal → bottom sheet at narrow width; centered box at wide ----
    async function openProbeModal() {
      await page.evaluate(async () => {
        const { openModal } = await import('/js/timeline/modals/modal-shell.js');
        const el = document.createElement('div');
        el.textContent = 'probe';
        openModal({ title: 'Probe', contentEl: el });
      });
      await page.waitForSelector('.mm-modal', { timeout: 4_000 });
      // The ≤760px sheet plays an 180ms mm-sheet-up translateY entry
      // animation; wait for it to settle so boundingBox reflects the
      // resting position (not a mid-translate frame).
      await page.evaluate(() => {
        const dg = document.querySelector('.mm-modal');
        const anims = dg.getAnimations ? dg.getAnimations() : [];
        return Promise.all(anims.map(a => a.finished.catch(() => {})));
      });
      await page.evaluate(() => new Promise(r => requestAnimationFrame(r)));
    }
    async function closeModal() {
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => document.querySelector('.mm-modal') == null, null, { timeout: 4_000 });
    }

    // Narrow (380px): full-width sheet docked to the bottom of the overlay.
    await openProbeModal();
    const sheet = await page.evaluate(() => {
      const overlay = document.querySelector('.mm-modal-overlay');
      const dialog = document.querySelector('.mm-modal');
      const or = overlay.getBoundingClientRect();
      const dr = dialog.getBoundingClientRect();
      return {
        vw: window.innerWidth, vh: window.innerHeight,
        dialogW: dr.width, dialogBottom: dr.bottom,
        overlayBottom: or.bottom,
        overlayAlign: getComputedStyle(overlay).alignItems,
      };
    });
    // Width spans (near) the full viewport — allow a few px for borders.
    assert.ok(sheet.dialogW >= sheet.vw - 4,
      `mobile modal should be full-width (~${sheet.vw}), got ${sheet.dialogW}`);
    // Docked to the bottom: dialog's bottom edge ≈ overlay's bottom edge.
    assert.ok(Math.abs(sheet.dialogBottom - sheet.overlayBottom) <= 2,
      `mobile modal should be docked to bottom; dialogBottom=${sheet.dialogBottom} overlayBottom=${sheet.overlayBottom}`);
    assert.equal(sheet.overlayAlign, 'flex-end', 'mobile overlay should align-items flex-end');
    await closeModal();

    // Wide (900px): centered box, NOT full width.
    await page.setViewportSize({ width: 900, height: 800 });
    await page.evaluate(() => new Promise(r => requestAnimationFrame(r)));
    await openProbeModal();
    const box = await page.evaluate(() => {
      const dialog = document.querySelector('.mm-modal');
      const dr = dialog.getBoundingClientRect();
      return { vw: window.innerWidth, dialogW: dr.width, left: dr.left, right: dr.right };
    });
    assert.ok(box.dialogW < box.vw - 40,
      `desktop modal should be a centered box, not full width: dialogW=${box.dialogW} vw=${box.vw}`);
    assert.ok(box.left > 8 && (box.vw - box.right) > 8,
      `desktop modal should have margins on both sides: left=${box.left} rightGap=${box.vw - box.right}`);
    await closeModal();

    // ---- 6. Now cards + live PLAYBACK_CHANGED update ----
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto(BASE + '/admin.html#now');
    await waitHydrated(page);
    await page.waitForFunction(() => Alpine.store('mm').activeTab === 'now', null, { timeout: 5_000 });
    await page.waitForFunction(
      () => document.querySelectorAll('[data-route="now"] .now-card').length > 0,
      null, { timeout: 5_000 });

    // Read an existing group's displayID off the rendered cards.
    const targetId = await page.evaluate(() => {
      const card = document.querySelector('[data-route="now"] .now-card .nc-name');
      return card ? card.textContent.trim() : null;
    });
    assert.ok(targetId, 'expected at least one .now-card with a display name');

    // Inject a live PLAYBACK_CHANGED frame through the page's SockJS hook
    // (sockjs-status.js wrapped window.sock.onmessage). ev.data is a JSON
    // string, exactly like a real broadcast.
    const injected = await page.evaluate((did) => {
      if (!window.sock || typeof window.sock.onmessage !== 'function') return false;
      const msg = JSON.stringify({
        REQUEST: 'PLAYBACK_CHANGED',
        PAYLOAD: { groups: [{
          displayID: did, state: 'playing', currentPlaylist: '__e2e_test',
          startedEpoch: 0, renderStatus: '',
        }] },
      });
      window.sock.onmessage({ data: msg });
      return true;
    }, targetId);
    assert.ok(injected, 'SockJS hook (window.sock.onmessage) not available to inject PLAYBACK_CHANGED');

    // The matching card flips to "▶ playing" + shows the playlist name.
    await page.waitForFunction((did) => {
      const cards = Array.from(document.querySelectorAll('[data-route="now"] .now-card'));
      const card = cards.find(c => {
        const n = c.querySelector('.nc-name');
        return n && n.textContent.trim() === did;
      });
      if (!card) return false;
      const pill = card.querySelector('.nc-pill');
      const txt = card.textContent;
      return pill && /playing/.test(pill.textContent) && txt.includes('__e2e_test');
    }, targetId, { timeout: 5_000 });

    const finalCard = await page.evaluate((did) => {
      const cards = Array.from(document.querySelectorAll('[data-route="now"] .now-card'));
      const card = cards.find(c => c.querySelector('.nc-name')?.textContent.trim() === did);
      return {
        pill: card.querySelector('.nc-pill').textContent.trim(),
        playing: card.classList.contains('playing'),
        hasPlaylist: card.textContent.includes('__e2e_test'),
      };
    }, targetId);
    assert.ok(/playing/.test(finalCard.pill), `card pill should read playing, got "${finalCard.pill}"`);
    assert.ok(finalCard.playing, 'card should carry the .playing class after PLAYBACK_CHANGED');
    assert.ok(finalCard.hasPlaylist, 'card should show the injected playlist name __e2e_test');

    return 'pass';
  } finally {
    await browser.close();
  }
}
