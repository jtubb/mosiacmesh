/**
 * Track-header row: per-display label, status, render-progress badge.
 *
 * Inputs (function-style — caller passes in resolved values from the
 * store; we don't reach into Alpine here, to keep this importable in
 * Node for future tests):
 *   {
 *     displayID:       string,
 *     friendlyName:    string | null,
 *     onlineCount:     int,
 *     totalCount:      int,
 *     renderInProgress: bool,
 *   }
 *
 * Returns an HTML string.
 *
 * Read-only in PR-4a. PR-4b adds a click handler that opens the
 * track popover (default-playlist + profile override).
 */

function dotColor(online, total) {
  if (total === 0) return '#888';            // no devices
  if (online === 0) return 'var(--err)';     // all offline
  if (online < total) return 'var(--warn)';  // partial
  return 'var(--ok)';                        // all online
}

export function trackHeaderHtml({ displayID, friendlyName, onlineCount, totalCount, renderInProgress }) {
  const label = friendlyName || displayID;
  const color = dotColor(onlineCount, totalCount);
  const badge = renderInProgress
    ? `<span class="mm-render-badge" title="render in progress">⟳ rendering</span>`
    : '';
  return `
    <div class="mm-track-header" data-display-id="${escapeAttr(displayID)}">
      <div class="mm-track-name">${escapeText(label)}</div>
      <div class="mm-track-status">
        <span class="mm-status-dot" style="background:${color}"></span>
        <span class="mm-status-count">${onlineCount}/${totalCount} online</span>
        ${badge}
      </div>
    </div>
  `;
}

function escapeText(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function escapeAttr(s) {
  return escapeText(s).replace(/"/g, '&quot;');
}
