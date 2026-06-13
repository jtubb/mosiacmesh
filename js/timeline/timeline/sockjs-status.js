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

  // PR-27 (2026-06-09): apply a per-device status update. Updates the
  // CLIENT record in store.displays (for any reactive renders that
  // count by .filter(c => c.displayID === ... && c.isOnline)) AND the
  // GROUP-summary fields in store.displayGroups (so the track-header
  // "N/M online" badge updates live without waiting for the next full
  // /api/displays refresh).
  function applyDeviceStatus(dev) {
    if (!dev || !dev.clientKey) return;
    const prev = (store.displays || []).find(c => c.clientKey === dev.clientKey);
    const prevOnline = !!prev?.isOnline;
    const nextOnline = !!dev.isOnline;
    store.setStatus(dev.clientKey, {
      isOnline: nextOnline,
      friendlyName: dev.friendlyName ?? prev?.friendlyName,
    });
    // Reconcile group onlineCount. Only nudge when the bit actually
    // flipped — we don't want repeated "still-online" frames to
    // miscount. The group lookup uses the device's displayID (preferred)
    // or the previous record's displayID (in case a brand-new device
    // arrived in the message without yet being in store.displays).
    if (prevOnline === nextOnline) return;
    const displayID = dev.displayID || prev?.displayID;
    if (!displayID) return;
    const group = (store.displayGroups || []).find(g => g.displayID === displayID);
    if (!group) return;
    const delta = nextOnline ? +1 : -1;
    group.onlineCount = Math.max(0, Math.min(group.clientCount, (group.onlineCount || 0) + delta));
  }

  function handle(msg) {
    if (!msg || typeof msg !== 'object') return;
    const req = msg.REQUEST;
    const payload = msg.PAYLOAD;
    if (req === 'DISCOVERY_HEARTBEAT') {
      // payload: {devices: [{clientKey, displayID, isOnline, ...}, ...]}
      // Server-side currently sends an aggregate-only heartbeat (no
      // devices array), but if a future revision starts including
      // per-device state in the heartbeat this branch picks it up.
      const devs = payload?.devices ?? [];
      if (devs.length > 0) applyMutation(() => devs.forEach(applyDeviceStatus));
      // Aggregate online count: server.py's DISCOVERY_HEARTBEAT sends
      // PAYLOAD.onlineClients (an integer). Mirror it into the store so
      // the connection indicator can show "N online".
      if (payload && typeof payload.onlineClients === 'number') {
        applyMutation(() => store.setConnection({ onlineClients: payload.onlineClients }));
      }
    } else if (req === 'CLIENTS_CAME_ONLINE' || req === 'CLIENTS_WENT_OFFLINE') {
      // PR-27: unified shape — both events carry {devices: [{clientKey,
      // displayID, isOnline, friendlyName}, ...]}. CLIENTS_WENT_OFFLINE
      // pre-PR-27 sent a raw list; the server is now updated to the
      // {devices: ...} shape, but accept either in case operators run
      // mixed versions during the rollout.
      const devs = payload?.devices ?? (Array.isArray(payload) ? payload : []);
      if (devs.length > 0) applyMutation(() => devs.forEach(applyDeviceStatus));
    } else if (req === 'RENDER_IN_PROGRESS') {
      // payload: {displayID, inProgress}
      if (payload?.displayID) {
        applyMutation(() => {
          store.setRenderInProgress(payload.displayID, !!payload.inProgress);
        });
      }
    } else if (req === 'PLAYBACK_CHANGED') {
      // payload: {groups: [{displayID, state, currentPlaylist, startedEpoch, renderStatus}, ...]}
      const rows = payload?.groups ?? [];
      if (rows.length > 0) applyMutation(() => rows.forEach((r) => store.setPlayback(r)));
    } else if (req === 'RENDERS_CHANGED') {
      // payload: {renders: [{displayID, playlist, state, percent?, ...}, ...]}
      // queueDepth is NOT included in the broadcast — leave it unchanged.
      const rows = payload?.renders ?? [];
      applyMutation(() => store.setRenders(rows));
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
    // Connection indicator (replaces the legacy jQuery #connDot/#connText poking).
    if (window.sock.readyState === 1 /* OPEN */) store.setConnection({ connected: true });
    const _prevOpen = window.sock.onopen;
    window.sock.onopen = function (e) { store.setConnection({ connected: true }); if (_prevOpen) _prevOpen.call(this, e); };
    const _prevClose = window.sock.onclose;
    window.sock.onclose = function (e) { store.setConnection({ connected: false }); if (_prevClose) _prevClose.call(this, e); };
  } else if (window.jQuery) {
    window.jQuery(window).on('mm:msg', (_e, msg) => handle(msg));
  } else {
    console.warn('[timeline] no SockJS hook available; status will not auto-refresh');
  }
}
