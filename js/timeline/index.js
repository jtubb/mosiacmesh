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
import { mmMediaBinComponent } from './bin/media-bin.js';
import { mmPlaylistBinComponent } from './bin/playlist-bin.js';
import { mmToastComponent } from './timeline/toast.js';
import { attachPlaylistToTrack } from './drag/playlist-to-track.js';
import { attachClipMove } from './drag/clip-move.js';
import { attachClipResize } from './drag/clip-resize.js';
import { attachSelection } from './select.js';
import { attachDrillIn } from './drill-in.js';
import { attachMediaToClip } from './drag/media-to-clip.js';
import { attachUpload } from './upload.js';

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
  Alpine.data('mmMediaBin', mmMediaBinComponent);
  // eslint-disable-next-line no-undef
  Alpine.data('mmPlaylistBin', mmPlaylistBinComponent);
  // eslint-disable-next-line no-undef
  Alpine.data('mmToast', mmToastComponent);
  // eslint-disable-next-line no-undef
  const store = Alpine.store('mm');   // the reactive Proxy
  store.hydrate().then(() => {
    requestAnimationFrame(() => autoscrollIntoView());
  });
  startStatusSubscriber(store);
  // eslint-disable-next-line no-undef
  startNowLine(() => Alpine.store('mm'));
  // PR-4b: install document-level drag listeners for playlist→track.
  attachPlaylistToTrack(store);
  attachClipMove(store);
  attachClipResize(store);
  attachSelection(store);
  attachDrillIn(store);
  attachMediaToClip(store);
  attachUpload(store);
}

if (window.__deferredAlpineStart) {
  // Path A: admin.html's deferLoadingAlpine hook caught Alpine's start.
  // Register everything, then start Alpine — it will process the DOM
  // with our store + components already in place.
  bootstrap();
  window.__deferredAlpineStart();
  delete window.__deferredAlpineStart;
} else if (window.Alpine) {
  // Path B: Alpine somehow loaded and started without the defer hook
  // (e.g. an admin variant without our inline script). Register now;
  // Alpine.initTree re-walks the timeline section so the freshly-
  // registered components attach to existing x-data nodes.
  bootstrap();
  // eslint-disable-next-line no-undef
  Alpine.initTree(document.querySelector('[data-route="timeline"]'));
} else {
  // Path C: Alpine hasn't loaded yet — fall back to its alpine:init
  // event (the documented default). This only fires if Alpine loads
  // AFTER this module, which shouldn't happen with the current admin.html
  // tag order, but is the safe fallback.
  document.addEventListener('alpine:init', bootstrap);
}
