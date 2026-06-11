/**
 * mmScheduleMobile — the phone Schedule stack (Section 3). Renders one of:
 *   - Day:   agenda (default) or vertical timeline (density sub-toggle)
 *   - Week:  day-sectioned agenda
 *   - Month: calendar-with-dots (tap a day -> Day agenda for that date)
 * via x-html, mirroring mmTimeline's compute-in-component / render-string
 * pattern. Reads viewMode/viewDate/schedules/displayGroups from the store.
 *
 * Phase C adds verticalTimelineHtml + monthGridHtml wiring; until then Day
 * density is agenda-only and Month falls through to agenda for the day.
 */
import { expandSchedule } from '../util/time.js';
import { agendaDayHtml, agendaWeekHtml } from './agenda-view.js';
import { monthGridHtml } from './month-grid.js';
import { openRecurrenceEditor, openScheduleCreator } from '../modals/recurrence-editor.js';
import { verticalTimelineHtml } from './vertical-timeline.js';

const DAY_MS = 24 * 60 * 60 * 1000;

function isoToUtcMidnight(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return Date.UTC(y, m - 1, d);
}

export function mmScheduleMobileComponent() {
  return {
    density: 'agenda',          // 'agenda' | 'vertical' (Day scope only)
    vtGroup: null,             // group shown in the vertical day timeline
    nowTick: 0,                 // bumped on a 30s interval to refresh now-state
    _timer: null,
    _onClick: null,

    init() {
      this._timer = setInterval(() => { this.nowTick++; }, 30_000);
      // Open the recurrence editor when an agenda row is tapped.
      this._onClick = (ev) => {
        const dayCell = ev.target.closest('[data-day-iso]');
        if (dayCell) {
          this.$store.mm.setViewDate(dayCell.dataset.dayIso);
          this.$store.mm.setViewMode('day');
          this.density = 'agenda';
          return;
        }
        const row = ev.target.closest('[data-schedule-id]');
        if (row) openRecurrenceEditor(this.$store.mm, row.dataset.scheduleId);
      };
      this.$root.addEventListener('click', this._onClick);
    },
    destroy() {
      if (this._timer) clearInterval(this._timer);
      if (this._onClick) this.$root.removeEventListener('click', this._onClick);
    },

    setDensity(d) { this.density = d; },
    openCreate() { openScheduleCreator(this.$store.mm, {}); },

    get tracks() {
      const groups = this.$store.mm.displayGroups;
      if (groups && groups.length > 0) return groups.map(g => g.displayID).filter(Boolean);
      const ids = new Set();
      for (const d of this.$store.mm.displays) if (d.displayID) ids.add(d.displayID);
      return Array.from(ids);
    },
    get vtGroupResolved() { return this.vtGroup || this.tracks[0] || null; },
    setVtGroup(id) { this.vtGroup = id; },

    _dayWindow() {
      const startMs = isoToUtcMidnight(this.$store.mm.viewDate);
      return { startMs, endMs: startMs + DAY_MS };
    },
    _weekStartMs() {
      const baseMs = isoToUtcMidnight(this.$store.mm.viewDate);
      const dow = (new Date(baseMs).getUTCDay() + 6) % 7; // Mon=0
      return baseMs - dow * DAY_MS;
    },
    _expandWindow(startMs, endMs) {
      const out = [];
      for (const s of this.$store.mm.schedules) out.push(...expandSchedule(s, startMs, endMs));
      return out;
    },

    render() {
      // Touch nowTick so Alpine re-renders this view on the 30s tick.
      void this.nowTick;
      if (!this.$store.mm.hydrated) return '<div style="color:var(--text-muted)">Loading…</div>';
      const mode = this.$store.mm.viewMode;
      const nowMs = Date.now();
      const playlists = this.$store.mm.playlists;
      const schedules = this.$store.mm.schedules;

      if (mode === 'week') {
        const weekStartMs = this._weekStartMs();
        const placements = this._expandWindow(weekStartMs, weekStartMs + 7 * DAY_MS);
        return agendaWeekHtml({ weekStartMs, tracks: this.tracks, placements, playlists, schedules, nowMs });
      }
      if (mode === 'month') {
        const did = this.$store.mm.selectedDisplay || this.tracks[0] || null;
        return monthGridHtml({
          schedules: this.$store.mm.schedules,
          viewDate: this.$store.mm.viewDate,
          displayID: did,
          expandSchedule,
        });
      }
      if (mode === 'day' && this.density === 'vertical') {
        const win = this._dayWindow();
        const did = this.vtGroupResolved;
        const placements = this._expandWindow(win.startMs, win.endMs).filter(p => p.displayID === did);
        return verticalTimelineHtml({ dayStartMs: win.startMs, placements, playlists, nowMs });
      }
      // Day -> day agenda.
      const win = this._dayWindow();
      const placements = this._expandWindow(win.startMs, win.endMs);
      return agendaDayHtml({ tracks: this.tracks, placements, playlists, schedules, nowMs });
    },
  };
}
