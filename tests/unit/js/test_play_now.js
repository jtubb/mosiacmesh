/**
 * PR-29: play-now.js. Tests cover the pure SockJS-frame paths
 * (firePlayNow / fireStopNow) — these are the parts the modal-picker
 * UI lands on after the operator clicks a playlist. Modal DOM is
 * covered by Playwright in tests/e2e/test-play-now.spec.js (browser).
 */
import { test } from 'node:test';
import assert from 'node:assert';

function makeStore() {
  return {
    playlists: { Alpha: { name: 'Alpha' }, Beta: { name: 'Beta' } },
    toasts: [],
    toast(msg, kind) { this.toasts.push({ msg, kind }); },
  };
}

function captureSocket() {
  const captured = [];
  global.window = {
    sock: { send: (frame) => captured.push(JSON.parse(frame)) },
    generateMessage: (dest, req, payload) => JSON.stringify({ DEST: dest, REQUEST: req, PAYLOAD: payload }),
  };
  return captured;
}

test('firePlayNow — emits ASSIGN_PLAYLIST then PLAY in that order', async () => {
  const captured = captureSocket();
  const { firePlayNow } = await import('../../../js/timeline/modals/play-now.js?cache=' + Date.now());
  const store = makeStore();
  firePlayNow(store, 'OEB Sign 1', 'Alpha');
  assert.equal(captured.length, 2, 'expected exactly two frames');
  assert.equal(captured[0].REQUEST, 'ASSIGN_PLAYLIST');
  assert.deepEqual(captured[0].PAYLOAD, { displayID: 'OEB Sign 1', name: 'Alpha' });
  assert.equal(captured[1].REQUEST, 'PLAY');
  assert.deepEqual(captured[1].PAYLOAD, { displayID: 'OEB Sign 1' });
  const lastToast = store.toasts[store.toasts.length - 1];
  assert.match(lastToast.msg, /Playing "Alpha" on "OEB Sign 1" now/);
});

test('fireStopNow — emits a single STOP frame', async () => {
  const captured = captureSocket();
  const { fireStopNow } = await import('../../../js/timeline/modals/play-now.js?cache=' + (Date.now() + 1));
  const store = makeStore();
  fireStopNow(store, 'OEB Sign 1');
  assert.equal(captured.length, 1);
  assert.equal(captured[0].REQUEST, 'STOP');
  assert.deepEqual(captured[0].PAYLOAD, { displayID: 'OEB Sign 1' });
  assert.match(store.toasts[0].msg, /Stopped playback on "OEB Sign 1"/);
});

test('firePlayNow — no SockJS → toasts error and emits no frames', async () => {
  global.window = {};  // sock + generateMessage missing
  const { firePlayNow } = await import('../../../js/timeline/modals/play-now.js?cache=' + (Date.now() + 2));
  const store = makeStore();
  firePlayNow(store, 'OEB Sign 1', 'Alpha');
  assert.equal(store.toasts.length, 1);
  assert.equal(store.toasts[0].kind, 'error');
  assert.match(store.toasts[0].msg, /SockJS not available/);
});

test('fireStopNow — no SockJS → toasts error and emits no frames', async () => {
  global.window = {};
  const { fireStopNow } = await import('../../../js/timeline/modals/play-now.js?cache=' + (Date.now() + 3));
  const store = makeStore();
  fireStopNow(store, 'OEB Sign 1');
  assert.equal(store.toasts.length, 1);
  assert.equal(store.toasts[0].kind, 'error');
});
