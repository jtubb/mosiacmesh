/**
 * Diagonal-stripe overlay for the lower-priority clip in a scheduling
 * conflict. Shared between Day view (horizontal stripes — start/end
 * positioned on the X-axis along the clip's width) and Week view
 * (vertical stripes — start/end on the Y-axis along the clip's
 * height). The math is identical in both: each overlap range maps to
 * a fraction of the clip's [startHr, endHr) span.
 *
 * CSS:
 *   - .mm-clip-stripe          → horizontal (Day view default): position
 *                                absolute, top:0 bottom:0, left/width %.
 *   - .mm-clip-stripe-vertical → Week view variant: left:0 right:0,
 *                                top/height %.
 *
 * Both share the diagonal repeating-linear-gradient + pointer-events:
 * none + red tint defined on .mm-clip-stripe in admin.html.
 */

const HOUR_MS = 60 * 60 * 1000;

function hourFractionFromDayStart(absMs, dayStartMs) {
  return Math.max(0, Math.min(24, (absMs - dayStartMs) / HOUR_MS));
}

/**
 * Internal: convert a list of overlap ranges to [{startFrac, sizeFrac}]
 * within a clip spanning [clipStartHr, clipEndHr]. `dayStartMs` is the
 * day boundary the overlap times are measured against (the clip's day
 * for week view, the view's day for day view).
 */
function stripeFractions(ranges, dayStartMs, clipStartHr, clipEndHr) {
  if (!ranges || ranges.length === 0) return [];
  const total = clipEndHr - clipStartHr;
  if (total <= 0) return [];
  return ranges.map((r) => {
    const rStart = hourFractionFromDayStart(r.overlapStartMs, dayStartMs);
    const rEnd   = hourFractionFromDayStart(r.overlapEndMs,   dayStartMs);
    return {
      startFrac: (rStart - clipStartHr) / total,
      sizeFrac:  (rEnd   - rStart)      / total,
    };
  });
}

/**
 * Day view: stripes laid horizontally across the clip.
 * Caller passes the day's viewDateMs as dayStartMs.
 */
export function renderDayStripesHtml(ranges, viewDateMs, clipStartHr, clipEndHr) {
  const frags = stripeFractions(ranges, viewDateMs, clipStartHr, clipEndHr);
  if (frags.length === 0) return '';
  return frags.map((f) =>
    `<div class="mm-clip-stripe" style="left:${f.startFrac * 100}%; width:${f.sizeFrac * 100}%"></div>`
  ).join('');
}

/**
 * Week view: stripes laid vertically along the clip's height.
 * Caller passes that clip's own day-start (since week clips span their
 * own day, not the whole week).
 */
export function renderWeekStripesHtml(ranges, dayStartMs, clipStartHr, clipEndHr) {
  const frags = stripeFractions(ranges, dayStartMs, clipStartHr, clipEndHr);
  if (frags.length === 0) return '';
  return frags.map((f) =>
    `<div class="mm-clip-stripe mm-clip-stripe-vertical" style="top:${f.startFrac * 100}%; height:${f.sizeFrac * 100}%"></div>`
  ).join('');
}
