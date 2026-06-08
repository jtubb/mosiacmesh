/**
 * PR-4c gap-fix (spec §362): click a track header → popover with a
 * `profileName` dropdown per client in that group. Calls
 * store.assignProfileToClient (already wired in PR-4c T-C1).
 *
 * PR-14 adds a "Group" dropdown next to the existing "Profile" select,
 * so operators can move individual devices into different display
 * groups without leaving the timeline (previously this required
 * navigating to discovery.html). Calls store.assignDeviceToDisplay,
 * which goes through POST /api/discovery/configure. The popover
 * closes after a successful move because the moved client is no
 * longer in the current group's filter, so the on-screen list would
 * be stale.
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
    const groupIDs = (store.displayGroups || []).map(g => g.displayID);

    pop.innerHTML = '';
    const header = document.createElement('div');
    header.className = 'mm-thp-header';
    const h = document.createElement('strong');
    h.textContent = `Devices in ${displayID}`;
    header.appendChild(h);
    const hint = document.createElement('span');
    hint.className = 'mm-thp-hint';
    hint.textContent = 'Profile: blank = auto-match · Group: move device';
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
        const clientKey = c.clientKey || c.id;
        const li = document.createElement('li');
        const name = document.createElement('span');
        name.className = 'mm-thp-name';
        name.textContent = c.friendlyName || clientKey || '(unknown)';
        const meta = document.createElement('span');
        meta.className = 'mm-thp-meta';
        meta.textContent = c.deviceType ? `(${c.deviceType})` : '';

        // Profile override dropdown (PR-4c).
        const profileSel = document.createElement('select');
        profileSel.className = 'mm-thp-profile';
        profileSel.dataset.clientKey = clientKey;
        profileSel.title = 'Scripting profile override';
        const optAuto = document.createElement('option');
        optAuto.value = '';
        optAuto.textContent = '(auto-match)';
        profileSel.appendChild(optAuto);
        for (const pn of profileNames) {
          const opt = document.createElement('option');
          opt.value = pn;
          opt.textContent = pn;
          if ((c.profileName || '') === pn) opt.selected = true;
          profileSel.appendChild(opt);
        }
        profileSel.addEventListener('change', () => {
          const newName = profileSel.value || '';
          store.assignProfileToClient(clientKey, newName).catch(() => {});
        });

        // Group dropdown (PR-14). Populated from store.displayGroups so
        // operators can't accidentally type a missing group (which the
        // server would reject anyway). Changing this fires
        // store.assignDeviceToDisplay; on success the popover closes
        // because the moved device is no longer in the current group.
        const groupSel = document.createElement('select');
        groupSel.className = 'mm-thp-group';
        groupSel.dataset.clientKey = clientKey;
        groupSel.title = 'Move this device to a different display group';
        for (const gid of groupIDs) {
          const opt = document.createElement('option');
          opt.value = gid;
          opt.textContent = gid;
          if (gid === displayID) opt.selected = true;
          groupSel.appendChild(opt);
        }
        groupSel.addEventListener('change', async () => {
          const target = groupSel.value;
          if (!target || target === displayID) return;
          try {
            await store.assignDeviceToDisplay(clientKey, target);
            store.toast(`Moved "${c.friendlyName || clientKey}" to ${target}.`, 'info');
            close();   // device is no longer in this group's popover
          } catch (_) {
            // withRollback already toasted the server error; revert
            // the local select to keep the UI consistent.
            groupSel.value = displayID;
          }
        });

        li.appendChild(name);
        li.appendChild(meta);
        li.appendChild(profileSel);
        li.appendChild(groupSel);
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
