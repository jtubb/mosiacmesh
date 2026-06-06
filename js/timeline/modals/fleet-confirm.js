/**
 * PR-4c gap-fix (spec §363): confirm modal for fleet-wide actions
 * that would affect more than 3 devices. Toolbar's Login/Start/Stop/
 * Reboot/Test buttons used to fire without confirmation (other than
 * the legacy reboot/stop `confirm()` from admin.html). For any fleet
 * larger than 3 devices, a stronger gesture is warranted.
 *
 * fireFleetAction(store, which) is the canonical entry point. It
 * counts store.displays, prompts via modal-shell if >3, and sends the
 * RUN_SCRIPT websocket frame via window.sock + window.generateMessage
 * (same plumbing the legacy runScriptAll uses — we don't call
 * runScriptAll itself to avoid double-confirming on stop/reboot).
 */
import { openModal, closeModal } from './modal-shell.js';

const ACTION_LABELS = {
  login:  { verb: 'Login',  description: 'Wake + unlock every device (SSH).' },
  start:  { verb: 'Start',  description: 'Open the display page on every device.' },
  stop:   { verb: 'Stop',   description: 'Close the display on every device.' },
  reboot: { verb: 'Reboot', description: 'Reboot every device.' },
  test:   { verb: 'Test',   description: 'Open the display in diagnostics (?tdbg) on every device.' },
};

const CONFIRM_THRESHOLD = 3;

export function fireFleetAction(store, which) {
  const count = (store.displays || []).length;
  if (count > CONFIRM_THRESHOLD) {
    showConfirm(store, which, count);
  } else {
    sendFrame(store, which, count);
  }
}

function showConfirm(store, which, count) {
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
  const tail = document.createTextNode(` on ${count} devices?`);
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
  confirm.textContent = `${labels.verb} all ${count} devices`;
  confirm.addEventListener('click', () => {
    closeModal();
    sendFrame(store, which, count);
  });
  actions.appendChild(cancel);
  actions.appendChild(confirm);
  root.appendChild(actions);

  openModal({ title: `Fleet action: ${labels.verb}`, contentEl: root });
}

function sendFrame(store, which, count) {
  const labels = ACTION_LABELS[which] || { verb: which };
  if (typeof window.sock === 'undefined' || typeof window.generateMessage !== 'function') {
    store.toast('SockJS not available; reload the page.', 'error');
    return;
  }
  try {
    window.sock.send(window.generateMessage('SRV', 'RUN_SCRIPT', { all: true, script: which }));
    store.toast(`${labels.verb} sent to ${count} device${count === 1 ? '' : 's'}.`, 'info');
  } catch (e) {
    store.toast(`Failed to send ${which}: ${e?.message || e}`, 'error');
  }
}
