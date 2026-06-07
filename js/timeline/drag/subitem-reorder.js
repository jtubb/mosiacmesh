/**
 * Drag a sub-item within the drilled-in row to reorder it inside the
 * parent playlist. Spec §358 — closes the "drag sub-clips to reorder"
 * sibling of PR-4c T-B1 (drag media → clip) and the PR-6 gap-1 Del-on-
 * sub-clip work.
 *
 * Gesture model:
 *   - Single-click  → SELECT (PR-6 gap-1: store.selectedSubItem)
 *   - Double-click  → OPEN editor (PR-4c T-B1: playlist-item editor)
 *   - Drag          → REORDER within row (this file)
 *   - Del key       → REMOVE selected (PR-6 gap-1: store.removePlaylistItem)
 *
 * Cross-row drags are intentionally NOT supported here — moving an
 * item from playlist A to playlist B is a different operation
 * (semantically: remove from A + add to B, with potentially different
 * defaults). The drop handler bails if the target row's playlist
 * differs from the source.
 *
 * Dragstate.kind 'sub-item' keeps this drag stream disjoint from the
 * existing kinds ('media' for bin→clip, 'playlist' for bin→track,
 * 'clip-move' for clip→track). The media-to-clip handler bails when
 * it sees a non-'media' kind, so a sub-item drag passing over the
 * drilled row never accidentally appends.
 */
import { setDrag, getDrag, clearDrag } from './dragstate.js';

export function attachSubItemReorder(store) {
  document.addEventListener('dragstart', (ev) => {
    const item = ev.target.closest('.mm-drillin-item');
    if (!item) return;
    const row = item.closest('.mm-drillin-row');
    if (!row) return;
    const playlistName = row.dataset.playlistName;
    const sourceIndex = Number(item.dataset.itemIndex);
    if (!playlistName || !Number.isFinite(sourceIndex)) return;
    ev.dataTransfer.effectAllowed = 'move';
    ev.dataTransfer.setData('application/x-mm-subitem', String(sourceIndex));
    setDrag({ kind: 'sub-item', playlistName, sourceIndex });
    document.body.classList.add('mm-dragging');
  }, true);

  document.addEventListener('dragover', (ev) => {
    const drag = getDrag();
    if (!drag || drag.kind !== 'sub-item') return;
    const overItem = ev.target.closest('.mm-drillin-item');
    const overRow  = ev.target.closest('.mm-drillin-row');
    if (!overRow || overRow.dataset.playlistName !== drag.playlistName) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'move';
    // Clear any prior target highlight in this row, then mark the new one.
    overRow.querySelectorAll('.mm-drillin-item.mm-drillin-drop-target')
      .forEach(el => el.classList.remove('mm-drillin-drop-target'));
    if (overItem && Number(overItem.dataset.itemIndex) !== drag.sourceIndex) {
      overItem.classList.add('mm-drillin-drop-target');
    }
  }, true);

  document.addEventListener('dragleave', (ev) => {
    const drag = getDrag();
    if (!drag || drag.kind !== 'sub-item') return;
    const overItem = ev.target.closest('.mm-drillin-item');
    if (overItem) overItem.classList.remove('mm-drillin-drop-target');
  }, true);

  document.addEventListener('drop', (ev) => {
    const drag = getDrag();
    if (!drag || drag.kind !== 'sub-item') return;
    const targetItem = ev.target.closest('.mm-drillin-item');
    const targetRow  = ev.target.closest('.mm-drillin-row');
    if (!targetRow || targetRow.dataset.playlistName !== drag.playlistName) {
      clearDrag();
      document.body.classList.remove('mm-dragging');
      return;
    }
    ev.preventDefault();
    targetRow.querySelectorAll('.mm-drillin-item.mm-drillin-drop-target')
      .forEach(el => el.classList.remove('mm-drillin-drop-target'));
    // No specific item under cursor → drop at end. Otherwise: insert
    // BEFORE the target item (or at end if dropping on the source's
    // own slot — caught by the no-op check below).
    let targetIndex = targetItem ? Number(targetItem.dataset.itemIndex) : -1;
    clearDrag();
    document.body.classList.remove('mm-dragging');

    const pl = store.playlists[drag.playlistName];
    if (!pl) return;
    const items = (pl.items || []).slice();
    if (drag.sourceIndex < 0 || drag.sourceIndex >= items.length) return;
    if (targetIndex === -1) targetIndex = items.length;   // dropped on empty row area → append
    if (targetIndex === drag.sourceIndex) return;          // no-op
    // Splice out, then splice in. Note: when targetIndex > sourceIndex,
    // the indices shift by 1 after the removal — adjust so we land at
    // the intended position rather than one past it.
    const [moved] = items.splice(drag.sourceIndex, 1);
    const adjustedTarget = targetIndex > drag.sourceIndex ? targetIndex - 1 : targetIndex;
    items.splice(adjustedTarget, 0, moved);
    store.updatePlaylist(drag.playlistName, { items }).catch(() => { /* toast via withRollback */ });
  }, true);

  document.addEventListener('dragend', () => {
    const drag = getDrag();
    if (drag && drag.kind === 'sub-item') {
      clearDrag();
      document.body.classList.remove('mm-dragging');
      // Defensive: clear any lingering target highlights from a drag
      // that ended outside a drop target (Esc, off-screen, etc.).
      document.querySelectorAll('.mm-drillin-item.mm-drillin-drop-target')
        .forEach(el => el.classList.remove('mm-drillin-drop-target'));
    }
  }, true);
}
