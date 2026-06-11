/**
 * Full schedule editor modal. Replaces PR-4b T13's inline popover.
 *
 * Fields:
 *   - dtstart (date input)
 *   - startTime + endTime (HH:MM time inputs)
 *   - freq (Daily/Weekly/Monthly/Yearly)
 *   - interval (number)
 *   - byweekday checkboxes (Weekly only)
 *   - end-type radio (Never / Until date / After N) with conditional inputs
 *   - priority (number)
 *
 * "Next 5 occurrences" preview shown below the form, recomputed on
 * every input change so the operator can see the recurrence resolve.
 *
 * Save calls store.updateSchedule (edit mode) or store.createSchedule (create mode).
 */
import { openModal, closeModal } from './modal-shell.js';
import { expandSchedule } from '../util/time.js';

const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export function attachRecurrenceEditor(store) {
  // Alt+click on a clip opens the modal. (Same trigger as PR-4b T13,
  // now routes through the shell.)
  document.addEventListener('click', (ev) => {
    if (!ev.altKey) return;
    const clip = ev.target.closest('.mm-clip');
    if (!clip) return;
    ev.preventDefault();
    ev.stopPropagation();
    open(store, clip.dataset.scheduleId);
  }, true);
}

export function openRecurrenceEditor(store, scheduleId) {
  open(store, scheduleId);
}

export function openScheduleCreator(store, prefill = {}) {
  open(store, null, prefill);
}

function open(store, scheduleId, prefill = {}) {
  const isCreate = scheduleId == null;
  const playlistNames = Object.keys(store.playlists || {}).sort();
  const groupIds = (store.displayGroups || []).map(g => g.displayID).filter(Boolean);

  // The schedule the form edits. In create mode it's a fresh default
  // (no id, no _serverVersion) seeded from prefill; in edit mode it's
  // the stored schedule.
  const s = isCreate
    ? {
        id: '__preview__',
        playlistName: prefill.playlistName || playlistNames[0] || '',
        displayID: prefill.displayID || groupIds[0] || '',
        dtstart: prefill.dtstart || new Date().toISOString().slice(0, 10),
        startTime: prefill.startTime || '09:00',
        endTime: prefill.endTime || '10:00',
        freq: 'DAILY', interval: 1, byweekday: [], priority: 0,
        end: { type: 'never' },
      }
    : store.schedules.find(x => x.id === scheduleId);
  if (!s) return;

  const playlistField = isCreate
    ? `<label>Playlist
        <select data-field="playlistName">
          ${playlistNames.map(n => `<option value="${escapeAttr(n)}"${n === s.playlistName ? ' selected' : ''}>${escapeAttr(n)}</option>`).join('')}
        </select></label>`
    : `<label>Playlist <input type="text" disabled value="${escapeAttr(s.playlistName)}"></label>`;
  const displayField = isCreate
    ? `<label>Display
        <select data-field="displayID">
          ${groupIds.map(g => `<option value="${escapeAttr(g)}"${g === s.displayID ? ' selected' : ''}>${escapeAttr(g)}</option>`).join('')}
        </select></label>`
    : `<label>Display <input type="text" disabled value="${escapeAttr(s.displayID)}"></label>`;

  const root = document.createElement('div');
  root.innerHTML = `
    <div class="mm-form-grid">
      ${playlistField}
      ${displayField}
      <label>Starts on <input type="date" data-field="dtstart" value="${escapeAttr(s.dtstart || '')}"></label>
      <label>Priority <input type="number" data-field="priority" min="0" value="${Number(s.priority || 0)}"></label>
      <label>Start time <input type="time" data-field="startTime" value="${escapeAttr(s.startTime || '00:00')}"></label>
      <label>End time <input type="time" data-field="endTime" value="${escapeAttr(s.endTime || '01:00')}"></label>
      <label>Frequency
        <select data-field="freq">
          ${['DAILY','WEEKLY','MONTHLY','YEARLY'].map(f =>
            `<option value="${f}"${(s.freq||'DAILY')===f?' selected':''}>${f[0]+f.slice(1).toLowerCase()}</option>`
          ).join('')}
        </select>
      </label>
      <label>Every <input type="number" data-field="interval" min="1" value="${Number(s.interval || 1)}" style="width:4em"> period(s)</label>
      <div class="mm-form-row" data-field="byweekday">
        ${DOW.map((d, i) => `<label><input type="checkbox" value="${i}"${(s.byweekday||[]).includes(i)?' checked':''}> ${d}</label>`).join('')}
      </div>
      <div class="mm-form-row" data-field="endType">
        <label><input type="radio" name="mmRcEnd" value="never"${((s.end&&s.end.type)||'never')==='never'?' checked':''}> Never</label>
        <label><input type="radio" name="mmRcEnd" value="until"${(s.end&&s.end.type)==='until'?' checked':''}> Until</label>
        <label><input type="radio" name="mmRcEnd" value="count"${(s.end&&s.end.type)==='count'?' checked':''}> After N times</label>
      </div>
      <label data-field="untilRow">Until <input type="date" data-field="untilDate" value="${escapeAttr(s.end?.untilDate || '')}"></label>
      <label data-field="countRow">Count <input type="number" data-field="count" min="1" value="${Number(s.end?.count || 1)}" style="width:5em"></label>
    </div>
    <div class="mm-form-actions">
      ${isCreate ? '' : '<button type="button" class="btn mm-btn-danger" data-action="delete">Delete</button>'}
      <span style="flex:1"></span>
      <button type="button" class="btn btn-ghost" data-action="cancel">Cancel</button>
      <button type="button" class="btn btn-primary" data-action="save">Save</button>
    </div>
    <div class="mm-recurrence-preview"><strong>Next occurrences</strong><ol data-field="preview"></ol></div>
  `;

  function readDraft() {
    const f = (sel) => root.querySelector(sel);
    const freq = f('[data-field="freq"]').value;
    const endTypeEl = f('[data-field="endType"] input:checked');
    const endType = endTypeEl ? endTypeEl.value : 'never';
    let end = { type: 'never' };
    if (endType === 'until') end = { type: 'until', untilDate: f('[data-field="untilDate"]').value };
    if (endType === 'count') end = { type: 'count', count: Math.max(1, parseInt(f('[data-field="count"]').value, 10) || 1) };
    return {
      dtstart: f('[data-field="dtstart"]').value,
      startTime: f('[data-field="startTime"]').value,
      endTime: f('[data-field="endTime"]').value,
      freq,
      interval: Math.max(1, parseInt(f('[data-field="interval"]').value, 10) || 1),
      byweekday: freq === 'WEEKLY'
        ? Array.from(root.querySelectorAll('[data-field="byweekday"] input:checked')).map(cb => Number(cb.value))
        : [],
      end,
      priority: Math.max(0, parseInt(f('[data-field="priority"]').value, 10) || 0),
      ...(isCreate ? {
        playlistName: f('[data-field="playlistName"]').value,
        displayID: f('[data-field="displayID"]').value,
      } : {}),
    };
  }

  function updateConditionals() {
    const freq = root.querySelector('[data-field="freq"]').value;
    root.querySelector('[data-field="byweekday"]').style.display = (freq === 'WEEKLY') ? '' : 'none';
    const endType = root.querySelector('[data-field="endType"] input:checked')?.value || 'never';
    root.querySelector('[data-field="untilRow"]').style.display = (endType === 'until') ? '' : 'none';
    root.querySelector('[data-field="countRow"]').style.display = (endType === 'count') ? '' : 'none';
  }

  function refreshPreview() {
    const draft = readDraft();
    const synthetic = { ...s, ...draft };
    const startIso = new Date().toISOString().slice(0, 10);
    const [y, m, d] = startIso.split('-').map(Number);
    const fromMs = Date.UTC(y, m - 1, d);
    const HORIZON_MS = 365 * 24 * 60 * 60 * 1000;
    const items = expandSchedule(synthetic, fromMs, fromMs + HORIZON_MS).slice(0, 5);
    const ol = root.querySelector('[data-field="preview"]');
    ol.innerHTML = items.length
      ? items.map(p => `<li>${new Date(p.startMs).toISOString().slice(0,10)} ${formatHm(p.startMs)}–${formatHm(p.endMs)}</li>`).join('')
      : '<li class="mm-recurrence-empty">No occurrences in the next 365 days.</li>';
  }

  root.addEventListener('input', () => { updateConditionals(); refreshPreview(); });
  root.addEventListener('change', () => { updateConditionals(); refreshPreview(); });

  const { dialog } = openModal({
    title: isCreate ? 'New schedule' : `Schedule: ${s.playlistName} on ${s.displayID}`,
    contentEl: root,
  });

  root.querySelector('[data-action="cancel"]').addEventListener('click', () => closeModal());
  // Delete (edit mode only). Confirm, then optimistic delete via the store.
  if (!isCreate) {
    root.querySelector('[data-action="delete"]').addEventListener('click', async () => {
      if (!window.confirm(`Delete this schedule (${s.playlistName} on ${s.displayID})?`)) return;
      try {
        await store.deleteSchedule(scheduleId);
        closeModal();
      } catch (_) { /* toast already shown via withRollback */ }
    });
  }
  root.querySelector('[data-action="save"]').addEventListener('click', async () => {
    const draft = readDraft();
    // Note: endTime <= startTime is INTENTIONALLY allowed — expandSchedule
    // (util/time.js) treats that case as a cross-midnight schedule
    // (e.g. 22:00-02:00 wraps). Don't add validation that rejects it.
    if (isCreate && (draft.playlistName === '' || draft.displayID === '')) {
      store.toast('Pick a playlist and a display group.', 'error');
      return;
    }
    if (draft.end.type === 'until' && !draft.end.untilDate) {
      store.toast('Pick an "until" date or change End to Never / After N.', 'error');
      return;
    }
    try {
      if (isCreate) await store.createSchedule(draft);
      else await store.updateSchedule(scheduleId, draft);
      closeModal();
    } catch (_) { /* toast already shown via withRollback */ }
  });

  updateConditionals();
  refreshPreview();

  void dialog; // dialog reference available if needed by future callers
}

function escapeAttr(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;'); }
function formatHm(ms) {
  const d = new Date(ms);
  return `${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`;
}
