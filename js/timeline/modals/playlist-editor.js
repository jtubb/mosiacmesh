// js/timeline/modals/playlist-editor.js
/**
 * Vertical-list playlist editor with an inline content picker.
 *
 * Items render top-to-bottom in play order. The operator can:
 *   - click a row to SELECT it; the settings box below edits the
 *     selected item's duration, backgroundColor, and (media only)
 *     play mode
 *   - drag a row's grip to reorder it within the playlist
 *   - click × on a row to remove it
 *   - "+ Add content" opens an inline picker (NOT a second modal) that
 *     merges media (/api/media) + animations (window.MM_ANIMATIONS) via
 *     buildContentItems; picking an animation maps to a {file,
 *     playmode:'SCRIPT'} item (the trigger fix) via
 *     contentItemToPlaylistItem
 *   - toggle Loop on the playlist as a whole
 *   - Save: PUT /api/playlists/{name} via store.updatePlaylist
 *           (optimistic + 412-refetch via the existing rollback path)
 *
 * Working state lives in a single `draft` object held by the open
 * modal. Cancel closes without writing; Save dispatches draft.items[]
 * + loop.
 *
 * Called by:
 *   - context-menu "Edit playlist items…" -> openPlaylistEditor(store, name)
 *   - Content > Playlists row click -> openPlaylistEditor(store, name)
 *   - drill-in DOUBLE-click on a .mm-drillin-item -> openPlaylistEditor(store, name, idx)
 */
import { openModal, closeModal } from './modal-shell.js';
import { buildContentItems, contentItemToPlaylistItem } from '../content/content-items.js';

const DEFAULT_DURATION_S = 10;
function basename(p) { return String(p || '').split('/').pop() || ''; }
function asObject(it) { return (typeof it === 'string') ? { file: it } : { ...it }; }
function isAnim(it) { return it.playmode === 'SCRIPT'; }
function maxDuration(item, store) {
  const probed = store.media?.videoDurations?.[item.file];
  return (probed != null && Number.isFinite(probed) && probed > 0) ? probed : null;
}

/**
 * Wire the drilled-in (Schedule timeline) entry points. Two listeners:
 *   - Single-click on a .mm-drillin-item -> SELECT (store.selectedSubItem)
 *     so timeline.js highlights it and select.js's Del-key removes it.
 *     Click on the row background (not an item) clears the selection.
 *   - Double-click on a .mm-drillin-item -> OPEN this editor at that index.
 *
 * NOTE: the single-click SELECT handler is load-bearing for the Schedule
 * drill-in view — timeline.js renders `mm-drillin-item-selected` from
 * store.selectedSubItem, and select.js's keyboard Del path reads it. It is
 * preserved here (it has no other home).
 */
export function attachPlaylistEditor(store) {
  // Single-click on a drilled-in item: SELECT (Del shortcut becomes remove).
  document.addEventListener('click', (ev) => {
    const item = ev.target.closest('.mm-drillin-item');
    if (item) {
      const row = item.closest('.mm-drillin-row');
      if (!row) return;
      const playlistName = row.dataset.playlistName;
      const itemIndex = Number(item.dataset.itemIndex || 0);
      store.selectSubItem(playlistName, itemIndex);
      return;
    }
    const row = ev.target.closest('.mm-drillin-row');
    if (row && store.selectedSubItem) {
      store.clearSubItemSelection();
    }
  }, true);

  // Double-click on a drilled-in item OPENS the editor.
  document.addEventListener('dblclick', (ev) => {
    const item = ev.target.closest && ev.target.closest('.mm-drillin-item');
    if (!item) return;
    const row = item.closest('.mm-drillin-row');
    if (!row) return;
    ev.preventDefault();
    ev.stopPropagation();
    openPlaylistEditor(store, row.dataset.playlistName, Number(item.dataset.itemIndex || 0));
  }, true);
}

export function openPlaylistEditor(store, playlistName, initialIndex = 0) {
  const pl = store.playlists[playlistName];
  if (!pl) return;
  const draft = { items: (pl.items || []).map(asObject), loop: !!pl.loop };
  let selectedIdx = draft.items.length ? Math.min(Math.max(0, initialIndex), draft.items.length - 1) : -1;
  let pickerOpen = false;
  let pickerFilter = 'all';

  const root = document.createElement('div');
  root.className = 'mm-ple';
  function render() {
    root.innerHTML = '';
    // header: loop toggle + summary
    const head = document.createElement('div'); head.className = 'mm-ple-head';
    const loopLbl = document.createElement('label');
    loopLbl.innerHTML = '<input type="checkbox"> Loop playlist';
    const loopCb = loopLbl.querySelector('input'); loopCb.checked = draft.loop;
    loopCb.addEventListener('change', () => { draft.loop = loopCb.checked; });
    head.appendChild(loopLbl);
    const sum = document.createElement('span'); sum.className = 'mm-ple-sum';
    sum.textContent = `${draft.items.length} item${draft.items.length === 1 ? '' : 's'}`;
    head.appendChild(sum);
    root.appendChild(head);

    // vertical item list (grip-drag reorder, × remove, click to select)
    const list = document.createElement('ul'); list.className = 'mm-ple-list';
    draft.items.forEach((it, idx) => {
      const li = document.createElement('li');
      li.className = 'mm-ple-row' + (idx === selectedIdx ? ' sel' : '');
      li.draggable = true;
      li.dataset.idx = String(idx);
      const kind = isAnim(it) ? 'animation' : (/\.(mp4|webm|mov)$/i.test(it.file) ? 'video' : 'image');
      li.innerHTML =
        '<span class="mm-ple-grip">⠿</span>' +
        '<span class="mm-ple-ic">' + (kind === 'image' ? '▦' : kind === 'video' ? '▶' : '✦') + '</span>' +
        '<span class="mm-ple-nm"></span><span class="mm-ple-dur"></span>' +
        '<button class="mm-ple-del" title="Remove">×</button>';
      li.querySelector('.mm-ple-nm').textContent = basename(it.file);
      li.querySelector('.mm-ple-dur').textContent = (it.duration != null ? it.duration + 's' : 'auto');
      li.addEventListener('click', (e) => { if (e.target.closest('.mm-ple-del')) return; selectedIdx = idx; render(); });
      li.querySelector('.mm-ple-del').addEventListener('click', () => { draft.items.splice(idx, 1); selectedIdx = Math.min(selectedIdx, draft.items.length - 1); render(); });
      // drag-reorder
      li.addEventListener('dragstart', (e) => { e.dataTransfer.setData('text/plain', String(idx)); });
      li.addEventListener('dragover', (e) => e.preventDefault());
      li.addEventListener('drop', (e) => {
        e.preventDefault();
        const from = Number(e.dataTransfer.getData('text/plain'));
        const to = idx; if (from === to) return;
        const [moved] = draft.items.splice(from, 1); draft.items.splice(to, 0, moved);
        selectedIdx = to; render();
      });
      list.appendChild(li);
    });
    root.appendChild(list);

    // selected-item settings
    if (selectedIdx >= 0) {
      const it = draft.items[selectedIdx];
      const box = document.createElement('div'); box.className = 'mm-ple-settings';
      // duration
      const durWrap = document.createElement('label'); durWrap.textContent = 'Duration (s) ';
      const dur = document.createElement('input'); dur.type = 'number'; dur.min = '0.1'; dur.step = '0.1';
      dur.value = it.duration != null ? String(it.duration) : '';
      const cap = maxDuration(it, store); if (cap != null) dur.max = String(cap);
      dur.addEventListener('input', () => {
        const v = dur.value.trim();
        if (!v) { delete it.duration; render(); return; }
        let n = Number(v); if (!Number.isFinite(n) || n <= 0) return;
        if (cap != null && n > cap) n = cap;
        it.duration = n; render();
      });
      durWrap.appendChild(dur); box.appendChild(durWrap);
      // backgroundColor
      const bgWrap = document.createElement('label'); bgWrap.textContent = 'Background ';
      const bg = document.createElement('input'); bg.type = 'text'; bg.placeholder = '#000000';
      bg.value = it.backgroundColor || '';
      bg.addEventListener('input', () => { const v = bg.value.trim(); if (v) it.backgroundColor = v; else delete it.backgroundColor; });
      bgWrap.appendChild(bg); box.appendChild(bgWrap);
      // play mode — media only (animations are implicitly SCRIPT)
      if (!isAnim(it)) {
        const pmWrap = document.createElement('label'); pmWrap.textContent = 'Play mode ';
        const pm = document.createElement('select');
        pm.innerHTML = '<option value="loop">Loop</option><option value="once">Play once</option>';
        pm.value = (it.playmode === 'once') ? 'once' : 'loop';
        pm.addEventListener('change', () => { it.playmode = pm.value; });
        pmWrap.appendChild(pm); box.appendChild(pmWrap);
      }
      root.appendChild(box);
    }

    // + Add content (inline picker panel — NOT a second modal)
    const addBtn = document.createElement('button');
    addBtn.className = 'btn mm-ple-add';
    addBtn.textContent = pickerOpen ? '✕ Close picker' : '+ Add content';
    addBtn.addEventListener('click', () => { pickerOpen = !pickerOpen; render(); });
    root.appendChild(addBtn);

    if (pickerOpen) {
      const panel = document.createElement('div'); panel.className = 'mm-ple-picker';
      const chips = document.createElement('div'); chips.className = 'mm-ple-picker-filters';
      ['all', 'image', 'video', 'animation'].forEach((f) => {
        const b = document.createElement('button');
        b.textContent = f === 'all' ? 'All' : f[0].toUpperCase() + f.slice(1) + 's';
        if (pickerFilter === f) b.className = 'on';
        b.addEventListener('click', () => { pickerFilter = f; render(); });
        chips.appendChild(b);
      });
      panel.appendChild(chips);
      const grid = document.createElement('div'); grid.className = 'mm-ple-picker-grid';
      const anims = (typeof window !== 'undefined' && window.MM_ANIMATIONS) || [];
      let ci = buildContentItems({ media: store.media, animations: anims });
      if (pickerFilter !== 'all') ci = ci.filter((c) => c.kind === pickerFilter);
      ci.forEach((c) => {
        const t = document.createElement('button'); t.className = 'mm-ple-picktile kind-' + c.kind;
        t.innerHTML = '<span>' + (c.kind === 'image' ? '▦' : c.kind === 'video' ? '▶' : '✦') + '</span>';
        const nm = document.createElement('span'); nm.textContent = c.name; t.appendChild(nm);
        t.addEventListener('click', () => {
          const item = contentItemToPlaylistItem(c);
          if (item.duration == null) delete item.duration;
          draft.items.push(item);
          selectedIdx = draft.items.length - 1;
          render();
        });
        grid.appendChild(t);
      });
      panel.appendChild(grid);
      root.appendChild(panel);
    }

    // actions
    const actions = document.createElement('div'); actions.className = 'mm-form-actions';
    const cancel = document.createElement('button'); cancel.className = 'btn btn-ghost'; cancel.textContent = 'Cancel';
    cancel.addEventListener('click', () => closeModal());
    const save = document.createElement('button'); save.className = 'btn btn-primary'; save.textContent = 'Save';
    save.addEventListener('click', () => {
      store.updatePlaylist(playlistName, { items: draft.items, loop: draft.loop })
        .then(() => closeModal()).catch(() => {/* store toasts 412 */});
    });
    actions.appendChild(cancel); actions.appendChild(save);
    root.appendChild(actions);
  }

  render();
  openModal({ title: `Edit playlist: ${playlistName}`, contentEl: root });
}
