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
import { dayAxisHtml, weekAxisHtml, monthWeekdayHeaderHtml } from './grid-axis.js';
import { trackHeaderHtml } from './track-header.js';
import { clipDayHtml }   from './clip.js';

const DAY_MS = 24 * 60 * 60 * 1000;

function colorForPlaylist(name) {
  // Stable, content-derived color via a tiny string hash.
  let h = 0;
  for (let i = 0; i < name.length; i++) {
    h = (h * 31 + name.charCodeAt(i)) >>> 0;
  }
  return `hsl(${h % 360} 65% 55%)`;
}

function escapeText(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function escapeAttr(s) {
  return escapeText(s).replace(/"/g, '&quot;');
}
function formatHm(ms) {
  const d = new Date(ms);
  return `${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`;
}

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
        // The track label IS the display-group name (displayID). Earlier
        // code grabbed `displays.find(...).friendlyName` here, which
        // returned the FIRST device's per-device friendly name (e.g.
        // "sign1screen15") rather than the group name (e.g. "Tablet") —
        // misleading the operator about which group the row represents.
        // Pass null friendlyName so track-header.js uses `displayID`.
        html += `<div class="mm-track-row" style="grid-column:1">${trackHeaderHtml({
          displayID: did, friendlyName: null,
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
      html += `<div class="mm-now-line"></div>`;
      html += `</div>`;
      return html;
    },

    monthWindow() {
      const [y, m] = this.$store.mm.viewDate.split('-').map(Number);
      const startMs = Date.UTC(y, m - 1, 1);
      const endMs   = Date.UTC(y, m, 1);
      return { startMs, endMs };
    },

    renderMonth() {
      const did = this.$store.mm.selectedDisplay;
      if (!did) return '<div style="color:var(--text-muted)">Pick a display to view the month.</div>';
      const win = this.monthWindow();
      // Build a map: dayIso -> [unique playlist names]
      const perDay = {};
      for (const s of this.$store.mm.schedules) {
        if (s.displayID !== did) continue;
        const placements = expandSchedule(s, win.startMs, win.endMs);
        for (const p of placements) {
          const d = new Date(p.startMs);
          const iso = `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
          if (!perDay[iso]) perDay[iso] = new Set();
          perDay[iso].add(p.playlistName);
        }
      }
      // Render calendar cells. Use the first day of the month, align
      // by getUTCDay (Mon=0..Sun=6).
      const firstDow = (new Date(win.startMs).getUTCDay() + 6) % 7;
      const daysInMonth = (new Date(win.endMs - DAY_MS).getUTCDate());

      let html = `<div class="mm-month-grid" style="display:grid; grid-template-columns: repeat(7, 1fr); gap:2px;">`;
      // Day-of-week header
      html += monthWeekdayHeaderHtml();
      // Leading blanks (cells before day 1)
      for (let i = 0; i < firstDow; i++) {
        html += `<div class="mm-month-cell mm-month-cell-blank"></div>`;
      }
      // Days
      for (let day = 1; day <= daysInMonth; day++) {
        const [y, m] = this.$store.mm.viewDate.split('-').map(Number);
        const iso = `${y}-${String(m).padStart(2,'0')}-${String(day).padStart(2,'0')}`;
        const playlists = Array.from(perDay[iso] || []);
        const dots = playlists.map(pl =>
          `<span class="mm-month-dot" title="${escapeAttr(pl)}" style="background:${colorForPlaylist(pl)}"></span>`
        ).join('');
        html += `<div class="mm-month-cell">
          <div class="mm-month-num">${day}</div>
          <div class="mm-month-dots">${dots}</div>
        </div>`;
      }
      html += `</div>`;
      return html;
    },

    weekWindow() {
      const [y, m, d] = this.$store.mm.viewDate.split('-').map(Number);
      const baseMs = Date.UTC(y, m - 1, d);
      // Find Monday of the week containing viewDate
      const dow = (new Date(baseMs).getUTCDay() + 6) % 7;
      const startMs = baseMs - dow * DAY_MS;
      return { startMs, endMs: startMs + 7 * DAY_MS };
    },

    renderWeek() {
      const did = this.$store.mm.selectedDisplay;
      if (!did) return '<div style="color:var(--text-muted)">Pick a display to view the week.</div>';
      const win = this.weekWindow();
      // Expand schedules for this one display across the week
      const all = [];
      for (const s of this.$store.mm.schedules) {
        if (s.displayID !== did) continue;
        all.push(...expandSchedule(s, win.startMs, win.endMs));
      }
      const conflicts = detectConflicts(all);

      // Hour rows 06..22 (typical wall-display operating hours); midnight
      // shoulders show as bonus rows.
      const HOUR_START = 0, HOUR_END = 24;
      let html = `<div class="mm-week-grid" style="display:grid; grid-template-columns: 60px repeat(7, 1fr); gap:2px;">`;
      // Header: hour-label col + 7 day labels
      html += `<div class="mm-axis-cell" style="grid-column:1">hr</div>`;
      html += weekAxisHtml(win.startMs);
      // Rows: one per hour
      for (let h = HOUR_START; h < HOUR_END; h++) {
        html += `<div class="mm-axis-cell" style="grid-column:1">${String(h).padStart(2,'0')}</div>`;
        for (let dIdx = 0; dIdx < 7; dIdx++) {
          html += `<div class="mm-week-cell" style="grid-column:${dIdx + 2}"></div>`;
        }
      }
      // Position clips: 1 column per day, vertical extent = % of hour range
      for (const p of all) {
        const dayIdx = Math.floor((p.startMs - win.startMs) / DAY_MS);
        if (dayIdx < 0 || dayIdx > 6) continue;
        const dayStart = win.startMs + dayIdx * DAY_MS;
        const hStart = (p.startMs - dayStart) / (60*60*1000);
        const hEnd   = Math.min(24, (p.endMs   - dayStart) / (60*60*1000));
        const conflictRanges = conflicts
          .filter(c => c.loserId === p.scheduleId)
          .map(c => ({ overlapStartMs: c.overlapStartMs, overlapEndMs: c.overlapEndMs }));
        // Position absolutely inside the day column. We use top/bottom %
        // relative to the (HOUR_END - HOUR_START)*100% total height.
        // Simpler: clip spans grid-row from hour h_start to h_end.
        const rowStart = 2 + Math.floor(hStart);
        const rowEnd   = 2 + Math.ceil(hEnd);
        html += `
          <div class="mm-clip" data-schedule-id="${escapeAttr(p.scheduleId)}"
               style="grid-column:${dayIdx + 2}; grid-row:${rowStart} / ${rowEnd};">
            <div class="mm-clip-title">${escapeText(p.playlistName)}</div>
            <div class="mm-clip-time">${formatHm(p.startMs)}–${formatHm(p.endMs)}</div>
          </div>
        `;
      }
      html += `<div class="mm-now-line"></div>`;
      html += `</div>`;
      return html;
    },

    render() {
      if (!this.$store.mm.hydrated) return '<div style="color:var(--text-muted)">Loading timeline…</div>';
      if (this.$store.mm.viewMode === 'day')   return this.renderDay();
      if (this.$store.mm.viewMode === 'week')  return this.renderWeek();
      if (this.$store.mm.viewMode === 'month') return this.renderMonth();
      return '';
    },
  };
}
