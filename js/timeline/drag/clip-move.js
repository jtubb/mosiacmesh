/**
 * Drag a clip body to move it within the same track (changes
 * startTime/endTime preserving duration) or across tracks (also
 * changes displayID). Uses HTML5 drag events with a `mm-clip-drag`
 * payload kind. Cross-track requires the target to be a track-droparea
 * with a different displayID.
 *
 * The drop point is "where the LEFT edge of the clip would land", not
 * "where the cursor is" — operators expect the clip to follow the
 * grabbed point, so we offset by where in the clip they clicked. 15-min
 * snap, duration preserved.
 */
import { setDrag, getDrag, clearDrag } from './dragstate.js';
import { pxToHour, snapTo15min, hourToHHMM } from '../util/snap.js';

const LABEL_COL_PX = 110;

export function attachClipMove(store) {
  document.addEventListener('dragstart', (ev) => {
    const clip = ev.target.closest('.mm-clip');
    if (!clip) return;
    const id = clip.dataset.scheduleId;
    if (!id) return;
    const sched = store.schedules.find(s => s.id === id);
    if (!sched) return;
    ev.dataTransfer.effectAllowed = 'move';
    ev.dataTransfer.setData('application/x-mm-clip', id);
    // Record the clip's rect + the click offset so we can compute the
    // drop position as "where the LEFT edge would land".
    const r = clip.getBoundingClientRect();
    setDrag({
      kind: 'clip-move',
      scheduleId: id,
      offsetXInClip: ev.clientX - r.left,
      originalStartTime: sched.startTime,
      originalEndTime: sched.endTime,
      originalDisplayID: sched.displayID,
    });
    document.body.classList.add('mm-dragging');
  }, true);

  document.addEventListener('dragover', (ev) => {
    const drag = getDrag();
    if (!drag || drag.kind !== 'clip-move') return;
    const droparea = ev.target.closest('.mm-track-droparea');
    if (!droparea) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'move';
    droparea.classList.add('mm-drag-target');
  }, true);

  document.addEventListener('dragleave', (ev) => {
    const drag = getDrag();
    if (!drag || drag.kind !== 'clip-move') return;
    const droparea = ev.target.closest('.mm-track-droparea');
    if (droparea) droparea.classList.remove('mm-drag-target');
  }, true);

  document.addEventListener('drop', (ev) => {
    const drag = getDrag();
    if (!drag || drag.kind !== 'clip-move') return;
    const droparea = ev.target.closest('.mm-track-droparea');
    if (!droparea) return;
    ev.preventDefault();
    droparea.classList.remove('mm-drag-target');
    const newDisplay = droparea.dataset.displayId;
    const grid = droparea.closest('.mm-day-grid');
    if (!grid) return;
    const gridRect = grid.getBoundingClientRect();
    const usableLeft = gridRect.left + LABEL_COL_PX;
    const usableWidth = gridRect.width - LABEL_COL_PX;
    // Drop X minus the offset = where the clip's LEFT edge lands.
    const startHr = snapTo15min(pxToHour((ev.clientX - drag.offsetXInClip) - usableLeft, usableWidth));
    const duration = hoursBetween(drag.originalStartTime, drag.originalEndTime);
    const endHr = Math.min(24, startHr + duration);
    const patch = {
      startTime: hourToHHMM(startHr),
      endTime: hourToHHMM(endHr),
    };
    if (newDisplay !== drag.originalDisplayID) patch.displayID = newDisplay;
    clearDrag();
    document.body.classList.remove('mm-dragging');
    store.updateSchedule(drag.scheduleId, patch).catch(() => {});
  }, true);

  document.addEventListener('dragend', () => {
    // Safety net if drop didn't land on a droparea — clear state so the
    // next drag starts clean.
    clearDrag();
    document.body.classList.remove('mm-dragging');
  }, true);
}

function hoursBetween(startHHMM, endHHMM) {
  const [sh, sm] = startHHMM.split(':').map(Number);
  const [eh, em] = endHHMM.split(':').map(Number);
  let h = (eh + em / 60) - (sh + sm / 60);
  if (h < 0) h += 24;
  return h;
}
