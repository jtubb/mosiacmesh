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
  wirePreviewHandlers(activeUi);
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

// T-C4: real form implementation. T-C5 replaces refreshPreview stub.
function renderForm(ui) {
  const formHost = ui.root.querySelector('.mm-pe-form');
  if (!ui.draft) { formHost.innerHTML = ''; const e = document.createElement('div'); e.className = 'mm-pe-empty'; e.textContent = 'Select a profile to edit.'; formHost.appendChild(e); return; }
  const d = ui.draft;
  formHost.innerHTML = `
    <div class="mm-form-grid">
      <label>Name <input type="text" data-field="name" value="${escapeAttr(d.name)}" ${ui.draftKind === 'edit' ? 'disabled' : ''}></label>
      <label>Label <input type="text" data-field="label" value="${escapeAttr(d.label || '')}"></label>
      <label>Match device type
        <select data-field="matchDeviceType">
          ${['Tablet','Mobile','Desktop','Default'].map(function(t) {
            return '<option value="' + t + '"' + ((d.matchDeviceType||'Tablet')===t?' selected':'') + '>' + t + '</option>';
          }).join('')}
        </select>
      </label>
      <label>Launch method
        <select data-field="launchMethod">
          ${['shell','vnc-tap','ssh-then-vnc'].map(function(m) {
            return '<option value="' + m + '"' + ((d.launch && d.launch.method ? d.launch.method : 'ssh-then-vnc')===m?' selected':'') + '>' + m + '</option>';
          }).join('')}
        </select>
      </label>
    </div>
    <details open><summary>Scripts</summary>
      ${['login','start','stop','test','reboot'].map(function(k) {
        return '<label class="mm-pe-script-row"><span>' + k + '</span><textarea data-field="script-' + k + '" rows="3">' + escapeText((d.scripts && d.scripts[k]) ? d.scripts[k] : '') + '</textarea></label>';
      }).join('')}
    </details>
    <details><summary>Launch config</summary>
      <div class="mm-form-grid">
        <label>VNC password <input type="text" data-field="vncPassword" value="${escapeAttr((d.launch && d.launch.vncPassword) ? d.launch.vncPassword : '')}"></label>
        <label>Wake script <input type="text" data-field="wakeScript" value="${escapeAttr((d.launch && d.launch.wakeScript) ? d.launch.wakeScript : '')}"></label>
        <label class="mm-form-row-wide" data-field="tapsRow">Taps (one fbX,fbY per line)
          <textarea data-field="taps" rows="2">${escapeText((d.launch && d.launch.taps) ? d.launch.taps.map(function(t) { return t.fbX + ',' + t.fbY; }).join('\n') : '')}</textarea>
        </label>
      </div>
    </details>
    <details><summary>Webclip</summary>
      <div class="mm-form-grid">
        <label>Bundle ID <input type="text" data-field="webclipBundleId" value="${escapeAttr((d.webclip && d.webclip.bundleId) ? d.webclip.bundleId : '')}"></label>
        <label>Title <input type="text" data-field="webclipTitle" value="${escapeAttr((d.webclip && d.webclip.title) ? d.webclip.title : '')}"></label>
      </div>
    </details>
    <details><summary>SSH</summary>
      <div class="mm-form-grid">
        <label>User <input type="text" data-field="sshUser" value="${escapeAttr((d.ssh && d.ssh.user) ? d.ssh.user : 'root')}"></label>
        <label>Key path <input type="text" data-field="sshKeyPath" value="${escapeAttr((d.ssh && d.ssh.keyPath) ? d.ssh.keyPath : '')}"></label>
        <label><input type="checkbox" data-field="sshLegacyCrypto"${(d.ssh && d.ssh.legacyCrypto) ? ' checked' : ''}> Legacy crypto (iOS 5)</label>
      </div>
    </details>
    <div class="mm-form-actions">
      <button type="button" class="btn btn-ghost" data-action="cancel-form">Discard changes</button>
      <button type="button" class="btn btn-primary" data-action="save-form">Save</button>
    </div>
  `;
  function updateLaunchVisibility() {
    var m = formHost.querySelector('[data-field="launchMethod"]').value;
    formHost.querySelector('[data-field="tapsRow"]').style.display = (m === 'shell') ? 'none' : '';
  }
  updateLaunchVisibility();
  formHost.addEventListener('input', function() { captureForm(ui); refreshPreview(ui); });
  formHost.addEventListener('change', function() { captureForm(ui); refreshPreview(ui); updateLaunchVisibility(); });
  formHost.querySelector('[data-action="cancel-form"]').addEventListener('click', function() {
    if (ui.draftKind === 'edit') { selectProfile(ui, ui.draft.name); }
    else { ui.draft = null; ui.draftKind = null; renderProfileList(ui); renderForm(ui); refreshPreview(ui); }
  });
  formHost.querySelector('[data-action="save-form"]').addEventListener('click', async function() {
    captureForm(ui);
    if (!ui.draft.name.trim()) { ui.store.toast('Name is required.', 'error'); return; }
    try {
      if (ui.draftKind === 'new') {
        await ui.store.createProfile(ui.draft);
        ui.draftKind = 'edit';
      } else {
        await ui.store.updateProfile(ui.draft.name, ui.draft);
      }
      renderProfileList(ui);
    } catch (_) { /* toast via withRollback */ }
  });
}

function captureForm(ui) {
  if (!ui.draft) return;
  var d = ui.draft;
  function f(sel) { return ui.root.querySelector(sel); }
  if (ui.draftKind === 'new') d.name = f('[data-field="name"]').value.trim();
  d.label = f('[data-field="label"]').value;
  d.matchDeviceType = f('[data-field="matchDeviceType"]').value;
  d.scripts = d.scripts || {};
  ['login','start','stop','test','reboot'].forEach(function(k) {
    d.scripts[k] = f('[data-field="script-' + k + '"]').value;
  });
  d.launch = d.launch || {};
  d.launch.method = f('[data-field="launchMethod"]').value;
  d.launch.vncPassword = f('[data-field="vncPassword"]').value || undefined;
  d.launch.wakeScript = f('[data-field="wakeScript"]').value || undefined;
  d.launch.taps = f('[data-field="taps"]').value.split('\n')
    .map(function(s) { return s.trim(); }).filter(Boolean)
    .map(function(s) {
      var parts = s.split(',');
      var x = Number(parts[0] ? parts[0].trim() : NaN);
      var y = Number(parts[1] ? parts[1].trim() : NaN);
      return { fbX: x, fbY: y };
    })
    .filter(function(t) { return isFinite(t.fbX) && isFinite(t.fbY); });
  d.webclip = d.webclip || {};
  d.webclip.bundleId = f('[data-field="webclipBundleId"]').value || undefined;
  d.webclip.title = f('[data-field="webclipTitle"]').value || undefined;
  d.ssh = d.ssh || {};
  d.ssh.user = f('[data-field="sshUser"]').value || 'root';
  d.ssh.keyPath = f('[data-field="sshKeyPath"]').value || undefined;
  d.ssh.legacyCrypto = !!f('[data-field="sshLegacyCrypto"]').checked;
}

function refreshPreview(ui) {
  const out = ui.root.querySelector('[data-field="previewBody"]');
  if (!ui.draft) { out.textContent = ''; return; }
  const clientKey = ui.root.querySelector('[data-field="sampleClient"]').value;
  const scriptKey = ui.root.querySelector('[data-field="sampleScript"]').value;
  const client = ui.store.displays.find(function(d) { return (d.clientKey || d.id) === clientKey; });
  const template = (ui.draft.scripts && ui.draft.scripts[scriptKey]) || '';
  const vars = buildPreviewVars(client, ui.draft);
  // Render the template with vars; mark unresolved {tokens} in red.
  const html = template.replace(/\{([a-zA-Z_]\w*)\}/g, function(full, key) {
    if (key in vars) return escapeText(String(vars[key]));
    return '<span class="mm-pe-unresolved">' + escapeText(full) + '</span>';
  }).replace(/\n/g, '<br>');
  // Switch from textContent to innerHTML because we hand-build the
  // highlighted spans. escapeText is applied to user-supplied values
  // above so this is safe.
  out.innerHTML = html;
}

function buildPreviewVars(client, draft) {
  // Mirror of mosaicmesh.template_vars.SafeDict + build_vars (the
  // server-side substitution). Keep the keys in sync — see
  // mosaicmesh/template_vars.py for the canonical list.
  const v = {
    clientID:        '',
    ip:              '',
    friendlyName:    '',
    displayId:       '',
    cacheMode:       '',
    webclipBundleId: '',
    webclipTitle:    '',
    vncPassword:     '',
    fbX:             '',
    fbY:             '',
    displayUrl:      window.location.origin + '/',
  };
  if (client) {
    v.clientID     = client.clientID  || client.clientKey || client.id || '';
    v.ip           = client.ip        || client.address   || '';
    v.friendlyName = client.friendlyName || '';
    v.displayId    = client.displayID || client.displayId || '';
    v.cacheMode    = client.cacheMode || '';
    v.displayUrl   = client.displayUrl || (window.location.origin + '/');
  }
  if (draft.webclip) {
    if (draft.webclip.bundleId) v.webclipBundleId = draft.webclip.bundleId;
    if (draft.webclip.title)    v.webclipTitle    = draft.webclip.title;
  }
  if (draft.launch) {
    if (draft.launch.vncPassword) v.vncPassword = draft.launch.vncPassword;
  }
  return v;
}

function wirePreviewHandlers(ui) {
  ui.root.querySelector('[data-field="sampleClient"]').addEventListener('change', function() { refreshPreview(ui); });
  ui.root.querySelector('[data-field="sampleScript"]').addEventListener('change', function() { refreshPreview(ui); });
}

function escapeText(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function escapeAttr(s) { return escapeText(s).replace(/"/g,'&quot;'); }
