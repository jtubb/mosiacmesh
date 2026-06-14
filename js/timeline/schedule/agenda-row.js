/**
 * The Rich agenda row — the shared atom across Day-agenda and Week
 * (day-sectioned). Pure: (placement, playlist, opts) -> HTML string.
 *
 * opts: { isNow: bool, conflict: bool, recurrenceText: string }
 */
import {
  escapeText, escapeAttr, formatHm, sparklineSegments,
  playlistKind, kindColor, kindIcon,
} from './util.js';

export function agendaRowHtml(placement, playlist, opts = {}) {
  const { isNow = false, conflict = false, recurrenceText = '' } = opts;
  const kind = playlistKind(playlist);
  const color = kindColor(kind);
  const icon = kindIcon(kind);
  const segs = sparklineSegments(playlist);
  const spark = segs.map(s =>
    `<span class="mm-agenda-seg" style="flex:${(s.frac * 1000) | 0};background:${kindColor(s.kind)}"></span>`
  ).join('');
  const cls = 'mm-agenda-row' + (isNow ? ' mm-agenda-now' : '');
  return `<div class="${cls}" data-schedule-id="${escapeAttr(placement.scheduleId)}" style="border-left:4px solid ${color}">
    <div class="mm-agenda-main">
      <span class="mm-agenda-ic">${icon}</span>
      <span class="mm-agenda-time">${formatHm(placement.startMs)}–${formatHm(placement.endMs)}</span>
      <span class="mm-agenda-name">${escapeText(placement.playlistName)}</span>
      ${isNow ? '<span class="mm-agenda-live">▶ now</span>' : ''}
      ${conflict ? '<span class="mm-agenda-conflict">conflict</span>' : ''}
      ${recurrenceText ? `<span class="mm-agenda-recur">· ${escapeText(recurrenceText)}</span>` : ''}
    </div>
    <div class="mm-agenda-spark">${spark}</div>
  </div>`;
}
