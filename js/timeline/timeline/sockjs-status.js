/**
 * Subscribe to the existing window-global SockJS connection and route
 * fleet status broadcasts into Alpine.store('mm').
 *
 * The legacy admin code already opens a SockJS connection at
 * window.sock and dispatches messages via REQUEST type. We add a tiny
 * listener that forwards the three status broadcasts we care about
 * without re-implementing the connection.
 *
 * Read-only in PR-4a: we update displays[].isOnline and per-display
 * renderInProgress, never mutate schedules.
 *
 * PR-20 (2026-06-09): defer mutations while body.mm-dragging is set.
 * The admin timeline renders via x-html="render()" which rewrites the
 * entire .mm-day-grid innerHTML on every reactive mutation. With 20+
 * active iPads pushing status frames, store.displays mutates often
 * enough that an HTML5 drag's source clip gets destroyed mid-drag,
 * the OS-level drag aborts, and the operator's gesture goes nowhere.
 * Symptom: dragstart fires, no dragover, dragend fires within 4-5px.
 * Queue incoming frames while dragging; flush on dragend so status
 * is at most a couple of seconds stale during a drag. The proper
 * architectural fix is to migrate the grid to x-for with stable keys
 * so clip elements survive re-renders — queued as a follow-up.
 */

export function startStatusSubscriber(store) {
  // PR-20: queue + flush plumbing.
  let pending = [];
  function isDragging() {
    if (typeof document === 'undefined') return false;
    const cl = document.body.classList;
    return cl.contains('mm-dragging') || cl.contains('mm-dragging-playlist');
  }
  // When the drag-active class flips off, drain the queue.
  if (typeof MutationObserver !== 'undefined' && typeof document !== 'undefined') {
    const obs = new MutationObserver(() => {
      if (!isDragging() && pending.length > 0) {
        const toFlush = pending;
        pending = [];
        for (const fn of toFlush) {
          try { fn(); } catch (_) { /* tolerate per-frame errors */ }
        }
      }
    });
    obs.observe(document.body, { attributes: true, attributeFilter: ['class'] });
  }

  function applyMutation(fn) {
    if (isDragging()) { pending.push(fn); return; }
    fn();
  }

  function handle(msg) {
    if (!msg || typeof msg !== 'object') return;
    const req = msg.REQUEST;
    const payload = msg.PAYLOAD;
    if (req === 'DISCOVERY_HEARTBEAT') {
      // payload: {devices: [{clientKey, displayID, isOnline, ...}, ...]}
      const devs = payload?.devices ?? [];
      applyMutation(() => {
        for (const d of devs) {
          store.setStatus(d.displayID || d.clientKey, {
            isOnline: !!d.isOnline,
            friendlyName: d.friendlyName,
          });
        }
      });
    } else if (req === 'CLIENTS_WENT_OFFLINE') {
      // payload: {clientKeys: [...]} or {displayIDs: [...]}
      const keys = payload?.clientKeys ?? [];
      const ids  = payload?.displayIDs ?? [];
      applyMutation(() => {
        for (const k of keys) store.setStatus(k, { isOnline: false });
        for (const id of ids) store.setStatus(id, { isOnline: false });
      });
    } else if (req === 'RENDER_IN_PROGRESS') {
      // payload: {displayID, inProgress}
      if (payload?.displayID) {
        applyMutation(() => {
          store.setRenderInProgress(payload.displayID, !!payload.inProgress);
        });
      }
    }
  }

  // The legacy code stores a SockJS connection in window.sock and
  // registers message handlers via $(window).on('mm:msg', ...). The
  // shape varies — we try both common paths and warn if we can't hook.
  if (window.sock && typeof window.sock.onmessage !== 'undefined') {
    const prev = window.sock.onmessage;
    window.sock.onmessage = function (ev) {
      try {
        const data = (typeof ev.data === 'string') ? JSON.parse(ev.data) : ev.data;
        handle(data);
      } catch (e) { /* ignore parse errors — not all messages are JSON */ }
      if (prev) prev.call(this, ev);
    };
  } else if (window.jQuery) {
    window.jQuery(window).on('mm:msg', (_e, msg) => handle(msg));
  } else {
    console.warn('[timeline] no SockJS hook available; status will not auto-refresh');
  }
}
