/**
 * Admin timeline view — entry point.
 *
 * Bootstrap order (the tricky bit):
 *   1. admin.html inline script sets window.deferLoadingAlpine = fn that
 *      captures Alpine's start() into window.__deferredAlpineStart.
 *   2. Alpine's <script defer> runs at DOMContentLoaded, calls
 *      deferLoadingAlpine — Alpine is loaded but NOT started.
 *   3. This module loads (also deferred; module scripts run AFTER classic
 *      defer scripts). We register the store + components + subscribers,
 *      THEN invoke __deferredAlpineStart() so Alpine processes the DOM
 *      with our components already registered.
 *
 * Without the defer hook, Alpine starts before this module runs and
 * x-data="mmTimeline" / x-store="mm" etc. evaluate against an empty
 * registration table, producing "mmTimeline is not defined" and
 * "Cannot read properties of undefined (reading 'hydrated')" errors.
 *
 * PR-4a (this PR): scaffolding + read-only render. Subsequent PRs
 * (4b interactivity, 4c modals) extend store.js + add component
 * modules, but this file stays small.
 */
import { makeStore } from './store.js';
import { mmTimelineComponent } from './timeline/timeline.js';
import { mmToolbarComponent } from './toolbar.js';
import { startStatusSubscriber } from './timeline/sockjs-status.js';
import { startNowLine, autoscrollIntoView } from './timeline/now-line.js';
import { mmPlaylistBinComponent } from './bin/playlist-bin.js';
import { mmToastComponent } from './timeline/toast.js';
import { attachPlaylistToTrack } from './drag/playlist-to-track.js';
import { attachClipMove } from './drag/clip-move.js';
import { attachClipResize } from './drag/clip-resize.js';
import { attachSelection } from './select.js';
import { attachDrillIn } from './drill-in.js';
import { attachSubItemReorder } from './drag/subitem-reorder.js';
import { attachRecurrenceEditor } from './modals/recurrence-editor.js';
import { attachContextMenu } from './context-menu.js';
import { attachPlaylistEditor } from './modals/playlist-editor.js';
import { attachTrackHeaderPopover } from './track-header-popover.js';
import { attachTrackHeaderContextMenu } from './track-header-context-menu.js';
import { startRouter } from './shell/router.js';
import { mmContentComponent } from './content/content-view.js';
import { mmScheduleMobileComponent } from './schedule/schedule-mobile.js';
import { mmFleetComponent } from './fleet/fleet-view.js';

function bootstrap() {
  // CRITICAL: `Alpine.store(name, obj)` wraps `obj` in a reactive Proxy
  // and stores the PROXY under `name`. Mutations on the raw `obj`
  // reference DO NOT trigger reactivity — only mutations on the proxy
  // do. So we MUST read the proxy back via `Alpine.store(name)` before
  // calling any method (like hydrate()) that mutates state. Caught by
  // Playwright smoke 2026-06-05 — hydrate() set this.hydrated = true
  // on the raw obj, the x-show binding never re-evaluated, and the
  // page sat on "Loading…" forever.
  // eslint-disable-next-line no-undef
  Alpine.store('mm', makeStore());
  // eslint-disable-next-line no-undef
  Alpine.data('mmTimeline', mmTimelineComponent);
  // eslint-disable-next-line no-undef
  Alpine.data('mmToolbar', mmToolbarComponent);
  // eslint-disable-next-line no-undef
  Alpine.data('mmPlaylistBin', mmPlaylistBinComponent);
  // eslint-disable-next-line no-undef
  Alpine.data('mmToast', mmToastComponent);
  // eslint-disable-next-line no-undef
  Alpine.data('mmContent', mmContentComponent);
  // eslint-disable-next-line no-undef
  Alpine.data('mmScheduleMobile', mmScheduleMobileComponent);
  // eslint-disable-next-line no-undef
  Alpine.data('mmFleet', mmFleetComponent);
  // eslint-disable-next-line no-undef
  const store = Alpine.store('mm');   // the reactive Proxy
  // Section 3: drive store.isMobile from the viewport so the Schedule
  // section can switch between the desktop grid and the mobile stack.
  if (typeof window.matchMedia === 'function') {
    const mq = window.matchMedia('(max-width: 759px)');
    store.setIsMobile(mq.matches);
    const onChange = (e) => store.setIsMobile(e.matches);
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else if (mq.addListener) mq.addListener(onChange); // older Safari
  }
  store.hydrate().then(() => {
    requestAnimationFrame(() => autoscrollIntoView());
  });
  // Section 1 (Task 12): drive section visibility from the URL hash via the
  // store. Replaces the old jQuery adminRoute() / .active toggling in
  // admin.html — x-show on each .section now owns visibility.
  startRouter(store);
  startStatusSubscriber(store);
  // eslint-disable-next-line no-undef
  startNowLine(() => Alpine.store('mm'));
  // PR-4b: install document-level drag listeners for playlist→track.
  attachPlaylistToTrack(store);
  attachClipMove(store);
  attachClipResize(store);
  attachSelection(store);
  attachDrillIn(store);
  attachSubItemReorder(store);
  attachRecurrenceEditor(store);
  attachContextMenu(store);
  attachPlaylistEditor(store);
  attachTrackHeaderPopover(store);
  attachTrackHeaderContextMenu(store);
}

// PR-19 (2026-06-09): use Alpine 3's documented alpine:init event
// instead of the deprecated deferLoadingAlpine hook. Alpine 3.13.10
// no longer honors deferLoadingAlpine — it auto-starts on script load.
// Our previous bootstrap relied on the hook and broke under load (24+
// SockJS clients pushing frames at hydrate time). The new pattern:
//
//   1. admin.html loads this module BEFORE Alpine (script source order).
//   2. This top-level code registers an alpine:init listener.
//   3. Alpine loads + fires alpine:init RIGHT BEFORE walking the DOM.
//   4. bootstrap() runs in that listener — Alpine.store/Alpine.data
//      calls land before Alpine evaluates any x-data attribute.
//
// The fallback branch handles a degenerate case: Alpine has already
// started by the time this module evaluates (e.g. someone reordered
// the scripts in admin.html). In that case we destroyTree + initTree
// the body to force a clean re-walk with components registered.
if (window.Alpine && typeof window.Alpine.destroyTree === 'function'
    && document.querySelector('[x-data]')?._x_dataStack) {
  // Alpine already walked — force a clean re-walk now that we can register.
  bootstrap();
  try { Alpine.destroyTree(document.body); } catch (_) { /* tolerate */ }
  Alpine.initTree(document.body);
} else {
  document.addEventListener('alpine:init', bootstrap);
}
