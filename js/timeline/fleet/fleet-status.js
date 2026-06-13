/**
 * Pure helpers for the Fleet view (Section 4). No DOM, no fetch —
 * node-importable for tests.
 *
 *   group shape:   { displayID, clientCount, onlineCount, scheduleCount, clients[] }
 *   device shape:  { clientKey, displayID, friendlyName, isOnline, profileName,
 *                    deviceType, measuredPerimeter? }
 *   playback[id]:  { state, currentPlaylist, ... }   state in PLAY|PAUSE|STOP|IDLE|PREPARING
 */
import { isReadyFromEntry, renderBadge } from '../util/render-helpers.js';

/** Group-level status for the master list + detail header. */
export function groupStatusLine(group, playback, renderInProgress) {
  const displayID = group && group.displayID;
  const online = (group && group.onlineCount != null) ? group.onlineCount : 0;
  const total = (group && group.clientCount != null) ? group.clientCount : 0;
  const pb = (playback && displayID && playback[displayID]) || null;
  // "playing" = an active (non-stopped, non-idle) playlist is mounted.
  const state = pb && pb.state;
  const playing = !!(pb && state && state !== 'STOP' && state !== 'IDLE' && state !== 'NOACTION');
  const playlistName = (playing && pb) ? (pb.currentPlaylist || null) : null;
  const rendering = !!(renderInProgress && displayID && renderInProgress[displayID]);
  return { displayID, online, total, playing, playlistName, rendering };
}

/**
 * The devices in a group, sorted by friendly name. Natural + case-insensitive
 * (so "screen2" sorts before "screen13", and case doesn't reorder).
 */
export function deviceRowsForGroup(group, displays) {
  const id = group && group.displayID;
  const rows = (displays || []).filter(d => d.displayID === id);
  rows.sort((a, b) => {
    const an = a.friendlyName || a.clientKey || '';
    const bn = b.friendlyName || b.clientKey || '';
    return an.localeCompare(bn, undefined, { numeric: true, sensitivity: 'base' });
  });
  return rows;
}

/** How many of these devices report a calibration quad. */
export function calibrationSummary(devices) {
  const list = devices || [];
  let calibratedCount = 0;
  for (const d of list) if (d.measuredPerimeter != null) calibratedCount += 1;
  return { calibratedCount, total: list.length };
}

/**
 * Per-playlist render readiness for the Fleet group-detail panel.
 * Returns an array sorted by playlist name; each entry:
 *   { name, label, ready }
 * Non-renderable playlists (no SEGMENT/INDIVIDUAL items) are always "ready".
 */
export function playlistReadinessForGroup(displayID, playlists, renders) {
  const reg = (renders && renders[displayID]) || {};
  return Object.keys(playlists || {}).sort().map((name) => {
    const pl = playlists[name];
    const renderable = (pl.items || []).some(
      (it) => it.playmode === 'SEGMENT' || it.playmode === 'INDIVIDUAL');
    if (!renderable) return { name, label: 'ready', ready: true };
    const entry = reg[name];
    return { name, label: renderBadge(entry), ready: isReadyFromEntry(entry) };
  });
}
