/**
 * Drag the left or right edge of a clip to resize the schedule. Uses
 * pointer events (not HTML5 drag) because we need continuous position
 * tracking + visual feedback as the operator drags — the HTML5 drag
 * API hides the source mid-drag, which would obscure the resize.
 *
 * On pointerdown on a `.mm-clip-resize-handle`, capture the pointer
 * and track pointermove until pointerup. The clip's startTime or
 * endTime updates locally on every move (visual feedback); the PUT
 * fires once on pointerup. Before the PUT, we roll the visual change
 * back to the original times so the store-level withRollback wrapper
 * sees a clean before-state — it applies its own optimistic update
 * + rollback path on top.
 *
 * Resize handles are `draggable="false"` so a pointerdown on them does
 * NOT start an HTML5 clip-move drag on the parent .mm-clip.
 */
import { pxToHour, snapTo15min, hourToHHMM } from '../util/snap.js';

const LABEL_COL_PX = 110;
const MIN_DURATION_HR = 0.25;

export function attachClipResize(store) {
  document.addEventListener('pointerdown', (ev) => {
    const handle = ev.target.closest('.mm-clip-resize-handle');
    if (!handle) return;
    const clip = handle.closest('.mm-clip');
    if (!clip) return;
    const id = clip.dataset.scheduleId;
    const edge = handle.dataset.edge;
    const sched = store.schedules.find(s => s.id === id);
    if (!sched) return;
    ev.preventDefault();
    ev.stopPropagation();
    // setPointerCapture lets the cursor leave the 6px handle without
    // dropping events. Failures (e.g. synthetic events with no real
    // pointer in tests) are non-fatal — the document-level listeners
    // below still catch everything.
    try { handle.setPointerCapture(ev.pointerId); } catch (_) { /* ok */ }
    const grid = clip.closest('.mm-day-grid');
    if (!grid) return;
    const gridRect = grid.getBoundingClientRect();
    const usableLeft = gridRect.left + LABEL_COL_PX;
    const usableWidth = gridRect.width - LABEL_COL_PX;
    const origStart = sched.startTime;
    const origEnd   = sched.endTime;
    let lastPatch = null;

    function onMove(mv) {
      const hr = snapTo15min(pxToHour(mv.clientX - usableLeft, usableWidth));
      if (edge === 'left') {
        const cap = hhmmToHour(sched.endTime) - MIN_DURATION_HR;
        sched.startTime = hourToHHMM(Math.min(Math.max(0, hr), cap));
        lastPatch = { startTime: sched.startTime };
      } else {
        const floor = hhmmToHour(sched.startTime) + MIN_DURATION_HR;
        sched.endTime = hourToHHMM(Math.max(Math.min(24, hr), floor));
        lastPatch = { endTime: sched.endTime };
      }
    }
    function onUp() {
      document.removeEventListener('pointermove', onMove, true);
      document.removeEventListener('pointerup', onUp, true);
      document.removeEventListener('pointercancel', onUp, true);
      if (!lastPatch) return;
      // Roll back the live preview so withRollback can apply + revert
      // from a clean before-state.
      sched.startTime = origStart;
      sched.endTime = origEnd;
      store.updateSchedule(id, lastPatch).catch(() => {});
    }
    // Listen on document (capture) so the resize keeps tracking even
    // when the cursor leaves the 6px handle.
    document.addEventListener('pointermove', onMove, true);
    document.addEventListener('pointerup', onUp, true);
    document.addEventListener('pointercancel', onUp, true);
  }, true);
}

function hhmmToHour(hhmm) {
  const [h, m] = hhmm.split(':').map(Number);
  return h + m / 60;
}
