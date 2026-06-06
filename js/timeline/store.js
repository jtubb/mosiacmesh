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
import { expandSchedule } from './util/time.js';

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
        // Re-shape playlists + profiles to lookup dicts (server returns arrays).
        // Warn (don't crash) on duplicate-name anomalies — the server's POST
        // 409 prevents new dupes but an older settings.dat could carry them.
        this.playlists = {};
        for (const p of (pl ?? [])) {
          if (p.name in this.playlists) {
            console.warn('[timeline] duplicate playlist name:', p.name);
          }
          this.playlists[p.name] = p;
        }
        this.profiles = {};
        for (const p of (pr ?? [])) {
          if (p.name in this.profiles) {
            console.warn('[timeline] duplicate profile name:', p.name);
          }
          this.profiles[p.name] = p;
        }
        this.schedules = sc ?? [];
        this.media     = me ?? { images: [], videos: [], videoDurations: {} };
        this.displays  = (dv?.devices) ?? [];
        // Default Week-view display = first display. Trailing `?? null` keeps
        // the field typed as string|null (never undefined) even when the
        // first device has neither displayID nor clientKey — anomaly state.
        if (this.selectedDisplay == null && this.displays.length > 0) {
          this.selectedDisplay = this.displays[0].displayID
                              ?? this.displays[0].clientKey
                              ?? null;
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

    // ---- Toast state (PR-4b) ----
    toasts: [],
    _nextToastId: 1,
    toast(msg, kind = 'info') {
      const id = this._nextToastId++;
      this.toasts.push({ id, msg: String(msg), kind });
      // Auto-dismiss info toasts after 4s; error toasts stick until clicked.
      if (kind !== 'error' && typeof setTimeout === 'function') {
        setTimeout(() => this.dismissToast(id), 4000);
      }
      return id;
    },
    dismissToast(id) {
      this.toasts = this.toasts.filter(t => t.id !== id);
    },

    // ---- Selection state (PR-4b) ----
    selection: new Set(),
    selectClip(id, multi = false) {
      if (!multi) {
        this.selection = new Set([id]);
      } else {
        const s = new Set(this.selection);
        if (s.has(id)) s.delete(id); else s.add(id);
        this.selection = s;
      }
    },
    clearSelection() { this.selection = new Set(); },

    // ---- Drill-in state (PR-4b) ----
    drilledIn: null,
    drillInto(id) {
      this.drilledIn = (this.drilledIn === id) ? null : id;
      // Collapsing the drill-in clears any sub-item selection so a
      // stray Del press later can't try to remove an item from a
      // playlist the operator can't see.
      if (this.drilledIn === null) this.selectedSubItem = null;
    },

    // ---- Sub-item selection (PR-4c gap-fix, spec §358) ----
    // When a playlist is drilled in, single-click on a sub-item sets
    // this to {playlistName, index}. The Del key handler in select.js
    // reads it and calls removePlaylistItem.
    selectedSubItem: null,
    selectSubItem(playlistName, index) {
      this.selectedSubItem = { playlistName, index };
    },
    clearSubItemSelection() { this.selectedSubItem = null; },

    // ---- Mutations (PR-4b) ----
    /**
     * POST a new schedule. Optimistic: append a placeholder with a temp
     * id; on success, swap in the server's authoritative id +
     * _serverVersion; on failure, roll back the array.
     */
    async createSchedule(partial) {
      const { withRollback } = await import('./util/optimistic.js');
      const { api } = await import('./api.js');
      const tempId = '__pending_' + Math.random().toString(36).slice(2);
      const placeholder = { id: tempId, _serverVersion: 0, ...partial };
      await withRollback(this, ['schedules'],
        () => { this.schedules.push(placeholder); },
        async () => {
          const created = await api.createSchedule(partial);
          const idx = this.schedules.findIndex(s => s.id === tempId);
          if (idx >= 0) this.schedules[idx] = created;
          else this.schedules.push(created);
          return created;
        },
      );
    },

    /**
     * PUT a partial patch with If-Match. Optimistic: apply the patch
     * locally; on success, replace with the server's returned object
     * (carrying the new _serverVersion); on failure (412 stale or
     * other), restore the pre-patch snapshot.
     */
    async updateSchedule(id, patch) {
      const { withRollback } = await import('./util/optimistic.js');
      const { api } = await import('./api.js');
      const cur = this.schedules.find(s => s.id === id);
      if (!cur) throw new Error(`updateSchedule: schedule '${id}' not found`);
      const ifMatch = cur._serverVersion;
      await withRollback(this, ['schedules'],
        () => { Object.assign(cur, patch); },
        async () => {
          const updated = await api.updateSchedule(id, patch, ifMatch);
          const idx = this.schedules.findIndex(s => s.id === id);
          if (idx >= 0) this.schedules[idx] = updated;
          return updated;
        },
        { conflictKind: 'schedule', conflictId: id },
      );
    },

    /**
     * DELETE a schedule. Optimistic: remove from local; on failure,
     * restore the snapshot (placeholder reinserted).
     */
    async deleteSchedule(id) {
      const { withRollback } = await import('./util/optimistic.js');
      const { api } = await import('./api.js');
      await withRollback(this, ['schedules'],
        () => { this.schedules = this.schedules.filter(s => s.id !== id); },
        async () => { await api.deleteSchedule(id); },
      );
    },

    /**
     * PUT a partial playlist patch with If-Match. Same rollback shape
     * as updateSchedule but the slice is `playlists` (a dict, not list).
     */
    async updatePlaylist(name, patch) {
      const { withRollback } = await import('./util/optimistic.js');
      const { api } = await import('./api.js');
      const cur = this.playlists[name];
      if (!cur) throw new Error(`updatePlaylist: playlist '${name}' not found`);
      const ifMatch = cur._serverVersion;
      await withRollback(this, ['playlists'],
        () => { this.playlists[name] = { ...cur, ...patch }; },
        async () => {
          const updated = await api.updatePlaylist(name, patch, ifMatch);
          this.playlists[name] = updated;
          return updated;
        },
        { conflictKind: 'playlist', conflictId: name },
      );
    },

    /**
     * Remove a single playlist item by index. Thin wrapper over
     * updatePlaylist — the server has no per-item endpoint, so this
     * just PUTs the full items array minus the indexed entry. Used by
     * the Del-on-sub-clip handler in select.js (PR-4c gap-fix). Also
     * clears selectedSubItem on success so the visual highlight goes
     * with the item.
     */
    async removePlaylistItem(name, index) {
      const cur = this.playlists[name];
      if (!cur) throw new Error(`removePlaylistItem: playlist '${name}' not found`);
      const items = (cur.items || []).slice();
      if (index < 0 || index >= items.length) {
        throw new Error(`removePlaylistItem: index ${index} out of range for '${name}'`);
      }
      items.splice(index, 1);
      await this.updatePlaylist(name, { items });
      // Only reaches this line on success (updatePlaylist throws on
      // rollback). Clear the selection so a follow-up Del press doesn't
      // try to remove the now-shifted index.
      if (this.selectedSubItem && this.selectedSubItem.playlistName === name
          && this.selectedSubItem.index === index) {
        this.selectedSubItem = null;
      }
    },

    // ---- Profile CRUD (PR-4c T-C1) ----

    async createProfile(profile) {
      const { withRollback } = await import('./util/optimistic.js');
      const { api } = await import('./api.js');
      return withRollback(this, ['profiles'], () => {
        // Optimistic: insert with a placeholder _serverVersion until
        // the server returns the authoritative copy.
        this.profiles[profile.name] = { ...profile, _serverVersion: 0 };
      }, async () => {
        const fresh = await api.createProfile(profile);
        this.profiles[fresh.name] = fresh;
      });
    },

    async updateProfile(name, patch) {
      // No opts.conflictKind: refetch-merge.js (T-A1) doesn't yet handle
      // the 'profile' kind. 412 here falls through to the plain-toast
      // rollback path. Extending refetch-merge for profiles is a follow-up.
      const { withRollback } = await import('./util/optimistic.js');
      const { api } = await import('./api.js');
      const current = this.profiles[name];
      if (!current) throw new Error(`profile not found: ${name}`);
      return withRollback(this, ['profiles'], () => {
        this.profiles[name] = { ...current, ...patch };
      }, async () => {
        const fresh = await api.updateProfile(name, patch, current._serverVersion);
        this.profiles[name] = fresh;
      });
    },

    async deleteProfile(name) {
      const { withRollback } = await import('./util/optimistic.js');
      const { api } = await import('./api.js');
      return withRollback(this, ['profiles'], () => {
        delete this.profiles[name];
      }, async () => {
        await api.deleteProfile(name);
      });
    },

    async assignProfileToClient(clientKey, profileName) {
      const { withRollback } = await import('./util/optimistic.js');
      const { api } = await import('./api.js');
      return withRollback(this, ['displays'], () => {
        const c = this.displays.find(d => d.clientKey === clientKey);
        if (c) c.profileName = profileName;
      }, async () => {
        await api.assignProfile(clientKey, profileName);
      });
    },

    // PR-4c: returns the next N concrete clip placements for a schedule,
    // looking forward from `fromIso` (default = today). Powers the
    // recurrence modal's "next 5 occurrences" preview. Re-uses the same
    // expander the day-grid renders with so the preview matches what
    // the operator will see once the schedule lands.
    nextOccurrences(scheduleId, n = 5, fromIso = null) {
      const s = this.schedules.find(x => x.id === scheduleId);
      if (!s) return [];
      const startIso = fromIso || new Date().toISOString().slice(0, 10);
      const [y, m, d] = startIso.split('-').map(Number);
      const fromMs = Date.UTC(y, m - 1, d);
      // 365 days forward is sufficient for any DAILY..YEARLY recurrence.
      const HORIZON_MS = 365 * 24 * 60 * 60 * 1000;
      const placements = expandSchedule(s, fromMs, fromMs + HORIZON_MS);
      return placements.slice(0, n);
    },
  };
}
