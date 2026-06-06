/**
 * Click selection + Del key + right-click → delete. Selection state
 * lives in store.selection (Set<scheduleId>) — clipDayHtml reads it
 * via timeline.js so Alpine's x-html re-render is enough to redraw the
 * outlines (no imperative DOM sync needed).
 *
 * Click on a .mm-clip selects it (Shift adds to multi-select). Click
 * empty timeline area clears. Del / Backspace deletes selected. Right-
 * click on a clip opens the context menu (context-menu.js, PR-4c T-A5).
 *
 * Clicks during/after an HTML5 drag fire a synthetic click on the
 * source element. We ignore clicks whose target sat under a clip whose
 * data-schedule-id was JUST involved in a drag — but for simplicity the
 * dragend in clip-move clears document.body.classList.mm-dragging, so
 * we check that here to avoid selecting on drop.
 */
export function attachSelection(store) {
  document.addEventListener('click', (ev) => {
    if (document.body.classList.contains('mm-dragging')) return;
    const clip = ev.target.closest('.mm-clip');
    if (clip) {
      const id = clip.dataset.scheduleId;
      if (id) store.selectClip(id, ev.shiftKey);
      return;
    }
    if (ev.target.closest('.mm-day-grid, .mm-week-grid, .mm-month-grid')) {
      if (!ev.shiftKey) store.clearSelection();
    }
  }, true);

  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Delete' && ev.key !== 'Backspace') return;
    const tag = (ev.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || ev.target.isContentEditable) return;
    if (store.selection.size === 0) return;
    ev.preventDefault();
    const ids = Array.from(store.selection);
    // Soft confirm only for bulk deletes — single-clip Del is the
    // common case and prompting would feel heavy.
    if (ids.length > 3 && !confirm(`Delete ${ids.length} schedules?`)) return;
    for (const id of ids) {
      store.deleteSchedule(id).catch(() => {});
    }
    store.clearSelection();
  }, true);
}
