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

/** How many of these devices are calibrated. Reads the derived `calibrated`
 * flag from /api/discovery/devices (the serializer does NOT ship the raw
 * measuredPerimeter array); falls back to measuredPerimeter for any caller
 * that passes raw client objects. */
export function calibrationSummary(devices) {
  const list = devices || [];
  let calibratedCount = 0;
  for (const d of list) if (d.calibrated || d.measuredPerimeter != null) calibratedCount += 1;
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

/**
 * Per-device cache download status for the Fleet Devices card. Pure — reads the
 * fields /api/discovery/devices already returns. Returns:
 *   { applicable, percent, cached, expected, inFlight, stalled, mbps, label, state }
 * `state` is a three-way discriminator: 'network' (streams, no local cache) |
 * 'caching' (download in progress) | 'local' (fully cached).
 * `applicable` is false when the device isn't locally caching (cacheMode 'none')
 * or has nothing to cache (expectedSegments 0) — the chip is hidden in those cases.
 */
export function deviceCacheStatus(device) {
  const d = device || {};
  const mode = d.cacheMode || 'none';
  const cached = Array.isArray(d.cachedSegments) ? d.cachedSegments.length : (d.cachedSegments || 0);
  const expected = d.expectedSegments || 0;
  const prog = d.cachePushProgress || null;
  if (mode === 'none') {
    return { applicable: false, percent: 100, cached, expected, inFlight: false,
             stalled: false, mbps: null, label: 'streams (no local cache)', state: 'network' };
  }
  if (!expected) {
    return { applicable: false, percent: 100, cached, expected: 0, inFlight: false,
             stalled: false, mbps: null, label: 'nothing to cache', state: 'network' };
  }
  const percent = Math.max(0, Math.min(100, Math.round((cached / expected) * 100)));
  const stalled = !!(prog && prog.status === 'stalled');
  const inFlight = !!(prog && prog.status === 'active');
  const mbps = prog && typeof prog.mbps === 'number' ? prog.mbps : null;
  let label;
  if (stalled) label = 'stalled';
  else if (inFlight) label = `downloading ${(Math.floor((mbps || 0) * 10) / 10).toFixed(1)} MB/s`;
  else label = `cached ${cached}/${expected}`;
  const state = percent >= 100 ? 'local' : 'caching';
  return { applicable: true, percent, cached, expected, inFlight, stalled, mbps, label, state };
}
