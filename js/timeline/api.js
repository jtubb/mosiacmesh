/**
 * Async wrappers over the PR-2 REST endpoints.
 *
 * GET methods land in PR-4a (listPlaylists, listSchedules, listProfiles,
 * listMedia, listDevices). PR-4b extends with POST/PUT/DELETE for the
 * create/edit/delete flows + a multipart `uploadMedia` for the media
 * bin's + Upload button.
 *
 * Every method returns the parsed JSON body on success, or throws an
 * ApiError on non-2xx. The thrown error has `.status` and `.body`
 * fields so callers can render a precise toast with the server's
 * `error` string and (for 412 stale) the `currentVersion` for resync.
 */

class ApiError extends Error {
  constructor(message, { status, body }) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function parseJsonOrText(resp) {
  const text = await resp.text();
  try { return text ? JSON.parse(text) : null; } catch (_) { return text; }
}

// PR-7: auto-retry on transient errors (spec §10).
//
// Network failures and 5xx responses get up to 3 retries with 2/5/10s
// backoff. 4xx (including 412 stale) are NOT retried — they're
// deterministic responses the caller already knows how to handle.
//
// On retry start a sticky 'Retrying…' toast surfaces the wait so the
// operator doesn't think their click was lost. The toast is dismissed
// when the retry chain ends — either successfully (returns) or
// permanently (the final throw propagates up to withRollback, which
// shows its own error toast). The spec calls for an in-toast Retry
// button on final failure; for v1 we deliver the auto-retry machinery
// and lean on the existing rollback-toast UX. An in-toast Retry button
// is a small follow-up.
// Exposed via api object so tests can shrink the delays to keep the
// suite fast. Production reads the original 2/5/10s.
const RETRY_DELAYS_MS = [2000, 5000, 10000];
let _retryDelaysOverride = null;
function _getRetryDelays() { return _retryDelaysOverride || RETRY_DELAYS_MS; }

function isTransientError(e) {
  if (!(e instanceof ApiError)) return true;          // network / fetch threw
  return e.status >= 500 && e.status < 600;
}

// Best-effort access to the Alpine store for toasts. The store is set
// up at admin bootstrap (index.js); api.js can be imported in Node
// tests before any store exists. Return null in those cases.
function _storeOrNull() {
  try {
    if (typeof window !== 'undefined' && window.Alpine && window.Alpine.store) {
      return window.Alpine.store('mm') || null;
    }
  } catch (_) { /* fall through */ }
  return null;
}

async function withRetry(fn) {
  const delays = _getRetryDelays();
  let retryToastId = null;
  let lastErr = null;
  for (let attempt = 0; attempt <= delays.length; attempt++) {
    try {
      const result = await fn();
      if (retryToastId != null) {
        const s = _storeOrNull();
        if (s) s.dismissToast(retryToastId);
      }
      return result;
    } catch (e) {
      lastErr = e;
      const giveUp = !isTransientError(e) || attempt === delays.length;
      if (giveUp) {
        if (retryToastId != null) {
          const s = _storeOrNull();
          if (s) s.dismissToast(retryToastId);
        }
        throw e;
      }
      // First transient → surface the 'Retrying…' toast.
      if (retryToastId == null) {
        const s = _storeOrNull();
        if (s) retryToastId = s.toast("Couldn't save — network issue. Retrying…", 'info', { sticky: true });
      }
      await new Promise((r) => setTimeout(r, delays[attempt]));
    }
  }
  throw lastErr;
}

async function getJson(url) {
  return withRetry(async () => {
    const resp = await fetch(url, {
      method: 'GET',
      headers: { 'Accept': 'application/json' },
      credentials: 'same-origin',
    });
    const body = await parseJsonOrText(resp);
    if (!resp.ok) throw new ApiError(`GET ${url} -> ${resp.status} ${resp.statusText}`, { status: resp.status, body });
    return body;
  });
}

async function postJson(url, body) {
  return withRetry(async () => {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(body),
    });
    const respBody = await parseJsonOrText(resp);
    if (!resp.ok) throw new ApiError(`POST ${url} -> ${resp.status} ${resp.statusText}`, { status: resp.status, body: respBody });
    return respBody;
  });
}

async function putJson(url, body, ifMatch) {
  const headers = {
    'Accept': 'application/json',
    'Content-Type': 'application/json',
  };
  if (ifMatch != null) headers['If-Match'] = String(ifMatch);
  return withRetry(async () => {
    const resp = await fetch(url, {
      method: 'PUT',
      headers,
      credentials: 'same-origin',
      body: JSON.stringify(body),
    });
    const respBody = await parseJsonOrText(resp);
    if (!resp.ok) throw new ApiError(`PUT ${url} -> ${resp.status} ${resp.statusText}`, { status: resp.status, body: respBody });
    return respBody;
  });
}

async function deleteReq(url) {
  return withRetry(async () => {
    const resp = await fetch(url, {
      method: 'DELETE',
      headers: { 'Accept': 'application/json' },
      credentials: 'same-origin',
    });
    if (!resp.ok && resp.status !== 204) {
      const body = await parseJsonOrText(resp);
      throw new ApiError(`DELETE ${url} -> ${resp.status} ${resp.statusText}`, { status: resp.status, body });
    }
    return null;
  });
}

async function uploadFile(url, file) {
  // Mirrors the legacy upload_handler — single-field multipart with the
  // file under any field name. Server reads via reader.next() so the
  // field name doesn't matter.
  const form = new FormData();
  form.append('file', file, file.name);
  const resp = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    body: form,
  });
  const body = await parseJsonOrText(resp);
  if (!resp.ok) throw new ApiError(`UPLOAD ${url} -> ${resp.status} ${resp.statusText}`, { status: resp.status, body });
  return body;
}

// PR-7: tests-only — override the 2/5/10s backoff with shorter delays
// (or [] to disable retry entirely) so the unit suite finishes in
// milliseconds instead of 17s per retry test. Returns a restore fn.
export function __testOverrideRetryDelays(delaysMs) {
  const prev = _retryDelaysOverride;
  _retryDelaysOverride = Array.isArray(delaysMs) ? delaysMs : null;
  return () => { _retryDelaysOverride = prev; };
}

export const api = {
  // ---- Read (PR-4a) ----
  async listPlaylists()  { const b = await getJson('/api/playlists');           return b?.playlists ?? []; },
  async listSchedules()  { const b = await getJson('/api/schedules');           return b?.schedules ?? []; },
  async listProfiles()   { const b = await getJson('/api/profiles');            return b?.profiles ?? []; },
  async listMedia()      { return await getJson('/api/media'); },
  async listDevices()    { return await getJson('/api/discovery/devices'); },

  // ---- Schedules ----
  /** POST /api/schedules — body must include playlistName + displayID. Returns the created schedule. */
  async createSchedule(partial) {
    const b = await postJson('/api/schedules', partial);
    return b?.schedule;
  },
  /** PUT /api/schedules/{id} — partial patch + If-Match. Returns the updated schedule (new _serverVersion). */
  async updateSchedule(id, patch, ifMatch) {
    const b = await putJson(`/api/schedules/${encodeURIComponent(id)}`, patch, ifMatch);
    return b?.schedule;
  },
  /** DELETE /api/schedules/{id} — 204 on success. */
  async deleteSchedule(id) {
    return await deleteReq(`/api/schedules/${encodeURIComponent(id)}`);
  },

  // ---- Playlists ----
  /** POST /api/playlists — body must include name. */
  async createPlaylist(partial) {
    const b = await postJson('/api/playlists', partial);
    return b?.playlist;
  },
  /** PUT /api/playlists/{name} — partial patch + If-Match. */
  async updatePlaylist(name, patch, ifMatch) {
    const b = await putJson(`/api/playlists/${encodeURIComponent(name)}`, patch, ifMatch);
    return b?.playlist;
  },
  /** DELETE /api/playlists/{name} — 204 or 409+refs. */
  async deletePlaylist(name) {
    return await deleteReq(`/api/playlists/${encodeURIComponent(name)}`);
  },

  // ---- Refetch (used by 412 conflict resolver) ----
  // Prefer the single-item GET if the server supports it (registered in PR-4c);
  // fall back to the list endpoint + filter so older deployments also work.
  async refetchSchedule(id) {
    try {
      const b = await getJson('/api/schedules/' + encodeURIComponent(id));
      if (b?.schedule) return b.schedule;
    } catch (_) { /* fall through to list */ }
    const b = await getJson('/api/schedules');
    return (b?.schedules || []).find(s => s.id === id) ?? null;
  },
  async refetchPlaylist(name) {
    try {
      const b = await getJson('/api/playlists/' + encodeURIComponent(name));
      if (b?.playlist) return b.playlist;
    } catch (_) { /* fall through to list */ }
    const b = await getJson('/api/playlists');
    return (b?.playlists || []).find(p => p.name === name) ?? null;
  },
  async refetchProfile(name) {
    try {
      const b = await getJson('/api/profiles/' + encodeURIComponent(name));
      if (b?.profile) return b.profile;
    } catch (_) { /* fall through to list */ }
    const b = await getJson('/api/profiles');
    return (b?.profiles || []).find(p => p.name === name) ?? null;
  },

  // ---- Profiles ----
  /** POST /api/profiles — body must include name. Returns the created profile. */
  async createProfile(profile) {
    const b = await postJson('/api/profiles', profile);
    return b?.profile ?? b;
  },
  /** PUT /api/profiles/{name} — partial patch + If-Match. Returns the updated profile. */
  async updateProfile(name, patch, ifMatch) {
    const b = await putJson(`/api/profiles/${encodeURIComponent(name)}`, patch, ifMatch);
    return b?.profile ?? b;
  },
  /** DELETE /api/profiles/{name} — 204 on success, 409+refs when in use. */
  async deleteProfile(name) {
    return await deleteReq(`/api/profiles/${encodeURIComponent(name)}`);
  },
  async assignProfile(clientKey, profileName) {
    // Server explicitly rejects empty string with 'use null to clear
    // the profile'. A <select> with <option value=""> produces ''
    // for the (no override) sentinel — normalise here so callers
    // don't have to remember the server quirk.
    const body = { profileName: profileName === '' ? null : profileName };
    const b = await postJson(`/api/clients/${encodeURIComponent(clientKey)}/profile`, body);
    return b;
  },

  // ---- Media ----
  /** POST /upload/image or /upload/video based on extension. Returns server's response. */
  async uploadMedia(file) {
    const ext = (file.name.split('.').pop() || '').toLowerCase();
    const isVideo = ['mp4', 'mov', 'mkv', 'webm', 'avi'].includes(ext);
    const dest = isVideo ? 'video' : 'image';
    return await uploadFile(`/upload/${dest}`, file);
  },
};

export { ApiError };
