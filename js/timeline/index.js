import { makeStore } from './store.js';
import { mmTimelineComponent } from './timeline/timeline.js';
import { mmToolbarComponent } from './toolbar.js';

document.addEventListener('alpine:init', () => {
  Alpine.store('mm', makeStore());
  Alpine.data('mmTimeline', mmTimelineComponent);
  Alpine.data('mmToolbar', mmToolbarComponent);
  Alpine.store('mm').hydrate();
});
