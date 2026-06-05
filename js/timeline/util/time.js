/**
 * Recurrence expansion for the admin timeline.
 *
 * `expandSchedule(schedule, windowStartMs, windowEndMs)` returns an array
 * of concrete clip placements within the window:
 *
 *     [{startMs, endMs, displayID, playlistName, priority, scheduleId}, ...]
 *
 * Mirrors `mosaicmesh.scheduling.schedule_active_at()`. Pure function —
 * no DOM, no fetch — safe to import in Node for tests.
 *
 * **Time zones**: dtstart/exdates/untilDate are interpreted as UTC dates
 * for now (matching the server's date-only handling). startTime/endTime
 * are HH:MM in UTC. A future task could make this local-time-aware via
 * a tzId field on the Schedule.
 */

const DAY_MS = 24 * 60 * 60 * 1000;

function parseYmd(s) {
  // 'YYYY-MM-DD' -> {y, m, d}
  const [y, m, d] = s.split('-').map(Number);
  return { y, m, d };
}

function ymdToMs(s) {
  // Midnight UTC of the given Y-M-D
  const { y, m, d } = parseYmd(s);
  return Date.UTC(y, m - 1, d);
}

function parseHHMM(s) {
  const [h, m] = s.split(':').map(Number);
  return h * 3600_000 + m * 60_000;
}

function jsDow(ms) {
  // JS getUTCDay: 0=Sun..6=Sat. Convert to 0=Mon..6=Sun matching server.
  const js = new Date(ms).getUTCDay();
  return (js + 6) % 7;
}

/**
 * Generate the candidate occurrence start-of-day timestamps within
 * [dtstart, end-clamp ∩ window] given freq + interval + byweekday.
 *
 * Returns midnight-UTC ms for each candidate day.
 */
function candidateDays(s, fromMs, toMs) {
  const dtstartMs = ymdToMs(s.dtstart);
  const startMs = Math.max(dtstartMs, fromMs - DAY_MS);  // pad 1 day for cross-midnight
  const days = [];
  for (let t = startMs; t <= toMs; t += DAY_MS) {
    if (s.freq === 'DAILY') {
      const idx = Math.round((t - dtstartMs) / DAY_MS);
      if (idx >= 0 && (idx % (s.interval || 1) === 0)) days.push(t);
    } else if (s.freq === 'WEEKLY') {
      const dow = jsDow(t);
      if (s.byweekday && s.byweekday.length > 0 && !s.byweekday.includes(dow)) continue;
      const weekIdx = Math.floor((t - dtstartMs) / (7 * DAY_MS));
      if (weekIdx >= 0 && (weekIdx % (s.interval || 1) === 0)) days.push(t);
    } else if (s.freq === 'MONTHLY' || s.freq === 'YEARLY') {
      // Minimal support: fire on same day-of-month as dtstart for MONTHLY,
      // or same month+day for YEARLY. interval respected the same way.
      const ds = new Date(dtstartMs);
      const td = new Date(t);
      if (s.freq === 'MONTHLY') {
        if (td.getUTCDate() !== ds.getUTCDate()) continue;
        const monthsBetween = (td.getUTCFullYear() - ds.getUTCFullYear()) * 12
                            + (td.getUTCMonth() - ds.getUTCMonth());
        if (monthsBetween >= 0 && monthsBetween % (s.interval || 1) === 0) days.push(t);
      } else {  // YEARLY
        if (td.getUTCMonth() !== ds.getUTCMonth()) continue;
        if (td.getUTCDate() !== ds.getUTCDate()) continue;
        const yearsBetween = td.getUTCFullYear() - ds.getUTCFullYear();
        if (yearsBetween >= 0 && yearsBetween % (s.interval || 1) === 0) days.push(t);
      }
    }
  }
  return days;
}

export function expandSchedule(s, windowStartMs, windowEndMs) {
  if (!s || s.enabled === false) return [];
  if (windowEndMs <= windowStartMs) return [];

  const exdateSet = new Set(s.exdates || []);
  const startOfDayOffset = parseHHMM(s.startTime || '00:00');
  let endOfDayOffset = parseHHMM(s.endTime || '23:59');
  const wrapsMidnight = endOfDayOffset <= startOfDayOffset;
  // For cross-midnight (endTime <= startTime), the window actually
  // extends into the NEXT day; add 24h to the end offset.
  if (wrapsMidnight) endOfDayOffset += DAY_MS;

  const days = candidateDays(s, windowStartMs, windowEndMs);

  // end={count} clamp: we count the Nth occurrence FROM dtstart, not
  // from windowStart, so a count-3 schedule yields the same 3 fires
  // regardless of which window we ask about.
  let count = null;
  if (s.end && s.end.type === 'count') count = Math.max(0, s.end.count|0);
  let emitted = 0;

  // end={until} clamp
  let untilMs = null;
  if (s.end && s.end.type === 'until' && s.end.untilDate) {
    // inclusive: 'until' includes that whole day
    untilMs = ymdToMs(s.end.untilDate) + DAY_MS - 1;
  }

  const out = [];
  for (const dayMs of days) {
    // Check exdates by YYYY-MM-DD (UTC)
    const dStr = isoDate(dayMs);
    if (exdateSet.has(dStr)) continue;

    if (count != null && emitted >= count) break;
    emitted += 1;

    let placeStart = dayMs + startOfDayOffset;
    let placeEnd   = dayMs + endOfDayOffset;

    if (untilMs != null && placeStart > untilMs) break;

    // Clip to window
    placeStart = Math.max(placeStart, windowStartMs);
    placeEnd   = Math.min(placeEnd,   windowEndMs);
    if (placeEnd <= placeStart) continue;

    out.push({
      startMs: placeStart,
      endMs:   placeEnd,
      playlistName: s.playlistName,
      displayID: s.displayID,
      priority: s.priority || 0,
      scheduleId: s.id,
    });
  }

  return out;
}

function isoDate(ms) {
  const d = new Date(ms);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  const dd = String(d.getUTCDate()).padStart(2, '0');
  return `${y}-${m}-${dd}`;
}
