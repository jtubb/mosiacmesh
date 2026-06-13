import { test } from 'node:test';
import assert from 'node:assert';
import { playlistReadinessForGroup } from '../../../js/timeline/fleet/fleet-status.js';

test('playlistReadinessForGroup labels each playlist', () => {
  const playlists = { A: { items: [{ playmode: 'SEGMENT' }] }, B: { items: [{ playmode: 'FULL' }] } };
  const renders = { G1: { A: { state: 'RENDERING', percent: 50 } } };
  const rows = playlistReadinessForGroup('G1', playlists, renders);
  assert.equal(rows.find(r => r.name === 'A').label, 'rendering… 50%');
  assert.equal(rows.find(r => r.name === 'B').label, 'ready'); // N/A → ready
  assert.equal(rows.find(r => r.name === 'B').ready, true);
});
