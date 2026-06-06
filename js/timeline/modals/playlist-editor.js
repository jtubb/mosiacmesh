// js/timeline/modals/playlist-editor.js
/**
 * Per-item editor for a playlist item. Currently we let operators
 * tweak playmode (loop/once), backgroundColor (hex/CSS), and an
 * optional duration override that wins over the file's own video
 * length.
 *
 * The modal edits a single item — not the whole playlist — but Save
 * issues a single PUT /api/playlists/{name} with the full items array
 * (the server has no per-item endpoint). withRollback handles the
 * optimistic + 412-refetch dance.
 *
 * Called by:
 *   - context-menu Edit playlist items (opens at the FIRST item; user
 *     can switch via the dropdown inside the modal)
 *   - drill-in DOUBLE-click on a .mm-drillin-item
 *
 * Single-click on a sub-item SELECTS it (sets store.selectedSubItem)
 * so the Del key can remove it. The selection<->open distinction
 * mirrors the main-grid pattern (single-click selects a clip; double-
 * click drills in).
 */
import { openModal, closeModal } from './modal-shell.js';

export function attachPlaylistEditor(store) {
  // Single-click on a drilled-in item: SELECT (Del shortcut becomes
  // remove). Click on empty drill-in row area clears selection.
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
    // Click on the drill-in row but NOT on an item: clear selection.
    const row = ev.target.closest('.mm-drillin-row');
    if (row && store.selectedSubItem) {
      store.clearSubItemSelection();
    }
  }, true);

  // Double-click on a drilled-in item OPENS the editor.
  document.addEventListener('dblclick', (ev) => {
    const item = ev.target.closest('.mm-drillin-item');
    if (!item) return;
    const row = item.closest('.mm-drillin-row');
    if (!row) return;
    ev.preventDefault();
    ev.stopPropagation();
    const playlistName = row.dataset.playlistName;
    const itemIndex = Number(item.dataset.itemIndex || 0);
    openPlaylistEditor(store, playlistName, itemIndex);
  }, true);
}

export function openPlaylistEditor(store, playlistName, initialIndex = 0) {
  const pl = store.playlists[playlistName];
  if (!pl) return;
  const items = (pl.items || []).slice();   // shallow draft; modal mutates copies
  if (items.length === 0) {
    store.toast(`Playlist "${playlistName}" has no items to edit.`, 'info');
    return;
  }
  let idx = Math.min(Math.max(0, initialIndex), items.length - 1);

  const root = document.createElement('div');
  root.innerHTML = `
    <label>Item
      <select data-field="itemPicker"></select>
    </label>
    <div class="mm-form-grid">
      <label>File <input type="text" data-field="file" disabled></label>
      <label>Play mode
        <select data-field="playmode">
          <option value="loop">Loop</option>
          <option value="once">Play once</option>
        </select>
      </label>
      <label>Background color <input type="text" data-field="backgroundColor" placeholder="#000000 or rgb(0,0,0)"></label>
      <label>Duration override (s) <input type="number" data-field="duration" min="0" step="0.1" placeholder="auto"></label>
    </div>
    <div class="mm-form-actions">
      <button type="button" class="btn btn-ghost" data-action="cancel">Cancel</button>
      <button type="button" class="btn btn-primary" data-action="save">Save</button>
    </div>
  `;
  const picker = root.querySelector('[data-field="itemPicker"]');
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    const file = (typeof it === 'string') ? it : (it.file || '');
    const opt = document.createElement('option');
    opt.value = String(i);
    opt.textContent = `${i + 1}. ${basename(file)}`;
    picker.appendChild(opt);
  }
  picker.value = String(idx);

  function asObject(it) { return (typeof it === 'string') ? { file: it } : { ...it }; }

  function loadItem() {
    const it = asObject(items[idx]);
    root.querySelector('[data-field="file"]').value = it.file || '';
    root.querySelector('[data-field="playmode"]').value = it.playmode || 'loop';
    root.querySelector('[data-field="backgroundColor"]').value = it.backgroundColor || '';
    root.querySelector('[data-field="duration"]').value = (it.duration == null) ? '' : String(it.duration);
  }

  function captureItem() {
    const draft = asObject(items[idx]);
    draft.playmode = root.querySelector('[data-field="playmode"]').value;
    const bg = root.querySelector('[data-field="backgroundColor"]').value.trim();
    if (bg) draft.backgroundColor = bg; else delete draft.backgroundColor;
    const dur = root.querySelector('[data-field="duration"]').value.trim();
    if (dur) draft.duration = Number(dur); else delete draft.duration;
    items[idx] = draft;
  }

  picker.addEventListener('change', () => { captureItem(); idx = Number(picker.value); loadItem(); });

  openModal({ title: `Edit items — ${playlistName}`, contentEl: root });

  root.querySelector('[data-action="cancel"]').addEventListener('click', () => closeModal());
  root.querySelector('[data-action="save"]').addEventListener('click', async () => {
    captureItem();
    try {
      await store.updatePlaylist(playlistName, { items });
      closeModal();
    } catch (_) { /* toast via withRollback */ }
  });
  loadItem();
}

function basename(p) { return String(p || '').split('/').pop() || ''; }
