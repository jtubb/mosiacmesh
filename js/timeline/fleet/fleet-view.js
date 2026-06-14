/**
 * mmFleet — the Fleet destination (Section 4). Master-detail:
 *   - groups list (master) — one row per store.displayGroups entry
 *   - per-group detail — sectioned cards (Playback / Calibration /
 *     Device scripts / Devices), shown as a full-screen sheet on mobile.
 *
 * State + thin method wrappers only; the markup lives in admin.html as
 * Alpine templates so the device <select>/checkbox controls stay reactive.
 * Every action reuses an existing store mutator or modal.
 */
import { groupStatusLine, deviceRowsForGroup, calibrationSummary, playlistReadinessForGroup, deviceCacheStatus } from './fleet-status.js';
import { openPlayNowModal, fireStopNow } from '../modals/play-now.js';
import { fireFleetAction } from '../modals/fleet-confirm.js';
import { openCalibrationModal } from '../modals/calibration.js';
import { openProfileEditor } from '../modals/profile-editor.js';

export function mmFleetComponent() {
  return {
    selectedGroupId: null,
    bulkSelection: new Set(),   // clientKeys; reassigned on change for Alpine reactivity
    bulkTarget: '',             // move-to-group target in the selection toolbar
    bulkProfile: '',            // profile target: '' none, '__auto' auto-match, else name
    bulkScript: '',             // script target: '' none, else login|start|stop|reboot|test
    expandedDevice: null,       // clientKey of the inline-expanded device row (accordion)
    selectMode: false,          // bulk-select mode: rows show checkboxes + the selection bar

    // ---- derived ----
    get groups() { return this.$store.mm.displayGroups || []; },
    get selectedGroup() {
      return this.groups.find(g => g.displayID === this.selectedGroupId) || null;
    },
    get devices() { return deviceRowsForGroup(this.selectedGroup, this.$store.mm.displays); },
    get profileNames() { return Object.keys(this.$store.mm.profiles || {}).sort(); },
    get allSelected() {
      const d = this.devices;
      return d.length > 0 && d.every(x => this.bulkSelection.has(x.clientKey));
    },
    get playlistReadiness() {
      return playlistReadinessForGroup(this.selectedGroupId, this.$store.mm.playlists, this.$store.mm.renders);
    },
    statusFor(group) {
      return groupStatusLine(group, this.$store.mm.playback, this.$store.mm.renderInProgress);
    },
    calibrationFor(group) {
      return calibrationSummary(deviceRowsForGroup(group, this.$store.mm.displays));
    },
    cacheStatus(device) { return deviceCacheStatus(device); },

    // ---- navigation ----
    selectGroup(id) {
      this.selectedGroupId = id;
      this._resetSelection();
      this.expandedDevice = null;
      this.selectMode = false;
    },
    backToList() { this.selectedGroupId = null; },

    // ---- device-row UI: accordion expand + bulk-select mode ----
    _resetSelection() { this.bulkSelection = new Set(); this.bulkTarget = ''; this.bulkProfile = ''; this.bulkScript = ''; },
    toggleExpand(clientKey) {
      this.expandedDevice = this.expandedDevice === clientKey ? null : clientKey;
    },
    toggleSelectMode() {
      this.selectMode = !this.selectMode;
      this.expandedDevice = null;           // expand + select are mutually exclusive
      if (!this.selectMode) this._resetSelection();
    },

    // ---- group-level actions (reuse existing modals/helpers) ----
    playNow() { if (this.selectedGroupId) openPlayNowModal(this.$store.mm, this.selectedGroupId); },
    stopNow() { if (this.selectedGroupId) fireStopNow(this.$store.mm, this.selectedGroupId); },
    reloadGroup() {
      const id = this.selectedGroupId;
      if (!id) return;
      if (typeof window.sock === 'undefined' || typeof window.generateMessage !== 'function') {
        this.$store.mm.toast('SockJS not available; reload the page.', 'error');
        return;
      }
      try {
        window.sock.send(window.generateMessage('SRV', 'RELOAD', { displayID: id }));
        const count = (this.$store.mm.displays || []).filter(d => d.displayID === id).length;
        this.$store.mm.toast(`Reload sent to "${id}" (${count} device${count === 1 ? '' : 's'}).`, 'info');
      } catch (e) {
        this.$store.mm.toast(`Failed to send reload: ${e?.message || e}`, 'error');
      }
    },
    calibrate() { if (this.selectedGroupId) openCalibrationModal(this.$store.mm, this.selectedGroupId); },
    runScript(which) { if (this.selectedGroupId) fireFleetAction(this.$store.mm, which, this.selectedGroupId); },
    openProfiles() { openProfileEditor(this.$store.mm); },

    // ---- device management ----
    setDeviceProfile(clientKey, name) {
      this.$store.mm.assignProfileToClient(clientKey, name).catch(() => {});
    },
    moveDevice(clientKey, displayID) {
      this.$store.mm.assignDeviceToDisplay(clientKey, displayID).catch(() => {});
    },
    toggleBulk(clientKey) {
      const s = new Set(this.bulkSelection);
      if (s.has(clientKey)) s.delete(clientKey); else s.add(clientKey);
      this.bulkSelection = s;
    },
    toggleBulkAll() {
      this.bulkSelection = this.allSelected ? new Set() : new Set(this.devices.map(d => d.clientKey));
    },
    async bulkMove(displayID) {
      if (!displayID || this.bulkSelection.size === 0) return;
      const keys = [...this.bulkSelection];
      try {
        const res = await this.$store.mm.bulkAssignDevicesToDisplay(keys, displayID);
        const moved = (res && res.moved ? res.moved.length : keys.length);
        this.$store.mm.toast(`Moved ${moved} device${moved === 1 ? '' : 's'} to "${displayID}".`, 'info');
        // Clear only on success — a failed move keeps the selection so the
        // operator can retry without re-checking every device.
        this._resetSelection();
      } catch (_) { /* store toasts on failure; selection preserved for retry */ }
    },
    // Set a profile on every selected device. bulkProfile '__auto' => clear to
    // auto-match (empty string). Loops the per-client assign (no bulk endpoint).
    async bulkSetProfile() {
      const sel = this.bulkProfile;
      if (!sel || this.bulkSelection.size === 0) return;
      const name = sel === '__auto' ? '' : sel;
      const keys = [...this.bulkSelection];
      if (!window.confirm(`Set profile "${name || 'Auto-match'}" on ${keys.length} device${keys.length === 1 ? '' : 's'}?`)) return;
      let ok = 0;
      for (const k of keys) {
        try { await this.$store.mm.assignProfileToClient(k, name); ok += 1; } catch (_) { /* store toasts */ }
      }
      this.$store.mm.toast(`Profile set on ${ok} device${ok === 1 ? '' : 's'}.`, 'info');
      this._resetSelection();
    },
    // Run a lifecycle script on every selected device via per-client
    // RUN_SCRIPT {clientKey, script} (the WS handler supports single-client targets).
    bulkRunScript() {
      const which = this.bulkScript;
      if (!which || this.bulkSelection.size === 0) return;
      if (typeof window.sock === 'undefined' || typeof window.generateMessage !== 'function') {
        this.$store.mm.toast('SockJS not available; reload the page.', 'error');
        return;
      }
      const keys = [...this.bulkSelection];
      if (!window.confirm(`Run "${which}" on ${keys.length} selected device${keys.length === 1 ? '' : 's'}?`)) return;
      let sent = 0;
      for (const k of keys) {
        try { window.sock.send(window.generateMessage('SRV', 'RUN_SCRIPT', { clientKey: k, script: which })); sent += 1; } catch (_) { /* skip */ }
      }
      this.$store.mm.toast(`${which} sent to ${sent} device${sent === 1 ? '' : 's'}.`, 'info');
      this._resetSelection();
    },

    // ---- group CRUD ----
    newGroup() {
      const raw = window.prompt('New display group name (e.g. Lobby, Tablet):');
      if (raw == null) return;
      const name = raw.trim();
      if (!name) return;
      if (this.groups.some(g => g.displayID === name)) {
        this.$store.mm.toast(`Display group "${name}" already exists.`, 'warn');
        return;
      }
      this.$store.mm.createDisplayGroup(name).catch(() => {});
    },
    async deleteGroup() {
      const id = this.selectedGroupId;
      if (!id) return;
      if (!window.confirm(`Delete display group "${id}"? This cannot be undone.`)) return;
      try { await this.$store.mm.deleteDisplayGroup(id); this.backToList(); }
      catch (_) { /* store toasts 409+refs */ }
    },
  };
}
