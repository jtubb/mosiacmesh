/**
 * Top toolbar: view-mode toggle, date nav, Today, display picker
 * (Week/Month modes), fleet-action buttons.
 *
 * Fleet actions proxy to the existing jQuery globals
 * (window.runScriptAll, etc.) rather than going through Alpine — this
 * keeps PR-4a compatible with the legacy SockJS-based fleet-action UX
 * that's been working in production.
 */

import { openProfileEditor } from './modals/profile-editor.js';
import { openCalibrationModal } from './modals/calibration.js';
import { fireFleetAction } from './modals/fleet-confirm.js';

const DAY_MS = 24 * 60 * 60 * 1000;

function isoDate(ms) {
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
}

export function mmToolbarComponent() {
  return {
    // PR-13: fleet-action scope. Empty string = "all devices" (the
    // legacy behaviour). Any other value is a displayID. The fleet
    // buttons read this on click so changing the scope doesn't fire
    // anything by itself — explicit click semantics preserved.
    fleetScope: '',
    get displays() { return this.$store.mm.displays; },
    get availableDisplayIds() {
      // Prefer first-class display groups (PR-12). Falls back to
      // client-derived list against a pre-PR-12 server.
      const groups = this.$store.mm.displayGroups;
      if (groups && groups.length > 0) return groups.map(g => g.displayID);
      const ids = new Set();
      for (const d of this.$store.mm.displays) if (d.displayID) ids.add(d.displayID);
      return Array.from(ids);
    },

    setMode(m) { this.$store.mm.setViewMode(m); },

    today() { this.$store.mm.goToday(); },

    openProfileEditor()  { openProfileEditor(this.$store.mm); },
    openCalibration()    { openCalibrationModal(this.$store.mm); },

    /**
     * PR-12: create a new display group. Simple prompt() for now — the
     * input is just an opaque string ID, no further fields needed at
     * create time (clientCount/scheduleCount populate as references
     * appear). Validation + 409 are surfaced via the optimistic
     * withRollback path: toast the server's error and revert the
     * placeholder track if the create fails.
     */
    async addDisplayGroup() {
      const raw = window.prompt('New display group name (e.g. Lobby, Tablet):');
      if (raw == null) return;
      const name = raw.trim();
      if (!name) return;
      if (this.$store.mm.displayGroups.some(g => g.displayID === name)) {
        this.$store.mm.toast(`Display group '${name}' already exists.`, 'warn');
        return;
      }
      try { await this.$store.mm.createDisplayGroup(name); }
      catch (_) { /* withRollback already toasted the server error */ }
    },

    /** Step by 1 day (Day view) / 7 days (Week) / 1 month (Month). */
    step(dir) {
      const cur = this.$store.mm.viewDate;
      const [y, m, d] = cur.split('-').map(Number);
      let next;
      if (this.$store.mm.viewMode === 'day') {
        next = Date.UTC(y, m - 1, d) + dir * DAY_MS;
        this.$store.mm.setViewDate(isoDate(next));
      } else if (this.$store.mm.viewMode === 'week') {
        next = Date.UTC(y, m - 1, d) + dir * 7 * DAY_MS;
        this.$store.mm.setViewDate(isoDate(next));
      } else {  // month
        next = Date.UTC(y, m - 1 + dir, 1);
        this.$store.mm.setViewDate(isoDate(next));
      }
    },

    setSelectedDisplay(id) { this.$store.mm.selectDisplay(id); },

    // PR-4c gap-fix (spec §363): route fleet actions through the
    // confirm modal in modals/fleet-confirm.js. >3 affected devices
    // prompts; ≤3 fires immediately. PR-13 adds the scope arg —
    // `this.fleetScope` is '' for all-devices or a displayID for
    // group-scoped actions; the modal + on-wire payload adjust
    // automatically.
    fleetAction(which) {
      fireFleetAction(this.$store.mm, which, this.fleetScope || null);
    },

    formatDate() {
      const cur = this.$store.mm.viewDate;
      const [y, m, d] = cur.split('-').map(Number);
      const dt = new Date(Date.UTC(y, m - 1, d));
      if (this.$store.mm.viewMode === 'day') {
        return dt.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
      }
      if (this.$store.mm.viewMode === 'week') {
        // Show week-of "Jun 1 – Jun 7, 2026"
        const dow = (dt.getUTCDay() + 6) % 7;
        const mon = new Date(Date.UTC(dt.getUTCFullYear(), dt.getUTCMonth(), dt.getUTCDate() - dow));
        const sun = new Date(mon.getTime() + 6 * DAY_MS);
        const fmt = (x) => x.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
        return `${fmt(mon)} – ${fmt(sun)}, ${sun.getUTCFullYear()}`;
      }
      // month
      return dt.toLocaleDateString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });
    },
  };
}
