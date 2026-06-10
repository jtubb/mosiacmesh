// js/timeline/modals/playlist-editor.js
/**
 * PR-17: horizontal-ribbon playlist editor.
 *
 * Items render left-to-right in play order, each clip's width
 * proportional to its duration (PX_PER_SECOND, with a MIN_CLIP_PX
 * floor so very short clips stay clickable). The operator can:
 *
 *   - drag the right edge of a clip to resize its duration
 *   - drag the body of a clip to reorder it within the playlist
 *   - drop a media file from the bin onto the ribbon to append
 *   - click a clip to SELECT it; the sidebar form below the ribbon
 *     edits the selected item's playmode, backgroundColor, and
 *     duration override (the same fields the pre-PR-17 form modal
 *     edited; we're just changing the surface)
 *   - click × on the selected clip to remove it
 *   - toggle Loop on the playlist as a whole
 *   - Save: PUT /api/playlists/{name} via store.updatePlaylist
 *           (optimistic + 412-refetch via the existing rollback path)
 *
 * Working state lives in a single `draft` object held by the open
 * modal. Cancel closes without writing; Save dispatches the draft
 * items[] + loop. No incremental writes — operators expect the
 * editor to be a discrete unit-of-change.
 *
 * Called by:
 *   - context-menu Edit playlist items
 *   - drill-in DOUBLE-click on a .mm-drillin-item
 */
import { openModal, closeModal } from './modal-shell.js';
import { getDrag, clearDrag } from '../drag/dragstate.js';
import { ANIMATIONS } from '../animations-catalog.js';

const PX_PER_SECOND = 6;     // 30s -> 180px wide; tweak for readability
const MIN_CLIP_PX = 60;      // every clip clickable even at 1-second duration
const DEFAULT_DURATION_S = 10;  // appended item gets this if media has no video duration

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

function asObject(it) { return (typeof it === 'string') ? { file: it } : { ...it }; }
function basename(p) { return String(p || '').split('/').pop() || ''; }

/**
 * Resolve an item's effective duration for ribbon sizing. Order:
 *   1. item.duration (operator's explicit override)
 *   2. store.media.videoDurations[file] (server-probed length for videos)
 *   3. DEFAULT_DURATION_S (images, or anything we can't probe)
 */
function effectiveDuration(item, store) {
  if (item.duration != null && Number.isFinite(item.duration) && item.duration > 0) return Number(item.duration);
  const probed = store.media?.videoDurations?.[item.file];
  if (probed != null) return probed;
  return DEFAULT_DURATION_S;
}

/**
 * Maximum allowable duration for an item. Videos cap at their probed
 * natural length (extending past would just freeze on the last frame
 * in playback — never the intent). Images have no upper bound — they
 * stay on screen for whatever duration the operator picks.
 *
 * Returns null when there's no cap (image, or video the server
 * couldn't probe — fall back to "no limit" rather than refusing to
 * let the operator set a duration).
 */
function maxDuration(item, store) {
  if (!item || !item.file) return null;
  // We consider an item a "video" iff store probed its duration.
  // Images don't appear in videoDurations even when listed under
  // /api/media's `images`. This double-checks against the videos
  // list in case the durations probe failed but we still know it's
  // a video. PR-18: cap only when probed length is available.
  const probed = store.media?.videoDurations?.[item.file];
  return (probed != null && Number.isFinite(probed) && probed > 0) ? probed : null;
}

export function openPlaylistEditor(store, playlistName, initialIndex = 0) {
  const pl = store.playlists[playlistName];
  if (!pl) return;

  // Working copy. Cancel discards; Save dispatches.
  const draft = {
    items: (pl.items || []).map(asObject),
    loop: !!pl.loop,
  };
  let selectedIdx = (draft.items.length > 0)
    ? Math.min(Math.max(0, initialIndex), draft.items.length - 1)
    : -1;

  // --- DOM scaffolding -------------------------------------------------
  const root = document.createElement('div');
  root.className = 'mm-plr';
  root.innerHTML = `
    <div class="mm-plr-toolbar">
      <label><input type="checkbox" class="mm-plr-loop"> Loop playlist</label>
      <span class="mm-plr-summary" data-field="summary"></span>
      <span style="flex:1"></span>
    </div>
    <div class="mm-plr-ribbon-scroll">
      <ul class="mm-plr-ribbon" data-dropzone="1"></ul>
    </div>
    <div class="mm-plr-picker">
      <div class="mm-plr-picker-header">
        <strong>Add item</strong>
        <input type="search" class="mm-plr-picker-search" placeholder="Search media…" />
      </div>
      <ul class="mm-plr-picker-list" data-field="picker-list"></ul>
    </div>
    <div class="mm-plr-sidebar">
      <div class="mm-plr-sidebar-header">
        <strong data-field="sel-title">No item selected</strong>
        <button type="button" class="btn btn-ghost mm-plr-remove" data-action="remove" disabled>Remove item</button>
      </div>
      <div class="mm-form-grid">
        <label>File <input type="text" data-field="file" disabled></label>
        <label data-field="anim-label" style="display:none">Animation
          <select data-field="file-anim" disabled></select>
        </label>
        <label>Play mode
          <select data-field="playmode" disabled>
            <option value="loop">Loop</option>
            <option value="once">Play once</option>
            <option value="SCRIPT">Animation (SCRIPT)</option>
          </select>
        </label>
        <label>Background color <input type="text" data-field="backgroundColor" placeholder="#000000 or rgb(0,0,0)" disabled></label>
        <label>Duration (s) <input type="number" data-field="duration" min="0.1" step="0.1" placeholder="auto" disabled>
          <span class="mm-plr-duration-hint" data-field="duration-hint"></span>
        </label>
      </div>
    </div>
    <div class="mm-form-actions">
      <button type="button" class="btn btn-ghost" data-action="cancel">Cancel</button>
      <button type="button" class="btn btn-primary" data-action="save">Save</button>
    </div>
  `;

  const ribbon = root.querySelector('.mm-plr-ribbon');
  const summary = root.querySelector('[data-field="summary"]');
  const loopCb = root.querySelector('.mm-plr-loop');
  loopCb.checked = draft.loop;
  loopCb.addEventListener('change', () => { draft.loop = loopCb.checked; });

  const selTitle = root.querySelector('[data-field="sel-title"]');
  const removeBtn = root.querySelector('[data-action="remove"]');
  const fields = {
    file: root.querySelector('[data-field="file"]'),
    fileAnim: root.querySelector('[data-field="file-anim"]'),
    animLabel: root.querySelector('[data-field="anim-label"]'),
    playmode: root.querySelector('[data-field="playmode"]'),
    backgroundColor: root.querySelector('[data-field="backgroundColor"]'),
    duration: root.querySelector('[data-field="duration"]'),
  };

  // Populate the animation <select> once: one option per catalog
  // entry plus a "?" sentinel that drops back to free-text entry (so
  // an operator can type a brand-new animation name not yet in the
  // catalog — forward-compat with index.html entries that landed
  // before the catalog was updated).
  for (const a of ANIMATIONS) {
    const opt = document.createElement('option');
    opt.value = a.key;
    opt.textContent = a.label;
    opt.title = a.description;
    fields.fileAnim.appendChild(opt);
  }
  const otherOpt = document.createElement('option');
  otherOpt.value = '?';
  otherOpt.textContent = 'Other (type a name)…';
  fields.fileAnim.appendChild(otherOpt);
  const ANIM_KEYS = ANIMATIONS.map((a) => a.key);

  // --- Sidebar wiring --------------------------------------------------
  fields.playmode.addEventListener('change', () => {
    if (selectedIdx < 0) return;
    const it = draft.items[selectedIdx];
    it.playmode = fields.playmode.value;
    // Switching INTO SCRIPT with a non-animation file (e.g. a media
    // URL left over from when it was a FULL item): seed the first
    // catalog animation so the item is immediately playable.
    if (it.playmode === 'SCRIPT' && ANIM_KEYS.indexOf(it.file) === -1) {
      it.file = ANIM_KEYS[0];
    }
    syncSidebar();
    renderRibbon();
  });

  // Free-text File input: keep it.file live (covers SCRIPT "Other…"
  // entry AND any future free-text use). Pre-SCRIPT this input had no
  // listener because files arrived via drag/drop; SCRIPT mode needs
  // it editable.
  fields.file.addEventListener('input', () => {
    if (selectedIdx < 0) return;
    draft.items[selectedIdx].file = fields.file.value.trim();
    renderRibbon();
  });

  // Animation <select>: pick a catalog animation, or "?" to reveal the
  // free-text File input for an off-catalog name.
  fields.fileAnim.addEventListener('change', () => {
    if (selectedIdx < 0) return;
    const it = draft.items[selectedIdx];
    if (fields.fileAnim.value === '?') {
      // Reveal the free-text input; leave it.file as-is for editing.
      fields.file.parentElement.style.display = '';
      fields.file.disabled = false;
      fields.file.focus();
    } else {
      it.file = fields.fileAnim.value;
      fields.file.parentElement.style.display = 'none';
      renderRibbon();
    }
  });
  fields.backgroundColor.addEventListener('input', () => {
    if (selectedIdx < 0) return;
    const v = fields.backgroundColor.value.trim();
    if (v) draft.items[selectedIdx].backgroundColor = v;
    else delete draft.items[selectedIdx].backgroundColor;
  });
  fields.duration.addEventListener('input', () => {
    if (selectedIdx < 0) return;
    const v = fields.duration.value.trim();
    if (v) {
      let n = Number(v);
      if (Number.isFinite(n) && n > 0) {
        // PR-18: cap video items to their natural length. Don't snap
        // the input field on every keystroke (annoying to type into);
        // store the clamped value on the draft. captureSelectedFromSidebar
        // + the duration input's `max` attribute keep the final save
        // honest, and the ribbon width reflects the clamped value.
        const cap = maxDuration(draft.items[selectedIdx], store);
        if (cap != null && n > cap) n = cap;
        draft.items[selectedIdx].duration = n;
        renderRibbon();
      }
    } else {
      delete draft.items[selectedIdx].duration;
      renderRibbon();
    }
  });
  // On blur, snap the input value to the clamped duration so the
  // operator can see what was actually stored. Without this, typing
  // "9999" into a 30s video shows "9999" in the field even though
  // the draft has 30.
  fields.duration.addEventListener('blur', () => {
    if (selectedIdx < 0) return;
    const it = draft.items[selectedIdx];
    if (it.duration != null) fields.duration.value = String(it.duration);
  });
  removeBtn.addEventListener('click', () => {
    if (selectedIdx < 0) return;
    draft.items.splice(selectedIdx, 1);
    selectedIdx = Math.min(selectedIdx, draft.items.length - 1);
    renderRibbon();
    syncSidebar();
  });

  const durationHint = root.querySelector('[data-field="duration-hint"]');
  function syncSidebar() {
    const enabled = selectedIdx >= 0;
    removeBtn.disabled = !enabled;
    fields.playmode.disabled = !enabled;
    fields.backgroundColor.disabled = !enabled;
    fields.duration.disabled = !enabled;
    if (!enabled) {
      selTitle.textContent = 'No item selected';
      fields.file.value = '';
      fields.file.parentElement.style.display = '';
      fields.playmode.value = 'loop';
      fields.animLabel.style.display = 'none';
      fields.fileAnim.disabled = true;
      fields.backgroundColor.value = '';
      fields.duration.value = '';
      fields.duration.removeAttribute('max');
      durationHint.textContent = '';
      return;
    }
    const it = draft.items[selectedIdx];
    selTitle.textContent = `Item ${selectedIdx + 1}: ${basename(it.file)}`;
    fields.file.value = it.file || '';
    fields.playmode.value = it.playmode || 'loop';
    // SCRIPT items edit `file` via the animation <select>; everything
    // else uses the free-text File input. Toggle the two controls.
    const isScript = it.playmode === 'SCRIPT';
    fields.animLabel.style.display = isScript ? '' : 'none';
    fields.fileAnim.disabled = !isScript;
    if (isScript) {
      const known = ANIM_KEYS.indexOf(it.file) !== -1;
      fields.fileAnim.value = known ? it.file : '?';
      // Free-text File input visible only for the "Other…" sentinel.
      fields.file.parentElement.style.display = known ? 'none' : '';
      fields.file.disabled = known;
    } else {
      fields.fileAnim.value = ANIM_KEYS[0];
      fields.file.parentElement.style.display = '';
      // Non-SCRIPT (media) items keep the File field read-only: their
      // file arrives via drag/drop or the picker, not free-text. Only
      // the SCRIPT "Other…" path enables typing (handled above + in the
      // fileAnim '?' listener), so a media clip can't silently be
      // retargeted to a non-existent path by a typo.
      fields.file.disabled = true;
    }
    fields.backgroundColor.value = it.backgroundColor || '';
    fields.duration.value = (it.duration == null) ? '' : String(it.duration);
    // PR-18: surface the video's natural length as both the input's
    // `max` (so the spinner respects it) and a hint string under the
    // field. Images have no cap.
    const cap = maxDuration(it, store);
    if (cap != null) {
      fields.duration.max = String(cap);
      durationHint.textContent = `max ${cap}s (video length)`;
    } else {
      fields.duration.removeAttribute('max');
      durationHint.textContent = '';
    }
  }

  // --- Ribbon render + interaction ------------------------------------
  function renderRibbon() {
    ribbon.innerHTML = '';
    let totalDur = 0;
    for (let i = 0; i < draft.items.length; i++) {
      const it = draft.items[i];
      const dur = effectiveDuration(it, store);
      totalDur += dur;
      const li = document.createElement('li');
      li.className = 'mm-plr-clip' + (i === selectedIdx ? ' mm-plr-clip-selected' : '');
      li.style.width = Math.max(MIN_CLIP_PX, Math.round(dur * PX_PER_SECOND)) + 'px';
      li.dataset.index = String(i);
      li.draggable = true;
      const titleEl = document.createElement('div');
      titleEl.className = 'mm-plr-clip-title';
      titleEl.textContent = basename(it.file);
      const durEl = document.createElement('div');
      durEl.className = 'mm-plr-clip-dur';
      durEl.textContent = `${dur.toFixed(dur < 10 ? 1 : 0)}s` + (it.duration != null ? '' : ' (auto)');
      li.appendChild(titleEl);
      li.appendChild(durEl);
      // Right-edge resize handle.
      const handle = document.createElement('div');
      handle.className = 'mm-plr-resize-handle';
      handle.dataset.index = String(i);
      handle.draggable = false;
      li.appendChild(handle);
      ribbon.appendChild(li);
    }
    if (draft.items.length === 0) {
      const empty = document.createElement('li');
      empty.className = 'mm-plr-empty';
      empty.textContent = 'No items yet. Drop media from the bin to add.';
      ribbon.appendChild(empty);
    }
    summary.textContent = `${draft.items.length} item${draft.items.length === 1 ? '' : 's'} · ${totalDur.toFixed(0)}s total`;
  }

  // Click → select.
  ribbon.addEventListener('click', (ev) => {
    const li = ev.target.closest('.mm-plr-clip');
    if (!li) return;
    selectedIdx = Number(li.dataset.index);
    renderRibbon();
    syncSidebar();
  });

  // ---- HTML5 drag: reorder within the ribbon ----
  // Uses a `playlist-item-move` drag kind on dragstate so other
  // listeners (e.g. clip-move) don't react.
  ribbon.addEventListener('dragstart', (ev) => {
    const li = ev.target.closest('.mm-plr-clip');
    if (!li) return;
    const idx = Number(li.dataset.index);
    ev.dataTransfer.effectAllowed = 'move';
    ev.dataTransfer.setData('application/x-mm-playlist-item', String(idx));
    // Store ourselves; don't go through global dragstate to keep
    // editor-scoped state private.
    ribbon._mmDragIdx = idx;
    li.classList.add('mm-plr-clip-dragging');
  });
  ribbon.addEventListener('dragend', (ev) => {
    const li = ev.target.closest('.mm-plr-clip');
    if (li) li.classList.remove('mm-plr-clip-dragging');
    ribbon._mmDragIdx = null;
  });
  ribbon.addEventListener('dragover', (ev) => {
    const fromIdx = ribbon._mmDragIdx;
    const drag = getDrag();
    // Accept either an in-ribbon reorder OR a media-bin drop.
    if (fromIdx == null && !(drag && drag.kind === 'media')) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = (fromIdx == null) ? 'copy' : 'move';
  });
  ribbon.addEventListener('drop', (ev) => {
    const fromIdx = ribbon._mmDragIdx;
    const drag = getDrag();
    // Compute target index from the cursor's X relative to clip midpoints.
    const targetIdx = computeDropIndex(ribbon, ev.clientX);
    if (fromIdx != null) {
      // Reorder.
      ev.preventDefault();
      const moved = draft.items.splice(fromIdx, 1)[0];
      const insertAt = (targetIdx > fromIdx) ? targetIdx - 1 : targetIdx;
      draft.items.splice(insertAt, 0, moved);
      selectedIdx = insertAt;
      ribbon._mmDragIdx = null;
      renderRibbon();
      syncSidebar();
    } else if (drag && drag.kind === 'media') {
      // Append a media item at the drop point.
      ev.preventDefault();
      appendMediaItem(drag.file, targetIdx);
      clearDrag();
      document.body.classList.remove('mm-dragging');
    }
  });

  // ---- Pointer-driven right-edge resize ----
  ribbon.addEventListener('pointerdown', (ev) => {
    const handle = ev.target.closest('.mm-plr-resize-handle');
    if (!handle) return;
    ev.preventDefault();
    ev.stopPropagation();
    const idx = Number(handle.dataset.index);
    const li = handle.closest('.mm-plr-clip');
    const startX = ev.clientX;
    const startWidth = li.getBoundingClientRect().width;
    const startDur = effectiveDuration(draft.items[idx], store);
    try { handle.setPointerCapture(ev.pointerId); } catch (_) { /* fine */ }

    // PR-18: cap drags for video items at the video's natural length.
    const cap = maxDuration(draft.items[idx], store);
    const maxPx = (cap != null) ? Math.max(MIN_CLIP_PX, Math.round(cap * PX_PER_SECOND)) : Infinity;
    function onMove(mv) {
      const dx = mv.clientX - startX;
      let newPx = Math.max(MIN_CLIP_PX, startWidth + dx);
      if (newPx > maxPx) newPx = maxPx;
      let newDur = Math.max(0.5, Math.round((newPx / PX_PER_SECOND) * 2) / 2);   // snap to 0.5s
      if (cap != null && newDur > cap) newDur = cap;
      draft.items[idx].duration = newDur;
      li.style.width = newPx + 'px';
      // Live-update sidebar if this is the selected item.
      if (idx === selectedIdx) fields.duration.value = String(newDur);
    }
    function onUp() {
      document.removeEventListener('pointermove', onMove, true);
      document.removeEventListener('pointerup', onUp, true);
      document.removeEventListener('pointercancel', onUp, true);
      renderRibbon();   // re-render to apply snap + update summary
    }
    document.addEventListener('pointermove', onMove, true);
    document.addEventListener('pointerup', onUp, true);
    document.addEventListener('pointercancel', onUp, true);
  });

  // Belt-and-suspenders: read current sidebar values into the draft
  // at save time. Input events keep the draft live as the operator
  // types, but a programmatic .value set (used by some test specs)
  // doesn't fire input — this capture covers that path AND saves
  // a stray pending-keystroke value if Save is clicked mid-type.
  function captureSelectedFromSidebar() {
    if (selectedIdx < 0) return;
    const it = draft.items[selectedIdx];
    it.playmode = fields.playmode.value || 'loop';
    const bg = fields.backgroundColor.value.trim();
    if (bg) it.backgroundColor = bg; else delete it.backgroundColor;
    const dur = fields.duration.value.trim();
    if (dur) {
      let n = Number(dur);
      if (Number.isFinite(n) && n > 0) {
        // PR-18: enforce the video-length cap at save time too, in
        // case the input event missed it (e.g. value set
        // programmatically + Save clicked before blur).
        const cap = maxDuration(it, store);
        if (cap != null && n > cap) n = cap;
        it.duration = n;
      }
    } else {
      delete it.duration;
    }
  }

  /** PR-18: shared "add this media file as a new playlist item"
   *  helper. Used by the in-modal picker AND the drag-from-bin drop
   *  path. inserts at `at` (default: end). Selects the new item so
   *  the sidebar populates with its fields immediately. */
  function appendMediaItem(file, at) {
    if (!file) return;
    const newItem = { file };
    const probed = store.media?.videoDurations?.[file];
    if (probed != null) newItem.duration = probed;
    const insertAt = (at == null) ? draft.items.length : at;
    draft.items.splice(insertAt, 0, newItem);
    selectedIdx = insertAt;
    renderRibbon();
    syncSidebar();
  }

  // --- Media picker (PR-18) -------------------------------------------
  const pickerList = root.querySelector('[data-field="picker-list"]');
  const pickerSearch = root.querySelector('.mm-plr-picker-search');
  function pickerEntries() {
    const m = store.media || {};
    const images = (m.images || []).map(url => ({ kind: 'image', url }));
    const videos = (m.videos || []).map(url => ({ kind: 'video', url,
      duration: m.videoDurations?.[url] }));
    return [...images, ...videos];
  }
  function renderPicker() {
    pickerList.innerHTML = '';
    const q = (pickerSearch.value || '').trim().toLowerCase();
    const entries = pickerEntries()
      .filter(e => !q || basename(e.url).toLowerCase().includes(q));
    if (entries.length === 0) {
      const li = document.createElement('li');
      li.className = 'mm-plr-picker-empty';
      li.textContent = q ? `No media matches "${q}".` : 'No media available — upload first.';
      pickerList.appendChild(li);
      return;
    }
    for (const e of entries) {
      const li = document.createElement('li');
      li.className = 'mm-plr-picker-row';
      li.dataset.url = e.url;
      const name = document.createElement('span');
      name.className = 'mm-plr-picker-name';
      name.textContent = basename(e.url);
      const meta = document.createElement('span');
      meta.className = 'mm-plr-picker-meta';
      meta.textContent = e.kind === 'video'
        ? (e.duration != null ? `${e.duration}s` : 'video')
        : 'image';
      const addBtn = document.createElement('button');
      addBtn.type = 'button';
      addBtn.className = 'mm-plr-picker-add';
      addBtn.textContent = '+';
      addBtn.title = 'Add to playlist (end)';
      addBtn.addEventListener('click', () => appendMediaItem(e.url));
      li.appendChild(name);
      li.appendChild(meta);
      li.appendChild(addBtn);
      // Double-click row also adds (matches the drag-onto-ribbon path).
      li.addEventListener('dblclick', () => appendMediaItem(e.url));
      pickerList.appendChild(li);
    }
  }
  pickerSearch.addEventListener('input', renderPicker);

  // --- Save / Cancel ---------------------------------------------------
  root.querySelector('[data-action="cancel"]').addEventListener('click', () => closeModal());
  root.querySelector('[data-action="save"]').addEventListener('click', async () => {
    captureSelectedFromSidebar();
    try {
      await store.updatePlaylist(playlistName, { items: draft.items, loop: draft.loop });
      closeModal();
    } catch (_) { /* withRollback toasted */ }
  });

  openModal({ title: `Edit playlist — ${playlistName}`, contentEl: root });
  renderRibbon();
  renderPicker();
  syncSidebar();
}

/**
 * Decide where to insert/move a dropped item: returns the index that
 * a new item should occupy AFTER insertion. Compares cursor X against
 * the midpoint of each rendered clip. Returns items.length if dropped
 * past the last clip.
 */
function computeDropIndex(ribbon, clientX) {
  const clips = Array.from(ribbon.querySelectorAll('.mm-plr-clip'));
  for (let i = 0; i < clips.length; i++) {
    const r = clips[i].getBoundingClientRect();
    if (clientX < r.left + r.width / 2) return i;
  }
  return clips.length;
}
