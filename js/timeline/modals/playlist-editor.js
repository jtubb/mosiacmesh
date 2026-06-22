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
import { buildContentItems, contentItemToPlaylistItem, mediaItemsMissingPlayType } from '../content/content-items.js';

function basename(p) { return String(p || '').split('/').pop() || ''; }
function asObject(it) { return (typeof it === 'string') ? { file: it } : { ...it }; }
function isAnim(it) { return it.playmode === 'SCRIPT'; }
function isVideo(file) { return /\.(mp4|m4v|mov|webm|ogv)$/i.test(file || ''); }

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

/**
 * Build one effect control group (Start or End) and append it to `container`.
 *
 * @param {object} opts
 *   label        - display label ("Start effect" / "End effect")
 *   getEffect    - () => it.startEffect or it.endEffect (null/undefined = none)
 *   setEffect    - (val) => assigns or deletes the field on `it`
 *   catalog      - store.effectCatalog array
 *   it           - the current draft item (for isVideo check on audioFade)
 */
function buildEffectControl({ label, getEffect, setEffect, catalog, it }) {
  const wrap = document.createElement('div');
  wrap.className = 'mm-ple-effect-group';

  const lbl = document.createElement('label');
  lbl.textContent = label + ' ';

  const sel = document.createElement('select');
  // "None" option
  const noneOpt = document.createElement('option');
  noneOpt.value = ''; noneOpt.textContent = 'None';
  sel.appendChild(noneOpt);
  for (const ef of catalog) {
    const o = document.createElement('option');
    o.value = ef.name; o.textContent = ef.label;
    sel.appendChild(o);
  }
  const current = getEffect();
  sel.value = current ? (current.name || '') : '';

  lbl.appendChild(sel);
  wrap.appendChild(lbl);

  // Params container — rebuilt when the select changes.
  const paramsWrap = document.createElement('div');
  paramsWrap.className = 'mm-ple-effect-params';
  wrap.appendChild(paramsWrap);

  function renderParams() {
    paramsWrap.innerHTML = '';
    const chosenName = sel.value;
    if (!chosenName) return;
    const efDef = catalog.find((e) => e.name === chosenName);
    if (!efDef) return;
    const existingEffect = getEffect();
    const existingParams = (existingEffect && existingEffect.name === chosenName)
      ? (existingEffect.params || {})
      : {};

    // Collect live param values for writeback. Rebuilt each renderParams() call.
    const live = {};

    // Seed live with all declared params (including those skipped below) using
    // current or default so the effect object is always complete.
    function commitEffect() {
      const params = {};
      for (const p of (efDef.params || [])) {
        if (p.key in live) {
          params[p.key] = live[p.key];
        } else {
          const val = (p.key in existingParams) ? existingParams[p.key] : p.default;
          if (p.type === 'number') params[p.key] = Number(val);
          else if (p.type === 'boolean') params[p.key] = !!(val);
          else params[p.key] = val;
        }
      }
      setEffect({ name: chosenName, params });
    }

    for (const p of (efDef.params || [])) {
      const existingVal = (p.key in existingParams) ? existingParams[p.key] : p.default;

      // audioFade (boolean) is video-only — skip for non-video items.
      if (p.type === 'boolean' && p.key === 'audioFade' && !isVideo(it.file)) continue;

      const pWrap = document.createElement('label');
      pWrap.className = 'mm-ple-effect-param';

      if (p.type === 'number') {
        pWrap.textContent = p.key + ' ';
        const inp = document.createElement('input');
        inp.type = 'number';
        if (p.min != null) inp.min = String(p.min);
        if (p.max != null) inp.max = String(p.max);
        inp.value = String(existingVal != null ? existingVal : p.default);
        live[p.key] = Number(inp.value);
        inp.addEventListener('input', () => {
          live[p.key] = Number(inp.value);
          commitEffect();
        });
        pWrap.appendChild(inp);
      } else if (p.type === 'choice') {
        pWrap.textContent = p.key + ' ';
        const csel = document.createElement('select');
        for (const ch of (p.choices || [])) {
          const co = document.createElement('option');
          co.value = ch; co.textContent = ch;
          if (ch === (existingVal != null ? existingVal : p.default)) co.selected = true;
          csel.appendChild(co);
        }
        live[p.key] = csel.value;
        csel.addEventListener('change', () => {
          live[p.key] = csel.value;
          commitEffect();
        });
        pWrap.appendChild(csel);
      } else if (p.type === 'boolean') {
        const cb = document.createElement('input'); cb.type = 'checkbox';
        cb.checked = !!(existingVal != null ? existingVal : p.default);
        live[p.key] = cb.checked;
        cb.addEventListener('change', () => {
          live[p.key] = cb.checked;
          commitEffect();
        });
        pWrap.appendChild(cb);
        pWrap.appendChild(document.createTextNode(' ' + p.key));
      }

      paramsWrap.appendChild(pWrap);
    }

    // Initial commit so the effect object reflects the current params state
    // even if the operator never touches a param.
    commitEffect();
  }

  sel.addEventListener('change', () => {
    if (!sel.value) {
      setEffect(null);
      paramsWrap.innerHTML = '';
    } else {
      renderParams();
    }
  });

  // Render params for the initially-selected effect (if any).
  renderParams();

  return wrap;
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

  // Update the selected row's duration label in place — WITHOUT a full
  // render(). Typing in the duration field must not rebuild the DOM (that
  // destroys the <input> and drops focus after each keystroke).
  function updateRowMeta() {
    const it = draft.items[selectedIdx];
    const el = root.querySelector('.mm-ple-row[data-idx="' + selectedIdx + '"] .mm-ple-dur');
    if (el) el.textContent = (it && it.duration != null ? it.duration + 's' : 'auto');
  }

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
      if (!isAnim(it) && !['SEGMENT', 'FULL', 'INDIVIDUAL'].includes(it.playmode)) {
        const warn = document.createElement('span');
        warn.className = 'mm-ple-warn'; warn.textContent = ' ⚠'; warn.title = 'pick a play type';
        li.appendChild(warn);
      }
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

    // selected-item settings: just Duration (blank = Auto) + Background.
    // Auto = the content's natural length (video) / 20s (image, animation),
    // resolved server-side. There is no per-item play-mode any more.
    if (selectedIdx >= 0) {
      const it = draft.items[selectedIdx];
      const box = document.createElement('div'); box.className = 'mm-ple-settings';

      // Play type — media only (animations are implicitly SCRIPT). Mesh=SEGMENT,
      // Mirror=FULL, Per-screen=INDIVIDUAL. No silent default: an unchosen media
      // item blocks Save (mediaItemsMissingPlayType) and shows a ⚠ on its row.
      if (!isAnim(it)) {
        const ptWrap = document.createElement('label'); ptWrap.textContent = 'Play type ';
        const pt = document.createElement('select');
        const opts = [['', '— pick play type —'], ['SEGMENT', 'Mesh (across the wall)'],
                      ['FULL', 'Mirror (same on every screen)'], ['INDIVIDUAL', 'Per-screen (warped to calibration)']];
        for (const [val, label] of opts) {
          const o = document.createElement('option');
          o.value = val; o.textContent = label;
          if (val === '') o.disabled = true;
          if ((it.playmode || '') === val) o.selected = true;
          pt.appendChild(o);
        }
        if (!['SEGMENT', 'FULL', 'INDIVIDUAL'].includes(it.playmode)) pt.value = '';
        pt.addEventListener('change', () => {
          if (pt.value) it.playmode = pt.value; else delete it.playmode;
          render();
        });
        ptWrap.appendChild(pt); box.appendChild(ptWrap);
      } else {
        // Animation (SCRIPT): mirror (every screen) vs mesh (span the wall).
        const wmWrap = document.createElement('label'); wmWrap.textContent = 'Wall mode ';
        const wm = document.createElement('select');
        const wopts = [['mirror', 'Mirror (same on every screen)'],
                       ['mesh', 'Mesh (across the wall)']];
        for (const [val, label] of wopts) {
          const o = document.createElement('option');
          o.value = val; o.textContent = label;
          if ((it.scriptSpan || 'mirror') === val) o.selected = true;
          wm.appendChild(o);
        }
        wm.addEventListener('change', () => {
          if (wm.value === 'mesh') it.scriptSpan = 'mesh'; else delete it.scriptSpan;
          render();
        });
        wmWrap.appendChild(wm); box.appendChild(wmWrap);
        const wmHint = document.createElement('span'); wmHint.className = 'mm-ple-hint';
        wmHint.textContent = 'Mesh needs a calibrated group; uncalibrated screens go black';
        wmWrap.appendChild(wmHint);
      }

      // Duration — blank means Auto (natural length / default).
      const durWrap = document.createElement('label'); durWrap.textContent = 'Duration (s) ';
      const dur = document.createElement('input');
      dur.type = 'number'; dur.min = '0.1'; dur.step = '0.1';
      dur.placeholder = 'Auto';
      dur.value = it.duration != null ? String(it.duration) : '';
      dur.addEventListener('input', () => {
        // NO render() here — rebuilding mid-typing drops focus.
        const v = dur.value.trim();
        if (!v) { delete it.duration; updateRowMeta(); return; }
        const n = Number(v);
        if (!Number.isFinite(n) || n <= 0) return;
        it.duration = n; updateRowMeta();
      });
      durWrap.appendChild(dur); box.appendChild(durWrap);
      const hint = document.createElement('span'); hint.className = 'mm-ple-hint';
      hint.textContent = 'blank = Auto (full length)';
      durWrap.appendChild(hint);

      // Background color
      const bgWrap = document.createElement('label'); bgWrap.textContent = 'Background ';
      const bg = document.createElement('input'); bg.type = 'text'; bg.placeholder = '#000000';
      bg.value = it.backgroundColor || '';
      bg.addEventListener('input', () => { const v = bg.value.trim(); if (v) it.backgroundColor = v; else delete it.backgroundColor; });
      bgWrap.appendChild(bg); box.appendChild(bgWrap);

      // Start effect — driven by store.effectCatalog (Task 6).
      const catalog = store.effectCatalog || [];
      box.appendChild(buildEffectControl({
        label: 'Start effect',
        getEffect: () => it.startEffect || null,
        setEffect: (val) => { if (val) it.startEffect = val; else delete it.startEffect; },
        catalog,
        it,
      }));

      // End effect
      box.appendChild(buildEffectControl({
        label: 'End effect',
        getEffect: () => it.endEffect || null,
        setEffect: (val) => { if (val) it.endEffect = val; else delete it.endEffect; },
        catalog,
        it,
      }));

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
        if (c.kind === 'image') {
          const im = document.createElement('img'); im.src = c.ref; im.loading = 'lazy'; im.alt = ''; t.appendChild(im);
        } else if (c.kind === 'video') {
          const v = document.createElement('video'); v.src = c.ref; v.muted = true; v.preload = 'metadata'; t.appendChild(v);
        } else {
          const ic = document.createElement('span'); ic.textContent = '✦'; t.appendChild(ic);
        }
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
      const _missing = mediaItemsMissingPlayType(draft.items);
      if (_missing.length > 0) {
        store.toast('Pick a play type for ' + _missing.length + ' item(s) before saving');
        return;
      }
      store.updatePlaylist(playlistName, { items: draft.items, loop: draft.loop })
        .then(() => closeModal()).catch(() => {/* store toasts 412 */});
    });
    // Disable Save when any media item lacks a play type.
    const _missing = mediaItemsMissingPlayType(draft.items);
    save.disabled = _missing.length > 0;
    save.title = _missing.length ? ('Pick a play type for ' + _missing.length + ' item(s)') : '';
    actions.appendChild(cancel); actions.appendChild(save);
    root.appendChild(actions);
  }

  render();
  openModal({ title: `Edit playlist: ${playlistName}`, contentEl: root });
}
