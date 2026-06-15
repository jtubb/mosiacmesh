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

  // --- PR-4c T-A2: 412 + opts branch ---

  test('412 + opts + refetch succeeds: info toast, no error toast, original error re-thrown', async () => {
    // Mock fetch so api.refetchSchedule returns a fresh entity.
    globalThis.fetch = async () => ({
      ok: true, status: 200,
      text: async () => JSON.stringify({ schedule: { id: 'sch1', playlistName: 'Morning', startTime: '12:00', _serverVersion: 5 } }),
    });

    const store = {
      schedules: [{ id: 'sch1', playlistName: 'Morning', startTime: '09:00', _serverVersion: 4 }],
      playlists: {},
      toasts: [], toast(msg, kind) { this.toasts.push({ msg, kind }); },
    };
    const err412 = Object.assign(new Error('stale'), { status: 412, body: { error: 'stale' } });

    let threw = null;
    try {
      await withRollback(store, ['schedules'],
        () => { store.schedules[0] = { ...store.schedules[0], startTime: '10:00' }; },
        async () => { throw err412; },
        { conflictKind: 'schedule', conflictId: 'sch1' },
      );
    } catch (e) { threw = e; }

    // refetchAfterConflict should have replaced the schedule with the server's copy.
    assert.equal(store.schedules[0].startTime, '12:00', 'schedule replaced by refetched copy');
    assert.equal(store.schedules[0]._serverVersion, 5, 'server version updated');
    // Exactly one toast — the info "updated by another admin" from refetchAfterConflict.
    assert.equal(store.toasts.length, 1, 'exactly one toast');
    assert.equal(store.toasts[0].kind, 'info', 'toast kind is info, not error');
    assert.match(store.toasts[0].msg, /updated by another admin/, 'toast mentions updated by another admin');
    // Original 412 error re-thrown so caller .catch sees it.
    assert.ok(threw !== null, 'error re-thrown');
    assert.equal(threw.status, 412, 'rethrown error is the 412');
  });

  test('412 + opts + refetch itself fails: plain rollback + error toast', async () => {
    // Mock fetch to simulate a network failure during refetch.
    globalThis.fetch = async () => { throw new Error('network down'); };

    const store = {
      schedules: [{ id: 'sch1', playlistName: 'Morning', startTime: '09:00', _serverVersion: 4 }],
      playlists: {},
      toasts: [], toast(msg, kind) { this.toasts.push({ msg, kind }); },
    };
    const err412 = Object.assign(new Error('stale'), { status: 412, body: { error: 'stale' } });

    let threw = null;
    try {
      await withRollback(store, ['schedules'],
        () => { store.schedules[0] = { ...store.schedules[0], startTime: '10:00' }; },
        async () => { throw err412; },
        { conflictKind: 'schedule', conflictId: 'sch1' },
      );
    } catch (e) { threw = e; }

    // Snapshot restored to original value (refetch failed, so the rollback snapshot stands).
    assert.equal(store.schedules[0].startTime, '09:00', 'snapshot restored after refetch failure');
    // Exactly one toast — the plain error toast from the fallthrough path.
    assert.equal(store.toasts.length, 1, 'exactly one toast');
    assert.equal(store.toasts[0].kind, 'error', 'toast kind is error');
    assert.match(store.toasts[0].msg, /stale/, 'toast includes server error message');
    // Original 412 still re-thrown.
    assert.ok(threw !== null, 'error re-thrown');
    assert.equal(threw.status, 412, 'rethrown error is the 412');
  });

  test('412 WITHOUT opts: plain rollback + error toast (opt-in gate)', async () => {
    // fetch should never be called — no opts means no refetch attempt.
    globalThis.fetch = async () => { throw new Error('fetch should not be called'); };

    const store = {
      schedules: [{ id: 'sch1', startTime: '09:00' }],
      playlists: {},
      toasts: [], toast(msg, kind) { this.toasts.push({ msg, kind }); },
    };
    const err412 = Object.assign(new Error('stale'), { status: 412, body: { error: 'stale' } });

    let threw = null;
    try {
      await withRollback(store, ['schedules'],
        () => { store.schedules[0].startTime = '10:00'; },
        async () => { throw err412; },
        // no opts argument — plain rollback path must run
      );
    } catch (e) { threw = e; }

    // Plain rollback: snapshot restored.
    assert.equal(store.schedules[0].startTime, '09:00', 'snapshot restored');
    // Exactly one toast — the plain error toast.
    assert.equal(store.toasts.length, 1, 'exactly one toast');
    assert.equal(store.toasts[0].kind, 'error', 'toast kind is error');
    // Error re-thrown.
    assert.ok(threw !== null, 'error re-thrown');
    assert.equal(threw.status, 412, 'rethrown error is the 412');
  });
});
