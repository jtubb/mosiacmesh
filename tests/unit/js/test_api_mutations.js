/**
 * Unit tests for js/timeline/api.js mutation methods. Mocks global
 * fetch and asserts request shape: method, URL, headers (especially
 * If-Match), JSON body.
 */
import { test, describe, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const apiUrl = pathToFileURL(path.join(__dirname, '../../../js/timeline/api.js')).href;

let fetchCalls;
let origFetch;
function installFakeFetch(responses) {
  origFetch = globalThis.fetch;
  fetchCalls = [];
  let i = 0;
  globalThis.fetch = async (url, opts) => {
    const r = responses[i++] ?? responses[responses.length - 1];
    fetchCalls.push({ url, opts });
    return {
      ok: r.status >= 200 && r.status < 300,
      status: r.status,
      statusText: r.statusText || '',
      text: async () => typeof r.body === 'string' ? r.body : JSON.stringify(r.body),
    };
  };
}
function restoreFetch() {
  if (origFetch) globalThis.fetch = origFetch;
}

describe('api.createSchedule', () => {
  beforeEach(() => installFakeFetch([{ status: 201, body: { success: true, schedule: { id: 'sch_new', _serverVersion: 1 } } }]));
  afterEach(restoreFetch);

  test('POSTs to /api/schedules with JSON body', async () => {
    const { api } = await import(apiUrl + '?t=' + Date.now());
    const out = await api.createSchedule({ playlistName: 'P', displayID: 'D', startTime: '09:00', endTime: '17:00' });
    assert.equal(fetchCalls.length, 1);
    const c = fetchCalls[0];
    assert.equal(c.url, '/api/schedules');
    assert.equal(c.opts.method, 'POST');
    assert.equal(c.opts.headers['Content-Type'], 'application/json');
    const body = JSON.parse(c.opts.body);
    assert.equal(body.playlistName, 'P');
    assert.equal(body.displayID, 'D');
    assert.equal(out.id, 'sch_new');
  });
});

describe('api.updateSchedule', () => {
  beforeEach(() => installFakeFetch([{ status: 200, body: { success: true, schedule: { id: 'sch_1', _serverVersion: 7 } } }]));
  afterEach(restoreFetch);

  test('PUTs to /api/schedules/{id} with If-Match header', async () => {
    const { api } = await import(apiUrl + '?t=' + Date.now() + '_b');
    await api.updateSchedule('sch_1', { startTime: '10:00' }, 6);
    const c = fetchCalls[0];
    assert.equal(c.url, '/api/schedules/sch_1');
    assert.equal(c.opts.method, 'PUT');
    assert.equal(c.opts.headers['If-Match'], '6');
  });
});

describe('api.deleteSchedule', () => {
  beforeEach(() => installFakeFetch([{ status: 204, body: '' }]));
  afterEach(restoreFetch);

  test('DELETEs to /api/schedules/{id}', async () => {
    const { api } = await import(apiUrl + '?t=' + Date.now() + '_c');
    await api.deleteSchedule('sch_1');
    const c = fetchCalls[0];
    assert.equal(c.url, '/api/schedules/sch_1');
    assert.equal(c.opts.method, 'DELETE');
  });
});

describe('api.updatePlaylist', () => {
  beforeEach(() => installFakeFetch([{ status: 200, body: { success: true, playlist: { name: 'P', _serverVersion: 3 } } }]));
  afterEach(restoreFetch);

  test('PUTs to /api/playlists/{name} with If-Match', async () => {
    const { api } = await import(apiUrl + '?t=' + Date.now() + '_d');
    await api.updatePlaylist('P', { items: [{ file: '/m/a.mp4' }] }, 2);
    const c = fetchCalls[0];
    assert.equal(c.url, '/api/playlists/P');
    assert.equal(c.opts.method, 'PUT');
    assert.equal(c.opts.headers['If-Match'], '2');
  });
});

describe('api throws ApiError on non-2xx', () => {
  beforeEach(() => installFakeFetch([{ status: 412, body: { success: false, error: 'stale', currentVersion: 9 } }]));
  afterEach(restoreFetch);

  test('412 PUT throws ApiError with status + body', async () => {
    const mod = await import(apiUrl + '?t=' + Date.now() + '_e');
    let thrown = null;
    try {
      await mod.api.updateSchedule('sch_1', { startTime: '11:00' }, 5);
    } catch (e) { thrown = e; }
    assert.ok(thrown, 'expected ApiError to be thrown');
    assert.equal(thrown.status, 412);
    assert.equal(thrown.body.currentVersion, 9);
  });
});
