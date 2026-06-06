/**
 * PR-4c gap-fix (spec §362): click a track header → popover with a
 * `profileName` dropdown per client in that group. Calls
 * store.assignProfileToClient (already wired in PR-4c T-C1).
 *
 * Why a per-client list (rather than one group-level profile picker):
 * a display group may contain multiple device types (an iPad + an
 * Android tablet in 'LobbyWall') and each device's profile selection
 * is independent. The track header is the natural entry point because
 * it's already showing the group's online-count.
 *
 * Mounted into the #mmTrackHeaderPopover element in admin.html so we
 * can style it without injecting CSS at runtime.
 *
 * Click outside / Esc dismisses. Auto-match (server-side) is the
 * (no override) sentinel option: setting profileName='' restores it.
 */
export function attachTrackHeaderPopover(store) {
  const pop = document.getElementById('mmTrackHeaderPopover');
  if (!pop) return;

  function close() {
    pop.style.display = 'none';
    pop.innerHTML = '';
  }

  function open(ev, displayID) {
    const clients = store.displays.filter(d => d.displayID === displayID);
    const profileNames = Object.keys(store.profiles || {}).sort();

    pop.innerHTML = '';
    const header = document.createElement('div');
    header.className = 'mm-thp-header';
    const h = document.createElement('strong');
    h.textContent = `Profile overrides for ${displayID}`;
    header.appendChild(h);
    const hint = document.createElement('span');
    hint.className = 'mm-thp-hint';
    hint.textContent = '(blank = auto-match by device type)';
    header.appendChild(hint);
    pop.appendChild(header);

    if (clients.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'mm-thp-empty';
      empty.textContent = `No clients currently in '${displayID}'. Devices will inherit the auto-matched profile when they register.`;
      pop.appendChild(empty);
    } else {
      const list = document.createElement('ul');
      list.className = 'mm-thp-list';
      for (const c of clients) {
        const li = document.createElement('li');
        const name = document.createElement('span');
        name.className = 'mm-thp-name';
        name.textContent = c.friendlyName || c.clientKey || c.id || '(unknown)';
        const meta = document.createElement('span');
        meta.className = 'mm-thp-meta';
        meta.textContent = c.deviceType ? `(${c.deviceType})` : '';
        const sel = document.createElement('select');
        sel.dataset.clientKey = c.clientKey || c.id;
        const optAuto = document.createElement('option');
        optAuto.value = '';
        optAuto.textContent = '(auto-match)';
        sel.appendChild(optAuto);
        for (const pn of profileNames) {
          const opt = document.createElement('option');
          opt.value = pn;
          opt.textContent = pn;
          if ((c.profileName || '') === pn) opt.selected = true;
          sel.appendChild(opt);
        }
        sel.addEventListener('change', () => {
          const newName = sel.value || '';
          store.assignProfileToClient(sel.dataset.clientKey, newName).catch(() => {});
        });
        li.appendChild(name);
        li.appendChild(meta);
        li.appendChild(sel);
        list.appendChild(li);
      }
      pop.appendChild(list);
    }

    // Position. Anchor relative to the clicked header rect.
    const anchor = (ev.target.closest('.mm-track-header') || ev.target).getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    pop.style.display = 'block';
    const pw = pop.offsetWidth || 280, ph = pop.offsetHeight || 100;
    // Open to the RIGHT of the header label (the label column is 110px).
    pop.style.left = `${Math.min(anchor.right + 6, vw - pw - 4)}px`;
    pop.style.top  = `${Math.min(anchor.top, vh - ph - 4)}px`;
  }

  document.addEventListener('click', (ev) => {
    const header = ev.target.closest('.mm-track-header');
    if (!header) return;
    ev.preventDefault();
    ev.stopPropagation();
    const displayID = header.dataset.displayId;
    if (displayID) open(ev, displayID);
  }, true);

  document.addEventListener('mousedown', (ev) => {
    if (pop.style.display === 'none' || !pop.style.display) return;
    if (pop.contains(ev.target)) return;
    // Allow clicking another track header to re-open (the click handler
    // above will close + reopen via innerHTML reset). Closing here would
    // race; just bail for the header case.
    if (ev.target.closest('.mm-track-header')) return;
    close();
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && pop.style.display === 'block') close();
  });
}
