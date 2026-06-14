/**
 * Month calendar-with-dots. Extracted from timeline.js renderMonth so the
 * desktop grid and the mobile Schedule stack share one renderer. Pure:
 * (schedules, viewDate, displayID, expandSchedule) -> HTML string.
 *
 * Each day cell carries data-day-iso so the mobile view can tap-drill into
 * that day's agenda. The desktop grid ignores the attribute.
 */
import { monthWeekdayHeaderHtml } from '../timeline/grid-axis.js';
import { escapeAttr } from './util.js';

const DAY_MS = 24 * 60 * 60 * 1000;

function colorForPlaylist(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return `hsl(${h % 360} 65% 55%)`;
}

export function monthGridHtml({ schedules, viewDate, displayID, expandSchedule }) {
  if (!displayID) return '<div style="color:var(--text-muted)">Pick a display to view the month.</div>';
  const [y, m] = viewDate.split('-').map(Number);
  const startMs = Date.UTC(y, m - 1, 1);
  const endMs = Date.UTC(y, m, 1);

  const perDay = {};
  for (const s of schedules) {
    if (s.displayID !== displayID) continue;
    for (const p of expandSchedule(s, startMs, endMs)) {
      const d = new Date(p.startMs);
      const iso = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
      (perDay[iso] = perDay[iso] || new Set()).add(p.playlistName);
    }
  }

  const firstDow = (new Date(startMs).getUTCDay() + 6) % 7;
  const daysInMonth = new Date(endMs - DAY_MS).getUTCDate();

  let html = '<div class="mm-month-grid" style="display:grid; grid-template-columns: repeat(7, 1fr); gap:2px;">';
  html += monthWeekdayHeaderHtml();
  for (let i = 0; i < firstDow; i++) html += '<div class="mm-month-cell mm-month-cell-blank"></div>';
  for (let day = 1; day <= daysInMonth; day++) {
    const iso = `${y}-${String(m).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    const playlists = Array.from(perDay[iso] || []);
    const dots = playlists.map(pl =>
      `<span class="mm-month-dot" title="${escapeAttr(pl)}" style="background:${colorForPlaylist(pl)}"></span>`
    ).join('');
    html += `<div class="mm-month-cell" data-day-iso="${iso}">
      <div class="mm-month-num">${day}</div>
      <div class="mm-month-dots">${dots}</div>
    </div>`;
  }
  html += '</div>';
  return html;
}
