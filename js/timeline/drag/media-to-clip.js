/**
 * Drag a media-bin item onto the drilled-in sub-row of a clip → append
 * the file to that clip's playlist (PUT /api/playlists/{name} with
 * If-Match). The store's optimistic-local + rollback path (see
 * store.updatePlaylist + util/optimistic.js) handles the UI feedback.
 *
 * The drilled-in row is the only valid drop target: the drag is
 * conceptually "add this media to *this specific* playlist", and the
 * drilled-in row IS the visual representation of that playlist's
 * items, so dropping anywhere else would be ambiguous.
 */
import { getDrag, clearDrag } from './dragstate.js';

export function attachMediaToClip(store) {
  document.addEventListener('dragover', (ev) => {
    const drag = getDrag();
    if (!drag || drag.kind !== 'media') return;
    const sub = ev.target.closest('.mm-drillin-row');
    if (!sub) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'copy';
    sub.classList.add('mm-drag-target');
  }, true);

  document.addEventListener('dragleave', (ev) => {
    const drag = getDrag();
    if (!drag || drag.kind !== 'media') return;
    const sub = ev.target.closest('.mm-drillin-row');
    if (sub) sub.classList.remove('mm-drag-target');
  }, true);

  document.addEventListener('drop', (ev) => {
    const drag = getDrag();
    if (!drag || drag.kind !== 'media') return;
    const sub = ev.target.closest('.mm-drillin-row');
    if (!sub) return;
    ev.preventDefault();
    sub.classList.remove('mm-drag-target');
    const playlistName = sub.dataset.playlistName;
    if (!playlistName) return;
    const playlist = store.playlists[playlistName];
    if (!playlist) return;
    const newItem = drag.duration != null
      ? { file: drag.file, duration: drag.duration }
      : { file: drag.file };
    const newItems = [...(playlist.items || []), newItem];
    clearDrag();
    document.body.classList.remove('mm-dragging');
    store.updatePlaylist(playlistName, { items: newItems }).catch(() => {});
  }, true);
}
