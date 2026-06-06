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

const DEFAULT_DURATION_HR = 1;
const LABEL_COL_PX = 110;  // matches grid-template-columns: 110px in renderDay

export function attachPlaylistToTrack(store) {
  document.addEventListener('dragover', (ev) => {
    const drag = getDrag();
    if (!drag || drag.kind !== 'playlist') return;
    const droparea = ev.target.closest('.mm-track-droparea');
    if (!droparea) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'copy';
    droparea.classList.add('mm-drag-target');
  }, true);

  document.addEventListener('dragleave', (ev) => {
    const drag = getDrag();
    if (!drag) return;
    const droparea = ev.target.closest('.mm-track-droparea');
    if (droparea) droparea.classList.remove('mm-drag-target');
  }, true);

  document.addEventListener('drop', (ev) => {
    const drag = getDrag();
    if (!drag || drag.kind !== 'playlist') return;
    const droparea = ev.target.closest('.mm-track-droparea');
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
    store.createSchedule({
      playlistName: drag.playlistName,
      displayID,
      startTime: hourToHHMM(startHr),
      endTime: hourToHHMM(endHr),
      freq: 'DAILY',
      dtstart: store.viewDate,
    }).catch(() => {/* toast already surfaced */});
  }, true);
}
