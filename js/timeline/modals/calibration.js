// js/timeline/modals/calibration.js
/**
 * Display calibration modal. Three steps stacked vertically:
 *   1. Pick a display group from a dropdown.
 *   2. Click 'Generate ArUco' — sends GENERATEARUCO websocket request
 *      so each device shows its unique marker.
 *   3. Upload a wall photo. POST /upload/calibrate, surface the result
 *      ("Detected N markers" or "Found 0 — check lighting").
 *
 * No new server code — the websocket request type already exists in
 * mosaicmesh.websocket.legacy.msg_response, and /upload/calibrate is
 * handled by mosaicmesh.api.media. The existing Displays page UI
 * stays for now; PR-6 (spec) deletes it.
 *
 * Security: all dynamic content (displayID values from the server) is
 * inserted via textContent / DOM properties — never interpolated into
 * innerHTML — so there is no XSS surface even if a displayID contains
 * HTML-special characters.
 */
import { openModal, closeModal } from './modal-shell.js';

export function openCalibrationModal(store, preGroup) {
  const root = document.createElement('div');
  root.className = 'mm-calibration';

  // --- Step list (static skeleton, no dynamic content in markup) ---
  const ol = document.createElement('ol');
  ol.className = 'steps';

  // Step 1: group picker
  const li1 = document.createElement('li');
  const num1 = document.createElement('span');
  num1.className = 'num';
  num1.textContent = '1';
  const label1 = document.createElement('label');
  label1.textContent = 'Display group';
  const select = document.createElement('select');
  select.dataset.field = 'group';
  // Populate options via DOM — no innerHTML, no escaping needed
  const groups = Array.from(new Set(
    store.displays.map(d => d.displayID).filter(Boolean)
  )).sort();
  groups.forEach(function(g) {
    const opt = document.createElement('option');
    opt.value = g;         // DOM property assignment — no HTML injection
    opt.textContent = g;   // textContent — no HTML injection
    select.appendChild(opt);
  });
  // Section 4: when opened from a group's Fleet detail, pre-select that
  // group so the operator doesn't re-pick it. The picker stays editable.
  if (preGroup && groups.includes(preGroup)) {
    select.value = preGroup;
  }
  label1.appendChild(select);
  li1.appendChild(num1);
  li1.appendChild(label1);

  // Step 2: generate button + status
  const li2 = document.createElement('li');
  const num2 = document.createElement('span');
  num2.className = 'num';
  num2.textContent = '2';
  const genBtn = document.createElement('button');
  genBtn.type = 'button';
  genBtn.className = 'btn btn-primary';
  genBtn.dataset.action = 'generate';
  genBtn.textContent = 'Generate ArUco on selected group';
  const statusSpan = document.createElement('span');
  statusSpan.className = 'mm-calibration-status';
  statusSpan.dataset.field = 'generateStatus';
  li2.appendChild(num2);
  li2.appendChild(genBtn);
  li2.appendChild(statusSpan);

  // Step 3: file input
  const li3 = document.createElement('li');
  const num3 = document.createElement('span');
  num3.className = 'num';
  num3.textContent = '3';
  const label3 = document.createElement('label');
  label3.textContent = 'Upload wall photo';
  const fileInput = document.createElement('input');
  fileInput.type = 'file';
  fileInput.accept = 'image/*';
  fileInput.dataset.field = 'photo';
  label3.appendChild(fileInput);
  li3.appendChild(num3);
  li3.appendChild(label3);

  ol.appendChild(li1);
  ol.appendChild(li2);
  ol.appendChild(li3);

  // Result area
  const resultDiv = document.createElement('div');
  resultDiv.className = 'mm-calibration-result';
  resultDiv.dataset.field = 'result';

  // Actions row
  const actionsDiv = document.createElement('div');
  actionsDiv.className = 'mm-form-actions';
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'btn btn-ghost';
  closeBtn.dataset.action = 'close';
  closeBtn.textContent = 'Close';
  actionsDiv.appendChild(closeBtn);

  root.appendChild(ol);
  root.appendChild(resultDiv);
  root.appendChild(actionsDiv);

  openModal({ title: 'Display calibration', contentEl: root });

  // --- Event handlers ---

  closeBtn.addEventListener('click', function() { closeModal(); });

  genBtn.addEventListener('click', function() {
    const group = select.value;
    if (!group) return;
    statusSpan.textContent = 'Sending GENERATEARUCO…';
    // The existing SockJS plumbing exposes a sock global; reuse it so
    // we don't recreate a connection just for this one message.
    try {
      if (typeof window.sock !== 'undefined' && typeof window.generateMessage === 'function') {
        window.sock.send(window.generateMessage('SRV', 'GENERATEARUCO', { id: group }));
        statusSpan.textContent = 'Markers requested for ' + group + '. Photograph and upload below.';
      } else {
        // Fallback: REST-only path doesn't exist for this — surface the
        // mismatch instead of silently failing.
        statusSpan.textContent = 'SockJS not available; reload the page.';
      }
    } catch (e) {
      statusSpan.textContent = 'Failed to send: ' + (e && e.message ? e.message : String(e));
    }
  });

  fileInput.addEventListener('change', async function(ev) {
    const file = ev.target.files && ev.target.files[0];
    if (!file) return;
    resultDiv.textContent = 'Uploading + detecting…';
    const fd = new FormData();
    fd.append('file', file, file.name);
    try {
      const r = await fetch('/upload/calibrate', { method: 'POST', body: fd });
      const body = await r.json().catch(function() { return {}; });
      if (r.ok && body.success !== false) {
        const n = body.detected != null ? body.detected : (body.markers != null ? body.markers : '?');
        resultDiv.textContent = 'Detected ' + n + ' markers.';
        store.toast('Calibration: detected ' + n + ' markers.', 'info');
      } else {
        resultDiv.textContent = body.error || 'Calibration failed.';
      }
    } catch (e) {
      resultDiv.textContent = 'Upload failed: ' + (e && e.message ? e.message : String(e));
    }
  });
}
