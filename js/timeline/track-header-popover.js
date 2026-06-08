/**
 * PR-4c gap-fix (spec §362): click a track header → popover with a
 * `profileName` dropdown per client in that group. Calls
 * store.assignProfileToClient (already wired in PR-4c T-C1).
 *
 * PR-14 adds a "Group" dropdown next to the existing "Profile" select,
 * so operators can move individual devices into different display
 * groups without leaving the timeline. Calls store.assignDeviceToDisplay.
 *
 * PR-15 adds bulk select — the per-row Group dropdown got clunky for
 * 20+ devices. Each row now has a checkbox; a footer bar appears
 * when ≥1 row is checked and lets the operator move the whole
 * selection to a target group in one server call (bulk_assign
 * action). The per-row Group dropdown stays for the single-device
 * workflow.
 *
 * (A drag-and-drop variant was prototyped during PR-15 but removed
 * — dragging out of a positioned popover onto a target you can
 * barely see was more cumbersome than the bulk-bar in practice.)
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
    // PR-15: bulk-selection state lives in a closure scoped to the
    // open popover. Rebuilt on every open() — selection doesn't
    // survive close/reopen, which matches the operator's mental model
    // (a popover is a transient panel, not persistent state).
    const selected = new Set();

    pop.innerHTML = '';
    const header = document.createElement('div');
    header.className = 'mm-thp-header';
    const h = document.createElement('strong');
    h.textContent = `Devices in ${displayID}`;
    header.appendChild(h);
    const hint = document.createElement('span');
    hint.className = 'mm-thp-hint';
    hint.textContent = 'Check rows + pick a target below to bulk-move';
    header.appendChild(hint);
    pop.appendChild(header);

    if (clients.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'mm-thp-empty';
      empty.textContent = `No clients currently in '${displayID}'. Devices will inherit the auto-matched profile when they register.`;
      pop.appendChild(empty);
      positionPopover(ev);
      return;
    }

    // PR-15: select-all toolbar above the list. Wires to the
    // bulk-bar's visible/hidden state via updateBulkBar().
    const tools = document.createElement('div');
    tools.className = 'mm-thp-tools';
    const selectAllLabel = document.createElement('label');
    const selectAll = document.createElement('input');
    selectAll.type = 'checkbox';
    selectAll.className = 'mm-thp-select-all';
    selectAllLabel.appendChild(selectAll);
    const sat = document.createElement('span');
    sat.textContent = ' Select all';
    selectAllLabel.appendChild(sat);
    tools.appendChild(selectAllLabel);
    const countSpan = document.createElement('span');
    countSpan.className = 'mm-thp-count';
    countSpan.textContent = `${clients.length} total`;
    tools.appendChild(countSpan);
    pop.appendChild(tools);

    const list = document.createElement('ul');
    list.className = 'mm-thp-list';
    const checkboxes = [];   // populated below; used by select-all
    for (const c of clients) {
      const clientKey = c.clientKey || c.id;
      const li = document.createElement('li');
      li.dataset.clientKey = clientKey;

      // Selection checkbox.
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'mm-thp-row-check';
      cb.dataset.clientKey = clientKey;
      cb.addEventListener('change', () => {
        if (cb.checked) selected.add(clientKey);
        else selected.delete(clientKey);
        selectAll.checked = selected.size === clients.length;
        selectAll.indeterminate = selected.size > 0 && selected.size < clients.length;
        updateBulkBar();
      });
      checkboxes.push(cb);

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

      // Group dropdown (PR-14). Stays for keyboard / single-device use.
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
          close();
        } catch (_) {
          groupSel.value = displayID;
        }
      });

      li.appendChild(cb);
      li.appendChild(name);
      li.appendChild(meta);
      li.appendChild(profileSel);
      li.appendChild(groupSel);
      list.appendChild(li);
    }
    pop.appendChild(list);

    // PR-15: select-all wiring.
    selectAll.addEventListener('change', () => {
      selected.clear();
      for (const cb of checkboxes) {
        cb.checked = selectAll.checked;
        if (selectAll.checked) selected.add(cb.dataset.clientKey);
      }
      selectAll.indeterminate = false;
      updateBulkBar();
    });

    // PR-15: bulk-move footer bar. Hidden until ≥1 row is selected.
    const bar = document.createElement('div');
    bar.className = 'mm-thp-bulkbar';
    bar.style.display = 'none';
    const bulkLabel = document.createElement('span');
    bulkLabel.className = 'mm-thp-bulk-count';
    bar.appendChild(bulkLabel);
    const bulkSel = document.createElement('select');
    bulkSel.className = 'mm-thp-bulk-target';
    bulkSel.title = 'Target display group';
    // First option is a "(pick group)" sentinel so the Apply button
    // can stay disabled until a real choice is made.
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = '(move to…)';
    bulkSel.appendChild(placeholder);
    for (const gid of groupIDs) {
      if (gid === displayID) continue;   // can't bulk-move to same group
      const opt = document.createElement('option');
      opt.value = gid;
      opt.textContent = gid;
      bulkSel.appendChild(opt);
    }
    const applyBtn = document.createElement('button');
    applyBtn.type = 'button';
    applyBtn.className = 'btn btn-primary';
    applyBtn.textContent = 'Apply';
    applyBtn.disabled = true;
    bulkSel.addEventListener('change', () => {
      applyBtn.disabled = !bulkSel.value || selected.size === 0;
    });
    applyBtn.addEventListener('click', async () => {
      const target = bulkSel.value;
      if (!target || selected.size === 0) return;
      applyBtn.disabled = true;
      try {
        const result = await store.bulkAssignDevicesToDisplay([...selected], target);
        const moved = (result?.moved || []).length || selected.size;
        const missing = (result?.missing || []).length;
        store.toast(
          `Moved ${moved} device${moved === 1 ? '' : 's'} to ${target}` +
            (missing ? ` (${missing} not found)` : '.'),
          'info');
        close();
      } catch (_) {
        // withRollback already toasted; re-enable the button.
        applyBtn.disabled = false;
      }
    });
    bar.appendChild(bulkSel);
    bar.appendChild(applyBtn);
    pop.appendChild(bar);

    function updateBulkBar() {
      const n = selected.size;
      if (n === 0) { bar.style.display = 'none'; return; }
      bar.style.display = 'flex';
      bulkLabel.textContent = `${n} selected`;
      applyBtn.disabled = !bulkSel.value;
    }

    positionPopover(ev);
  }

  function positionPopover(ev) {
    const anchor = (ev.target.closest('.mm-track-header') || ev.target).getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    pop.style.display = 'block';
    const pw = pop.offsetWidth || 320, ph = pop.offsetHeight || 100;
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
    if (ev.target.closest('.mm-track-header')) return;
    close();
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && pop.style.display === 'block') close();
  });
}
