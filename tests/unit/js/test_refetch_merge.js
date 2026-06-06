import { test } from 'node:test';
import assert from 'node:assert';

test('refetchAfterConflict replaces schedule + toasts', async () => {
  // Mock the api module by overriding globalThis.fetch.
  // parseJsonOrText in api.js calls resp.text() then JSON.parse, so the
  // mock response must include text() (not just json()).
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(url);
    const payload = { id: 'sch1', playlistName: 'Morning', startTime: '12:00', endTime: '13:00', _serverVersion: 5 };
    return {
      ok: true, status: 200,
      text: async () => JSON.stringify(payload),
    };
  };
  // Re-import after fetch stub so api.js sees it.
  const { refetchAfterConflict } = await import('../../../js/timeline/util/refetch-merge.js?t=' + Date.now());

  const store = {
    schedules: [{ id: 'sch1', playlistName: 'Morning', startTime: '09:00', endTime: '10:00', _serverVersion: 4 }],
    playlists: {},
    toasts: [], toast(msg, kind) { this.toasts.push({ msg, kind }); },
  };
  await refetchAfterConflict(store, 'schedule', 'sch1');
  assert.equal(store.schedules[0].startTime, '12:00');
  assert.equal(store.schedules[0]._serverVersion, 5);
  assert.equal(store.toasts.length, 1);
  assert.match(store.toasts[0].msg, /updated by another admin/);
  assert.equal(store.toasts[0].kind, 'info');
  assert.ok(calls.length === 1 && calls[0].includes('/api/schedules/sch1'), `expected /api/schedules/sch1, got ${calls[0]}`);
});

test('refetchAfterConflict for playlist replaces by name', async () => {
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(url);
    const payload = { name: 'Morning', items: [{ file: 'a.mp4' }, { file: 'b.mp4' }], _serverVersion: 9 };
    return { ok: true, status: 200, text: async () => JSON.stringify(payload) };
  };
  const { refetchAfterConflict } = await import('../../../js/timeline/util/refetch-merge.js?t=' + (Date.now()+1));
  const store = {
    schedules: [], playlists: { Morning: { name: 'Morning', items: [{ file: 'a.mp4' }], _serverVersion: 8 } },
    toasts: [], toast(msg, kind) { this.toasts.push({ msg, kind }); },
  };
  await refetchAfterConflict(store, 'playlist', 'Morning');
  assert.equal(store.playlists.Morning.items.length, 2);
  assert.equal(store.playlists.Morning._serverVersion, 9);
  assert.equal(store.toasts.length, 1);
  assert.match(store.toasts[0].msg, /updated by another admin/);
  assert.equal(store.toasts[0].kind, 'info');
  assert.ok(calls.length === 1 && calls[0].includes('/api/playlists/Morning'), `expected /api/playlists/Morning, got ${calls[0]}`);
});
