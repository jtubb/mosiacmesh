/**
 * Vertical day timeline for ONE group: hours 00..23 top-to-bottom, blocks
 * positioned by their fraction of the day, a now-line, tappable blocks.
 * Pure: ({ dayStartMs, placements, playlists, nowMs }) -> HTML string.
 * `placements` are pre-filtered to the single selected group + the day.
 */
import { escapeText, escapeAttr, formatHm, isNowPlacement } from './util.js';

const DAY_MS = 24 * 60 * 60 * 1000;
const HOUR_PX = 40;          // vertical scale: 40px per hour
const TOTAL_PX = 24 * HOUR_PX;

export function verticalTimelineHtml({ dayStartMs, placements, playlists, nowMs }) {
  let html = `<div class="mm-vt" style="position:relative; height:${TOTAL_PX}px;">`;
  // Hour gridlines + labels.
  for (let h = 0; h < 24; h++) {
    html += `<div class="mm-vt-hour" style="position:absolute; top:${h * HOUR_PX}px; height:${HOUR_PX}px;">
      <span class="mm-vt-label">${String(h).padStart(2, '0')}</span>
    </div>`;
  }
  // Blocks.
  for (const p of placements) {
    const topFrac = Math.max(0, (p.startMs - dayStartMs) / DAY_MS);
    const endFrac = Math.min(1, (p.endMs - dayStartMs) / DAY_MS);
    const top = topFrac * TOTAL_PX;
    const height = Math.max(14, (endFrac - topFrac) * TOTAL_PX);
    const nowCls = isNowPlacement(p, nowMs) ? ' mm-vt-block-now' : '';
    html += `<div class="mm-vt-block${nowCls}" data-schedule-id="${escapeAttr(p.scheduleId)}"
      style="position:absolute; left:42px; right:6px; top:${top}px; height:${height}px;">
      <span class="mm-vt-block-name">${escapeText(p.playlistName)}</span>
      <span class="mm-vt-block-time">${formatHm(p.startMs)}–${formatHm(p.endMs)}</span>
    </div>`;
  }
  // Now-line (only when nowMs falls within this day).
  if (nowMs >= dayStartMs && nowMs < dayStartMs + DAY_MS) {
    const top = ((nowMs - dayStartMs) / DAY_MS) * TOTAL_PX;
    html += `<div class="mm-vt-nowline" style="position:absolute; left:0; right:0; top:${top}px;"></div>`;
  }
  html += '</div>';
  return html;
}
