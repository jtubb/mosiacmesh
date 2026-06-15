import { test } from 'node:test';
import assert from 'node:assert';
import { buildNowSummary } from '../../../js/timeline/now-summary.js';

const groups = [{ displayID: 'Lobby', clientCount: 4, onlineCount: 2 }];

test('playing group reflects playback row', () => {
  const cards = buildNowSummary({
    displayGroups: groups,
    playback: { Lobby: { state: 'playing', currentPlaylist: 'Lunch Menu', renderStatus: '' } },
  });
  assert.equal(cards.length, 1);
  assert.deepEqual(cards[0], {
    displayID: 'Lobby', screenCount: 4, onlineCount: 2,
    state: 'playing', currentPlaylist: 'Lunch Menu', renderStatus: '',
  });
});

test('group with no playback entry is idle', () => {
  const cards = buildNowSummary({ displayGroups: groups, playback: {} });
  assert.equal(cards[0].state, 'idle');
  assert.equal(cards[0].currentPlaylist, null);
});

test('renderInProgress fallback sets renderStatus', () => {
  const cards = buildNowSummary({ displayGroups: groups, playback: {}, renderInProgress: { Lobby: true } });
  assert.equal(cards[0].renderStatus, 'rendering');
});

test('counts fall back to displays when group lacks them', () => {
  const cards = buildNowSummary({
    displayGroups: [{ displayID: 'Lobby' }],
    displays: [
      { displayID: 'Lobby', isOnline: true },
      { displayID: 'Lobby', isOnline: false },
      { displayID: 'Other', isOnline: true },
    ],
    playback: {},
  });
  assert.equal(cards[0].screenCount, 2);
  assert.equal(cards[0].onlineCount, 1);
});
