/**
 * PR-7: api.js auto-retry on 5xx + network errors. Spec §10.
 *
 * Each test installs a different fetch mock that fails N times then
 * succeeds (or always fails), and asserts the retry count + final
 * outcome. Delays are shrunk to [1, 1, 1]ms via the test-only override
 * so the suite finishes in milliseconds.
 */
import { test } from 'node:test';
import assert from 'node:assert';

// Each test loads a fresh module instance so the lazy fetch mock + the
// retry-delay override don't leak between cases.
async function loadApi() {
  const url = '../../../js/timeline/api.js?t=' + Date.now() + Math.random();
  return import(url);
}

function makeFetchMock(plan) {
  // plan: [{ status, body? } | { throw: Error }] — one entry per call.
  // After plan exhausts, every subsequent call returns the LAST entry.
  let i = 0;
  const calls = [];
  globalThis.fetch = async (url, opts) => {
    calls.push({ url, method: (opts && opts.method) || 'GET' });
    const step = plan[Math.min(i++, plan.length - 1)];
    if (step.throw) throw step.throw;
    return {
      ok: step.status >= 200 && step.status < 300,
      status: step.status,
      statusText: '',
      text: async () => typeof step.body === 'string' ? step.body : JSON.stringify(step.body ?? null),
    };
  };
  return calls;
}

test('5xx → retries, eventually succeeds', async () => {
  const calls = makeFetchMock([
    { status: 503, body: 'gateway' },
    { status: 503, body: 'gateway' },
    { status: 200, body: { playlists: [{ name: 'p' }] } },
  ]);
  const { api, __testOverrideRetryDelays } = await loadApi();
  const restore = __testOverrideRetryDelays([1, 1, 1]);
  try {
    const result = await api.listPlaylists();
    assert.equal(calls.length, 3);
    assert.deepEqual(result, [{ name: 'p' }]);
  } finally { restore(); }
});

test('Network error (fetch throws) → retries', async () => {
  const calls = makeFetchMock([
    { throw: new TypeError('Failed to fetch') },
    { status: 200, body: { schedules: [] } },
  ]);
  const { api, __testOverrideRetryDelays } = await loadApi();
  const restore = __testOverrideRetryDelays([1, 1, 1]);
  try {
    const result = await api.listSchedules();
    assert.equal(calls.length, 2);
    assert.deepEqual(result, []);
  } finally { restore(); }
});

test('4xx → NOT retried; throws immediately', async () => {
  const calls = makeFetchMock([{ status: 400, body: { error: 'bad input' } }]);
  const { api, __testOverrideRetryDelays } = await loadApi();
  const restore = __testOverrideRetryDelays([1, 1, 1]);
  try {
    await assert.rejects(api.listMedia(), (e) => e.status === 400);
    assert.equal(calls.length, 1, 'expected exactly 1 call (no retries) for 4xx');
  } finally { restore(); }
});

test('412 → NOT retried; the refetch-merge path handles it elsewhere', async () => {
  const calls = makeFetchMock([{ status: 412, body: { error: 'stale' } }]);
  const { api, __testOverrideRetryDelays } = await loadApi();
  const restore = __testOverrideRetryDelays([1, 1, 1]);
  try {
    await assert.rejects(
      api.updateSchedule('sid', { startTime: '10:00' }, 1),
      (e) => e.status === 412
    );
    assert.equal(calls.length, 1);
  } finally { restore(); }
});

test('Always-5xx → 4 attempts (1 initial + 3 retries), final throw', async () => {
  const calls = makeFetchMock([{ status: 503, body: 'down' }]);
  const { api, __testOverrideRetryDelays } = await loadApi();
  const restore = __testOverrideRetryDelays([1, 1, 1]);
  try {
    await assert.rejects(api.listDevices(), (e) => e.status === 503);
    assert.equal(calls.length, 4, 'expected 1 initial + 3 retries');
  } finally { restore(); }
});

test('POST mutations also retry on 5xx', async () => {
  const calls = makeFetchMock([
    { status: 502, body: 'bad gw' },
    { status: 201, body: { schedule: { id: 'sch1', _serverVersion: 1 } } },
  ]);
  const { api, __testOverrideRetryDelays } = await loadApi();
  const restore = __testOverrideRetryDelays([1, 1, 1]);
  try {
    const result = await api.createSchedule({ playlistName: 'p', displayID: 'd' });
    // createSchedule unwraps the envelope; result is the schedule itself
    assert.ok(result && (result.id === 'sch1' || (result.schedule && result.schedule.id === 'sch1')),
      `expected schedule in result, got ${JSON.stringify(result)}`);
    assert.equal(calls.length, 2);
    // Both calls were POSTs to the right URL
    assert.equal(calls[0].method, 'POST');
    assert.match(calls[0].url, /\/api\/schedules/);
  } finally { restore(); }
});

test('DELETE also retries on 5xx', async () => {
  const calls = makeFetchMock([
    { status: 502, body: 'down' },
    { status: 204, body: '' },
  ]);
  const { api, __testOverrideRetryDelays } = await loadApi();
  const restore = __testOverrideRetryDelays([1, 1, 1]);
  try {
    await api.deleteSchedule('sid');
    assert.equal(calls.length, 2);
    assert.equal(calls[1].method, 'DELETE');
  } finally { restore(); }
});
