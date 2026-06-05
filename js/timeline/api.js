/**
 * Thin async wrappers over the PR-2 REST endpoints.
 *
 * GET-only in PR-4a (read-only timeline). PR-4b adds POST/PUT/DELETE
 * methods for the create/edit/delete flows.
 *
 * Every method returns the parsed JSON body on success, or throws on
 * non-2xx. The thrown Error has `.status` and `.body` fields so the
 * caller can render a precise toast (PR-4b — not used yet).
 */

class ApiError extends Error {
  constructor(message, { status, body }) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function getJson(url) {
  const resp = await fetch(url, {
    method: 'GET',
    headers: { 'Accept': 'application/json' },
    credentials: 'same-origin',
  });
  const text = await resp.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch (_) { body = text; }
  if (!resp.ok) {
    throw new ApiError(
      `GET ${url} -> ${resp.status} ${resp.statusText}`,
      { status: resp.status, body }
    );
  }
  return body;
}

export const api = {
  /** GET /api/playlists -> [{name, items, loop, _serverVersion}, ...] */
  async listPlaylists() {
    const b = await getJson('/api/playlists');
    return b?.playlists ?? [];
  },

  /** GET /api/schedules -> [Schedule, ...] */
  async listSchedules() {
    const b = await getJson('/api/schedules');
    return b?.schedules ?? [];
  },

  /** GET /api/profiles -> [ScriptingProfile, ...] */
  async listProfiles() {
    const b = await getJson('/api/profiles');
    return b?.profiles ?? [];
  },

  /** GET /api/media -> {images, videos, videoDurations} */
  async listMedia() {
    return await getJson('/api/media');
  },

  /**
   * GET /api/discovery/devices ->
   *   {devices: [{clientKey, displayID, friendlyName, isOnline, ...}], total, online}
   */
  async listDevices() {
    return await getJson('/api/discovery/devices');
  },
};

export { ApiError };
