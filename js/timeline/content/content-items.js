/**
 * Unified content model. Merges media (/api/media) + the animations registry
 * (window.MM_ANIMATIONS) into one list of content items, and maps a picked
 * content item to a playlist item. Pure + DOM-free (testable).
 */
function basename(p) { return String(p || '').split('/').pop() || ''; }

export function buildContentItems({ media = {}, animations = [] } = {}) {
  const out = [];
  for (const url of media.images || []) {
    out.push({ kind: 'image', ref: url, name: basename(url) });
  }
  for (const url of media.videos || []) {
    out.push({ kind: 'video', ref: url, name: basename(url), duration: (media.videoDurations || {})[url] });
  }
  for (const a of animations || []) {
    out.push({ kind: 'animation', ref: a.key, name: a.key, label: a.label });
  }
  return out;
}

// A picked content item becomes a playlist item with its duration left as
// "Auto" (no `duration` key) — the server resolves Auto to the content's
// natural length (video) or a 20s default (image/animation). Animations
// carry playmode:'SCRIPT' (the render-mode flag the display client reads);
// media carry no playmode (the server defaults to FULL).
export function contentItemToPlaylistItem(ci) {
  if (ci.kind === 'animation') return { file: ci.ref, playmode: 'SCRIPT' };
  return { file: ci.ref };
}

// Play-type vocabulary shared by the editor. Maps PlayMode → operator label.
const PLAY_TYPE_LABELS = { SEGMENT: 'Mesh', FULL: 'Mirror', INDIVIDUAL: 'Per-screen', SCRIPT: 'Animation' };
const MEDIA_PLAY_TYPES = ['SEGMENT', 'FULL', 'INDIVIDUAL'];

export function playTypeLabel(mode) {
  return PLAY_TYPE_LABELS[mode] || '— pick play type —';
}

// Media items (non-animation) must have an explicit, valid play type before a
// playlist can be saved/played. Returns the items still needing a choice.
export function mediaItemsMissingPlayType(items) {
  return (items || []).filter(
    (it) => it.playmode !== 'SCRIPT' && !MEDIA_PLAY_TYPES.includes(it.playmode));
}
