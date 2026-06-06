/**
 * 412 conflict resolver. When a PUT fails with 412 ("If-Match stale"),
 * fetching the fresh entity + replacing the store slice + toasting the
 * server's update message is friendlier than the bare rollback that
 * PR-4b shipped — operators get to keep their session state and know
 * what happened.
 *
 * Returns nothing; mutates the store as a side-effect. Throws if the
 * refetch itself fails (rare; caller falls back to plain rollback).
 */
import { api } from '../api.js';

export async function refetchAfterConflict(store, kind, id) {
  let displayName;
  if (kind === 'schedule') {
    const idx = store.schedules.findIndex(s => s.id === id);
    if (idx === -1) return;   // not in local store; nothing to merge or surface
    const fresh = await api.refetchSchedule(id);
    store.schedules[idx] = fresh;
    displayName = fresh.playlistName || id;
  } else if (kind === 'playlist') {
    const fresh = await api.refetchPlaylist(id);   // id = playlist name
    store.playlists[id] = fresh;
    displayName = id;
  } else {
    throw new Error(`refetchAfterConflict: unknown kind ${kind}`);
  }
  store.toast(`"${displayName}" was updated by another admin — pulled latest.`, 'info');
}
