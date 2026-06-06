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
