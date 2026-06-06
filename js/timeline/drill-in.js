/**
 * Double-click a clip → store.drillInto(scheduleId). The timeline.js
 * renderer then emits an inline sub-track BELOW the day grid showing
 * the playlist's items as separate sub-clips. A second double-click on
 * the same clip collapses (store.drillInto toggles).
 *
 * Why dblclick instead of (say) a chevron button: dblclick is the
 * universal "open this" gesture from desktop file managers + tools
 * like Premiere/Final Cut; an extra UI affordance would clutter the
 * clip. Tradeoff: dblclick can't be discovered without a tooltip,
 * which we'll add in a polish pass.
 */
export function attachDrillIn(store) {
  document.addEventListener('dblclick', (ev) => {
    const clip = ev.target.closest('.mm-clip');
    if (!clip) return;
    const id = clip.dataset.scheduleId;
    if (id) store.drillInto(id);
  });
}
