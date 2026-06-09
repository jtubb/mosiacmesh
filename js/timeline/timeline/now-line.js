/**
 * Red now-line overlay for Day and Week views.
 *
 * Day view: vertical 2px bar at (now - viewDateStart) / 24h fraction,
 *           spanning the full grid height. Hidden when viewDate isn't today.
 * Week view: thin 2px HORIZONTAL bar at the current hour, spanning all 7
 *            day columns. Hidden when today isn't in the displayed week.
 * Month view: hidden (single position ambiguous on a calendar).
 *
 * Each updateOne() call resets ALL positioning props back to a clean
 * state before applying the mode-specific overrides — without this, a
 * mode switch from Week → Day would carry over Week's `top` value and
 * the Day-view line would render as a horizontal slice instead of a
 * vertical bar (seen during Playwright smoke 2026-06-05).
 *
 * One setInterval at the module level updates all .mm-now-line elements
 * every 1s. autoscrollIntoView() runs once on first paint to bring the
 * current time into view on Day-view load.
 */

const DAY_MS = 24 * 60 * 60 * 1000;

let _intervalId = null;

export function startNowLine(getStore) {
  function tick() {
    const lines = document.querySelectorAll('.mm-now-line');
    for (const el of lines) updateOne(el, getStore());
  }
  if (_intervalId) clearInterval(_intervalId);
  _intervalId = setInterval(tick, 1000);
  tick();
}

function resetStyle(el) {
  // Wipe every positioning prop we ever touch so a mode switch
  // doesn't carry stale values from the previous mode.
  el.style.display = '';
  el.style.top = '';
  el.style.bottom = '';
  el.style.left = '';
  el.style.right = '';
  el.style.width = '';
  el.style.height = '';
}

function updateOne(el, store) {
  resetStyle(el);

  const mode = store.viewMode;
  if (mode === 'month') { el.style.display = 'none'; return; }

  const now = Date.now();
  const [y, m, d] = store.viewDate.split('-').map(Number);
  // PR-26 (2026-06-09): use BROWSER-LOCAL midnight as the day base. The
  // server evaluates schedule_active_at with `datetime.datetime.now()`
  // (server-local time), and the modal's time inputs capture browser-
  // local HH:MM strings. The pre-PR-26 now-line used Date.UTC (i.e.
  // UTC time-of-day fraction), which on a -4 hour offset (EDT) rendered
  // the now-line ~4 hours ahead of what the operator's wall clock read
  // — making schedules look "active" before the server agreed they
  // were. Same calendar day in both interpretations on a normal day;
  // breaks subtly only on DST transitions, which the rest of the time
  // math (Schedule.startTime as opaque HH:MM strings) also doesn't
  // model. Co-located server + browser is the assumed deployment.
  const baseMs = new Date(y, m - 1, d).getTime();

  if (mode === 'day') {
    if (now < baseMs || now >= baseMs + DAY_MS) { el.style.display = 'none'; return; }
    el.style.display = 'block';
    // Vertical 2px bar, full grid height; horizontal position by fraction
    el.style.top = '0';
    el.style.bottom = '0';
    el.style.width = '2px';
    const frac = (now - baseMs) / DAY_MS;
    el.style.left = (frac * 100) + '%';
  } else if (mode === 'week') {
    // Week view: same local-midnight basis. getDay()/getMonth()/getDate
    // (no -UTC variant) match the browser-local interpretation.
    const baseDate = new Date(y, m - 1, d);
    const dow = (baseDate.getDay() + 6) % 7;
    const monday = baseMs - dow * DAY_MS;
    const today = new Date();
    const todayBase = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
    if (todayBase < monday || todayBase >= monday + 7 * DAY_MS) {
      el.style.display = 'none'; return;
    }
    el.style.display = 'block';
    // Horizontal 2px bar at the current hour, spans all 7 day columns
    // (skipping the 60px hour-label column on the left). Operator gets
    // a "now" reference line across the whole week — more useful than
    // trying to highlight just today's column with imprecise math.
    const hourFrac = ((now - todayBase) / (24 * 3600000));
    el.style.left = '60px';
    el.style.right = '0';
    el.style.height = '2px';
    el.style.top = `calc(${hourFrac * 100}% - 1px)`;
  }
}

export function autoscrollIntoView() {
  // Day view only — bring the now-line into the visible scrollport
  const el = document.querySelector('.mm-day-grid .mm-now-line');
  if (!el) return;
  el.scrollIntoView({ behavior: 'auto', inline: 'center', block: 'nearest' });
}
