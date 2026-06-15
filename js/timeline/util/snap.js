/**
 * Pixel-to-time conversion + snap-to-grid helpers for the timeline's
 * drag-and-drop handlers. Pure functions, Node-testable, no DOM.
 *
 * Convention: hours are floats 0.0..24.0 where 24.0 is end-of-day
 * (and clamped to '23:59' in HH:MM rendering — the schedule rep
 * doesn't have a 24:00 form). Sub-hour precision is snapped to 15 min.
 */

const DAY_MS = 24 * 60 * 60 * 1000;

export function pxToHour(pxFromGridLeft, gridWidthPx) {
  if (!gridWidthPx || gridWidthPx <= 0) return 0;
  const frac = pxFromGridLeft / gridWidthPx;
  if (frac <= 0) return 0;
  if (frac >= 1) return 24;
  return frac * 24;
}

export function snapTo15min(hour) {
  return Math.round(hour * 4) / 4;
}

export function hourToHHMM(hour) {
  if (hour >= 24) return '23:59';
  if (hour < 0) hour = 0;
  const h = Math.floor(hour);
  const m = Math.round((hour - h) * 60);
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

export function isoDateAddHour(isoDate, hour) {
  // Returns YYYY-MM-DD of (isoDate's midnight UTC + hour). Used when
  // a clip drag crosses midnight.
  const [y, m, d] = isoDate.split('-').map(Number);
  const baseMs = Date.UTC(y, m - 1, d);
  const target = baseMs + Math.floor(hour) * 3600000;
  const td = new Date(target);
  return `${td.getUTCFullYear()}-${String(td.getUTCMonth()+1).padStart(2,'0')}-${String(td.getUTCDate()).padStart(2,'0')}`;
}
