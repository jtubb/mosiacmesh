import { test, describe } from 'node:test';
import assert from 'node:assert';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const storeUrl = pathToFileURL(path.join(here, '../../../js/timeline/store.js')).href;

async function loadStore() {
  const mod = await import(storeUrl + '?t=' + Date.now() + Math.random());
  return mod.makeStore();
}

function installFetch(handlers) {
  globalThis.fetch = async (url, opts) => {
    const m = (opts && opts.method) || 'GET';
    const key = `${m} ${url}`;
    const handler = handlers[key];
    if (!handler) throw new Error('unhandled fetch: ' + key);
    const result = await handler(opts);
    return {
      ok: result.status >= 200 && result.status < 300,
      status: result.status,
      statusText: result.statusText || '',
      text: async () => typeof result.body === 'string' ? result.body : JSON.stringify(result.body ?? null),
    };
  };
}

describe('store.createSchedule', () => {
  test('optimistic: appends placeholder, confirms with server id', async () => {
    const store = await loadStore();
    store.playlists = { P: { name: 'P', items: [], _serverVersion: 1 } };
    store.displays = [{ displayID: 'D' }];
    store.schedules = [];
    installFetch({
      'POST /api/schedules': async () => ({ status: 201, body: { success: true, schedule: { id: 'sch_server', playlistName: 'P', displayID: 'D', startTime: '09:00', endTime: '17:00', _serverVersion: 1 } } }),
    });
    await store.createSchedule({ playlistName: 'P', displayID: 'D', startTime: '09:00', endTime: '17:00' });
    assert.equal(store.schedules.length, 1);
    assert.equal(store.schedules[0].id, 'sch_server');
    assert.equal(store.toasts.length, 0);
  });

  test('failure: rolls back, toasts error', async () => {
    const store = await loadStore();
    store.playlists = { P: { name: 'P', items: [], _serverVersion: 1 } };
    store.displays = [{ displayID: 'D' }];
    store.schedules = [];
    installFetch({
      'POST /api/schedules': async () => ({ status: 400, body: { success: false, error: "playlist 'Ghost' not found" } }),
    });
    let thrown = null;
    try {
      await store.createSchedule({ playlistName: 'Ghost', displayID: 'D' });
    } catch (e) { thrown = e; }
    assert.equal(store.schedules.length, 0, 'placeholder removed on failure');
    assert.ok(thrown);
    assert.equal(store.toasts.length, 1);
    assert.equal(store.toasts[0].kind, 'error');
    assert.match(store.toasts[0].msg, /Ghost/);
  });
});

describe('store.updateSchedule', () => {
  test('PUT with If-Match, replaces with server returned object', async () => {
    const store = await loadStore();
    store.schedules = [{ id: 'sch_1', playlistName: 'P', displayID: 'D', startTime: '09:00', endTime: '17:00', _serverVersion: 5 }];
    installFetch({
      'PUT /api/schedules/sch_1': async (opts) => {
        assert.equal(opts.headers['If-Match'], '5');
        return { status: 200, body: { success: true, schedule: { id: 'sch_1', playlistName: 'P', displayID: 'D', startTime: '10:00', endTime: '17:00', _serverVersion: 6 } } };
      },
    });
    await store.updateSchedule('sch_1', { startTime: '10:00' });
    assert.equal(store.schedules[0].startTime, '10:00');
    assert.equal(store.schedules[0]._serverVersion, 6);
  });

  test('412 stale: rolls back, toasts conflict', async () => {
    const store = await loadStore();
    store.schedules = [{ id: 'sch_1', startTime: '09:00', _serverVersion: 5 }];
    installFetch({
      'PUT /api/schedules/sch_1': async () => ({ status: 412, body: { success: false, error: 'schedule was modified by another writer', currentVersion: 7 } }),
    });
    let thrown = null;
    try {
      await store.updateSchedule('sch_1', { startTime: '10:00' });
    } catch (e) { thrown = e; }
    assert.equal(store.schedules[0].startTime, '09:00');
    assert.equal(store.schedules[0]._serverVersion, 5);
    assert.ok(thrown);
    assert.equal(thrown.status, 412);
    assert.equal(store.toasts.length, 1);
  });
});

describe('store.deleteSchedule', () => {
  test('removes from local + DELETE', async () => {
    const store = await loadStore();
    store.schedules = [{ id: 'sch_1' }, { id: 'sch_2' }];
    installFetch({
      'DELETE /api/schedules/sch_1': async () => ({ status: 204, body: '' }),
    });
    await store.deleteSchedule('sch_1');
    assert.deepEqual(store.schedules.map(s => s.id), ['sch_2']);
  });
});

describe('store.toast + dismissToast', () => {
  test('toast appends; dismissToast removes by id', async () => {
    const store = await loadStore();
    const id = store.toast('hello', 'info');
    assert.equal(store.toasts.length, 1);
    store.dismissToast(id);
    assert.equal(store.toasts.length, 0);
  });
});

describe('store.selection', () => {
  test('selectClip single mode replaces; multi mode toggles', async () => {
    const store = await loadStore();
    store.selectClip('a');
    assert.deepEqual([...store.selection], ['a']);
    store.selectClip('b');
    assert.deepEqual([...store.selection], ['b']);
    store.selectClip('a', true);
    assert.deepEqual([...store.selection].sort(), ['a', 'b']);
    store.selectClip('a', true);
    assert.deepEqual([...store.selection], ['b']);
    store.clearSelection();
    assert.equal(store.selection.size, 0);
  });
});

describe('store.createProfile', () => {
  test('happy path: inserts server-authoritative profile into profiles dict', async () => {
    const store = await loadStore();
    installFetch({
      'POST /api/profiles': async () => ({
        status: 201,
        body: { profile: { name: 'p1', label: 'Profile 1', _serverVersion: 1 } },
      }),
    });
    await store.createProfile({ name: 'p1', label: 'Profile 1' });
    assert.equal(store.profiles.p1.label, 'Profile 1');
    assert.equal(store.profiles.p1._serverVersion, 1);
  });
});

describe('store.updateProfile', () => {
  test('rollback on 4xx: profile restored to original', async () => {
    const store = await loadStore();
    store.profiles = { p1: { name: 'p1', label: 'orig', _serverVersion: 1 } };
    installFetch({
      'PUT /api/profiles/p1': async () => ({
        status: 400,
        body: { success: false, error: 'bad name' },
      }),
    });
    let thrown = null;
    try {
      await store.updateProfile('p1', { label: 'new' });
    } catch (e) { thrown = e; }
    assert.ok(thrown, 'should throw on 4xx');
    assert.equal(store.profiles.p1.label, 'orig');   // rolled back
  });
});

describe('store.deleteProfile', () => {
  test('removes optimistic; server confirm leaves it absent', async () => {
    const store = await loadStore();
    store.profiles = { p1: { name: 'p1' } };
    installFetch({
      'DELETE /api/profiles/p1': async () => ({ status: 204, body: '' }),
    });
    await store.deleteProfile('p1');
    assert.equal(store.profiles.p1, undefined);
  });
});

describe('store.removePlaylistItem', () => {
  test('removes by index + clears selectedSubItem when it matches', async () => {
    const store = await loadStore();
    store.playlists = { Morning: { name: 'Morning', items: [{ file: 'a.mp4' }, { file: 'b.mp4' }, { file: 'c.mp4' }], _serverVersion: 4 } };
    store.selectedSubItem = { playlistName: 'Morning', index: 1 };
    installFetch({
      'PUT /api/playlists/Morning': async (opts) => {
        const body = JSON.parse(opts.body);
        return { status: 200, body: { playlist: { name: 'Morning', items: body.items, _serverVersion: 5 } } };
      },
    });
    await store.removePlaylistItem('Morning', 1);
    assert.deepEqual(store.playlists.Morning.items.map(i => i.file), ['a.mp4', 'c.mp4']);
    assert.equal(store.playlists.Morning._serverVersion, 5);
    assert.equal(store.selectedSubItem, null);
  });

  test('out-of-range index throws without server call', async () => {
    const store = await loadStore();
    store.playlists = { Morning: { name: 'Morning', items: [{ file: 'a.mp4' }], _serverVersion: 1 } };
    installFetch({}); // no handlers — would throw if a request were attempted
    await assert.rejects(() => store.removePlaylistItem('Morning', 5), /out of range/);
    assert.equal(store.playlists.Morning.items.length, 1);   // unchanged
  });

  test('leaves selection alone when it points at a different item', async () => {
    const store = await loadStore();
    store.playlists = { Morning: { name: 'Morning', items: [{ file: 'a' }, { file: 'b' }, { file: 'c' }], _serverVersion: 1 } };
    store.selectedSubItem = { playlistName: 'Morning', index: 2 };
    installFetch({
      'PUT /api/playlists/Morning': async (opts) => {
        const body = JSON.parse(opts.body);
        return { status: 200, body: { playlist: { name: 'Morning', items: body.items, _serverVersion: 2 } } };
      },
    });
    await store.removePlaylistItem('Morning', 0);   // remove index 0, selection at index 2
    // Selection unchanged in this implementation — operator may need to clear it themselves.
    // What matters: it's not the removed index, so the function leaves it.
    assert.deepEqual(store.selectedSubItem, { playlistName: 'Morning', index: 2 });
  });
});
