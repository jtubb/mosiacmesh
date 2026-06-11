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
import { buildNowSummary } from './now-summary.js';
import { buildContentItems } from './content/content-items.js';

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
    // PR-12: display GROUPS — first-class. Hydrated from GET /api/displays,
    // includes groups with zero clients. The timeline reads tracks from here
    // (not from deduping client.displayID), so empty groups are visible and
    // operators can pre-stage schedules for displays not yet online.
    displayGroups: [],
    playlists: {},
    schedules: [],
    media: { images: [], videos: [], videoDurations: {} },
    profiles: {},

    viewMode: 'day',
    viewDate: todayIso(),
    selectedDisplay: null,

    activeTab: 'now',                 // 'now' | 'content' | 'schedule' | 'fleet'
    connection: { connected: false, onlineClients: 0 },
    playback: {},                     // displayID -> {state, currentPlaylist, startedEpoch, renderStatus}

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
        const [pl, sc, pr, me, dv, dg, pb] = await Promise.all([
          api.listPlaylists(),
          api.listSchedules(),
          api.listProfiles(),
          api.listMedia(),
          api.listDevices(),
          api.listDisplays(),
          api.getPlayback(),
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
        this.displayGroups = dg ?? [];
        this.playback = Object.fromEntries((pb || []).map((r) => [r.displayID, r]));
        // Default Week-view display = first display group (was: first
        // client). Groups + clients are correlated by displayID so the
        // existing per-group views keep working. Falls back to client
        // list if no groups yet (unusual but possible on a brand-new
        // server before any clients ever connect).
        if (this.selectedDisplay == null) {
          if (this.displayGroups.length > 0) {
            this.selectedDisplay = this.displayGroups[0].displayID ?? null;
          } else if (this.displays.length > 0) {
            this.selectedDisplay = this.displays[0].displayID
                                ?? this.displays[0].clientKey
                                ?? null;
          }
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

    // ---- Now view + connection + playback (Section 1) ----
    setActiveTab(tab) { this.activeTab = tab; },
    goTo(tab) { if (typeof location !== 'undefined') location.hash = '#' + tab; },
    setConnection(patch) { this.connection = { ...this.connection, ...patch }; },
    setPlayback(row) { if (row && row.displayID) this.playback[row.displayID] = row; },
    get nowCards() {
      return buildNowSummary({
        displayGroups: this.displayGroups,
        displays: this.displays,
        playback: this.playback,
        renderInProgress: this.renderInProgress,
      });
    },
    get contentItems() {
      const anims = (typeof window !== 'undefined' && window.MM_ANIMATIONS)
        ? window.MM_ANIMATIONS
        : (typeof globalThis !== 'undefined' && globalThis.MM_ANIMATIONS) || [];
      return buildContentItems({ media: this.media, animations: anims });
    },

    // ---- UI-state mutations (no server calls) ----
    setViewMode(mode)   { this.viewMode = mode; },
    setViewDate(isoYmd) { this.viewDate = isoYmd; },
    goToday()           { this.viewDate = todayIso(); },
    selectDisplay(id)   { this.selectedDisplay = id; },

    // ---- Toast state (PR-4b) ----
    toasts: [],
    _nextToastId: 1,
    /**
     * Push a toast. opts:
     *   sticky=true  → no auto-dismiss; caller (or a dismissToast click)
     *                  removes it. PR-7 uses this for the 'Retrying…' toast
     *                  that has to survive the 2/5/10s backoff cycle.
     * Backward-compatible with the original (msg, kind) signature.
     */
    toast(msg, kind = 'info', opts = {}) {
      const id = this._nextToastId++;
      const t = { id, msg: String(msg), kind, sticky: !!opts.sticky };
      this.toasts.push(t);
      // Auto-dismiss everything EXCEPT error or sticky toasts. Errors
      // stick until clicked (existing PR-4b convention); sticky opts
      // override the default for the in-flight retry case.
      const autoDismiss = !t.sticky && kind !== 'error';
      if (autoDismiss && typeof setTimeout === 'function') {
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
     * POST a new (empty) playlist. Optimistic: insert a placeholder dict
     * entry so the Playlists list shows it immediately; on success swap in
     * the server's authoritative object (with _serverVersion); on failure
     * (e.g. 409 duplicate name) roll back the slice + toast the error.
     */
    async createPlaylist(name) {
      const { withRollback } = await import('./util/optimistic.js');
      const { api } = await import('./api.js');
      return withRollback(this, ['playlists'], () => {
        this.playlists[name] = { name, items: [], _serverVersion: 0 };
      }, async () => {
        const created = await api.createPlaylist({ name });
        if (created && created.name) this.playlists[created.name] = created;
      });
    },

    /**
     * DELETE a playlist by name. Optimistic: drop the dict entry; on
     * failure (e.g. 409+refs when a schedule references it) roll back +
     * toast the server's error.
     */
    async deletePlaylist(name) {
      const { withRollback } = await import('./util/optimistic.js');
      const { api } = await import('./api.js');
      return withRollback(this, ['playlists'], () => {
        delete this.playlists[name];
      }, async () => {
        await api.deletePlaylist(name);
      });
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
      const { withRollback } = await import('./util/optimistic.js');
      const { api } = await import('./api.js');
      const current = this.profiles[name];
      if (!current) throw new Error(`profile not found: ${name}`);
      return withRollback(this, ['profiles'],
        () => { this.profiles[name] = { ...current, ...patch }; },
        async () => {
          const fresh = await api.updateProfile(name, patch, current._serverVersion);
          this.profiles[name] = fresh;
        },
        { conflictKind: 'profile', conflictId: name },
      );
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

    // ---- Display group CRUD (PR-12) ----

    async createDisplayGroup(displayID) {
      const { withRollback } = await import('./util/optimistic.js');
      const { api } = await import('./api.js');
      return withRollback(this, ['displayGroups'], () => {
        // Optimistic: insert a placeholder so the new track appears
        // immediately. Server response replaces it with the canonical
        // shape (which is identical for freshly-created groups).
        this.displayGroups.push({
          displayID, clients: [], clientCount: 0, onlineCount: 0, scheduleCount: 0,
        });
      }, async () => {
        const fresh = await api.createDisplay(displayID);
        const idx = this.displayGroups.findIndex(g => g.displayID === displayID);
        if (idx >= 0) this.displayGroups[idx] = fresh;
        else this.displayGroups.push(fresh);
      });
    },

    async deleteDisplayGroup(displayID) {
      const { withRollback } = await import('./util/optimistic.js');
      const { api } = await import('./api.js');
      return withRollback(this, ['displayGroups'], () => {
        this.displayGroups = this.displayGroups.filter(g => g.displayID !== displayID);
      }, async () => {
        await api.deleteDisplay(displayID);
      });
    },

    /**
     * PR-14: move a single device from its current group to `newDisplayID`.
     * Optimistic: updates client.displayID locally on the client record
     * + nudges both groups' clientCount/onlineCount/clients[] so the
     * track-header popover + status badges reflect the move immediately.
     * Server confirms with 200 ({success}) or rejects with 404 ("display
     * group not found — create it first"), in which case the slice
     * snapshot rolls back and the toast surfaces the server's error.
     */
    async assignDeviceToDisplay(clientKey, newDisplayID) {
      const { withRollback } = await import('./util/optimistic.js');
      const { api } = await import('./api.js');
      const client = this.displays.find(d => (d.clientKey || d.id) === clientKey);
      if (!client) throw new Error(`assignDeviceToDisplay: client '${clientKey}' not in store`);
      const oldDisplayID = client.displayID;
      if (oldDisplayID === newDisplayID) return;   // no-op
      return withRollback(this, ['displays', 'displayGroups'], () => {
        client.displayID = newDisplayID;
        const oldGroup = this.displayGroups.find(g => g.displayID === oldDisplayID);
        const newGroup = this.displayGroups.find(g => g.displayID === newDisplayID);
        const wasOnline = !!client.isOnline;
        if (oldGroup) {
          oldGroup.clients = (oldGroup.clients || []).filter(k => k !== clientKey);
          oldGroup.clientCount = Math.max(0, (oldGroup.clientCount || 1) - 1);
          if (wasOnline) oldGroup.onlineCount = Math.max(0, (oldGroup.onlineCount || 1) - 1);
        }
        if (newGroup) {
          newGroup.clients = [...(newGroup.clients || []), clientKey];
          newGroup.clientCount = (newGroup.clientCount || 0) + 1;
          if (wasOnline) newGroup.onlineCount = (newGroup.onlineCount || 0) + 1;
        }
      }, async () => {
        await api.assignDeviceToDisplay(clientKey, newDisplayID);
      });
    },

    /**
     * PR-15: atomic bulk move. Same optimistic shape as the single
     * assignDeviceToDisplay, but iterates over `clientKeys`. Groups
     * by source so a single call handles a heterogeneous selection
     * (e.g. moving some Tablets + some Mobiles to Lobby at once).
     *
     * Returns {moved, missing} from the server so callers can toast
     * an accurate summary. Missing keys are NOT a failure — the
     * Promise resolves successfully; the caller decides how to
     * surface the partial result.
     */
    async bulkAssignDevicesToDisplay(clientKeys, newDisplayID) {
      const { withRollback } = await import('./util/optimistic.js');
      const { api } = await import('./api.js');
      if (!Array.isArray(clientKeys) || clientKeys.length === 0) {
        throw new Error('bulkAssignDevicesToDisplay: clientKeys must be non-empty');
      }
      // Snapshot what needs to move (so the optimistic update is
      // correct even when some keys are unknown or already in the
      // target group). The withRollback snapshot covers `displays` +
      // `displayGroups` for a full revert on server failure.
      const targets = [];
      for (const ck of clientKeys) {
        const client = this.displays.find(d => (d.clientKey || d.id) === ck);
        if (client && client.displayID !== newDisplayID) {
          targets.push({ clientKey: ck, oldDisplayID: client.displayID, wasOnline: !!client.isOnline });
        }
      }
      if (targets.length === 0) return { moved: [], missing: [] };
      let result;
      await withRollback(this, ['displays', 'displayGroups'], () => {
        for (const t of targets) {
          const client = this.displays.find(d => (d.clientKey || d.id) === t.clientKey);
          client.displayID = newDisplayID;
          const oldGroup = this.displayGroups.find(g => g.displayID === t.oldDisplayID);
          if (oldGroup) {
            oldGroup.clients = (oldGroup.clients || []).filter(k => k !== t.clientKey);
            oldGroup.clientCount = Math.max(0, (oldGroup.clientCount || 1) - 1);
            if (t.wasOnline) oldGroup.onlineCount = Math.max(0, (oldGroup.onlineCount || 1) - 1);
          }
        }
        const newGroup = this.displayGroups.find(g => g.displayID === newDisplayID);
        if (newGroup) {
          for (const t of targets) {
            newGroup.clients = [...(newGroup.clients || []), t.clientKey];
            newGroup.clientCount = (newGroup.clientCount || 0) + 1;
            if (t.wasOnline) newGroup.onlineCount = (newGroup.onlineCount || 0) + 1;
          }
        }
      }, async () => {
        result = await api.bulkAssignDevicesToDisplay(clientKeys, newDisplayID);
      });
      return result || { moved: targets.map(t => t.clientKey), missing: [] };
    },

    /**
     * PR-16: delete a media file from the shared library.
     * Optimistic: removes the URL from store.media.images or .videos
     * (and from videoDurations) so the bin updates instantly; rollback
     * restores both on failure. 409+refs surfaces via the rollback +
     * toast path with the playlist names that block the delete.
     */
    async deleteMedia(url) {
      const { withRollback } = await import('./util/optimistic.js');
      const { api } = await import('./api.js');
      return withRollback(this, ['media'], () => {
        const m = this.media;
        if (m.images?.includes(url)) m.images = m.images.filter(u => u !== url);
        if (m.videos?.includes(url)) m.videos = m.videos.filter(u => u !== url);
        if (m.videoDurations && url in m.videoDurations) {
          const next = { ...m.videoDurations };
          delete next[url];
          m.videoDurations = next;
        }
      }, async () => {
        await api.deleteMedia(url);
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
