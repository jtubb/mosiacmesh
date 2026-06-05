/**
 * Alpine.store('mm') — single source of truth for the timeline view.
 *
 * Shape:
 *   {
 *     // hydrated from REST
 *     displays:  [{clientKey, displayID, friendlyName, isOnline, ...}, ...],
 *     playlists: { name -> {name, items, loop, _serverVersion} },
 *     schedules: [{id, playlistName, displayID, freq, ..., _serverVersion}, ...],
 *     media:     {images: [...], videos: [...], videoDurations: {}},
 *     profiles:  { name -> ScriptingProfile },
 *     // UI state
 *     viewMode:  'day' | 'week' | 'month',
 *     viewDate:  ISO-date string ('YYYY-MM-DD'),
 *     selectedDisplay: displayID | null,   // for Week view
 *     // bookkeeping
 *     hydrated: false,
 *     hydrateError: null,
 *     renderInProgress: {},  // displayID -> bool
 *   }
 *
 * Mutation methods (createSchedule, updateSchedule, etc.) throw in
 * PR-4a — they're stubbed so a misclick during PR-4b development
 * surfaces a clear error rather than a silent no-op.
 */
import { api } from './api.js';

function todayIso() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${dd}`;
}

export function makeStore() {
  return {
    displays: [],
    playlists: {},
    schedules: [],
    media: { images: [], videos: [], videoDurations: {} },
    profiles: {},

    viewMode: 'day',
    viewDate: todayIso(),
    selectedDisplay: null,

    hydrated: false,
    hydrateError: null,
    renderInProgress: {},

    /**
     * Fire all five GETs in parallel; populate state on success.
     * On error, leaves the store empty and sets `hydrateError` so the
     * UI can show a retry banner.
     */
    async hydrate() {
      this.hydrated = false;
      this.hydrateError = null;
      try {
        const [pl, sc, pr, me, dv] = await Promise.all([
          api.listPlaylists(),
          api.listSchedules(),
          api.listProfiles(),
          api.listMedia(),
          api.listDevices(),
        ]);
        // Re-shape playlists + profiles to lookup dicts (server returns arrays)
        this.playlists = Object.fromEntries((pl ?? []).map(p => [p.name, p]));
        this.profiles  = Object.fromEntries((pr ?? []).map(p => [p.name, p]));
        this.schedules = sc ?? [];
        this.media     = me ?? { images: [], videos: [], videoDurations: {} };
        this.displays  = (dv?.devices) ?? [];
        // Default Week-view display = first display
        if (this.selectedDisplay == null && this.displays.length > 0) {
          this.selectedDisplay = this.displays[0].displayID
                              ?? this.displays[0].clientKey;
        }
        this.hydrated = true;
      } catch (e) {
        console.error('[timeline] hydrate failed:', e);
        this.hydrateError = e.message || String(e);
        this.hydrated = false;
      }
    },

    /**
     * SockJS-broadcast hook (wired in Task 13). Updates one display's
     * status fields in-place without re-fetching the full list.
     */
    setStatus(displayID, patch) {
      const d = this.displays.find(x => (x.displayID === displayID)
                                     || (x.clientKey === displayID));
      if (d) Object.assign(d, patch);
    },

    setRenderInProgress(displayID, inProgress) {
      this.renderInProgress = { ...this.renderInProgress, [displayID]: !!inProgress };
    },

    // ---- UI-state mutations (no server calls) ----
    setViewMode(mode)   { this.viewMode = mode; },
    setViewDate(isoYmd) { this.viewDate = isoYmd; },
    goToday()           { this.viewDate = todayIso(); },
    selectDisplay(id)   { this.selectedDisplay = id; },

    // ---- Stubs for PR-4b. Implemented later; throw if called now. ----
    async createSchedule(/*partial*/) {
      throw new Error('createSchedule: not implemented in PR-4a (lands in PR-4b)');
    },
    async updateSchedule(/*id, patch*/) {
      throw new Error('updateSchedule: not implemented in PR-4a (lands in PR-4b)');
    },
    async deleteSchedule(/*id*/) {
      throw new Error('deleteSchedule: not implemented in PR-4a (lands in PR-4b)');
    },
    async updatePlaylist(/*name, patch*/) {
      throw new Error('updatePlaylist: not implemented in PR-4a (lands in PR-4b)');
    },
  };
}
