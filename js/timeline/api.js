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

async function getJson(url) {
  const resp = await fetch(url, {
    method: 'GET',
    headers: { 'Accept': 'application/json' },
    credentials: 'same-origin',
  });
  const body = await parseJsonOrText(resp);
  if (!resp.ok) throw new ApiError(`GET ${url} -> ${resp.status} ${resp.statusText}`, { status: resp.status, body });
  return body;
}

async function postJson(url, body) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(body),
  });
  const respBody = await parseJsonOrText(resp);
  if (!resp.ok) throw new ApiError(`POST ${url} -> ${resp.status} ${resp.statusText}`, { status: resp.status, body: respBody });
  return respBody;
}

async function putJson(url, body, ifMatch) {
  const headers = {
    'Accept': 'application/json',
    'Content-Type': 'application/json',
  };
  if (ifMatch != null) headers['If-Match'] = String(ifMatch);
  const resp = await fetch(url, {
    method: 'PUT',
    headers,
    credentials: 'same-origin',
    body: JSON.stringify(body),
  });
  const respBody = await parseJsonOrText(resp);
  if (!resp.ok) throw new ApiError(`PUT ${url} -> ${resp.status} ${resp.statusText}`, { status: resp.status, body: respBody });
  return respBody;
}

async function deleteReq(url) {
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
  async refetchSchedule(id)        { return await getJson('/api/schedules/' + encodeURIComponent(id)); },
  async refetchPlaylist(name)      { return await getJson('/api/playlists/' + encodeURIComponent(name)); },

  // ---- Profiles ----
  async assignProfile(clientKey, profileName) {
    const b = await postJson(`/api/clients/${encodeURIComponent(clientKey)}/profile`, { profileName });
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
