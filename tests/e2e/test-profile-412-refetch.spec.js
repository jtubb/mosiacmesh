/**
 * PR-10: 412 refetch for profiles. When an out-of-band PUT bumps a
 * profile's _serverVersion and the operator's stale store then tries
 * to update it, the 412 path should refetch the fresh profile into
 * the store AND toast "another admin" — same shape as the schedule
 * and playlist conflict resolvers, just over store.profiles.
 *
 * Creates a temp profile (__e2e_p412_*), edits it out-of-band, then
 * triggers updateProfile from the stale store. Asserts the store now
 * holds the OOB value (not the local attempt) and that the toast fired.
 */
import { chromium } from 'playwright';
import assert from 'node:assert';
import { TIMELINE, waitForHydrated, cleanupE2eOrphans } from './helpers.js';

export default async function () {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  try {
    const PROFILE = '__e2e_p412_' + Date.now();
    await page.goto(TIMELINE()); await waitForHydrated(page);
    await cleanupE2eOrphans(page);

    // Create the profile via REST + hydrate it into the local store so
    // updateProfile() has a current _serverVersion to send with If-Match.
    await page.evaluate(async (pn) => {
      await fetch('/api/profiles', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: pn, label: 'initial', matchDeviceType: '' }),
      });
      const r = await fetch('/api/profiles');
      const j = await r.json();
      const fresh = (j.profiles || []).find(p => p.name === pn);
      Alpine.store('mm').profiles[pn] = fresh;
    }, PROFILE);

    // Out-of-band PUT: bumps _serverVersion. Use the list endpoint to
    // discover the current version (single-item GET landed in PR-10 but
    // only takes effect after the dev server restarts).
    await page.evaluate(async (pn) => {
      const list = await (await fetch('/api/profiles')).json();
      const cur = (list.profiles || []).find(p => p.name === pn);
      await fetch('/api/profiles/' + encodeURIComponent(pn), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'If-Match': String(cur._serverVersion) },
        body: JSON.stringify({ label: 'oob-edit' }),
      });
    }, PROFILE);

    // Local store still holds the old _serverVersion → updateProfile 412s.
    await page.evaluate(async (pn) => {
      try { await Alpine.store('mm').updateProfile(pn, { label: 'local-attempt' }); }
      catch (_) { /* expected — withRollback surfaces the rejection */ }
    }, PROFILE);

    // After refetch, the store should reflect the OOB edit (not the local attempt).
    await page.waitForFunction((pn) => {
      const p = Alpine.store('mm').profiles[pn];
      return p && p.label === 'oob-edit';
    }, PROFILE, { timeout: 5000 });

    const sawToast = await page.evaluate(
      () => Alpine.store('mm').toasts.some(t => /another admin/.test(t.msg)));
    assert.ok(sawToast, 'expected "another admin" toast after 412');

    // Cleanup.
    await page.evaluate(async (pn) => {
      await fetch('/api/profiles/' + encodeURIComponent(pn), { method: 'DELETE' });
    }, PROFILE);

    return 'pass';
  } finally { await browser.close(); }
}
