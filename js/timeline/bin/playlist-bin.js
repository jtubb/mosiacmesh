import { setDrag, clearDrag } from '../drag/dragstate.js';

export function mmPlaylistBinComponent() {
  return {
    get list() {
      return Object.values(this.$store.mm.playlists || {})
        .sort((a, b) => a.name.localeCompare(b.name));
    },
    dragStart(name, ev) {
      // HTML5 drag: set a payload on dataTransfer so the drop handler
      // works across re-renders. dragstate.js mirrors it for our
      // multi-handler coordination since dataTransfer is opaque in
      // some browser contexts.
      ev.dataTransfer.setData('application/x-mm-playlist', name);
      ev.dataTransfer.effectAllowed = 'copy';
      setDrag({ kind: 'playlist', playlistName: name });
      // Disable pointer-events on clips for the duration of the drag so
      // the track-row droparea (sitting under clips in the same grid
      // cell) receives the dragover/drop events. See admin.html CSS.
      document.body.classList.add('mm-dragging-playlist');
    },
    dragEnd() {
      clearDrag();
      document.body.classList.remove('mm-dragging-playlist');
    },
  };
}
