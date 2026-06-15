import { test } from 'node:test';
import assert from 'node:assert';
import { makeStore } from '../../../js/timeline/store.js';

test('store defaults: activeTab now, empty connection/playback', () => {
  const s = makeStore();
  assert.equal(s.activeTab, 'now');
  assert.deepEqual(s.playback, {});
  assert.equal(s.connection.connected, false);
});

test('setActiveTab + setConnection + setPlayback mutate state', () => {
  const s = makeStore();
  s.setActiveTab('fleet');
  assert.equal(s.activeTab, 'fleet');
  s.setConnection({ connected: true, onlineClients: 5 });
  assert.equal(s.connection.connected, true);
  assert.equal(s.connection.onlineClients, 5);
  s.setPlayback({ displayID: 'Lobby', state: 'playing', currentPlaylist: 'P' });
  assert.equal(s.playback.Lobby.state, 'playing');
});

test('nowCards getter derives cards from slices', () => {
  const s = makeStore();
  s.displayGroups = [{ displayID: 'Lobby', clientCount: 3, onlineCount: 1 }];
  s.setPlayback({ displayID: 'Lobby', state: 'playing', currentPlaylist: 'P', renderStatus: '' });
  const cards = s.nowCards;
  assert.equal(cards.length, 1);
  assert.equal(cards[0].state, 'playing');
  assert.equal(cards[0].screenCount, 3);
});
