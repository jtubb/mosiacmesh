/**
 * Optimistic-local + server-confirm + rollback wrapper for store
 * mutations. The standard shape for every PR-4b mutation method:
 *
 *   await withRollback(this, ['schedules'],
 *     () => { this.schedules.push(temp); },        // local mutation
 *     async () => await api.createSchedule(body),  // server call
 *   );
 *
 * - Snapshots a deep-clone of each named store slice BEFORE the local
 *   mutation runs.
 * - Runs the mutation locally so the UI updates immediately.
 * - Awaits the API call. On success, returns its result.
 * - On error, restores every snapshotted slice and emits a toast with
 *   the server's `error` string (falling back to `e.message`).
 * - Re-throws so the caller can chain extra cleanup (e.g. removing an
 *   ephemeral placeholder by id) — but the snapshot restoration has
 *   already happened.
 *
 * Pure function aside from the `store.toast(...)` call. Testable in
 * Node without DOM.
 */

function deepClone(value) {
  if (value === null || typeof value !== 'object') return value;
  if (Array.isArray(value)) return value.map(deepClone);
  if (value instanceof Set) return new Set(Array.from(value).map(deepClone));
  const out = {};
  for (const k of Object.keys(value)) out[k] = deepClone(value[k]);
  return out;
}

export async function withRollback(store, snapshotKeys, mutationFn, apiFn) {
  const snapshot = {};
  for (const k of snapshotKeys) snapshot[k] = deepClone(store[k]);
  try {
    mutationFn();
    return await apiFn();
  } catch (e) {
    for (const k of snapshotKeys) store[k] = snapshot[k];
    const errMsg = (e && e.body && e.body.error) || (e && e.message) || String(e);
    if (typeof store.toast === 'function') store.toast(errMsg, 'error');
    throw e;
  }
}
