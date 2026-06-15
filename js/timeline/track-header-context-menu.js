/**
 * Right-click on a track header → context menu.
 *
 * Section 4 (Fleet): device/group management moved to the Fleet tab.
 * The single item here deep-links the operator to Fleet and
 * pre-selects the relevant display group so they land in the right
 * place without extra navigation.
 *
 * Reuses #mmContextMenu (already populated by context-menu.js for
 * clip right-clicks) — only one menu is open at a time, so sharing the
 * element works. context-menu.js's outside-click / Esc handlers also
 * close ours; no extra dismiss wiring needed here.
 */
export function attachTrackHeaderContextMenu(store) {
  const menu = document.getElementById('mmContextMenu');
  if (!menu) return;

  function close() { menu.style.display = 'none'; menu.innerHTML = ''; }

  function open(ev, displayID) {
    menu.innerHTML = '';
    const items = [
      {
        label: 'Manage in Fleet →',
        action: () => {
          // Section 4: device/group/playback management lives in the
          // Fleet destination now. Route there and select this group.
          store.goTo('fleet');
          const fleet = document.querySelector('[x-data="mmFleet"]');
          if (fleet && fleet._x_dataStack) {
            try { window.Alpine.$data(fleet).selectGroup(displayID); } catch (_) { /* tolerate */ }
          }
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
