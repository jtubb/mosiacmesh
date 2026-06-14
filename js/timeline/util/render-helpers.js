// Pure helpers for render-state UI. No DOM, no store — node-testable.

export function playlistGroupSummary(name, displayGroups, renders, isRenderable, needsCalibration) {
  // returns {total, ready, rendering, failed:[displayID], queued}
  // `total` is the count of groups ELIGIBLE to render this playlist: for
  // mesh/per-screen (needsCalibration) only calibrated groups can render, so
  // uncalibrated groups are excluded from the denominator (a mesh playlist
  // can never render on a group with no calibrated screens). For mirror/FULL
  // (!needsCalibration) every group is eligible. Backward-compatible: if the
  // group lacks a calibratedCount (older /api/displays), no filtering is
  // applied so the count degrades to "all groups" rather than zero.
  const out = { total: 0, ready: 0, rendering: 0, failed: [], queued: 0 };
  if (!isRenderable) return out;   // N/A — nothing to summarize
  for (const g of (displayGroups || [])) {
    if (needsCalibration && g.calibratedCount != null && g.calibratedCount <= 0) continue;
    out.total += 1;
    const e = (renders[g.displayID] || {})[name];
    if (!e) continue;
    if (e.state === 'READY') out.ready += 1;
    else if (e.state === 'RENDERING') out.rendering += 1;
    else if (e.state === 'QUEUED') out.queued += 1;
    else if (e.state === 'FAILED') out.failed.push(g.displayID);
  }
  return out;
}

export function isReadyFromEntry(entry) {
  return !!(entry && entry.state === 'READY');
}

export function renderBadge(entry) {
  if (!entry) return 'not rendered';
  switch (entry.state) {
    case 'READY': return 'ready';
    case 'QUEUED': return 'queued';
    case 'RENDERING':
      return (typeof entry.percent === 'number')
        ? `rendering… ${entry.percent}%` : 'rendering…';
    case 'STALE': return 'needs re-render';
    case 'FAILED': return 'render failed';
    default: return 'not rendered';
  }
}
