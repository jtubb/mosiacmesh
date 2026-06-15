/**
 * Drag a playlist from the left bin onto a Day-view track row →
 * createSchedule(playlistName, displayID, startTime, endTime).
 *
 * Each track droparea in Day view has `data-display-id` (added in
 * timeline.js's renderDay). The drop event reads the playlist name
 * from the drag payload + computes the drop hour from the X
 * coordinate within the GRID's usable width (subtracting the
 * track-label column width).
 */
import { getDrag, clearDrag } from './dragstate.js';
import { pxToHour, snapTo15min, hourToHHMM } from '../util/snap.js';
import { showDragStartMarker, hideDragStartMarker } from './clip-move.js';

const DEFAULT_DURATION_HR = 1;
const LABEL_COL_PX = 110;  // matches grid-template-columns: 110px in renderDay

export function attachPlaylistToTrack(store) {
  // PR-21: walk the element stack at the cursor to find the droparea
  // when ev.target is a clip on top of it. See clip-move.js for the
  // full rationale (Chromium cancels drags when source has
  // pointer-events:none; we needed to remove that and use this
  // fallback to still find droparea targets reliably).
  function findDroparea(ev) {
    const direct = ev.target.closest && ev.target.closest('.mm-track-droparea');
    if (direct) return direct;
    if (typeof document.elementsFromPoint === 'function') {
      const stack = document.elementsFromPoint(ev.clientX, ev.clientY);
      for (const el of stack) {
        if (el.classList && el.classList.contains('mm-track-droparea')) return el;
      }
    }
    return null;
  }
  document.addEventListener('dragover', (ev) => {
    const drag = getDrag();
    if (!drag || drag.kind !== 'playlist') return;
    const droparea = findDroparea(ev);
    if (!droparea) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'copy';
    droparea.classList.add('mm-drag-target');
    // PR-25: snapped-start marker on the row the cursor is over.
    const grid = droparea.closest('.mm-day-grid');
    if (grid) {
      const gridRect = grid.getBoundingClientRect();
      const usableLeft = gridRect.left + LABEL_COL_PX;
      const usableWidth = gridRect.width - LABEL_COL_PX;
      const startHr = snapTo15min(pxToHour(ev.clientX - usableLeft, usableWidth));
      const markerX = usableLeft + (startHr / 24) * usableWidth;
      showDragStartMarker(markerX, droparea.getBoundingClientRect(), hourToHHMM(startHr));
    }
  }, true);

  document.addEventListener('dragleave', (ev) => {
    const drag = getDrag();
    if (!drag) return;
    const droparea = ev.target.closest && ev.target.closest('.mm-track-droparea');
    if (droparea) droparea.classList.remove('mm-drag-target');
  }, true);

  document.addEventListener('drop', (ev) => {
    const drag = getDrag();
    if (!drag || drag.kind !== 'playlist') return;
    const droparea = findDroparea(ev);
    if (!droparea) return;
    ev.preventDefault();
    droparea.classList.remove('mm-drag-target');
    const displayID = droparea.dataset.displayId;
    const grid = droparea.closest('.mm-day-grid');
    if (!grid) return;
    const gridRect = grid.getBoundingClientRect();
    const usableLeft = gridRect.left + LABEL_COL_PX;
    const usableWidth = gridRect.width - LABEL_COL_PX;
    const startHr = snapTo15min(pxToHour(ev.clientX - usableLeft, usableWidth));
    const endHr = Math.min(24, startHr + DEFAULT_DURATION_HR);
    clearDrag();
    hideDragStartMarker();
    store.createSchedule({
      playlistName: drag.playlistName,
      displayID,
      startTime: hourToHHMM(startHr),
      endTime: hourToHHMM(endHr),
      freq: 'DAILY',
      dtstart: store.viewDate,
    }).catch(() => {/* toast already surfaced */});
  }, true);

  // Hide marker if drag ends without dropping on a valid target.
  document.addEventListener('dragend', () => {
    const drag = getDrag();
    if (drag && drag.kind === 'playlist') hideDragStartMarker();
  }, true);
}
