import { makeStore } from './store.js';
import { mmTimelineComponent } from './timeline/timeline.js';
import { mmToolbarComponent } from './toolbar.js';
import { startStatusSubscriber } from './timeline/sockjs-status.js';

document.addEventListener('alpine:init', () => {
  const store = makeStore();
  Alpine.store('mm', store);
  Alpine.data('mmTimeline', mmTimelineComponent);
  Alpine.data('mmToolbar', mmToolbarComponent);
  store.hydrate();
  startStatusSubscriber(store);
});
