/**
 * Pure helpers shared by the mobile Schedule views (Section 3).
 * No DOM, no fetch — importable in Node for tests.
 *
 * Placement shape (from util/time.js expandSchedule):
 *   { startMs, endMs, playlistName, displayID, priority, scheduleId }
 * byweekday: 0=Mon .. 6=Sun (matches util/time.js + the recurrence editor).
 */

const DOW_SHORT = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
/** Default seconds for an Auto (absent-duration) item in the sparkline. */
const DEFAULT_DUR_S = 20;

export function escapeText(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
export function escapeAttr(s) {
  return escapeText(s).replace(/"/g, '&quot;');
}
export function formatHm(ms) {
  const d = new Date(ms);
  return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
}
export function basename(p) { return String(p || '').split('/').pop() || ''; }

/** Content kind of a playlist item: 'animation' | 'video' | 'image'. */
export function kindForItem(it) {
  if (it && it.playmode === 'SCRIPT') return 'animation';
  const f = (it && it.file) || (typeof it === 'string' ? it : '');
  return /\.(mp4|webm|mov)$/i.test(f) ? 'video' : 'image';
}
export function kindColor(kind) {
  return kind === 'video' ? '#22c55e'
       : kind === 'animation' ? '#8b5cf6'
       : kind === 'mixed' ? '#ffb84e'
       : '#4ea1ff'; // image / default
}
export function kindIcon(kind) {
  return kind === 'video' ? '▶' : kind === 'animation' ? '✦' : kind === 'mixed' ? '◫' : '▦';
}

/** A playlist's dominant kind: the single kind of all items, else 'mixed'. */
export function playlistKind(playlist) {
  const items = (playlist && playlist.items) || [];
  if (items.length === 0) return 'image';
  const kinds = new Set(items.map(kindForItem));
  return kinds.size === 1 ? [...kinds][0] : 'mixed';
}

/** { [displayID]: placement[] }, each bucket sorted by startMs ascending. */
export function groupPlacementsByGroup(placements) {
  const out = {};
  for (const p of placements) {
    (out[p.displayID] = out[p.displayID] || []).push(p);
  }
  for (const k of Object.keys(out)) out[k].sort((a, b) => a.startMs - b.startMs);
  return out;
}

/** { [iso]: placement[] } for exactly the days in dayIsoList (empty arrays kept). */
export function groupPlacementsByDay(placements, dayIsoList) {
  const out = {};
  for (const iso of dayIsoList) {
    const [y, m, d] = iso.split('-').map(Number);
    const dayStart = Date.UTC(y, m - 1, d);
    const dayEnd = Date.UTC(y, m - 1, d + 1);
    const bucket = [];
    for (const p of placements) {
      // Clip each placement to this day. A cross-midnight placement
      // (e.g. 22:00 Mon -> 02:00 Tue) lands in BOTH days it overlaps,
      // with day-local times — so the Week view shows it under each day
      // AND per-group conflict detection sees the real overlap on each
      // day (a next-morning schedule would otherwise never be compared
      // against the wrapped tail).
      const s = Math.max(p.startMs, dayStart);
      const e = Math.min(p.endMs, dayEnd);
      if (e > s) bucket.push({ ...p, startMs: s, endMs: e });
    }
    bucket.sort((a, b) => a.startMs - b.startMs);
    out[iso] = bucket;
  }
  return out;
}

/** Human recurrence label: "Daily" / "Mon–Fri" / "Every 2 weeks" / "Once". */
export function formatRecurrence(s) {
  if (!s) return '';
  if (s.end && s.end.type === 'count' && Number(s.end.count) === 1) return 'Once';
  const interval = Number(s.interval) || 1;
  const freq = s.freq || 'DAILY';
  if (freq === 'DAILY') return interval === 1 ? 'Daily' : `Every ${interval} days`;
  if (freq === 'WEEKLY') {
    const bwd = (s.byweekday || []).slice().sort((a, b) => a - b);
    let days = '';
    if (bwd.length && !(interval > 1)) {
      if (bwd.length === 5 && bwd.every((v, i) => v === i)) days = 'Mon–Fri';
      else days = bwd.map(i => DOW_SHORT[i]).join(', ');
    }
    if (interval > 1) return `Every ${interval} weeks`;
    return days || 'Weekly';
  }
  if (freq === 'MONTHLY') return interval === 1 ? 'Monthly' : `Every ${interval} months`;
  if (freq === 'YEARLY') return interval === 1 ? 'Yearly' : `Every ${interval} years`;
  return '';
}

/** [{kind, frac}] for the playlist-item sparkline (duration-proportional, Auto=20s). */
export function sparklineSegments(playlist) {
  const items = (playlist && playlist.items) || [];
  if (items.length === 0) return [];
  const durs = items.map(it => Number((it && it.duration != null) ? it.duration : DEFAULT_DUR_S));
  const total = durs.reduce((a, b) => a + b, 0);
  return items.map((it, i) => ({
    kind: kindForItem(it),
    frac: total > 0 ? durs[i] / total : 1 / items.length,
  }));
}

/** Half-open [startMs, endMs) "is playing now" test. */
export function isNowPlacement(p, nowMs) {
  return nowMs >= p.startMs && nowMs < p.endMs;
}
