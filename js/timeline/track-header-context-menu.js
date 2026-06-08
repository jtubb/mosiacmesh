/**
 * Right-click on a track header → context menu. Items:
 *   - Login / Start / Stop / Reboot / Test (PR-13): RUN_SCRIPT scoped
 *     to displayID. Same fleet-confirm modal as the toolbar buttons,
 *     just pre-scoped so the operator doesn't have to set the toolbar
 *     dropdown first when they're already pointing at a specific
 *     track. Routes through fleet-confirm.fireFleetAction so >3-device
 *     prompts behave identically.
 *   - Reload group (spec §361, PR-9): SockJS RELOAD scoped to displayID.
 *   - Delete group (PR-12): DELETE /api/displays/{displayID}, blocks
 *     with the server's 409+refs error if the group has any clients or
 *     schedules.
 *
 * Reuses #mmContextMenu (already populated by context-menu.js for
 * clip right-clicks) — only one menu is open at a time, so sharing the
 * element works. context-menu.js's outside-click / Esc handlers also
 * close ours; no extra dismiss wiring needed here.
 *
 * Distinct from track-header-popover.js, which handles LEFT-click on
 * the same element to open the per-client profile override popover
 * (PR-4c gap-2).
 */
import { fireFleetAction } from './modals/fleet-confirm.js';

export function attachTrackHeaderContextMenu(store) {
  const menu = document.getElementById('mmContextMenu');
  if (!menu) return;

  function close() { menu.style.display = 'none'; menu.innerHTML = ''; }

  function open(ev, displayID) {
    menu.innerHTML = '';
    const items = [
      // PR-13: per-group fleet actions. Sequence matches the toolbar
      // button order (login → start → stop → reboot → test) so the
      // muscle memory is the same.
      { label: 'Login',  action: () => fireFleetAction(store, 'login',  displayID) },
      { label: 'Start',  action: () => fireFleetAction(store, 'start',  displayID) },
      { label: 'Stop',   action: () => fireFleetAction(store, 'stop',   displayID) },
      { label: 'Reboot', action: () => fireFleetAction(store, 'reboot', displayID) },
      { label: 'Test',   action: () => fireFleetAction(store, 'test',   displayID) },
      { separator: true },
      {
        label: 'Reload group',
        action: () => {
          if (typeof window.sock === 'undefined' || typeof window.generateMessage !== 'function') {
            store.toast('SockJS not available; reload the page.', 'error');
            return;
          }
          try {
            window.sock.send(window.generateMessage('SRV', 'RELOAD', { displayID }));
            const count = (store.displays || []).filter(d => d.displayID === displayID).length;
            store.toast(`Reload sent to "${displayID}" (${count} device${count === 1 ? '' : 's'}).`, 'info');
          } catch (e) {
            store.toast(`Failed to send reload: ${e?.message || e}`, 'error');
          }
        },
      },
      {
        label: 'Delete group',
        action: async () => {
          // Confirm even when the group looks empty — display groups
          // are persistent infrastructure, not throwaway. The server's
          // 409+refs is the real backstop; this confirm is just a
          // sanity check against an errant right-click.
          if (!window.confirm(`Delete display group "${displayID}"? This cannot be undone.`)) return;
          try { await store.deleteDisplayGroup(displayID); }
          catch (_) { /* withRollback already toasted the server error */ }
        },
      },
    ];
    for (const it of items) {
      if (it.separator) {
        const sep = document.createElement('li');
        sep.className = 'mm-context-divider';
        sep.setAttribute('aria-hidden', 'true');
        menu.appendChild(sep);
        continue;
      }
      const li = document.createElement('li');
      li.textContent = it.label;
      li.addEventListener('click', () => { it.action(); close(); });
      menu.appendChild(li);
    }
    // Position. Clamp to viewport — same math as context-menu.js.
    const vw = window.innerWidth, vh = window.innerHeight;
    menu.style.display = 'block';
    const mw = menu.offsetWidth || 160, mh = menu.offsetHeight || 100;
    menu.style.left = `${Math.min(ev.clientX, vw - mw - 4)}px`;
    menu.style.top  = `${Math.min(ev.clientY, vh - mh - 4)}px`;
  }

  document.addEventListener('contextmenu', (ev) => {
    const header = ev.target.closest('.mm-track-header');
    if (!header) return;
    ev.preventDefault();
    const displayID = header.dataset.displayId;
    if (displayID) open(ev, displayID);
  }, true);
}
