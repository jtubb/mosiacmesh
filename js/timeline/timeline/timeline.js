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
import { renderWeekStripesHtml } from './conflict-stripes.js';

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
function basename(p) { return String(p || '').split('/').pop() || ''; }

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
      // PR-4b: explicit grid-row per track row so the .mm-track-droparea
      // cell (cols 2-26) shares its cell with the clips that overlay it.
      // Without explicit rows, auto-placement could split clips and the
      // droparea onto different rows when their counts disagree.
      let html = `<div class="mm-day-grid" style="display:grid; grid-template-columns: 110px repeat(24, 1fr); grid-auto-rows: minmax(32px, auto); gap:2px;">`;
      // Axis row
      html += `<div class="mm-axis-cell" style="grid-row:1; grid-column:1">Track</div>`;
      html += dayAxisHtml();
      // Tracks
      for (let i = 0; i < tracks.length; i++) {
        const did = tracks[i];
        const row = i + 2;
        const placements = this.placementsForTrack(did);
        const conflicts = detectConflicts(placements);
        const status = this.statusForTrack(did);
        // The track label IS the display-group name (displayID). Earlier
        // code grabbed `displays.find(...).friendlyName` here, which
        // returned the FIRST device's per-device friendly name (e.g.
        // "sign1screen15") rather than the group name (e.g. "Tablet") —
        // misleading the operator about which group the row represents.
        // Pass null friendlyName so track-header.js uses `displayID`.
        html += `<div class="mm-track-row" style="grid-row:${row}; grid-column:1">${trackHeaderHtml({
          displayID: did, friendlyName: null,
          onlineCount: status.online, totalCount: status.total,
          renderInProgress: status.renderInProgress
        })}</div>`;
        // PR-4b: per-track droparea covering all 24 hour columns. Used by
        // js/timeline/drag/playlist-to-track.js to handle drag→create.
        html += `<div class="mm-track-droparea" data-display-id="${escapeAttr(did)}" style="grid-row:${row}; grid-column:2 / 26"></div>`;
        const selection = this.$store.mm.selection;
        for (const p of placements) {
          const conflictRanges = conflicts
            .filter(c => c.loserId === p.scheduleId)
            .map(c => ({ overlapStartMs: c.overlapStartMs, overlapEndMs: c.overlapEndMs }));
          html += clipDayHtml({
            placement: p, viewDateMs: win.startMs, conflictRanges, gridRow: row,
            isSelected: selection.has(p.scheduleId),
          });
        }
      }
      html += `<div class="mm-now-line"></div>`;
      // PR-4b: drill-in sub-row placed via auto-flow at the row right
      // after the last explicit track row, spanning the full grid. The
      // header carries the playlist name so the association with the
      // drilled clip is obvious without complex inline placement.
      const drilled = this.$store.mm.drilledIn;
      if (drilled) {
        const allPlacements = tracks.flatMap(did => this.placementsForTrack(did));
        const dp = allPlacements.find(p => p.scheduleId === drilled);
        if (dp) html += this.renderDrillInRow(dp, this.$store.mm.playlists[dp.playlistName]);
      }
      html += `</div>`;
      return html;
    },

    renderDrillInRow(placement, playlist) {
      const items = (playlist && playlist.items) || [];
      const name = (playlist && playlist.name) || placement.playlistName;
      let inner;
      if (items.length === 0) {
        inner = `<div class="mm-drillin-empty">No items in playlist "${escapeText(name)}". Drag media files here to add.</div>`;
      } else {
        // Items run left-to-right across the row, sized by per-item
        // duration if known, else equal slices. Playback order is the
        // items array's order — there's no time-of-day inside a playlist,
        // so this is a visual approximation.
        const durations = items.map(it => Number((typeof it === 'object' && it && it.duration) || 0));
        const total = durations.reduce((a, b) => a + b, 0);
        const widths = total > 0 ? durations.map(d => (d / total) * 100) : items.map(() => 100 / items.length);
        // PR-4c gap-fix: highlight the selected sub-item so Del's effect
        // is obvious. Alpine reactivity on store.selectedSubItem causes
        // x-html to re-render this row when selection changes.
        const sel = this.$store.mm.selectedSubItem;
        const selectedIdx = (sel && sel.playlistName === name) ? sel.index : -1;
        let left = 0;
        inner = items.map((it, i) => {
          const file = (typeof it === 'string') ? it : (it && it.file) || '';
          const widthPct = widths[i];
          const cls = 'mm-drillin-item' + (i === selectedIdx ? ' mm-drillin-item-selected' : '');
          const html = `<div class="${cls}" data-item-index="${i}" style="left:${left}%; width:${widthPct}%;" title="${escapeAttr(file)}">${escapeText(basename(file))}</div>`;
          left += widthPct;
          return html;
        }).join('');
      }
      return `<div class="mm-drillin-row" style="grid-column:1 / 26" data-playlist-name="${escapeAttr(name)}" data-schedule-id="${escapeAttr(placement.scheduleId)}">
        <div class="mm-drillin-header"><span class="mm-drillin-label">${escapeText(name)}</span> <span class="mm-drillin-hint">single-click to select · double-click to edit · Del to remove</span></div>
        <div class="mm-drillin-items">${inner}</div>
      </div>`;
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
      // grid-template-rows pinned: header row auto, plus 24 fixed-height
      // hour rows. Without this, CSS Grid auto-sizes the hour rows from
      // their content's min-content, which collapses to ~0px when a
      // multi-row clip claims the entire column in those rows and pushes
      // the per-row week-cell out — there's no per-row content left to
      // hold the row open. Result: a 09:00-17:00 schedule rendered as
      // a single-row-tall block instead of 8 hours tall.
      // The explicit row sizing also gives a consistent vertical scale
      // (24px per hour = 576px total grid height) regardless of how many
      // schedules occupy each row.
      const ROW_PX = 24;
      let html = `<div class="mm-week-grid" style="display:grid; grid-template-columns: 60px repeat(7, 1fr); grid-template-rows: auto repeat(24, ${ROW_PX}px); gap:2px;">`;
      // Header: hour-label col + 7 day labels
      html += `<div class="mm-axis-cell" style="grid-column:1">hr</div>`;
      html += weekAxisHtml(win.startMs);
      // Rows: one per hour. Explicit grid-row keeps each label aligned
      // with its row even if a multi-row clip later spans across.
      for (let h = HOUR_START; h < HOUR_END; h++) {
        const row = 2 + h;
        html += `<div class="mm-axis-cell" style="grid-column:1; grid-row:${row}">${String(h).padStart(2,'0')}</div>`;
        for (let dIdx = 0; dIdx < 7; dIdx++) {
          html += `<div class="mm-week-cell" style="grid-column:${dIdx + 2}; grid-row:${row}"></div>`;
        }
      }
      // Position clips: 1 column per day, vertical extent = % of hour range
      for (const p of all) {
        const dayIdx = Math.floor((p.startMs - win.startMs) / DAY_MS);
        if (dayIdx < 0 || dayIdx > 6) continue;
        const dayStart = win.startMs + dayIdx * DAY_MS;
        const hStart = (p.startMs - dayStart) / (60*60*1000);
        const hEnd   = Math.min(24, (p.endMs   - dayStart) / (60*60*1000));
        // Filter conflicts to ones that actually intersect THIS
        // placement. Without the time-intersection guard, a daily-
        // recurring schedule's `loserId === p.scheduleId` match picks
        // up cross-day conflict entries whose overlap ranges sit in
        // other days — clamped to 24h by hourFractionFromDayStart,
        // they produced `top:187.5%; height:0%` stripes. The day-view
        // path doesn't need this guard because day-view only expands
        // placements for a single day.
        const conflictRanges = conflicts
          .filter(c => c.loserId === p.scheduleId
                    && c.overlapStartMs < p.endMs
                    && c.overlapEndMs   > p.startMs)
          .map(c => ({ overlapStartMs: c.overlapStartMs, overlapEndMs: c.overlapEndMs }));
        // PR-4c gap-fix (spec §367): diagonal stripe overlay on the
        // lower-priority clip in an overlap region. Day view already
        // has this via clip.js; week view emits its own here with
        // vertical orientation (top/height %, since week clips are
        // sized vertically inside their day column).
        const stripes = renderWeekStripesHtml(conflictRanges, dayStart, hStart, hEnd);
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
            ${stripes}
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
