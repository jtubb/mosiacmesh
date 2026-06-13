// Pure helpers for render-state UI. No DOM, no store — node-testable.

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
