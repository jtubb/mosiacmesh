/**
 * Inline recurrence popover. Alt+click on a clip opens a small floating
 * form (freq, interval, byweekday-for-WEEKLY, end-type with conditional
 * until/count fields). Save calls store.updateSchedule. Alt+click is a
 * temporary trigger until PR-4c lands the right-click context menu.
 *
 * Why not Alpine: the popover is a one-off, mostly-imperative widget
 * (focus management, conditional visibility, anchoring). The extra
 * machinery of x-data + Alpine bindings would be more code than the
 * direct querySelector calls below.
 */
export function attachRecurrencePopover(store) {
  const pop = document.getElementById('mmRecurrencePopover');
  if (!pop) return;
  let openForScheduleId = null;

  const $ = (sel) => pop.querySelector(sel);
  const $$ = (sel) => pop.querySelectorAll(sel);

  function open(scheduleId, anchorRect) {
    const s = store.schedules.find(x => x.id === scheduleId);
    if (!s) return;
    openForScheduleId = scheduleId;
    pop.style.display = 'block';
    pop.style.left = `${Math.max(8, anchorRect.left)}px`;
    pop.style.top  = `${anchorRect.bottom + 4}px`;
    $('[data-field="freq"]').value = s.freq || 'DAILY';
    $('[data-field="interval"]').value = s.interval || 1;
    $$('[data-field="byweekday"] input').forEach(cb => {
      cb.checked = (s.byweekday || []).includes(Number(cb.value));
    });
    const endType = (s.end && s.end.type) || 'never';
    $$('[data-field="endType"] input').forEach(r => { r.checked = (r.value === endType); });
    $('[data-field="untilDate"]').value = s.end?.untilDate || '';
    $('[data-field="count"]').value = s.end?.count || 1;
    updateConditionalVisibility();
  }

  function close() {
    pop.style.display = 'none';
    openForScheduleId = null;
  }

  function updateConditionalVisibility() {
    const freq = $('[data-field="freq"]').value;
    $('[data-field="byweekday"]').style.display = (freq === 'WEEKLY') ? '' : 'none';
    const endType = $('[data-field="endType"] input:checked')?.value || 'never';
    $('[data-field="untilRow"]').style.display = (endType === 'until') ? '' : 'none';
    $('[data-field="countRow"]').style.display = (endType === 'count') ? '' : 'none';
  }

  $('[data-field="freq"]').addEventListener('change', updateConditionalVisibility);
  $$('[data-field="endType"] input').forEach(r => r.addEventListener('change', updateConditionalVisibility));
  $('[data-action="cancel"]').addEventListener('click', close);
  $('[data-action="save"]').addEventListener('click', () => {
    if (!openForScheduleId) return;
    const freq = $('[data-field="freq"]').value;
    const interval = Math.max(1, parseInt($('[data-field="interval"]').value, 10) || 1);
    const byweekday = freq === 'WEEKLY'
      ? Array.from($$('[data-field="byweekday"] input:checked')).map(cb => Number(cb.value))
      : [];
    const endTypeEl = $('[data-field="endType"] input:checked');
    const endType = endTypeEl ? endTypeEl.value : 'never';
    let end = { type: 'never' };
    if (endType === 'until') end = { type: 'until', untilDate: $('[data-field="untilDate"]').value };
    if (endType === 'count') end = { type: 'count', count: Math.max(1, parseInt($('[data-field="count"]').value, 10) || 1) };
    const patch = { freq, interval, byweekday, end };
    store.updateSchedule(openForScheduleId, patch).then(close, close);
  });

  document.addEventListener('click', (ev) => {
    if (!ev.altKey) return;
    const clip = ev.target.closest('.mm-clip');
    if (!clip) return;
    ev.preventDefault();
    ev.stopPropagation();
    open(clip.dataset.scheduleId, clip.getBoundingClientRect());
  }, true);

  // Click outside the popover closes it. mousedown rather than click so
  // the close happens before any click event the user starts elsewhere.
  document.addEventListener('mousedown', (ev) => {
    if (pop.style.display === 'block' && !pop.contains(ev.target)) close();
  });
}
