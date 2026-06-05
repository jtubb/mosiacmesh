/**
 * Top toolbar: view-mode toggle, date nav, Today, display picker
 * (Week/Month modes), fleet-action buttons.
 *
 * Fleet actions proxy to the existing jQuery globals
 * (window.runScriptAll, etc.) rather than going through Alpine — this
 * keeps PR-4a compatible with the legacy SockJS-based fleet-action UX
 * that's been working in production.
 */

const DAY_MS = 24 * 60 * 60 * 1000;

function isoDate(ms) {
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
}

export function mmToolbarComponent() {
  return {
    get displays() { return this.$store.mm.displays; },
    get availableDisplayIds() {
      const ids = new Set();
      for (const d of this.$store.mm.displays) if (d.displayID) ids.add(d.displayID);
      return Array.from(ids);
    },

    setMode(m) { this.$store.mm.setViewMode(m); },

    today() { this.$store.mm.goToday(); },

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

    // Fleet actions proxy to existing jQuery handlers
    fleetAction(which) {
      if (typeof window.runScriptAll === 'function') {
        window.runScriptAll(which);
      } else {
        console.warn('[timeline] runScriptAll not available on window');
      }
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
