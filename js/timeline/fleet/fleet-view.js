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
import { groupStatusLine, deviceRowsForGroup, calibrationSummary } from './fleet-status.js';
import { openPlayNowModal, fireStopNow } from '../modals/play-now.js';
import { fireFleetAction } from '../modals/fleet-confirm.js';
import { openCalibrationModal } from '../modals/calibration.js';
import { openProfileEditor } from '../modals/profile-editor.js';

export function mmFleetComponent() {
  return {
    selectedGroupId: null,
    bulkSelection: new Set(),   // clientKeys; reassigned on change for Alpine reactivity
    bulkTarget: '',

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
    statusFor(group) {
      return groupStatusLine(group, this.$store.mm.playback, this.$store.mm.renderInProgress);
    },
    calibrationFor(group) {
      return calibrationSummary(deviceRowsForGroup(group, this.$store.mm.displays));
    },

    // ---- navigation ----
    selectGroup(id) { this.selectedGroupId = id; this.bulkSelection = new Set(); this.bulkTarget = ''; },
    backToList() { this.selectedGroupId = null; },

    // ---- group-level actions (reuse existing modals/helpers) ----
    playNow() { if (this.selectedGroupId) openPlayNowModal(this.$store.mm, this.selectedGroupId); },
    stopNow() { if (this.selectedGroupId) fireStopNow(this.$store.mm, this.selectedGroupId); },
    renderNow() {
      const id = this.selectedGroupId;
      if (!id) return;
      if (typeof window.sock === 'undefined' || typeof window.generateMessage !== 'function') {
        this.$store.mm.toast('SockJS not available; reload the page.', 'error');
        return;
      }
      try {
        window.sock.send(window.generateMessage('SRV', 'RENDER', { displayID: id }));
        this.$store.mm.toast(`Render requested for "${id}".`, 'info');
      } catch (e) {
        this.$store.mm.toast(`Failed to send render: ${e?.message || e}`, 'error');
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
      } catch (_) { /* store toasts on failure */ }
      this.bulkSelection = new Set();
      this.bulkTarget = '';
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
