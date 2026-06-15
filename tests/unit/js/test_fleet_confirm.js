/**
 * PR-13: fleet-confirm.js scope arg. Tests cover the pure logic
 * paths — countTargets, the payload shape sent on send, and the
 * "no targets" early-return. Modal UI is browser-driven; verified
 * by Playwright in tests/e2e/test-fleet-scope.spec.js.
 */
import { test } from 'node:test';
import assert from 'node:assert';

// JSDOM-free stub: fleet-confirm.js uses `document.createElement` only
// inside showConfirm (which the threshold gates). Sub-3-device calls
// skip the modal and go straight to sendFrame, which only uses
// window.sock + window.generateMessage. So we just stub those.

function makeStore(displays) {
  return {
    displays,
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

test('countTargets — null scope counts all', async () => {
  const { countTargets } = await import('../../../js/timeline/modals/fleet-confirm.js');
  const store = makeStore([
    { displayID: 'Tablet' }, { displayID: 'Tablet' }, { displayID: 'Desktop' },
  ]);
  assert.equal(countTargets(store, null), 3);
  assert.equal(countTargets(store, ''),   3);
  assert.equal(countTargets(store, undefined), 3);
});

test('countTargets — scoped only counts that displayID', async () => {
  const { countTargets } = await import('../../../js/timeline/modals/fleet-confirm.js');
  const store = makeStore([
    { displayID: 'Tablet' }, { displayID: 'Tablet' }, { displayID: 'Desktop' },
  ]);
  assert.equal(countTargets(store, 'Tablet'),  2);
  assert.equal(countTargets(store, 'Desktop'), 1);
  assert.equal(countTargets(store, 'Lobby'),   0);
});

test('fireFleetAction(all) — sends {all:true} when scope is null and ≤3 devices', async () => {
  const captured = captureSocket();
  const { fireFleetAction } = await import('../../../js/timeline/modals/fleet-confirm.js?cache=' + Date.now());
  const store = makeStore([{ displayID: 'Tablet' }, { displayID: 'Desktop' }]);
  fireFleetAction(store, 'login', null);
  assert.equal(captured.length, 1, 'expected exactly one RUN_SCRIPT frame');
  assert.equal(captured[0].REQUEST, 'RUN_SCRIPT');
  assert.deepEqual(captured[0].PAYLOAD, { all: true, script: 'login' });
});

test('fireFleetAction(scoped) — sends {displayID, script}, not {all}', async () => {
  const captured = captureSocket();
  const { fireFleetAction } = await import('../../../js/timeline/modals/fleet-confirm.js?cache=' + (Date.now()+1));
  const store = makeStore([
    { displayID: 'Tablet' }, { displayID: 'Tablet' }, { displayID: 'Desktop' },
  ]);
  fireFleetAction(store, 'reboot', 'Tablet');
  assert.equal(captured.length, 1);
  assert.deepEqual(captured[0].PAYLOAD, { displayID: 'Tablet', script: 'reboot' });
  // Toast should mention scope + count (2 Tablets, not 3 total).
  const lastToast = store.toasts[store.toasts.length - 1];
  assert.match(lastToast.msg, /2 "Tablet" device/);
});

test('fireFleetAction — empty scope is treated as null (all)', async () => {
  const captured = captureSocket();
  const { fireFleetAction } = await import('../../../js/timeline/modals/fleet-confirm.js?cache=' + (Date.now()+2));
  const store = makeStore([{ displayID: 'Tablet' }]);
  fireFleetAction(store, 'start', '');
  assert.deepEqual(captured[0].PAYLOAD, { all: true, script: 'start' });
});

test('fireFleetAction — 0 targets warns and sends nothing', async () => {
  const captured = captureSocket();
  const { fireFleetAction } = await import('../../../js/timeline/modals/fleet-confirm.js?cache=' + (Date.now()+3));
  const store = makeStore([{ displayID: 'Tablet' }]);
  fireFleetAction(store, 'reboot', 'Lobby');   // no Lobby clients
  assert.equal(captured.length, 0, 'no frame should be sent when scope has 0 targets');
  assert.equal(store.toasts.length, 1);
  assert.equal(store.toasts[0].kind, 'warn');
  assert.match(store.toasts[0].msg, /nothing to do/i);
});

test('fireFleetAction — ≤3 targets goes straight to sendFrame (no modal)', async () => {
  // The 3-device ceiling lets us exercise the no-modal path without
  // a DOM. >3 devices would call showConfirm and require document.
  const captured = captureSocket();
  const { fireFleetAction } = await import('../../../js/timeline/modals/fleet-confirm.js?cache=' + (Date.now()+4));
  const store = makeStore([
    { displayID: 'Tablet' }, { displayID: 'Tablet' }, { displayID: 'Tablet' },
  ]);
  fireFleetAction(store, 'stop', 'Tablet');
  // Sent immediately (no confirm prompt to traverse).
  assert.equal(captured.length, 1);
  assert.deepEqual(captured[0].PAYLOAD, { displayID: 'Tablet', script: 'stop' });
});
