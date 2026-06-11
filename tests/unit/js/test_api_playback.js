import { test } from 'node:test';
import assert from 'node:assert';

test('getPlayback returns the groups array', async () => {
  global.fetch = async (url) => {
    assert.equal(url, '/api/playback');
    return { ok: true, json: async () => ({ success: true, groups: [{ displayID: 'Lobby', state: 'playing' }] }) };
  };
  const { api } = await import('../../../js/timeline/api.js?cache=' + Date.now());
  const groups = await api.getPlayback();
  assert.deepEqual(groups, [{ displayID: 'Lobby', state: 'playing' }]);
});

test('getPlayback returns [] when body has no groups', async () => {
  global.fetch = async () => ({ ok: true, json: async () => ({ success: true }) });
  const { api } = await import('../../../js/timeline/api.js?cache=' + (Date.now() + 1));
  assert.deepEqual(await api.getPlayback(), []);
});
