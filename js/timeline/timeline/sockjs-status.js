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
 */

export function startStatusSubscriber(store) {
  function handle(msg) {
    if (!msg || typeof msg !== 'object') return;
    const req = msg.REQUEST;
    const payload = msg.PAYLOAD;
    if (req === 'DISCOVERY_HEARTBEAT') {
      // payload: {devices: [{clientKey, displayID, isOnline, ...}, ...]}
      const devs = payload?.devices ?? [];
      for (const d of devs) {
        store.setStatus(d.displayID || d.clientKey, {
          isOnline: !!d.isOnline,
          friendlyName: d.friendlyName,
        });
      }
    } else if (req === 'CLIENTS_WENT_OFFLINE') {
      // payload: {clientKeys: [...]} or {displayIDs: [...]}
      const keys = payload?.clientKeys ?? [];
      for (const k of keys) store.setStatus(k, { isOnline: false });
      const ids  = payload?.displayIDs ?? [];
      for (const id of ids) store.setStatus(id, { isOnline: false });
    } else if (req === 'RENDER_IN_PROGRESS') {
      // payload: {displayID, inProgress}
      if (payload?.displayID) {
        store.setRenderInProgress(payload.displayID, !!payload.inProgress);
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
