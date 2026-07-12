/**
 * Generic confirm modal on modal-shell. fleet-confirm.js is RUN_SCRIPT-bound, so
 * destructive fleet actions (e.g. Clear cache) use this parameterized confirm instead.
 *   confirmModal({ title, message, confirmLabel, danger, onConfirm })
 * Cancel -> closeModal(); Confirm -> closeModal() then onConfirm().
 */
import { openModal, closeModal } from './modal-shell.js';

export function confirmModal({ title, message, confirmLabel, danger, onConfirm }) {
  const root = document.createElement('div');
  root.className = 'mm-confirm-modal';

  const msg = document.createElement('p');
  msg.className = 'mm-confirm-modal-msg';
  msg.textContent = message || '';
  root.appendChild(msg);

  const actions = document.createElement('div');
  actions.className = 'mm-form-actions';

  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'btn btn-ghost';
  cancel.textContent = 'Cancel';
  cancel.addEventListener('click', () => closeModal());

  const confirm = document.createElement('button');
  confirm.type = 'button';
  confirm.className = danger ? 'btn btn-primary mm-fleet-confirm-danger' : 'btn btn-primary';
  confirm.textContent = confirmLabel || 'Confirm';
  confirm.addEventListener('click', () => { closeModal(); if (onConfirm) onConfirm(); });

  actions.appendChild(cancel);
  actions.appendChild(confirm);
  root.appendChild(actions);

  openModal({ title: title || 'Confirm', contentEl: root });
}
