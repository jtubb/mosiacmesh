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

function isoDate(ms) {
  const d = new Date(ms);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  const dd = String(d.getUTCDate()).padStart(2, '0');
  return `${y}-${m}-${dd}`;
}

// Hard cap so a misconfigured schedule (e.g. count without dtstart) can't
// loop forever. 10 years of daily fires = 3650 iterations — way more than
// any real admin schedule needs. Tested below this cap; raise only if a
// genuine use case appears.
const MAX_CANDIDATE_DAYS = 365 * 10;

/**
 * Generator: yield every occurrence-day from dtstart forward, in order,
 * applying freq + interval + byweekday rules. Stops at MAX_CANDIDATE_DAYS
 * so a bad schedule can't lock the UI.
 *
 * **Important**: yields from dtstart, NOT from windowStart. The caller
 * uses this to count occurrences (for `end={count}`) consistently — the
 * Nth fire is the Nth fire regardless of which window the caller asks
 * about. The caller then applies windowStart/windowEnd clipping.
 */
function* allCandidateDays(s) {
  const dtstartMs = ymdToMs(s.dtstart);
  const dtstartDow = jsDow(dtstartMs);
  // WEEKLY with empty byweekday: iCal/dateutil default is once per week
  // on dtstart's day-of-week (NOT every day, which was a PR-4a-T4 bug).
  const weeklyBwd = (s.byweekday && s.byweekday.length > 0)
                  ? s.byweekday
                  : [dtstartDow];
  const interval = s.interval || 1;
  let t = dtstartMs;
  for (let i = 0; i < MAX_CANDIDATE_DAYS; i++, t += DAY_MS) {
    if (s.freq === 'DAILY') {
      if (i % interval === 0) yield t;
    } else if (s.freq === 'WEEKLY') {
      const dow = jsDow(t);
      if (!weeklyBwd.includes(dow)) continue;
      const weekIdx = Math.floor((t - dtstartMs) / (7 * DAY_MS));
      if (weekIdx % interval === 0) yield t;
    } else if (s.freq === 'MONTHLY' || s.freq === 'YEARLY') {
      // Minimal support: fire on same day-of-month as dtstart for MONTHLY,
      // or same month+day for YEARLY. interval respected the same way.
      // O(window/DAY) — fine for the admin's Day/Week/Month views; a wider
      // schedule (e.g. yearly fire across a 10-yr window) walks ~3650 days
      // and rejects almost all at the date check. Cheap enough to skip an
      // optimization pass until profiling says otherwise.
      const ds = new Date(dtstartMs);
      const td = new Date(t);
      if (s.freq === 'MONTHLY') {
        if (td.getUTCDate() !== ds.getUTCDate()) continue;
        const monthsBetween = (td.getUTCFullYear() - ds.getUTCFullYear()) * 12
                            + (td.getUTCMonth() - ds.getUTCMonth());
        if (monthsBetween >= 0 && monthsBetween % interval === 0) yield t;
      } else {  // YEARLY
        if (td.getUTCMonth() !== ds.getUTCMonth()) continue;
        if (td.getUTCDate() !== ds.getUTCDate()) continue;
        const yearsBetween = td.getUTCFullYear() - ds.getUTCFullYear();
        if (yearsBetween >= 0 && yearsBetween % interval === 0) yield t;
      }
    }
  }
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

  // end={count} clamp: counts the Nth occurrence FROM dtstart (iCal
  // RFC 5545 semantics, matching dateutil.rrule on the server). The
  // count INCLUDES exdates — RFC says COUNT counts the original set
  // before EXDATE filtering. So `count=5` + exdate-of-day-3 yields
  // 4 placements (days 1,2,4,5), not 5.
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
  for (const dayMs of allCandidateDays(s)) {
    if (count != null && emitted >= count) break;
    emitted += 1;

    const placeStartRaw = dayMs + startOfDayOffset;
    const placeEndRaw   = dayMs + endOfDayOffset;

    if (untilMs != null && placeStartRaw > untilMs) break;
    // Past the window's end — no later occurrence can be visible.
    if (placeStartRaw >= windowEndMs) break;
    // Entirely before the window — skip but keep counting toward quota.
    if (placeEndRaw <= windowStartMs) continue;
    // Exdate skip (still consumed quota above per iCal semantics).
    if (exdateSet.has(isoDate(dayMs))) continue;

    const placeStart = Math.max(placeStartRaw, windowStartMs);
    const placeEnd   = Math.min(placeEndRaw,   windowEndMs);
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
