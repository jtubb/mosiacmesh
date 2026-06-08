/**
 * PR-4c gap-fix (spec §363): confirm modal for fleet-wide actions
 * that would affect more than 3 devices. Toolbar's Login/Start/Stop/
 * Reboot/Test buttons used to fire without confirmation (other than
 * the legacy reboot/stop `confirm()` from admin.html). For any fleet
 * larger than 3 devices, a stronger gesture is warranted.
 *
 * PR-13: `fireFleetAction(store, which, scope)` accepts a scope arg.
 *   scope = null / undefined / ''  → all devices (legacy behaviour)
 *   scope = '<displayID>'           → only that group's devices
 * The count, confirm copy, and on-wire payload all adjust:
 *   - all   → RUN_SCRIPT {all:true,  script}
 *   - scope → RUN_SCRIPT {displayID, script}
 * The backend (mosaicmesh/websocket/legacy.py RUN_SCRIPT handler) has
 * always supported {displayID}; PR-13 just adds the UI surface.
 */
import { openModal, closeModal } from './modal-shell.js';

const ACTION_LABELS = {
  login:  { verb: 'Login',  description: 'Wake + unlock the targeted devices (SSH).' },
  start:  { verb: 'Start',  description: 'Open the display page on the targeted devices.' },
  stop:   { verb: 'Stop',   description: 'Close the display on the targeted devices.' },
  reboot: { verb: 'Reboot', description: 'Reboot the targeted devices.' },
  test:   { verb: 'Test',   description: 'Open the display in diagnostics (?tdbg) on the targeted devices.' },
};

const CONFIRM_THRESHOLD = 3;

/**
 * Count how many devices a scope targets. Exported so tests + UI can
 * preview the count without dispatching the action (e.g. a tooltip
 * "Reboot 5 Tablet devices").
 */
export function countTargets(store, scope) {
  const devices = store.displays || [];
  if (!scope) return devices.length;
  return devices.filter(d => d.displayID === scope).length;
}

function scopeLabel(scope) {
  return scope ? `group "${scope}"` : 'every device';
}

export function fireFleetAction(store, which, scope) {
  const count = countTargets(store, scope);
  if (count === 0) {
    store.toast(`No ${scope ? `devices in "${scope}"` : 'devices online'} — nothing to do.`, 'warn');
    return;
  }
  if (count > CONFIRM_THRESHOLD) {
    showConfirm(store, which, scope, count);
  } else {
    sendFrame(store, which, scope, count);
  }
}

function showConfirm(store, which, scope, count) {
  const labels = ACTION_LABELS[which] || { verb: which, description: '' };
  const root = document.createElement('div');
  root.className = 'mm-fleet-confirm';

  const desc = document.createElement('p');
  desc.className = 'mm-fleet-confirm-desc';
  desc.textContent = labels.description;
  root.appendChild(desc);

  const summary = document.createElement('p');
  summary.className = 'mm-fleet-confirm-summary';
  const verbStrong = document.createElement('strong');
  verbStrong.textContent = labels.verb.toLowerCase();
  const tail = document.createTextNode(` on ${count} ${scope ? `"${scope}" ` : ''}device${count === 1 ? '' : 's'}?`);
  const lead = document.createTextNode('Are you sure you want to ');
  summary.appendChild(lead);
  summary.appendChild(verbStrong);
  summary.appendChild(tail);
  root.appendChild(summary);

  const actions = document.createElement('div');
  actions.className = 'mm-form-actions';
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'btn btn-ghost';
  cancel.textContent = 'Cancel';
  cancel.addEventListener('click', () => closeModal());
  const confirm = document.createElement('button');
  confirm.type = 'button';
  confirm.className = (which === 'reboot' || which === 'stop') ? 'btn btn-primary mm-fleet-confirm-danger' : 'btn btn-primary';
  confirm.textContent = `${labels.verb} ${count} ${scope ? `"${scope}" ` : ''}device${count === 1 ? '' : 's'}`;
  confirm.addEventListener('click', () => {
    closeModal();
    sendFrame(store, which, scope, count);
  });
  actions.appendChild(cancel);
  actions.appendChild(confirm);
  root.appendChild(actions);

  openModal({ title: `Fleet action: ${labels.verb} (${scopeLabel(scope)})`, contentEl: root });
}

function sendFrame(store, which, scope, count) {
  const labels = ACTION_LABELS[which] || { verb: which };
  if (typeof window.sock === 'undefined' || typeof window.generateMessage !== 'function') {
    store.toast('SockJS not available; reload the page.', 'error');
    return;
  }
  const payload = scope ? { displayID: scope, script: which }
                        : { all: true,         script: which };
  try {
    window.sock.send(window.generateMessage('SRV', 'RUN_SCRIPT', payload));
    const targetDesc = scope ? `${count} "${scope}" device${count === 1 ? '' : 's'}`
                             : `${count} device${count === 1 ? '' : 's'}`;
    store.toast(`${labels.verb} sent to ${targetDesc}.`, 'info');
  } catch (e) {
    store.toast(`Failed to send ${which}: ${e?.message || e}`, 'error');
  }
}
