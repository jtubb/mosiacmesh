// js/timeline/modals/profile-editor.js
/**
 * 3-pane profile editor modal.
 *   Left:   profile list (scroll + New + Delete buttons)
 *   Center: profile form (name, label, matchDeviceType, 5 script
 *           textareas, launch config, webclip, ssh)
 *   Right:  preview — currently-edited script rendered through
 *           SafeDict against a selected sample client; unresolved
 *           tokens highlighted red.
 *
 * Selecting a profile loads it into the form. Editing the form mutates
 * a local DRAFT; Save commits via store.updateProfile (or createProfile
 * if it's a brand-new entry). Switching profiles before saving prompts.
 */
import { openModal, closeModal } from './modal-shell.js';

let activeUi = null;

export function openProfileEditor(store) {
  if (activeUi) { closeModal(); activeUi = null; }
  const root = document.createElement('div');
  root.className = 'mm-profile-editor';
  root.innerHTML = `
    <div class="mm-pe-list">
      <div class="mm-pe-list-actions">
        <button type="button" class="btn btn-ghost" data-action="new">+ New</button>
        <button type="button" class="btn btn-ghost mm-pe-danger" data-action="delete" disabled>Delete</button>
      </div>
      <ul class="mm-pe-profiles"></ul>
    </div>
    <div class="mm-pe-form">
      <div class="mm-pe-empty">Select a profile (or create one) to edit.</div>
    </div>
    <div class="mm-pe-preview">
      <div class="mm-pe-preview-header">
        <label>Preview against
          <select data-field="sampleClient"></select>
        </label>
        <label>Script
          <select data-field="sampleScript">
            <option value="login">login</option><option value="start">start</option>
            <option value="stop">stop</option><option value="test">test</option>
            <option value="reboot">reboot</option>
          </select>
        </label>
      </div>
      <pre class="mm-pe-preview-body" data-field="previewBody"></pre>
    </div>
  `;
  openModal({ title: 'Profiles', contentEl: root });

  activeUi = { store, root, draft: null, draftKind: null /* 'edit' | 'new' */ };
  renderProfileList(activeUi);
  populateSampleSelectors(activeUi);
  wireShellHandlers(activeUi);
}

function renderProfileList(ui) {
  const list = ui.root.querySelector('.mm-pe-profiles');
  list.innerHTML = '';
  const names = Object.keys(ui.store.profiles || {}).sort();
  for (const n of names) {
    const li = document.createElement('li');
    li.textContent = ui.store.profiles[n].label || n;
    li.dataset.name = n;
    li.addEventListener('click', () => selectProfile(ui, n));
    if (ui.draft && ui.draftKind === 'edit' && ui.draft.name === n) li.classList.add('selected');
    list.appendChild(li);
  }
}

function populateSampleSelectors(ui) {
  const clientSel = ui.root.querySelector('[data-field="sampleClient"]');
  for (const c of ui.store.displays) {
    const opt = document.createElement('option');
    opt.value = c.clientKey || c.id;
    opt.textContent = c.friendlyName || c.clientKey || c.id;
    clientSel.appendChild(opt);
  }
}

function wireShellHandlers(ui) {
  ui.root.querySelector('[data-action="new"]').addEventListener('click', () => {
    ui.draftKind = 'new';
    ui.draft = { name: '', label: '', matchDeviceType: 'Tablet',
                 scripts: { login: '', start: '', stop: '', test: '', reboot: '' },
                 launch: { method: 'ssh-then-vnc', taps: [] },
                 webclip: { bundleId: '', title: '' },
                 ssh: { legacyCrypto: true, user: 'root', keyPath: '~/.ssh/mosaic_ipad' } };
    renderForm(ui);
    refreshPreview(ui);
  });
  ui.root.querySelector('[data-action="delete"]').addEventListener('click', async () => {
    if (!ui.draft || ui.draftKind !== 'edit') return;
    const name = ui.draft.name;
    if (!confirm(`Delete profile "${name}"?`)) return;
    try {
      await ui.store.deleteProfile(name);
      ui.draft = null; ui.draftKind = null;
      renderProfileList(ui);
      ui.root.querySelector('.mm-pe-form').innerHTML = '<div class="mm-pe-empty">Select a profile to edit.</div>';
      refreshPreview(ui);
    } catch (e) { /* toast via withRollback; if 409 with refs, server's error string surfaces */ }
  });
  // T-C4 wires the form's own change handlers; T-C5 wires preview onChange.
}

function selectProfile(ui, name) {
  const src = ui.store.profiles[name];
  if (!src) return;
  ui.draftKind = 'edit';
  ui.draft = JSON.parse(JSON.stringify(src));   // deep clone so edits don't leak
  renderProfileList(ui);
  renderForm(ui);
  refreshPreview(ui);
  ui.root.querySelector('[data-action="delete"]').disabled = false;
}

// Stubs that T-C4 + T-C5 will replace with real implementations.
function renderForm(ui) {
  const formHost = ui.root.querySelector('.mm-pe-form');
  const stub = document.createElement('div');
  stub.className = 'mm-pe-empty';
  stub.textContent = 'Form coming in T-C4 — editing ' + (ui.draft ? (ui.draft.name || '(new)') : 'nothing');
  formHost.innerHTML = '';
  formHost.appendChild(stub);
}
function refreshPreview(ui) {
  const out = ui.root.querySelector('[data-field="previewBody"]');
  out.textContent = ui.draft ? '(preview comes in T-C5)' : '';
}
