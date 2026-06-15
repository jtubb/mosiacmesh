/**
 * Given placements (from util/time.js's expandSchedule applied to all
 * schedules on one display + window), return conflict descriptors:
 *
 *     [{loserId, winnerId, overlapStartMs, overlapEndMs}, ...]
 *
 * One entry per (loser × overlapping-higher-priority-placement) pair.
 * `clip.js` uses these to render the diagonal-stripe overlay on the
 * loser's clip across the overlap region.
 *
 * Equal-priority overlaps are NOT flagged — the server's schedule_active_at
 * does not define a tiebreaker, and the UI shows both clips as parallel
 * (a stripe would imply one wins, which would be misleading).
 */

export function detectConflicts(placements) {
  const out = [];
  // O(n^2) — fine for the per-display visible placement count (<100 typically)
  for (let i = 0; i < placements.length; i++) {
    const a = placements[i];
    for (let j = 0; j < placements.length; j++) {
      if (i === j) continue;
      const b = placements[j];
      if (b.priority <= a.priority) continue;
      const oStart = Math.max(a.startMs, b.startMs);
      const oEnd   = Math.min(a.endMs,   b.endMs);
      if (oEnd <= oStart) continue;
      out.push({
        loserId:  a.scheduleId,
        winnerId: b.scheduleId,
        overlapStartMs: oStart,
        overlapEndMs:   oEnd,
      });
    }
  }
  return out;
}
