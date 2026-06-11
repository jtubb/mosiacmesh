/**
 * Day-agenda and Week (day-sectioned) assemblers. Pure string builders;
 * the mmScheduleMobile component computes the args from the store.
 *
 * agendaDayHtml({ tracks, placements, playlists, schedules, nowMs })
 *   - placements: all placements for the visible day (any group)
 *   - tracks: ordered displayIDs to show as sections
 * agendaWeekHtml({ weekStartMs, tracks, placements, playlists, schedules, nowMs })
 *   - placements: all placements across the 7-day window
 */
import { detectConflicts } from '../util/conflicts.js';
import {
  groupPlacementsByGroup, groupPlacementsByDay, isNowPlacement, escapeText,
  formatRecurrence,
} from './util.js';
import { agendaRowHtml } from './agenda-row.js';

const DAY_MS = 24 * 60 * 60 * 1000;

function recurrenceTextFor(schedules, scheduleId) {
  const s = schedules.find(x => x.id === scheduleId);
  return s ? formatRecurrence(s) : '';
}

/**
 * Render one group's rows. Conflict detection runs on THIS GROUP'S
 * placements only — detectConflicts has no per-display segmentation, so
 * feeding it cross-group placements would fabricate bogus conflicts
 * (a Lobby high-priority schedule "conflicting" with a Cafe one).
 */
function rowsForGroup(groupPlacements, playlists, schedules, nowMs) {
  if (groupPlacements.length === 0) {
    return '<div class="mm-agenda-empty">nothing scheduled</div>';
  }
  const losers = new Set(detectConflicts(groupPlacements).map(c => c.loserId));
  return groupPlacements.map(p => agendaRowHtml(p, playlists[p.playlistName], {
    isNow: isNowPlacement(p, nowMs),
    conflict: losers.has(p.scheduleId),
    recurrenceText: recurrenceTextFor(schedules, p.scheduleId),
  })).join('');
}

export function agendaDayHtml({ tracks, placements, playlists, schedules, nowMs }) {
  const byGroup = groupPlacementsByGroup(placements);
  let html = '<div class="mm-agenda">';
  for (const did of tracks) {
    const gp = byGroup[did] || [];
    html += `<section class="mm-agenda-group"><h3 class="mm-agenda-group-title">${escapeText(did)}</h3>`;
    html += rowsForGroup(gp, playlists, schedules, nowMs);
    html += '</section>';
  }
  html += '</div>';
  return html;
}

export function agendaWeekHtml({ weekStartMs, tracks, placements, playlists, schedules, nowMs }) {
  // Seven day-section headers; under each, the day's agenda grouped by group.
  const dayIsos = [];
  for (let i = 0; i < 7; i++) {
    const d = new Date(weekStartMs + i * DAY_MS);
    dayIsos.push(`${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`);
  }
  const byDay = groupPlacementsByDay(placements, dayIsos);
  let html = '<div class="mm-agenda mm-agenda-week">';
  for (let i = 0; i < 7; i++) {
    const iso = dayIsos[i];
    const dayMs = weekStartMs + i * DAY_MS;
    const label = new Date(dayMs).toLocaleDateString('en-US',
      { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' });
    const dayPlacements = byDay[iso];
    html += `<div class="mm-agenda-day-header">${escapeText(label)}</div>`;
    if (dayPlacements.length === 0) {
      html += '<div class="mm-agenda-empty">nothing scheduled</div>';
      continue;
    }
    const byGroup = groupPlacementsByGroup(dayPlacements);
    for (const did of tracks) {
      const gp = byGroup[did] || [];
      if (gp.length === 0) continue; // week view: omit empty groups to stay compact
      html += `<section class="mm-agenda-group"><h4 class="mm-agenda-group-title">${escapeText(did)}</h4>`;
      html += rowsForGroup(gp, playlists, schedules, nowMs);
      html += '</section>';
    }
  }
  html += '</div>';
  return html;
}
