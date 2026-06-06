// js/timeline/context-menu.js
/**
 * Right-click context menu for clips. Renders a <ul> at the cursor
 * with: Edit schedule, Edit playlist items, Duplicate, Delete.
 * Click on an item invokes the appropriate action + closes the menu.
 * Click anywhere else closes it.
 *
 * The menu element lives in admin.html as #mmContextMenu so we can
 * style it without injecting CSS at runtime.
 */
import { openRecurrenceEditor } from './modals/recurrence-editor.js';
import { openPlaylistEditor }   from './modals/playlist-editor.js';

export function attachContextMenu(store) {
  const menu = document.getElementById('mmContextMenu');
  if (!menu) return;

  function close() { menu.style.display = 'none'; menu.innerHTML = ''; }

  function open(ev, scheduleId) {
    const s = store.schedules.find(x => x.id === scheduleId);
    if (!s) return;
    menu.innerHTML = '';
    const items = [
      { label: 'Edit schedule…',        action: () => openRecurrenceEditor(store, scheduleId) },
      { label: 'Edit playlist items…',  action: () => openPlaylistEditor(store, s.playlistName) },
      { label: 'Duplicate',             action: () => duplicate(store, s) },
      { divider: true },
      { label: 'Delete', danger: true,  action: () => deleteOne(store, scheduleId) },
    ];
    for (const it of items) {
      const li = document.createElement('li');
      if (it.divider) { li.className = 'mm-context-divider'; menu.appendChild(li); continue; }
      li.textContent = it.label;
      if (it.danger) li.className = 'mm-context-danger';
      li.addEventListener('click', () => { it.action(); close(); });
      menu.appendChild(li);
    }
    // Position. Clamp to viewport so the menu never opens off-screen.
    const vw = window.innerWidth, vh = window.innerHeight;
    menu.style.display = 'block';
    const mw = menu.offsetWidth || 160, mh = menu.offsetHeight || 100;
    menu.style.left = `${Math.min(ev.clientX, vw - mw - 4)}px`;
    menu.style.top  = `${Math.min(ev.clientY, vh - mh - 4)}px`;
  }

  document.addEventListener('contextmenu', (ev) => {
    const clip = ev.target.closest('.mm-clip');
    if (!clip) return;
    ev.preventDefault();
    open(ev, clip.dataset.scheduleId);
  }, true);

  document.addEventListener('mousedown', (ev) => {
    if (menu.style.display === 'none') return;
    if (!menu.contains(ev.target)) close();
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && menu.style.display !== 'none') close();
  });
}

function duplicate(store, sched) {
  // Same shape, no id (server generates one), default to one hour later
  // to avoid visual stacking on the same start time.
  const [sh, sm] = (sched.startTime || '09:00').split(':').map(Number);
  const [eh, em] = (sched.endTime   || '10:00').split(':').map(Number);
  const startMin = sh * 60 + sm + 60;
  const endMin   = eh * 60 + em + 60;
  const newStart = `${String(Math.min(23, Math.floor(startMin / 60))).padStart(2,'0')}:${String(startMin % 60).padStart(2,'0')}`;
  const newEnd   = `${String(Math.min(23, Math.floor(endMin   / 60))).padStart(2,'0')}:${String(endMin   % 60).padStart(2,'0')}`;
  store.createSchedule({
    playlistName: sched.playlistName,
    displayID: sched.displayID,
    freq: sched.freq, interval: sched.interval,
    byweekday: [...(sched.byweekday || [])],
    dtstart: sched.dtstart, end: { ...(sched.end || { type: 'never' }) },
    startTime: newStart, endTime: newEnd,
    priority: sched.priority,
  }).catch(() => {});
}

function deleteOne(store, scheduleId) {
  store.deleteSchedule(scheduleId).catch(() => {});
}
