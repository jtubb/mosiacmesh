import { makeStore } from './store.js';
import { mmTimelineComponent } from './timeline/timeline.js';
import { mmToolbarComponent } from './toolbar.js';
import { startStatusSubscriber } from './timeline/sockjs-status.js';
import { startNowLine, autoscrollIntoView } from './timeline/now-line.js';
import { mmMediaBinComponent } from './bin/media-bin.js';
import { mmPlaylistBinComponent } from './bin/playlist-bin.js';

document.addEventListener('alpine:init', () => {
  const store = makeStore();
  Alpine.store('mm', store);
  Alpine.data('mmTimeline', mmTimelineComponent);
  Alpine.data('mmToolbar', mmToolbarComponent);
  Alpine.data('mmMediaBin', mmMediaBinComponent);
  Alpine.data('mmPlaylistBin', mmPlaylistBinComponent);
  store.hydrate().then(() => {
    requestAnimationFrame(() => autoscrollIntoView());
  });
  startStatusSubscriber(store);
  startNowLine(() => Alpine.store('mm'));
});
