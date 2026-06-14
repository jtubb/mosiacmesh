/**
 * PR-29: ad-hoc "Play now" affordance. Right-click a track header →
 * "Play playlist now…" opens this modal. The operator picks a playlist
 * and the modal sends two SockJS messages — ASSIGN_PLAYLIST + PLAY —
 * which lands at the same handlers (mosaicmesh/websocket/legacy.py)
 * that the scheduler uses, skipping evaluate_schedules entirely.
 *
 * Why bypass the scheduler: evaluate_schedules() runs on a 5-second
 * tick, so a freshly-created schedule has up to a 5s lag before
 * playback begins. Ad-hoc "I want to show this RIGHT NOW" jobs (demo,
 * customer walk-in) need sub-second feedback. The protocol-level path
 * is already coordinated-start aware (PLAY → _begin_prepare → group
 * ACK → synchronized epoch), so we get the same multi-screen timing
 * correctness as a scheduled play.
 *
 * The operator can also pick "Stop playback" directly from the
 * track-header context menu — that fires the existing STOP request
 * without a picker.
 */
import { openModal, closeModal } from './modal-shell.js';

function sockReady(store) {
  if (typeof window.sock !== 'undefined' && typeof window.generateMessage === 'function') return true;
  store.toast('SockJS not available; reload the page.', 'error');
  return false;
}

export function firePlayNow(store, displayID, playlistName) {
  if (!sockReady(store)) return;
  if (!store.isPlaylistReady(playlistName, displayID)) {
    store.toast(`"${playlistName}" isn't rendered for "${displayID}" yet.`, 'error');
    return;
  }
  try {
    window.sock.send(window.generateMessage('SRV', 'ASSIGN_PLAYLIST', { displayID, name: playlistName }));
    window.sock.send(window.generateMessage('SRV', 'PLAY', { displayID }));
    store.toast(`Playing "${playlistName}" on "${displayID}" now.`, 'info');
  } catch (e) {
    store.toast(`Failed to start playback: ${(e && e.message) || e}`, 'error');
  }
}

export function fireStopNow(store, displayID) {
  if (!sockReady(store)) return;
  try {
    window.sock.send(window.generateMessage('SRV', 'STOP', { displayID }));
    store.toast(`Stopped playback on "${displayID}".`, 'info');
  } catch (e) {
    store.toast(`Failed to stop playback: ${(e && e.message) || e}`, 'error');
  }
}

export function openPlayNowModal(store, displayID) {
  const root = document.createElement('div');
  root.className = 'mm-play-now';

  const note = document.createElement('p');
  note.className = 'mm-play-now-note';
  note.textContent = `Pick a playlist to play immediately on "${displayID}". This bypasses the scheduler — no schedule is created.`;
  root.appendChild(note);

  const names = Object.keys(store.playlists || {}).sort();
  if (names.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'mm-play-now-empty';
    empty.textContent = 'No playlists yet. Create one in the bin first.';
    root.appendChild(empty);
  } else {
    const ul = document.createElement('ul');
    ul.className = 'mm-play-now-list';
    for (const name of names) {
      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.type = 'button';
      const ready = store.isPlaylistReady(name, displayID);
      btn.className = 'btn mm-play-now-pick';
      btn.textContent = name;
      if (!ready) {
        const entry = store.renderEntry(name, displayID);
        const why = entry && entry.state === 'FAILED' ? 'render failed'
          : entry && (entry.state === 'RENDERING' || entry.state === 'QUEUED') ? 'rendering…'
          : 'not rendered for this group';
        btn.disabled = true;
        btn.title = why;
        btn.textContent = `${name} — ${why}`;
      } else {
        btn.addEventListener('click', function () {
          firePlayNow(store, displayID, name);
          closeModal();
        });
      }
      li.appendChild(btn);
      ul.appendChild(li);
    }
    root.appendChild(ul);
  }

  const actions = document.createElement('div');
  actions.className = 'mm-form-actions';
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'btn btn-ghost';
  cancel.textContent = 'Cancel';
  cancel.addEventListener('click', function () { closeModal(); });
  actions.appendChild(cancel);
  root.appendChild(actions);

  openModal({ title: `Play now on "${displayID}"`, contentEl: root });
}
