/**
 * Top-level Day-view renderer.
 *
 * The Alpine x-data='mmTimeline' wraps the timeline DOM region. On
 * store updates, the x-html binding re-runs render() which produces
 * the full grid HTML.
 *
 * Day view layout: CSS Grid with 25 columns (track-header label +
 * 24 hour columns) and N + 1 rows (axis header + N display tracks).
 *
 * Week and Month renderers land in Tasks 10-11; this task is
 * Day-view only so we can see something work end-to-end before
 * expanding.
 */
import { expandSchedule } from '../util/time.js';
import { detectConflicts } from '../util/conflicts.js';
import { dayAxisHtml }   from './grid-axis.js';
import { trackHeaderHtml } from './track-header.js';
import { clipDayHtml }   from './clip.js';

const DAY_MS = 24 * 60 * 60 * 1000;

export function mmTimelineComponent() {
  return {
    get visibleWindow() {
      const [y, m, d] = this.$store.mm.viewDate.split('-').map(Number);
      const startMs = Date.UTC(y, m - 1, d);
      return { startMs, endMs: startMs + DAY_MS };
    },

    get tracks() {
      // Unique displayIDs from the device list + 'Default' fallback
      const ids = new Set();
      for (const d of this.$store.mm.displays) {
        if (d.displayID) ids.add(d.displayID);
      }
      if (ids.size === 0) ids.add('Default');
      return Array.from(ids);
    },

    placementsForTrack(displayID) {
      const win = this.visibleWindow;
      const out = [];
      for (const s of this.$store.mm.schedules) {
        if (s.displayID !== displayID) continue;
        out.push(...expandSchedule(s, win.startMs, win.endMs));
      }
      return out;
    },

    statusForTrack(displayID) {
      let online = 0, total = 0;
      let renderInProgress = false;
      for (const c of this.$store.mm.displays) {
        if (c.displayID !== displayID) continue;
        total += 1;
        if (c.isOnline) online += 1;
      }
      if (this.$store.mm.renderInProgress[displayID]) renderInProgress = true;
      return { online, total, renderInProgress };
    },

    renderDay() {
      const tracks = this.tracks;
      const win = this.visibleWindow;
      let html = `<div class="mm-day-grid" style="display:grid; grid-template-columns: 110px repeat(24, 1fr); gap:2px;">`;
      // Axis row
      html += `<div class="mm-axis-cell" style="grid-column:1">Track</div>`;
      html += dayAxisHtml();
      // Tracks
      for (const did of tracks) {
        const placements = this.placementsForTrack(did);
        const conflicts = detectConflicts(placements);
        const status = this.statusForTrack(did);
        const friendly = (this.$store.mm.displays.find(c => c.displayID === did) || {}).friendlyName || did;
        html += `<div class="mm-track-row" style="grid-column:1">${trackHeaderHtml({
          displayID: did, friendlyName: friendly,
          onlineCount: status.online, totalCount: status.total,
          renderInProgress: status.renderInProgress
        })}</div>`;
        for (const p of placements) {
          const conflictRanges = conflicts
            .filter(c => c.loserId === p.scheduleId)
            .map(c => ({ overlapStartMs: c.overlapStartMs, overlapEndMs: c.overlapEndMs }));
          html += clipDayHtml({ placement: p, viewDateMs: win.startMs, conflictRanges });
        }
      }
      html += `</div>`;
      return html;
    },

    render() {
      if (!this.$store.mm.hydrated) return '<div style="color:var(--text-muted)">Loading timeline…</div>';
      if (this.$store.mm.viewMode === 'day')   return this.renderDay();
      if (this.$store.mm.viewMode === 'week')  return '<div>Week view: implemented in Task 10.</div>';
      if (this.$store.mm.viewMode === 'month') return '<div>Month view: implemented in Task 11.</div>';
      return '';
    },
  };
}
