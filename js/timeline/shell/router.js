/**
 * URL-hash ⇄ store.activeTab. The hash is the single source of route truth
 * (bookmarkable, back-button friendly); the store mirrors it. store.goTo(tab)
 * sets the hash, this listener reflects it back into activeTab.
 */
export const ROUTES = ['now', 'content', 'schedule', 'fleet'];

export function parseHash(hash) {
  const t = String(hash || '').replace(/^#/, '');
  return ROUTES.includes(t) ? t : 'now';
}

export function startRouter(store) {
  const sync = () => store.setActiveTab(parseHash(location.hash));
  window.addEventListener('hashchange', sync);
  sync();   // initial route on load
}
