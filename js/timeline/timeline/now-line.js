/**
 * Red now-line overlay for Day and Week views.
 *
 * Day view: horizontal 100%, the line is a vertical 2px bar at
 * (now - viewDateStart) / 24h * width.
 *
 * Week view: positioned in the column matching today's day-of-week,
 * if today is within the displayed week; otherwise hidden.
 *
 * Month view: hidden (a single position would be ambiguous on a
 * calendar).
 *
 * One setInterval at the module level updates the line's transform
 * every 1s. autoscrollIntoView() runs once on first paint to bring
 * the current time into view on Day-view load.
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

function updateOne(el, store) {
  const mode = store.viewMode;
  if (mode === 'month') { el.style.display = 'none'; return; }

  const now = Date.now();
  const [y, m, d] = store.viewDate.split('-').map(Number);
  const baseMs = Date.UTC(y, m - 1, d);

  if (mode === 'day') {
    if (now < baseMs || now >= baseMs + DAY_MS) { el.style.display = 'none'; return; }
    el.style.display = 'block';
    const frac = (now - baseMs) / DAY_MS;
    el.style.left = (frac * 100) + '%';
  } else if (mode === 'week') {
    const dow = (new Date(baseMs).getUTCDay() + 6) % 7;
    const monday = baseMs - dow * DAY_MS;
    const today = new Date();
    const todayBase = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
    if (todayBase < monday || todayBase >= monday + 7 * DAY_MS) {
      el.style.display = 'none'; return;
    }
    el.style.display = 'block';
    const colIdx = Math.floor((todayBase - monday) / DAY_MS);
    // Anchor to the day column: 1 (label) + colIdx + 1 (1-indexed grid)
    // Approximate horizontal placement: ~60px label + colIdx * (col width)
    // We can't easily compute the exact px without measuring; instead
    // CSS Grid spans handle column placement and we offset by a tiny
    // hour-fraction. Practical approach: skip horizontal anim in Week
    // view and just show the line at the day-column boundary. Vertical
    // anim happens via top % within the day column.
    const hourFrac = ((now - todayBase) / (24*3600000));
    el.style.top  = (hourFrac * 100) + '%';
    el.style.left = `calc(60px + ${colIdx} * ((100% - 60px) / 7))`;
    el.style.width = `calc((100% - 60px) / 7)`;
  }
}

export function autoscrollIntoView() {
  // Day view only — bring the now-line into the visible scrollport
  const el = document.querySelector('.mm-day-grid .mm-now-line');
  if (!el) return;
  el.scrollIntoView({ behavior: 'auto', inline: 'center', block: 'nearest' });
}
