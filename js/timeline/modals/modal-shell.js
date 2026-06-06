/**
 * Tiny reusable modal scaffold. ONE modal at a time — opening a new
 * modal closes the current one. Handles:
 *   - focus trap (focus moves to first focusable; Tab cycles within)
 *   - Esc closes
 *   - click on overlay closes
 *   - aria-labelledby points at the title element
 *
 * Modals own their own content (form, layout, save handler). The shell
 * just wires the chrome.
 */

let currentClose = null;

export function openModal({ title, contentEl, onClose }) {
  if (currentClose) currentClose();
  const host = document.getElementById('mmModalHost');
  if (!host) throw new Error('modal-shell: #mmModalHost not found');

  const overlay = document.createElement('div');
  overlay.className = 'mm-modal-overlay';
  overlay.setAttribute('role', 'presentation');

  const dialog = document.createElement('div');
  dialog.className = 'mm-modal';
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');
  const titleId = 'mm-modal-title-' + Math.floor(Math.random() * 1e9).toString(36);
  dialog.setAttribute('aria-labelledby', titleId);

  const header = document.createElement('div');
  header.className = 'mm-modal-header';
  const h2 = document.createElement('h2');
  h2.id = titleId;
  h2.textContent = title;
  const closeBtn = document.createElement('button');
  closeBtn.className = 'mm-modal-close btn btn-ghost';
  closeBtn.type = 'button';
  closeBtn.setAttribute('aria-label', 'Close');
  closeBtn.textContent = '✕';
  header.appendChild(h2);
  header.appendChild(closeBtn);

  const body = document.createElement('div');
  body.className = 'mm-modal-body';
  body.appendChild(contentEl);

  dialog.appendChild(header);
  dialog.appendChild(body);
  overlay.appendChild(dialog);
  host.appendChild(overlay);

  function close() {
    if (currentClose !== close) return;
    document.removeEventListener('keydown', onKey);
    overlay.remove();
    currentClose = null;
    if (typeof onClose === 'function') onClose();
  }

  function onKey(ev) {
    if (ev.key === 'Escape') { ev.preventDefault(); close(); }
    else if (ev.key === 'Tab') trapFocus(ev, dialog);
  }
  closeBtn.addEventListener('click', close);
  overlay.addEventListener('mousedown', (ev) => { if (ev.target === overlay) close(); });
  document.addEventListener('keydown', onKey);

  // Focus the first focusable; fall back to dialog itself for Esc.
  const first = dialog.querySelector('input, select, textarea, button:not(.mm-modal-close), [tabindex]:not([tabindex="-1"])');
  (first || closeBtn).focus();

  currentClose = close;
  return { close, dialog };
}

export function closeModal() {
  if (currentClose) currentClose();
}

function trapFocus(ev, root) {
  const els = Array.from(root.querySelectorAll(
    'input, select, textarea, button, [tabindex]:not([tabindex="-1"])'
  )).filter(el => !el.disabled && el.offsetParent !== null);
  if (!els.length) return;
  const first = els[0], last = els[els.length - 1];
  if (ev.shiftKey && document.activeElement === first) {
    ev.preventDefault(); last.focus();
  } else if (!ev.shiftKey && document.activeElement === last) {
    ev.preventDefault(); first.focus();
  }
}
