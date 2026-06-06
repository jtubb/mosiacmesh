import { test, describe } from 'node:test';
import assert from 'node:assert';
import { withRollback } from '../../../js/timeline/util/optimistic.js';

function makeFakeStore() {
  return {
    schedules: [{ id: 'a', startTime: '09:00' }],
    toasts: [],
    toast(msg, kind) { this.toasts.push({ msg, kind }); },
  };
}

describe('withRollback', () => {
  test('success path: applies local mutation, returns API result', async () => {
    const store = makeFakeStore();
    const result = await withRollback(store, ['schedules'],
      () => { store.schedules[0].startTime = '10:00'; },
      async () => ({ id: 'a', startTime: '10:00', _serverVersion: 2 }),
    );
    assert.equal(store.schedules[0].startTime, '10:00');
    assert.equal(result.startTime, '10:00');
    assert.equal(store.toasts.length, 0);
  });

  test('failure path: restores snapshot + toasts the error', async () => {
    const store = makeFakeStore();
    const err = Object.assign(new Error('PUT failed'), { status: 412, body: { error: 'stale' } });
    let thrown = null;
    try {
      await withRollback(store, ['schedules'],
        () => { store.schedules[0].startTime = '10:00'; },
        async () => { throw err; },
      );
    } catch (e) { thrown = e; }
    assert.equal(store.schedules[0].startTime, '09:00', 'snapshot restored');
    assert.ok(thrown, 'rethrows so caller can react');
    assert.equal(store.toasts.length, 1);
    assert.equal(store.toasts[0].kind, 'error');
  });

  test('preserves multiple snapshot keys', async () => {
    const store = makeFakeStore();
    store.playlists = { P: { name: 'P', items: [] } };
    await withRollback(store, ['schedules', 'playlists'],
      () => { store.schedules[0].startTime = '10:00'; store.playlists.P.items.push('x'); },
      async () => { throw new Error('boom'); },
    ).catch(() => {});
    assert.equal(store.schedules[0].startTime, '09:00');
    assert.deepEqual(store.playlists.P.items, []);
  });
});
