/**
 * Admin timeline view — entry point.
 *
 * Bootstrap order: Alpine.js auto-starts when the `defer` script loads,
 * triggering `alpine:init`. We register the store + components in that
 * handler so everything is set up before any `x-data` on the page
 * evaluates.
 *
 * PR-4a (this PR): scaffolding + read-only render. Subsequent PRs
 * (4b interactivity, 4c modals) extend store.js + add component
 * modules, but this file stays small.
 */
import { makeStore } from './store.js';

document.addEventListener('alpine:init', () => {
  // eslint-disable-next-line no-undef
  Alpine.store('mm', makeStore());

  // Kick off hydration — the section's x-init can also call this, but
  // doing it here means the timeline is ready as soon as Alpine is.
  // eslint-disable-next-line no-undef
  Alpine.store('mm').hydrate();
});
