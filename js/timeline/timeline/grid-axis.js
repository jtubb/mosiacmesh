/**
 * Pure render-helpers for the time-grid axes. No state, no DOM access —
 * returns HTML strings or template fragments that callers inject.
 *
 * Day view: 24 hourly columns. Returns a header strip.
 * Week view: 7 day columns. Returns Mon..Sun header strip.
 * Month view: 7 weekday labels (Mon..Sun) above the calendar grid.
 *
 * CSS Grid columns are defined at the component level (timeline.js);
 * these helpers just produce the column header cells.
 */

const DOW_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export function dayAxisHtml() {
  let html = '';
  for (let h = 0; h < 24; h++) {
    const label = String(h).padStart(2, '0');
    html += `<div class="mm-axis-cell" style="grid-column:${h + 2}">${label}</div>`;
  }
  return html;
}

export function weekAxisHtml(viewDateMs) {
  // viewDateMs is any timestamp within the desired week (UTC). We render
  // Mon..Sun labels with the actual date (e.g. "Mon Jun 1").
  const d = new Date(viewDateMs);
  // Find Monday of this week (UTC)
  const dow = (d.getUTCDay() + 6) % 7;
  const monday = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() - dow));
  let html = '';
  for (let i = 0; i < 7; i++) {
    const cell = new Date(Date.UTC(monday.getUTCFullYear(), monday.getUTCMonth(), monday.getUTCDate() + i));
    const day = cell.getUTCDate();
    const mon = cell.toLocaleString('en-US', { month: 'short', timeZone: 'UTC' });
    html += `<div class="mm-axis-cell" style="grid-column:${i + 2}">${DOW_LABELS[i]} ${mon} ${day}</div>`;
  }
  return html;
}

export function monthWeekdayHeaderHtml() {
  let html = '';
  for (let i = 0; i < 7; i++) {
    html += `<div class="mm-axis-cell" style="grid-column:${i + 1}">${DOW_LABELS[i]}</div>`;
  }
  return html;
}
