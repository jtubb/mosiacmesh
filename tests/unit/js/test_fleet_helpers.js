import { test } from 'node:test';
import assert from 'node:assert';
import { groupStatusLine, deviceRowsForGroup, calibrationSummary } from '../../../js/timeline/fleet/fleet-status.js';

const group = { displayID: 'Lobby', clientCount: 3, onlineCount: 2 };

test('groupStatusLine reads counts + playback + render state', () => {
  const s = groupStatusLine(group,
    { Lobby: { state: 'PLAY', currentPlaylist: 'Menu' } },
    { Lobby: true });
  assert.equal(s.displayID, 'Lobby');
  assert.equal(s.online, 2);
  assert.equal(s.total, 3);
  assert.equal(s.playing, true);
  assert.equal(s.playlistName, 'Menu');
  assert.equal(s.rendering, true);
});

test('groupStatusLine: idle group has no playback, not playing/rendering', () => {
  const s = groupStatusLine(group, {}, {});
  assert.equal(s.playing, false);
  assert.equal(s.playlistName, null);
  assert.equal(s.rendering, false);
});

test('groupStatusLine: STOP/IDLE states are not "playing"', () => {
  assert.equal(groupStatusLine(group, { Lobby: { state: 'STOP' } }, {}).playing, false);
  assert.equal(groupStatusLine(group, { Lobby: { state: 'IDLE' } }, {}).playing, false);
  // PAUSE counts as an active (non-idle) playlist.
  assert.equal(groupStatusLine(group, { Lobby: { state: 'PAUSE', currentPlaylist: 'X' } }, {}).playing, true);
});

test('deviceRowsForGroup filters by displayID and sorts by name (case-insensitive, ignores online)', () => {
  const displays = [
    { clientKey: 'a', displayID: 'Lobby', friendlyName: 'Zed', isOnline: true },
    { clientKey: 'b', displayID: 'Lobby', friendlyName: 'ann', isOnline: false },
    { clientKey: 'c', displayID: 'Cafe',  friendlyName: 'Cy',  isOnline: true },
    { clientKey: 'd', displayID: 'Lobby', friendlyName: 'Bob', isOnline: true },
  ];
  const rows = deviceRowsForGroup({ displayID: 'Lobby' }, displays);
  // Pure name sort: ann (offline!) , Bob, Zed — online status does NOT reorder.
  assert.deepEqual(rows.map(d => d.clientKey), ['b', 'd', 'a']);
});

test('deviceRowsForGroup sorts numbered names naturally (screen2 before screen13)', () => {
  const displays = [
    { clientKey: 'k13', displayID: 'X', friendlyName: 'Screen13' },
    { clientKey: 'k2',  displayID: 'X', friendlyName: 'screen2' },
    { clientKey: 'k1',  displayID: 'X', friendlyName: 'Screen1' },
  ];
  const rows = deviceRowsForGroup({ displayID: 'X' }, displays);
  assert.deepEqual(rows.map(d => d.clientKey), ['k1', 'k2', 'k13']);
});

test('calibrationSummary counts devices with the derived calibrated flag', () => {
  // /api/discovery/devices ships a derived `calibrated` boolean (not the raw
  // measuredPerimeter array). The summary must count that.
  const rows = [
    { clientKey: 'a', calibrated: true },
    { clientKey: 'b', calibrated: false },
    { clientKey: 'c' },
  ];
  assert.deepEqual(calibrationSummary(rows), { calibratedCount: 1, total: 3 });
  assert.deepEqual(calibrationSummary([]), { calibratedCount: 0, total: 0 });
});

test('calibrationSummary falls back to measuredPerimeter for raw client objects', () => {
  const rows = [
    { clientKey: 'a', measuredPerimeter: [[0, 0]] },
    { clientKey: 'b', measuredPerimeter: null },
  ];
  assert.deepEqual(calibrationSummary(rows), { calibratedCount: 1, total: 2 });
});
