/**
 * Render one clip block, positioned via CSS grid-column.
 *
 * Day view positioning:
 *   - The grid has 25 columns: column 1 = track-header label, columns
 *     2..25 = hours 0..23.
 *   - A placement from 08:00 to 11:00 occupies columns 2 + 8 = 10
 *     through 2 + 11 = 13 → `grid-column: 10 / 13`.
 *
 * Week view positioning is handled by a different helper inside
 * timeline.js — this function is Day-view specific.
 *
 * Inputs:
 *   {
 *     placement: {scheduleId, startMs, endMs, playlistName, priority},
 *     viewDateMs:   midnight UTC of the day being rendered,
 *     conflictRanges: [{overlapStartMs, overlapEndMs}, ...]  // for the
 *         stripe overlay on this clip (may be empty),
 *   }
 *
 * Returns an HTML string with `data-schedule-id` so PR-4b's click
 * handlers can target it.
 */

import { renderDayStripesHtml } from './conflict-stripes.js';

const DAY_MS = 24 * 60 * 60 * 1000;
const HOUR_MS = 60 * 60 * 1000;

function hourFractionFromDayStart(absMs, dayStartMs) {
  return Math.max(0, Math.min(24, (absMs - dayStartMs) / HOUR_MS));
}

export function clipDayHtml({ placement, viewDateMs, conflictRanges = [], gridRow = null, isSelected = false }) {
  const startHr = hourFractionFromDayStart(placement.startMs, viewDateMs);
  const endHr   = hourFractionFromDayStart(placement.endMs,   viewDateMs);
  if (endHr <= startHr) return '';

  // Use sub-hour precision via CSS percentages within a single column
  // group. Simpler: pin to integer columns + override left/right with %.
  const colStart = 2 + Math.floor(startHr);
  const colEnd   = 2 + Math.ceil(endHr);
  // sub-hour offsets (0..1) for both ends, applied via inline style:
  const leftPct  = (startHr - Math.floor(startHr)) * 100;
  const rightPct = (Math.ceil(endHr) - endHr) * 100;

  const stripes = renderDayStripesHtml(conflictRanges, viewDateMs, startHr, endHr);
  const tStart  = formatHm(placement.startMs);
  const tEnd    = formatHm(placement.endMs);
  // PR-4b: explicit grid-row keeps the clip aligned with its track row's
  // .mm-track-droparea cell so the clip visually sits on top of the
  // drop target (same cell, later in DOM = above in stacking order).
  const rowStyle = gridRow != null ? ` grid-row:${gridRow};` : '';

  return `
    <div class="mm-clip${isSelected ? ' mm-clip-selected' : ''}" draggable="true" data-schedule-id="${escapeAttr(placement.scheduleId)}"
         style="grid-column:${colStart} / ${colEnd};${rowStyle} margin-left:${leftPct}%; margin-right:${rightPct}%;">
      <div class="mm-clip-title">${escapeText(placement.playlistName)}</div>
      <div class="mm-clip-time">${tStart}–${tEnd}</div>
      ${stripes}
      <div class="mm-clip-resize-handle" data-edge="left" draggable="false"></div>
      <div class="mm-clip-resize-handle" data-edge="right" draggable="false"></div>
    </div>
  `;
}

function formatHm(ms) {
  const d = new Date(ms);
  const h = String(d.getUTCHours()).padStart(2, '0');
  const m = String(d.getUTCMinutes()).padStart(2, '0');
  return `${h}:${m}`;
}

function escapeText(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function escapeAttr(s) {
  return escapeText(s).replace(/"/g, '&quot;');
}
