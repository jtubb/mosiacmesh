/**
 * Pure derivation of the Now-landing cards from store slices. One card per
 * display group: screen/online counts (from the group summary, falling back
 * to counting `displays`), the playback state + current playlist (from the
 * /api/playback surface), and a render indicator.
 *
 * Kept pure + DOM-free so it's unit-testable; the Now markup is a declarative
 * x-for over store.nowCards (which calls this).
 */
export function buildNowSummary({ displayGroups = [], displays = [], playback = {}, renderInProgress = {} } = {}) {
  return displayGroups.map((g) => {
    const pb = playback[g.displayID] || {};
    let screenCount = g.clientCount;
    let onlineCount = g.onlineCount;
    if (screenCount == null || onlineCount == null) {
      let s = 0, o = 0;
      for (const c of displays) {
        if (c.displayID !== g.displayID) continue;
        s += 1;
        if (c.isOnline) o += 1;
      }
      if (screenCount == null) screenCount = s;
      if (onlineCount == null) onlineCount = o;
    }
    const renderStatus = pb.renderStatus || (renderInProgress[g.displayID] ? 'rendering' : '');
    return {
      displayID: g.displayID,
      screenCount,
      onlineCount,
      state: pb.state || 'idle',
      currentPlaylist: pb.currentPlaylist || null,
      renderStatus,
    };
  });
}
