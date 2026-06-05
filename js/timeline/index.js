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

document.addEventListener('alpine:init', () => {
  // Subsequent tasks register Alpine.store('mm') and components here.
  console.log('[timeline] alpine:init fired');
});
