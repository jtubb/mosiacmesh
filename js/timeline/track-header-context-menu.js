/**
 * Right-click on a track header → context menu. Items:
 *   - Reload group (spec §361, PR-9): SockJS RELOAD scoped to displayID.
 *   - Delete group (PR-12): DELETE /api/displays/{displayID}, blocks
 *     with the server's 409+refs error if the group has any clients or
 *     schedules; the server requires the operator to reassign before
 *     the group can be removed.
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
export function attachTrackHeaderContextMenu(store) {
  const menu = document.getElementById('mmContextMenu');
  if (!menu) return;

  function close() { menu.style.display = 'none'; menu.innerHTML = ''; }

  function open(ev, displayID) {
    menu.innerHTML = '';
    const items = [
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
